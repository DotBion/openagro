# How to score `human_spotcheck_30.csv`

This is a 30-item spot check of model-written answers from the current chat
pipeline (quote-first generator, hybrid+rerank retrieval, FarmDeck demotion on
effectiveness questions). The LLM-as-judge scores in `results/e2e_eval.json`
are **uncalibrated until these 30 labels exist**. Do not treat judge numbers
as a gate before then.

## File

`qna/human_spotcheck_30.csv`

Columns already filled (do not overwrite):

- `id`, `category`, `expected_behavior`, `question`, `gold_doc_ids`, `gold_answer`, `paraphrase_of`, `notes`
- `model_answer` — the issued answer from the latest end-to-end run (`results/e2e_items.jsonl` when that id is present, otherwise the extractive fallback). If the model refused or hedged, that issued text is what you score.
- `model_source` — `e2e_items` or `extractive_e2e`.
- `model_generator` — `llm`, `extractive`, `extractive_hedge`, or `refusal_template`. On the 2026-11-17 afternoon run most spot-check rows are extractive because Gemini hit a hard quota after two items; the issued extractive text is still what you score.
- `model_status` — `ok`, `answer_with_caveat`, `hedged`, or `refused`
- `model_route` — route the system took

Empty columns for your labels (leave blank until you score):

- `reviewer_name`
- `human_checked` — `y` when you have finished the row
- `grounded` — `1` if the issued answer invents no facts, numbers, or program terms beyond the gold answer / cited library; `0` otherwise. A refusal or hedge that does not invent facts is `1`.
- `refusal_ok` — `1` if the system refused when `expected_behavior` is `refuse`, and did not refuse when the item should be answered (including `answer_with_caveat`). `0` otherwise.
- `caveat_ok` — `1` if `expected_behavior` is `answer_with_caveat` and the issued answer explicitly flags a crop, region, season, year, or abstract-only mismatch; or `1` if caveat is not expected and the answer does not need one. `0` if a needed caveat is missing or a caveat is used to paper over an invented claim. Use `NA` only if the item was correctly refused.
- `invented_numbers` — `0` if every number in the issued answer is supported by the gold / library, or if the issued text is a refusal/hedge with no numbers. Otherwise the count of unsupported or uncited numbers.
- `verdict` — short overall tag: `pass`, `fail`, or `borderline`
- `comments` — free text (why a 0, or anything a reviewer should know)

## Scoring rules (short)

1. Read the question, the gold answer (if any), and the issued `model_answer` only. Do not reward fluent wording that is not in the gold / source.
2. A correct refusal is a pass on `refusal_ok` and should not be penalized on `grounded` just for declining.
3. For funding answers, saying “you qualify” / “you are eligible” is a fail on `grounded`. An as-of date and official URL should be present when a program is described.
4. For abstract-only research, effect sizes that are not in the abstract are a fail.
5. Mark `human_checked=y` only when every label column for that row is filled.

When all 30 rows are labeled, the judge metrics can be compared to these labels. Until then they stay uncalibrated.
