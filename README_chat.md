# OpenAgronomy local RAG chat

A local-only retrieval-and-chat prototype over the Soil Health Exchange / FarmDeck knowledge base in `/workspace/openagronomy`. Nothing here is emailed, posted, or sent to Slack or any person. Do not invent FarmX yields or other ungrounded numbers.

## How to run

From `/workspace/openagronomy`, using the project venv:

```bash
# 1) (once) embed chunks_v2 with BAAI/bge-m3 on CPU, if kb/index/embeddings_v2.npy is missing
.venv/bin/python scripts/embed_chunks.py

# 2) start the local web UI (default 127.0.0.1:8765)
.venv/bin/python -m app.server
# or: scripts/run_chat.sh
```

Open `http://127.0.0.1:8765`. The process refuses to start if `kb/index/embed_meta.json` does not match `kb/index_manifest.json` (embedding model, dimension 1024, chunk version). A server on this box was started with `scripts/run_chat.sh` / `python -m app.server` and answers on that URL (local only).

Other commands:

```bash
PYTHONPATH=/workspace/openagronomy:/workspace/openagronomy/scripts \
  .venv/bin/python scripts/eval_retrieval.py

PYTHONPATH=/workspace/openagronomy:/workspace/openagronomy/scripts \
  .venv/bin/python scripts/eval_e2e.py          # uses an LLM if a working key is in the environment
  .venv/bin/python scripts/eval_e2e.py --no-llm # extractive stub + rules router only
  .venv/bin/python scripts/fill_spotcheck.py    # copy issued answers into the 30-item human CSV
```

Dependencies live in `requirements-chat.txt`. Install the CPU torch wheel first:

```bash
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements-chat.txt
```

## Architecture

```
question
   │
   ├─ rules router (safety / FarmX / funding / weather) ── optionally a cheap LLM if no rule fires
   │     routes: evidence | funding | weather | refuse
   │     at most one extra tool (two tools total) for combined questions
   │
   ├─ tools
   │     search_evidence   hybrid (or the kept variant) over chunks_v2
   │     find_funding      field-level name/state BM25 fused in; state / practice / deadline filters; expired dropped
   │     get_weather       Open-Meteo; Boone County, IA default; meteorological spray window only
   │
   ├─ generator           quote-first LLM if a working key exists, else extractive stub
   │     2–4 short cited sentences (verbatim or close paraphrase); funding includes as-of + URL and never "you qualify"
   │
   └─ verify_answer.py    every draft; one regenerate on failure; then hedge/refuse
         traces: logs/chat_traces.jsonl  (no Langfuse: Docker and Langfuse keys are unavailable)
```

### Retrieval index (Days 3–5)

**Postgres was not used.** Justification:

- The box has `postgresql-client`, but the server package (`postgresql` / `postgresql-17`) has no apt candidate, and Docker is not installed, so ParadeDB / `pg_search` cannot be run.
- About 8 GB RAM is typically available on this shared 15 GB machine, which is already needed for `BAAI/bge-m3` on CPU.
- The corpus is 1,838 chunks; a 1838 × 1024 float32 matrix is 7.2 MB.

**Choice:** in-process NumPy cosine index (L2-normalized `embed_text` vectors from `BAAI/bge-m3`, dim 1024) plus Okapi BM25 (`rank_bm25` over `embed_text`). Hybrid fusion is Reciprocal Rank Fusion with **k = 60**. On funding-like questions a third list is fused in: a field-level BM25 over program name and state fields, plus exact/fuzzy name match (e.g. EQIP). Strong name hits are promoted so RRF or the reranker cannot bury them. The optional cross-encoder is `BAAI/bge-reranker-v2-m3` over the hybrid top 30. State and season (and a light crop signal) are **boosts, not filters** on evidence search. The `find_funding` tool may apply a structured state filter (state or nationwide) and fuses the same field index. Funding chunks with `expired=True` (34 programs vs 2026-11-17) are excluded from retrieval.

