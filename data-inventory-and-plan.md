# OpenAgronomy: verified data inventory and grounded build plan (Nov 15, 2026)
Read-only check. Nothing was sent. Data requirements tracker: /workspace/openagronomy/required-data.md

## 1. Data inventory
| Platform / dataset | Verified size and fields | How verified | Access | Quality / gaps | Status vs. Saurav's claim |
|---|---|---|---|---|---|
| SHE funding library | 206 programs on soilhealthexchange.com/funding ("206 programs, checked weekly"; "157 state and regional"). The homepage and /impact both say 207 (57 national + 150 state/regional), "207 verified in the last 90 days". The sitemap has 206 program slugs. Fields per program: type, funder, amount, readiness, who can apply, coverage, deadline text, eligibility prose, tags, official URL, last-verified date (e.g. /funding/usda-nrcs-eqip, verified Nov 7, 2026) | /funding, /, /impact, /sitemap.xml | Public pages plus schema.org JSON-LD | Eligibility is free text, not structured rules. US only. | 207 claim: partially verified (the site shows 206 and 207 on different pages) |
| SHE expert network | Site says 29 verified experts in 17 states and 5 countries. The /ask dropdown lists 30 named people plus "SHE Team". The sitemap has 30 individual profile slugs. | /, /impact, /ask, sitemap | Public | 3 India-based experts; US-heavy | 29: verified as the site's own number (I count 30 names; small discrepancy) |
| SHE field answers (Q&A) | 33 published answers, 10 expert contributors, 2,104 total reads (/hub). 36 questions received, 33 answered, 15 states, 40,806 acres reported on 17 questions (/impact). Each answer has topic, crop, state, acres, the farmer's verbatim question, the expert, dates, and cited sources. Nebraska 10, Wisconsin 6, Iowa 3. | /hub, /impact | Public | Too few for a 100–200 question eval. No India questions. | Slack Nov 10 said 29; it's 33 now (growing): verified |
| SHE Journal | 73 articles (sitemap: 74 blog URLs minus /top-posts). Slack Nov 10 said 67. | /, /impact, sitemap | Public | Good RAG source | Verified (growing) |
| Cover Crop Lab | Live, v4.1.0. Built on USDA PHZM 2023, ERA5 normals, GeoNames, SARE | /tools/cover-crop-selector | Public UI | No public API | 75 plans: unverified (internal) |
| Soil report interpretation (/assess) | Live. Inputs: location→MLRA, depth, land use, crop. 1 free report, then $9 | /assess | Public UI | No API | n/a |
| CEMA 216 course | Live, $25, certificate at 85% or higher | /learn/cema-216 | Public | n/a | 49 certificates to 21 learners: unverified |
| My Farm | Live; free plus $9/mo membership. Profile: state, system, practices, goal, fields, soil reports, saved funding | /my-farm | Login | Saurav calls it "being developed and tested" | 13 accounts: unverified |
| Newsletter, GA4 | n/a | Slack #soil-health-exchange, Nov 10 | Internal | n/a | 100 subscribers; 6,369 / 7,737 / 14,083: unverified |
| OpenAgData papers | 1,937 source papers, 49 US states + DC, 12 research hubs (homepage). /data: "1,937 published records… 41 records with a source-reviewed county or field site in the structured layer"; crop-year and practice extraction "underway". The observation schema (25-row public sample): state, county, FIPS, coordinates, tillage, cover crop, crop, irrigation, outcome with units and depth, verbatim sentence, confidence, title, DOI. Also 1,154 researcher pages, 1,144 topic pages, 81 research funding calls (sitemap) | open-ag-data.com, /data, /hub/rodale-institute, sitemap | Public counts. Paper data loads through /api, which robots.txt disallows, so I didn't read it. | Rich schema, but only about 2% (41/1,937) has curated location. Season, soil, and crop coverage are unknown. US only. Research funding calls are for researchers, not farmers. | Verified count; metadata coverage contradicts any assumption that it's ready to filter by place and season |
| FieldLog | Live PWA (manifest: "Log field work in seconds — activities, inputs, observations, and yields"). The client bundle shows auth, billing, projects/activities, /quick-log/voice-parse, /quick-log/message-ingest, /upload/parse, /weather/spray-check, offline logs, organic/certifier fields, pre-harvest interval, spray-record fields (applicator, product, wind/temperature, times), yields with moisture and lot, modules (Today, Work journal, Fields, Inputs & costs, Grazing, Yield history, Reports & records, Ask an expert) | fieldlog.info, /manifest.webmanifest, /assets/index-*.js | Login for data | Voice already exists, via the browser mic plus a server parse. No phone channel. | Live: verified. User count: never claimed, unverified |
| FarmDeck | 57 product research profiles: Seeds 29, Biologicals 6, Cover crops 7, Seed treatments 4, Crop protection 5, Fertility 6. Each has DOI-cited studies (e.g. cereal rye: 5 sources), "sources checked" date, limitations. Buying is a "demo"; 4 tools | farmdeck.co/products, sitemap, cereal-rye page | Public | No prices or supplier inventory; marketplace not live | Products link to research: verified |
| TerraValue | Live: Field report (score, six outcomes vs. a "conventional twin" from SSURGO + ERA5 + NASS, $/acre by who pays) and the 45Z tool; 3 more tools. Proposed: source-water contracts, watershed ledger, supply-shed scoring, lender/insurer file. Site: "not verified credits… not yet been checked against measured outcomes"; dashboard is a "private pilot" | terravalue.tech, /product, /about, /dashboard | Public; workspace needs an access link | A screening twin already exists. No payments yet. | Today: modeled estimates. Payments are aspirational: verified |

