# OpenAgronomy knowledge base

Local corpus of public Soil Health Exchange and FarmDeck pages, plus abstracts of studies cited on FarmDeck (and other DOIs found in those pages). Nothing in this folder was emailed or posted; it lives on this computer only.

- README generated: 2026-11-17 10:25 EST (America/New_York is the box timezone).
- Scrape started: `2026-11-17T09:43:27-05:00`
- Scrape / fetch finished: `2026-11-17T09:51:04-05:00`
- Scripts: `/workspace/openagronomy/scripts`

## Counts versus expected

Expected counts come from the earlier public-site inventory (206 funding programs, 73 Journal articles, 33 expert Q&As, 57 FarmDeck product profiles). Live sitemaps can add or remove pages after that inventory.

| Source | Expected | Discovered | On disk (raw HTML or abstract sidecar) | KB records | Discrepancy vs expected |
|---|---:|---:|---:|---:|---|
| `she_funding` | 206 | 206 | 206 | 206 | match |
| `she_journal` | 73 | 76 | 76 | 76 | +3 |
| `she_qa` | 33 | 33 | 33 | 33 | match |
| `farmdeck_product` | 57 | 57 | 57 | 57 | match |
| `paper_abstract` | n/a (optional) | 347 unique DOIs | 280 sidecars | 280 | 280 abstracts fetched; 63 DOIs had metadata but no abstract; 4 still not found after cleaning trailing /full from extracted DOIs |

### Discrepancy notes

- `she_journal` live-sitemap-only URLs (added since the Nov 15, 2026 snapshot): `https://soilhealthexchange.com/blog/integrated-crop-livestock-economics`, `https://soilhealthexchange.com/blog/manure-management-economics`, `https://soilhealthexchange.com/blog/no-till-economics`

The Journal count is 76 rather than 73 because the live sitemap now includes pages that were not in the Nov 15, 2026 snapshot, or listing-page filters differed. See the live-only / snapshot-only lists above.

## Failures and blocks

- Abstract not found: `10.1007/s13593-021-00729-y` — not_found
- Abstract not found: `10.1007/s00374-002-0522-8` — not_found
- Abstract not found: `10.1016/j.apsoil.2022.104702` — not_found
- Abstract not found: `10.1093/jee/tox235.full` — not_found
- DOIs with no abstract in Crossref or OpenAlex: `10.1016/j.scitotenv.2023.166948`, `10.1007/s11104-012-1237-0`, `10.1007/s11104-009-0131-x`, `10.1007/s13593-013-0197-y`, `10.1007/s00572-008-0181-6`, `10.1007/s11104-011-1036-z`, `10.1023/A:1014408723664`, `10.1007/s00572-019-00884-4`, `10.1016/j.biocontrol.2008.01.014`, `10.1007/s11104-009-0223-7`, `10.1016/j.still.2011.08.001`, `10.1016/j.still.2023.105956`, `10.1016/j.fcr.2019.107661`, `10.4315/0362-028X-70.4.828`, `10.1016/j.eja.2019.01.003`, `10.1016/j.agee.2014.10.024`, `10.1007/BF00010845`, `10.1016/j.fcr.2014.05.005`, `10.1007/s11104-018-3653-2`, `10.1016/S0007-1536(70)80110-3`, `10.1016/S0007-1536(63)80079-0`, `10.1016/j.soilbio.2022.108574`, `10.1016/j.soilbio.2010.12.010`, `10.1007/s00572-007-0118-5`, `10.1016/j.apsoil.2021.104225`, `10.1016/S0929-1393(01)00152-4`, `10.1016/S0929-1393(03)00044-1`, `10.1016/j.soilbio.2010.11.021`, `10.1016/j.soilbio.2019.05.023`, `10.1016/j.apsoil.2015.06.001`, `10.1007/s00374-022-01670-9`, `10.1016/S0038-0717(03)00154-8`, `10.1007/s002489900082`, `10.1016/j.geoderma.2020.114890`, `10.1016/j.apsoil.2023.104810`, `10.1016/j.soilbio.2018.10.010`, `10.1016/j.apsoil.2010.10.002`, `10.1007/s00248-007-9295-1`, `10.1016/j.geoderma.2020.114814`, `10.1007/s003740050533`, `10.1007/s11368-025-03991-2`, `10.1016/j.agee.2023.108591`, `10.1016/j.agee.2020.106841`, `10.1007/s11104-017-3199-8`, `10.1038/nrmicro.2017.87`, `10.1016/j.soilbio.2019.107686`, `10.1016/j.geoderma.2020.114696`, `10.1071/AP08029`, `10.1038/nmicrobiol.2016.242`, `10.1016/j.mimet.2011.01.016`, `10.1038/s41587-020-0548-6`, `10.1038/nrmicro3109`, `10.1038/nature13855`, `10.1038/nrmicro1987`, `10.1016/j.apsoil.2011.11.008`, `10.1038/s41559-023-01986-1`, `10.1016/j.agee.2008.05.003`, `10.1016/j.agee.2010.03.010`, `10.1007/BF00000786`, `10.1016/j.soilbio.2015.02.020`, `10.31274/cpn-20240904-0`, `10.31274/cpn-20190620-014`, `10.1023/B:FRES.0000025287.52565.99`