Startup guard: `rag/manifest_guard.py` compares `kb/index_manifest.json` to `kb/index/embed_meta.json` and the matrix shape, and refuses on mismatch.

Files: `kb/index/embeddings_v2.npy`, `kb/index/ids.json`, `kb/index/embed_meta.json`, `kb/index/bm25.pkl`, `kb/index/funding_fields_bm25.pkl`.

### Chat (Days 6–8)

- Router rules refuse pesticide label/tank-mix rates, livestock dosing/withdrawal, legal/tax advice, “do I qualify / am I guaranteed payment,” FarmX farm-record questions, credentials, and obvious out-of-scope topics. Clear funding or weather cues are routed without an LLM. Remaining questions can be classified by a cheap chat model, defaulting to `evidence`.
- `find_funding` returns program criteria, `as_of`, and a URL. It never asserts that the user qualifies.
- `get_weather` uses Open-Meteo. The default point is Boone County, Iowa (approx. 42.0365, −93.9313). The “spray window” is a simple meteorological filter (wind, temperature, RH, no precip) and is **not** a pesticide recommendation.
- Generators write 2–4 short sentences that are verbatim quotes or close paraphrases of the cited chunk, each sentence cited. The extractive fallback picks the 1–2 best-supported sentences rather than dumping passages. The verifier is unchanged. One retry, then hedge.
- When the evidence only partly matches (different crop, region, or season, or abstract-only), the pipeline attaches explicit caveat wording in a cited sentence and sets status `answer_with_caveat` if the verifier still passes.
- Every answer is passed through `scripts/verify_answer.py`. A failure triggers one regeneration (with the verifier report as feedback) and then a hedge if it still fails.
- Traces (`query`, `route`, `retrieved_ids`, `answer`, verifier report) are appended to `logs/chat_traces.jsonl`. Langfuse is not used (no Docker, no Langfuse keys).

### LLM availability

Keys are read from the environment only. Values are never logged or written to traces.

See `results/llm_availability.json` for the last probe (names and success only). In this build:

- Present but unusable: `OPENAI_API_KEY` (rejected by the API), `XAI_API_KEY` (rejected by the API), `OPENROUTER_API_KEY` (402, out of credits).
- Present and working (last probe): `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (Bedrock Nova Lite `us.amazon.nova-lite-v1:0` as the default generator), `GROQ_API_KEY` (router and judge; 429s are retried with Retry-After / exponential backoff), `GEMINI_API_KEY` (generator fallback).
- Absent: `ANTHROPIC_API_KEY`.
- Preference for the generator is Bedrock Nova Lite, then Gemini, then Groq, then OpenAI, then OpenRouter, then xAI. The router prefers Groq, then Nova Lite. If no working generator is found, the extractive stub still runs retrieval and the verifier.

## Retrieval results (eval_v2 items with `gold_doc_ids`)

<!-- RETRIEVAL_TABLES -->

Evaluated on 172 items that have gold document ids. The **after** numbers are from 2026-11-17, 1:48 PM ET, after field-level funding search was added. The **before** numbers are the previous run (2026-11-17, 11:41 AM ET), archived in `results/before_fixes/`. Recall is 1 when any retrieved chunk’s `parent_id` or `id` is in `gold_doc_ids`. Bootstrap 95% CIs are over items (10,000 resamples, seed 20261117). Expired funding programs were excluded. RRF k=60; reranker is `BAAI/bge-reranker-v2-m3` on the hybrid top 30. State still boosts, never filters, on this index.

### Overall, before → after

| Variant | Recall@5 before | Recall@5 after | MRR before | MRR after |
|---|---|---|---|---|
| `bm25` | 93.6% (89.5–97.1) | **96.5% (93.6–98.8)** | 0.886 (0.843–0.926) | **0.921 (0.886–0.955)** |
| `dense` | 91.9% (87.8–95.3) | **95.9% (93.0–98.8)** | 0.876 (0.829–0.918) | **0.926 (0.887–0.958)** |
| `hybrid` | 92.4% (88.4–95.9) | **95.3% (91.9–98.3)** | 0.864 (0.818–0.906) | **0.901 (0.861–0.938)** |
| `hybrid_rerank` | 96.5% (93.6–98.8) | **98.8% (97.1–100.0)** | 0.925 (0.888–0.957) | **0.962 (0.936–0.985)** |

Recall@10 after: `bm25` 97.7% (95.3–99.4), `dense` 97.7% (95.3–99.4), `hybrid` 98.3% (95.9–100.0), `hybrid_rerank` 98.8% (97.1–100.0). n=172.

### Funding category, before → after (n=15)

| Variant | Recall@5 before | Recall@5 after | MRR before | MRR after |
|---|---|---|---|---|
| `bm25` | 73.3% (53.3–93.3) | **100% (100–100)** | 0.662 (0.441–0.875) | **1.000** |
| `dense` | 60.0% (33.3–86.7) | **100% (100–100)** | 0.440 (0.238–0.656) | **0.947 (0.840–1.000)** |
| `hybrid` | 73.3% (46.7–93.3) | **100% (100–100)** | 0.641 (0.422–0.841) | **1.000** |
| `hybrid_rerank` | 80.0% (60.0–100) | **100% (100–100)** | 0.632 (0.431–0.833) | **1.000** |

All 15 funding golds are at rank 1 under `hybrid_rerank` after the change (three of them, including the official EQIP URL / last-verified question, had been misses at cutoff 5).

### Real vs synthetic after the change (recall@5 / MRR)

| Variant | Real recall@5 | Real MRR | Synthetic recall@5 | Synthetic MRR |
|---|---|---|---|---|
| `hybrid_rerank` | 100.0% (n=33) | 1.000 | 98.6% (96.4–100.0, n=139) | 0.953 (0.921–0.981) |

### Recall@5 by category after (`hybrid_rerank`)

| Category | n | Recall@5 | Recall@10 | MRR |
|---|---:|---|---|---|
| `evidence` | 15 | 93.3% | 93.3% | 0.933 |
| `expert_qa` | 33 | 100.0% | 100.0% | 1.000 |
| `funding` | 15 | 100.0% | 100.0% | 1.000 |
| `paraphrase` | 99 | 99.0% | 99.0% | 0.965 |
| `partial_match` | 10 | 100.0% | 100.0% | 0.803 |

### Reranker decision

**Keep `BAAI/bge-reranker-v2-m3`:** `True`. After the funding-field change, recall@5 rose by **3.49 percentage points** (hybrid 95.3% → hybrid+rerank 98.8%), which still meets the +3 percentage-point rule. Top-1 state match on queries that mention a state did **not** improve (0.940 → 0.800, n=50); nor did top-1 crop match (0.956 → 0.912, n=114). Default serving variant: `hybrid_rerank`.

### FarmDeck vs independent evidence (effectiveness questions, n=49)

Unchanged from the earlier run (funding-field fusion is off on non-funding questions):

| Variant | FarmDeck outranks independent | Rate |
|---|---:|---:|
| `bm25` | 5/49 | 10.2% |
| `dense` | 7/49 | 14.3% |
| `hybrid` | 4/49 | 8.2% |
| `hybrid_rerank` | 6/49 | 12.2% |
| `hybrid_rerank` + FarmDeck demotion | 0/49 | 0.0% |

Serving applies the demotion. After demotion, recall@5 is 97.7% (95% CI 95.3–99.4, n=172), recall@10 97.7%, MRR 0.948 (95% CI 0.917–0.976).

## End-to-end metrics vs gates

Gates: citation pass ≥ 85%, numbers pass ≥ 90%, refusal accuracy ≥ 90%, zero invented (uncited or unsupported) numbers in **issued** answers.

### Extractive stub + rules router (`--no-llm`), before → after

The after run is 2026-11-17, 1:52 PM ET, on the quote-first extractive generator. Before (2026-11-17, 11:44 AM ET) is in `results/before_fixes/`. Variant `hybrid_rerank`. n=200.

Status mix (answered = `ok` + `answer_with_caveat` + retries):

| Status | Before | After |
|---|---:|---:|
| answered (`ok`) | 34 | 127 |
| answered with caveat | 0 | 43 |
| hedged | 137 | 1 |
| refused | 29 | 29 |
| **issued factual answers** | **34** | **170** |

| Metric | Before | After | Gate | Met after? |
|---|---|---|---|---|
| Route accuracy | 98.5% (96.5–100.0, n=200) | 98.5% (96.5–100.0, n=200) | — | — |
| Retrieval recall@5 (during chat) | 96.5% (93.6–98.8, n=172) | 96.5% (93.6–98.8, n=172); **funding 15/15** (see note) | — | — |
| Citation pass (issued factual answers) | 100% (n=34) | 100% (n=170) | ≥ 85% | yes |
| Numbers pass (issued factual answers) | 100% (n=34) | 100% (n=170) | ≥ 90% | yes |
| Refusal accuracy | 100% (n=200) | 100% (n=200) | ≥ 90% | yes |
| Invented numbers (issued answers) | 0 | 0 | 0 | yes |
| Caveat behavior (9 expected `answer_with_caveat`) | 22.2% (n=9; most were hedged) | **55.6% (5/9 issued with caveat wording)** | — | — |
| Caveat behavior (10 `partial_match` items) | — | **60% (6/10)**; includes a correct refusal on `syn-partial-005` | — | — |

Citation/number rates are over issued factual answers (verifier not skipped). Hedged items return a non-answer; they contain no invented numbers. Refusal accuracy is over all 200 items. Chat-path recall@5 staying at 96.5% (166/172) is **not** a sign that the funding-field index change was skipped. The index eval (`results/retrieval_eval.json`, 1:48 PM ET) already re-measured after that change: overall `hybrid_rerank` recall@5 went **96.5% → 98.8%**, and funding recall@5 / MRR went **80.0% / 0.632 → 100% / 1.000**. Chat-path recall is measured on tool-returned ids: `search_evidence` applies FarmDeck demotion, and `find_funding` returns a shorter, state-filtered list. Funding chat recall is 15/15 after the change. The six chat-path misses are `syn-para-SHE-FA-2026-0037-02`, `syn-ev-008`, `syn-ev-009` (routed to funding, so the FarmDeck gold was not in the funding tool's top 8), `syn-ev-014`, `syn-partial-002`, and `syn-partial-005` (correct refuse, 0 retrieved).

The four expected-caveat misses issued a verified `ok` answer without caveat wording, because `detect_mismatches` never fired:

- `syn-partial-002` ("cereal rye ahead of winter wheat in Kansas ... the way those Iowa corn studies show"): the question names both Kansas/wheat and Iowa/corn. A hit on *any* named crop or state counts as a match, so a K-State wheat chunk plus an Iowa recipe cleared the detector.
- `syn-partial-006` ("anhydrous ammonia ... Louisiana sugarcane ... Nebraska corn ground"): same any-match logic. Illinois/Nebraska corn guidance matched the Nebraska/corn wording, so Louisiana sugarcane was never flagged.
- `syn-partial-009` ("virtual fence collars ... sheep ... hill farms in the UK"): "UK" is not in the US-centric region list, and the retrieved SHE article already mentions sheep, so neither region nor crop fired.
- `syn-partial-010` ("nitrogen credit ... 1990s Minnesota trial work, not the newer Iowa studies"): the question names both Minnesota and Iowa; the newer Iowa source matched the Iowa mention. Year/era contrast is not a mismatch signal.

Category route / refusal accuracy after is unchanged from the earlier extractive run (funding and evidence 93.3% route accuracy; `partial_match` 90%; all other categories 100%; refusal 100% in every category).

<!-- E2E_TABLES -->
### LLM generator + judge (Amazon Nova Lite, 4:41 PM ET)

The default generator is now **Amazon Nova Lite** on AWS Bedrock (`us.amazon.nova-lite-v1:0` via the Converse API, us-east-1 / us-west-2). Gemini and Groq remain fallbacks if Bedrock is unavailable. The router stays on Groq (`qwen/qwen3.8-27b`) when that key works; otherwise Nova Lite can route. A 200-item model-written eval finished at **4:41 PM ET** on 2026-11-17 (`--tag nova`, `results/e2e_nova_eval.json`, `results/e2e_nova_items.jsonl`). Quote-first prompting and the strict verifier were unchanged. After a first pass, Nova often cited a parent document id (`[c:SHE-FA-2026-0037]`) instead of the exact chunk id (`[c:SHE-FA-2026-0037::c1]`) and sometimes placed the marker after the period; `normalize_llm_citations()` rewrites those slips to a retrieved chunk id and the 60 hedged rows were rerun. The numbers below are the final 200-item file.

**Generator mix:** `llm` (Nova) **150/200 (75%)**, refusal template 30, verified extractive fallback 19, unverified hedge 1. Among non-refusal items, 150/170 (88%) are Nova-written.

Status mix: answered (`ok`) 115, `ok_after_retry` 18, answered with caveat 16, `answer_with_caveat_after_retry` 3, verified extractive fallback (`hedged_extractive`) 17, unverified hedge 1, refused 30. Issued factual answers (excluding the 17 verified extractive fallbacks and the 1 hedge): **152/200**.

| Metric | Extractive (`--no-llm`) | Nova Lite | Gate | Met (Nova)? |
|---|---|---|---|---|
| Route accuracy | 98.5% (96.5–100, n=200) | 98.0% (96.0–99.5, n=200) | — | — |
| Chat recall@5 | 96.5% (93.6–98.8, n=172) | 95.9% (93.0–98.8, n=172) | — | — |
| Citation pass | 100% (n=170) | 100% (n=169) | ≥ 85% | yes |
| Numbers pass | 100% (n=170) | 100% (n=169) | ≥ 90% | yes |
| Refusal accuracy | 100% (n=200) | 99.5% (98.5–100, n=200) | ≥ 90% | yes |
| Invented numbers | 0 | 0 | 0 | yes |
| Caveat (9 expected) | 55.6% (5/9) | **77.8% (7/9)** | — | — |
| Caveat (10 partial_match) | 60% (6/10) | **80% (8/10)** | — | — |
| Hedge / extractive fallback | 1 unverified hedge | 17 verified extractive fallbacks + 1 unverified hedge | — | — |
| Avg issued-answer length | 449 characters | 325 characters (322 for the 150 Nova answers) | — | — |

The one refusal miss is `syn-para-SHE-FA-2026-0021-03` (a paraphrase that should have been answered; the router refused it). Caveat misses on this run: `syn-partial-007`, `syn-partial-010`.

Nova Lite tokens for this eval effort (both the first pass and the 60-item resume), from `results/llm_usage.jsonl`: **1,836,294 input + 33,751 output = 1,870,045 total tokens** in 337 Bedrock calls. Estimated on-demand cost **$0.118** at the 2026-11-17 list price of **$0.06 / $0.24 per 1M input/output tokens** for `amazon.nova-lite-v1:0` in us-east-1 / us-west-2 ([Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)).

The LLM judge is Groq (different family from Nova) and stays **uncalibrated**. Only 4 items were scored before Groq 429s; do not use those scores as a gate.

Three example pairs (Nova first, extractive second) are in `results/e2e_nova_vs_extractive.json`. Short versions:

- `SHE-FA-2026-0011` (aragonite studies): Nova writes two short cited sentences ("Aragonite and calcite are two crystal forms of the same compound, CaCO3"). The extractive fallback copies marketing bullets from the question, including "20–30% potential".
- `syn-fund-005` (official EQIP page and last-verified date): Nova cites the official NRCS URL and "November 14, 2026". Extractive quoted a different program (an EQIP bridge loan).
- `syn-para-SHE-FA-2024-0001-02` (Nebraska gumbo under pivot): both are grounded; Nova is slightly tighter ("Gumbo is a dense, clay-rich soil with low permeability. Hold off on re-tiling and moving soil.").

See also `results/e2e_extractive_eval.json` and `results/e2e_extractive_before_after.json`.

## Known gaps

- No Postgres / pgvector / ParadeDB on this box; the in-process index is a deliberate fallback, not a production service.
- `OPENAI_API_KEY` and `XAI_API_KEY` were set but rejected by those APIs; they are not used.
- Langfuse is not running (no Docker, no Langfuse keys). Traces are local JSONL only.
- `eligibility_text` was omitted from `chunks_v2.jsonl` at rechunk time; the store copies it from `kb.jsonl` at load (163/206 programs). Other funding flags (`as_of`, `deadline_iso`, `expired`) are already on the v2 chunks.
- There is no peer-reviewed full-text source in the corpus (trust tier 2 is empty). Abstracts stay `abstract_only`.
- FarmX field data is not in the KB; FarmX questions are refused rather than invented.
- The meteorological spray window is not a label, legal limit, or crop-specific recommendation.
- As-of dates, official URLs, and some eligibility text often live in chunk metadata rather than the body; `visible_text()` copies those fields into the text the generator and verifier see, without changing embeddings.
- Extractive coverage is much higher after quote-first generation (170/200 issued vs 34/200 before) with no rise in invented numbers or citation failures. One item was still hedged.
- Caveat wording reached 7/9 expected `answer_with_caveat` items (8/10 on all `partial_match` items, counting the correct refusal) on the completed after-fix e2e run. The two remaining misses (`syn-partial-006`, `syn-partial-010`) still publish a verified answer without an explicit caveat, because `detect_mismatches` treats any overlap with a crop or state named in the question as a match and does not compare years/eras.
- Funding retrieval is no longer the weak category on this index (hybrid+rerank funding recall@5 100%, MRR 1.000). The reranker can still bury a name match unless the promotion hook runs; that hook is in `rag/retrieve.py`.
- The reranker still slightly increases how often FarmDeck outranks independent evidence (6/49 vs 4/49 for hybrid) until the serving demotion is applied.

- Journal / FarmDeck chunk lengths still wander outside 350–500 tokens (documented in `kb/README.md`).
- E2E LLM-as-judge is uncalibrated until the 30-item human spot-check is labeled. How to score: `qna/human_spotcheck_howto.md`. A 200-item Nova Lite generator eval is in `results/e2e_nova_eval.json` (150/200 answers are model-written). The spot-check CSV can be refreshed from that file with `scripts/fill_spotcheck.py` if desired; label columns stay empty until a person scores them.
- Open-Meteo is a live public forecast; it is the only outbound HTTP the chat tools make at answer time (plus the optional LLM provider).

## File paths

```
kb/chunks_v2.jsonl
kb/index_manifest.json
kb/index/embeddings_v2.npy
kb/index/embed_meta.json
kb/index/ids.json
kb/index/bm25.pkl
kb/index/funding_fields_bm25.pkl
qna/eval_v2.jsonl
qna/human_spotcheck_30.csv
qna/human_spotcheck_howto.md
rag/          retrieval, features, manifest guard
chat/         router, tools, generator, pipeline, traces
app/server.py + app/static/index.html
scripts/embed_chunks.py
scripts/eval_retrieval.py
scripts/eval_e2e.py
scripts/verify_answer.py
scripts/run_chat.sh
logs/chat_traces.jsonl
results/retrieval_eval.json
results/e2e_eval.json
results/e2e_extractive_eval.json
results/retrieval_before_after.json
results/llm_availability.json
README_chat.md
```