## 2. Plan (Nov 2026 – Jan 2027)
Architecture: Postgres + PostGIS + pgvector (+ ParadeDB pg_search for BM25). No GraphRAG. Relationships are join tables. Typed tools only, with row-level security per farm. A reranker runs only on the paper-search route. A post-generation numeric/citation verifier. A location and season resolver (PHZM/MLRA/county FIPS; US first). Voice read-back of numbers, products, and fields. Refusal with an "as of" date is a valid outcome.

Phase 0, Nov 16–30: data access and evals
- Get exports #1, #3, #5, #8 from required-data.md. Build the eval v0 from the 33 public field answers (topic, crop, state, verbatim question, expert answer, sources): split each into answerable sub-questions, add unanswerable and out-of-scope cases, and write noisy-speech variants. Target is about 100 items, labeled honestly as derived from 33 seeds.
- Relies on: SHE /hub (have), Journal 73 (have), FarmDeck 57 (have).

Phase 1, Dec 1–20: structured tools first
- Tools: `search_funding(state, applicant_type, practice_tags)`, `get_program(id)` (returns last_verified and URL), `get_field_history(field_id, range)`, `log_activity(...)` (with read-back), `spray_check(field)` (existing endpoint), `cover_crop_plan(...)` and `soil_report(...)` if they can be called.
- Eligibility: deterministic for about 20 hand-encoded programs. All others get filtering by coverage/state/applicant type plus "check the official page" with a link (no LLM eligibility verdicts).
- Blocked by: #2 (structured rules), #8/#10 (schemas), #11/#12 (tool access).

Phase 2, Dec 15 – Jan 10: evidence search
- Hybrid search over Journal (73) + field answers (33) + FarmDeck profiles (57) + OpenAgData records, with region as a boost, not a filter, plus an "evidence is from elsewhere" disclosure. Rerank only here. The verifier checks every number against a cited chunk or tool output.
- Blocked by: #5 export, #6 metadata (only 41/1,937 have a curated location; until then, filter on state at most), #7 full-text rights.

Phase 3, Jan 5–25: demo
- Voice FieldLog flow (build on the existing voice-parse plus read-back), morning brief (weather/spray-check plus funding deadlines), "scenario compare" by calling the existing TerraValue twin (#13), shown with its own "modeled, not measured" caveat.
- Metrics: router accuracy, recall@k, exact match on numbers and units, citation support, false and missed refusals, latency, and cost per channel. Baseline metrics (#15) only if Saurav provides them.
- Out of scope for Jan: phone telephony and India (#16 missing), payer/insurer data, FarmDeck checkout.

Eval set: what it can draw from today
- Have: 33 public Q&As, 73 articles, 57 product profiles, 206 program pages (for funding-lookup questions with deterministic answers).
- Needs a request: 3 unpublished questions, FieldLog voice samples, India questions (Pushpa), and an agronomist to spot-check about 20%.

## 3. Questions for Saurav and Mithun (draft only, not sent; fits the proposed weekly build call)
1. Funding: 206 or 207? Can we get a full export, and is any eligibility structured?
2. OpenAgData: can we get an export of the 1,937 records with column completeness? What can we legally store of the paper text? When will crop/practice/season extraction go beyond the 41 curated records?
3. FieldLog: how many accounts and weekly active loggers? Can we get the schema and the voice-parse I/O, plus 20–50 anonymized samples?
4. My Farm and FieldLog: one profile and account system, or two?
5. Can the assistant call Cover Crop Lab, /assess, and the TerraValue twin as functions? Who owns each one?
6. Can you send a GA4 export or screenshot backing 6,369 / 7,737 / 14,083 with the date range, plus the funnel from visitor to Ask to My Farm?
7. Consent: what do the current terms allow for sharing farmer records with advisors, buyers, and insurers?
8. India: which states and languages are Pushpa's groups in? Is India in scope for the January demo or after?