Robots.txt was respected. Soil Health Exchange and FarmDeck both disallow `/api/` (and admin/studio paths); those were not requested. OpenAgData `/api` remains disallowed and was not used. Requests were spaced at about one per second with a normal browser User-Agent.

## Field coverage

A field is counted as filled when it is present and non-empty on the KB record. Crop and practice are filled only from tags or labeled fields stated on the page, not inferred from body prose.

### Expert Q&As (`she_qa`)

| Field | Filled | Of | % |
|---|---:|---:|---:|
| `title` | 33 | 33 | 100.0 |
| `date` | 33 | 33 | 100.0 |
| `author` | 33 | 33 | 100.0 |
| `expert` | 33 | 33 | 100.0 |
| `question` | 33 | 33 | 100.0 |
| `states` | 31 | 33 | 93.9 |
| `region` | 33 | 33 | 100.0 |
| `crop` | 19 | 33 | 57.6 |
| `practice` | 24 | 33 | 72.7 |
| `tags` | 33 | 33 | 100.0 |
| `identifier` | 33 | 33 | 100.0 |
| `expert_answer` | 33 | 33 | 100.0 |
| `references` | 27 | 33 | 81.8 |

### Journal articles (`she_journal`)

| Field | Filled | Of | % |
|---|---:|---:|---:|
| `title` | 76 | 76 | 100.0 |
| `date` | 76 | 76 | 100.0 |
| `author` | 76 | 76 | 100.0 |
| `identifier` | 76 | 76 | 100.0 |
| `crop` | 7 | 76 | 9.2 |
| `practice` | 52 | 76 | 68.4 |
| `tags` | 67 | 76 | 88.2 |
| `states` | 6 | 76 | 7.9 |
| `references` | 71 | 76 | 93.4 |
| `keywords` | 64 | 76 | 84.2 |

### Funding programs (`she_funding`)

| Field | Filled | Of | % |
|---|---:|---:|---:|
| `title` | 206 | 206 | 100.0 |
| `funder` | 206 | 206 | 100.0 |
| `amount` | 206 | 206 | 100.0 |
| `cost_share` | 206 | 206 | 100.0 |
| `deadline` | 206 | 206 | 100.0 |
| `states` | 206 | 206 | 100.0 |
| `coverage` | 206 | 206 | 100.0 |
| `eligibility_text` | 163 | 206 | 79.1 |
| `application_url` | 206 | 206 | 100.0 |
| `last_verified` | 206 | 206 | 100.0 |
| `who_can_apply` | 206 | 206 | 100.0 |
| `readiness` | 206 | 206 | 100.0 |
| `practice` | 149 | 206 | 72.3 |
| `tags` | 206 | 206 | 100.0 |

### FarmDeck products (`farmdeck_product`)

| Field | Filled | Of | % |
|---|---:|---:|---:|
| `title` | 57 | 57 | 100.0 |
| `category` | 57 | 57 | 100.0 |
| `cited_dois` | 21 | 57 | 36.8 |
| `last_verified` | 57 | 57 | 100.0 |
| `description` | 57 | 57 | 100.0 |
| `crop` | 32 | 57 | 56.1 |
| `practice` | 7 | 57 | 12.3 |
| `tags` | 57 | 57 | 100.0 |

### Paper abstracts (`paper_abstract`)

| Field | Filled | Of | % |
|---|---:|---:|---:|
| `title` | 280 | 280 | 100.0 |
| `date` | 280 | 280 | 100.0 |
| `author` | 280 | 280 | 100.0 |
| `doi` | 280 | 280 | 100.0 |
| `abstract` | 280 | 280 | 100.0 |
| `provider` | 280 | 280 | 100.0 |
| `licenses` | 265 | 280 | 94.6 |

## Chunks (v1, kept)

`kb/chunks.jsonl` is the first pass. Token figures below are the original word-based `token_estimate`, not the bge-m3 tokenizer. Keep this file; retrieval work should use v2.

| Source | Chunks | Parents | Min | Median | Max | Avg |
|---|---:|---:|---:|---:|---:|---:|
| all | 1903 | 652 | 82 | 417 | 580 | 393.9 |
| `she_qa` | 133 | 33 | 115 | 401 | 560 | 394.7 |
| `she_journal` | 986 | 76 | 82 | 420 | 578 | 395.7 |
| `she_funding` | 209 | 206 | 162 | 334 | 570 | 345.8 |
| `farmdeck_product` | 204 | 57 | 83 | 451.5 | 580 | 416.6 |
| `paper_abstract` | 371 | 280 | 108 | 436 | 578 | 403.7 |

1903 chunks from 652 parent documents. 640 chunks fall outside the original 300–500 word-estimate target, usually short funding pages or leftover tails.

## Chunks v2 (bge-m3 tokens)

`kb/chunks_v2.jsonl` is the retrieval corpus. Token counts use the real `BAAI/bge-m3` tokenizer (`AutoTokenizer.from_pretrained('BAAI/bge-m3').encode(text, add_special_tokens=False)`). 
Embedding model `BAAI/bge-m3`, expected dimension `1024`, chunk version `v2`. See `kb/index_manifest.json` for the startup guard fields.

| Source | Chunks | Parents | Min | Median | Max | Avg |
|---|---:|---:|---:|---:|---:|---:|
| all | 1838 | 652 | 117 | 462 | 1668 | 460.0 |
| `she_qa` | 87 | 33 | 395 | 743 | 991 | 724.9 |
| `she_journal` | 1045 | 76 | 117 | 446 | 620 | 434.4 |
| `she_funding` | 206 | 206 | 171 | 343 | 705 | 357.2 |
| `farmdeck_product` | 220 | 57 | 125 | 438.5 | 589 | 409.3 |
| `paper_abstract` | 280 | 280 | 182 | 582.5 | 1668 | 588.8 |

Chunking rules:

- SHE expert Q&A: keep each question+answer whole unless it is over ~1000 tokens; then split at sections and repeat a short document header.
- Funding programs: keep each program page whole (structured fields + eligibility) unless over ~1000 tokens. `deadline_iso` is parsed only when a single close date is clearly stated; `expired` is versus 2026-11-17; `as_of` is the last-verified ISO date.
- Journal articles: heading-aware windows of 350–500 tokens with about 15% overlap. Short heading stubs may be folded into the next window.
- Paper abstracts: exactly one chunk per abstract, even if outside the 350–500 window. `abstract_only: true`, trust tier 4.
- FarmDeck products: heading-aware 350–500 tokens, no overlap. Trust tier 5.
- Every chunk has raw `text` plus `embed_text` (contextual header: title + section path, source type, state/coverage if known, then the text).

Funding flags on 206 program chunks: `deadline_iso` filled on 91; `expired` True=34, False=131, unknown/null=41; `as_of` filled on 206. Expired is computed against 2026-11-17.

Window notes: 394 chunks are under 350 tokens (short funding pages kept whole, leftover tails after heading-aware splits, and a few short abstracts). 691 are over 500 (SHE Q&As kept whole up to ~1000, journal overlap plus folded stubs, long abstracts kept whole). 5 abstracts exceed 1000 tokens and were still kept as a single chunk.

Trust tiers in v2 (different from v1): `1` expert Q&A, `2` peer-reviewed full source (none in this corpus), `3` SHE Journal article, `4` abstract-only paper (`abstract_only: true`), `5` FarmDeck product/commercial, `program_page` for funding program pages.

## Q&A sets

- Real expert Q&As (kept): `/workspace/openagronomy/qna/real_qa.jsonl` — 33 rows, copied from the published SHE pages.
- Synthetic questions (kept): `/workspace/openagronomy/qna/synthetic_qa.jsonl` — 52 rows, all with `synthetic: true`. By category: {'funding': 15, 'evidence': 15, 'farmx': 10, 'should_refuse': 6, 'out_of_scope': 6}. 30 have a gold answer grounded in KB text; 22 have `gold_answer` null (FarmX items, refuse/out-of-scope items, or facts that could not be verified in the KB).

- Eval v2: `/workspace/openagronomy/qna/eval_v2.jsonl` — 200 items (33 real, 167 synthetic). Prior real and synthetic items are included, remapped to the eval schema. Paraphrases of the 33 published Q&As, ~10 partial-match items, and ~6 safety-refusal items were added. Every item has `human_checked: false`. `gold_answer` is null unless already grounded in the prior set or copied from a published expert answer.

Eval v2 counts by category:

| Category | Count |
|---|---:|
| `expert_qa` | 33 |
| `paraphrase` | 99 |
| `funding` | 15 |
| `evidence` | 15 |
| `farmx` | 10 |
| `should_refuse` | 6 |
| `out_of_scope` | 6 |
| `partial_match` | 10 |
| `safety_refusal` | 6 |

Eval v2 counts by expected behavior:

| expected_behavior | Count |
|---|---:|
| `answer` | 162 |
| `refuse` | 29 |
| `answer_with_caveat` | 9 |

- Human spot-check sample: `/workspace/openagronomy/qna/human_spotcheck_30.csv` — 30 synthetic items, stratified with a floor of 2 per category (paraphrase stratum capped) using `random.Random(20261117)`. By category in the sample: {'evidence': 2, 'farmx': 3, 'funding': 5, 'out_of_scope': 2, 'paraphrase': 7, 'partial_match': 4, 'safety_refusal': 3, 'should_refuse': 4}. Columns include `reviewer_name`, `verdict` (pass / fail / revise), and `comments`. Nothing has been sent for review.

## Answer verifier

`scripts/verify_answer.py` checks a model answer that uses citation markers of the form `[c:<chunk_id>]` against the retrieved chunks. It reports pass/fail as JSON, with a reason for each check.

Checks:

- Every cited chunk id must be in the retrieved set.
- Every number in the answer (plain values, units, %, $, dates, ranges) must appear in a cited chunk after normalization (comma stripping, unit synonyms such as bu/ac and bushels per acre, and deterministic unit conversion). Uncited numbers fail.
- Each sentence with a factual claim must have a citation. Key-claim spans need lexical support (token overlap / trigram match, configurable threshold, default 0.30) in the cited chunk; otherwise the report flags `cited_but_unsupported`.
- Funding answers must include an as-of / last-verified date and a source URL, and must not assert eligibility (`you qualify`, `you are eligible`, and similar).
- Citations to `abstract_only` chunks must not carry effect sizes that are absent from the abstract.

`scripts/units.py` converts lb/ac ↔ kg/ha and bu/ac ↔ kg/ha for corn (56 lb/bu) and soy (60 lb/bu). Tests live in `scripts/tests/` and use real v2 chunks.

Usage (from `/workspace/openagronomy`, after `python3 -m venv .venv` and installing `transformers`, `tokenizers`, `pytest`, `python-dateutil`, `requests`):

```bash
.venv/bin/pytest -q scripts/tests/
.venv/bin/python -c 'from verify_answer import verify_answer'  # run from scripts/, or:
.venv/bin/python scripts/verify_answer.py --help
```

If the verifier is run as a module, import `verify_answer(answer_text, retrieved_chunks)` and write the returned dict. Do not send reports off this computer.

## Files

```
kb/
  raw/she_qa/, raw/she_journal/, raw/she_funding/, raw/farmdeck_product/   # one HTML file per page
  raw/sitemaps/                                                             # live sitemaps used for discovery
  raw/paper_abstract/                                                       # compact Crossref/OpenAlex sidecars
  kb.jsonl
  chunks.jsonl            # v1, word-based token_estimate, kept
  chunks_v2.jsonl         # retrieval chunks, bge-m3 token_count
  index_manifest.json     # embedding model, tokenizer, dim 1024, chunk version
  logs/                   # discovery, fetch, parse, abstracts, chunks, qna, eval
  README.md
qna/real_qa.jsonl
qna/synthetic_qa.jsonl
qna/eval_v2.jsonl
qna/human_spotcheck_30.csv
scripts/rechunk_v2.py
scripts/build_eval_v2.py
scripts/verify_answer.py
scripts/units.py
scripts/tests/
```

## How to regenerate

From `/workspace/openagronomy`:

```bash
python3 scripts/build_all.py
```

Or step by step (use `.venv/bin/python` once the venv exists):

```bash
python3 scripts/fetch_pages.py            # discover sitemaps + fetch HTML (~1 req/sec)
python3 scripts/parse_kb.py               # parse raw HTML -> kb.jsonl + chunks.jsonl (v1)
python3 scripts/fetch_abstracts.py        # Crossref/OpenAlex abstracts for cited DOIs, append to kb.jsonl
python3 scripts/parse_kb.py               # re-chunk v1 after abstracts
.venv/bin/python scripts/rechunk_v2.py    # chunks_v2.jsonl + index_manifest.json
python3 scripts/build_qna.py              # real_qa.jsonl + synthetic_qa.jsonl (kept)
.venv/bin/python scripts/build_eval_v2.py # eval_v2.jsonl + human_spotcheck_30.csv
.venv/bin/pytest -q scripts/tests/        # verifier + unit conversion
python3 scripts/build_readme.py           # this file
```

`fetch_pages.py` skips HTML files that already exist and are larger than 500 bytes unless you pass `--force`.
Abstracts are retrieved for unique DOIs found in FarmDeck `cited_dois` and in page text or reference lists. Only abstracts are stored, not full text. License or OA status is copied from the API when present.

Do not fetch OpenAgData `/api` (disallowed by robots.txt). Do not send these files to anyone from the scripts.

