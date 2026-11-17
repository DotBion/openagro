# FarmX Boone plan (for the FieldLog brainstorm, Nov 17, 2026, 8:00–9:30 PM ET)

One-page briefing. Read-only checks on Nov 17, 2026. Nothing has been sent. Detailed tracker: `/workspace/openagronomy/required-data.md` (rows 20–35).

## Why Boone County, Iowa (FIPS 19015)

Boone has the rare combination of a long, public plot archive and a local practice-comparison experiment, plus the usual free federal layers (soils, crop history, field outlines, weather, county yields) and 63 SHE funding programs that geographically match Iowa. Story County is close, but it does not have this pair of plot datasets.

Use two research sites, and do not mix them up:

- **COBS** (ISU South Reynoldson Research Farm, corner of 300th St. and Violet Ave.) is the measured history: layout, field operation dates, weather, grain yields, and rye biomass, 2008 into 2025.
- **Kelley Farm Drainage Plots** (ISU Kelley Research Farm, about 8 km northwest of Ames; USDA ARS / LTAR) is the “what if we changed practices” comparison, including tile-drainage nutrient loss.

Both are small research plots, not a farm. FarmX should be a clearly labeled synthetic Boone corn–soy operation that borrows those records.

## What FarmX is

A demo farm, not a real one. Label it synthetic in the data and in every screen.

Keep three kinds of numbers separate:

1. **Measured plot history** — COBS operations and harvests (and Kelley results only when a published source exists).
2. **County benchmark** — official NASS Boone County yields, once we confirm the 2025 figures from Quick Stats or the ISU table.
3. **Simulated FarmX field** — any yield or outcome we invent or get from TerraValue’s twin. Say that it is modeled or simulated.

Do not average these together or present plot yields as typical farm yields. Plot yields usually run higher.

## Data layers (as of Nov 17, 2026)

| Layer | Role | Status |
|---|---|---|
| COBS management + weather (DOI [10.6073/pasta/1fcda1d6564931b1dd4f15b05cf5cfa4](https://doi.org/10.6073/pasta/1fcda1d6564931b1dd4f15b05cf5cfa4), EDI `edi.2449.1`) | Season replay: planting, harvest, and spray dates; plot map; hourly weather | **Partial.** Public metadata and **CC BY 4.0** (cite; contact authors with questions; data “as is”). Tables include `planting_activities` (planting and emergence dates), `harvest_activities` (activity dates), `chemical_inputs` (application dates), treatments, and `weather_raw`. Also an ESRI plot shapefile. **All COBS treatments were no-till.** Metadata coverage is 2008-01-01 to **2024-12-31**, despite a 2008–2025 title. Drainage and soil-moisture packages are “forthcoming.” Files were not downloaded: EDI showed a human-verification wall, and the API returned 403 (also on unrelated packages). SILPH’s live database is request-only (`silph@iastate.edu`). |
| COBS plant measurements (DOI [10.6073/pasta/45f4da1f677d3194502109880a53da92](https://doi.org/10.6073/pasta/45f4da1f677d3194502109880a53da92), EDI `edi.2457.1`) | Measured grain yields and rye biomass | **Partial.** Same license and same download issue. Metadata coverage 2008-03-01 to 2025-12-31. Combine grain yields are documented for all project years (268 records). Some hand-harvest series end earlier. |
| Kelley treatments and papers (ARS project [443545](https://www.ars.usda.gov/research/project/?accnNo=443545&fy=2025)) | Practice compare | **Partial.** Treatments verified from the FY2024 and FY2025 reports: conventional tilled corn–soy with fixed N, versus four alternative systems that all use late-spring nitrate-test sidedress N — (1) no-till, (2) no-till + cereal rye, (3) no-till + fertilized rye biomass double-cropped with soybean, (4) a winter oilseed (camelina in FY2024; covercress in FY2025) double-cropped with soybean. These are whole-system contrasts. |
| Kelley plot-level data | Measured yields and tile nutrient loss | **Not publicly downloadable.** Data sit in an internal NLAE database and are exported “as available” to LTAR / NutriNet / ARS databases. No Ag Data Commons dataset was found. Do not use the Transforming Drainage “private farm, Boone County” files — that is a different site. |
| NOAA [USC00130200](https://www.ncdc.noaa.gov/cdo-web/datasets/GHCND/stations/GHCND:USC00130200/detail) (Ames 8 WSW) | Public daily weather | **Verified.** In Boone County (Iowa Environmental Mesonet). Record 1964-11-01 to 2026-11-12. This is the ISU Agronomy Research Farm station, about 11 km north of COBS, and is one of three stations in the COBS weather table. |
| Boone 2025 county yields (claimed 209.9 corn / 66.2 soy bu/ac) | County benchmark | **Unverified.** The ISU [a1-12.pdf](https://www.extension.iastate.edu/agdm/crops/pdf/a1-12.pdf) returned 403 (bot check). Quick Stats is free but was not queried (API needs a key). An ISU blog confirms the 2025 county release (May 5, 2026) but does not list Boone. A third-party site repeats those two figures and attributes them to NASS. Do not put them in a slide until we confirm. |
| [SSURGO](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo) / Web Soil Survey | Soils | **Verified as freely downloadable** (shapefiles + tables; also Soil Data Access). Web Soil Survey itself returned 502 during this check. |
| [CDL 2008–2025](https://www.nass.usda.gov/Research_and_Science/Cropland/Release/index.php) | Rotation on FarmX polygons | **Verified as freely downloadable.** |
| [Crop Sequence Boundaries](https://www.nass.usda.gov/Research_and_Science/Crop-Sequence-Boundaries/index.php) | Field outlines (not ownership) | **Verified as freely downloadable** (8-year editions 2008–2015 through 2018–2025; 2018–2025 released March 27, 2026). |
| [Quick Stats](https://quickstats.nass.usda.gov/) | Official yields and crop progress | **Verified as a free public tool.** |
| Planting-date handbook + [Crop Progress](https://www.nass.usda.gov/Surveys/Guide_to_NASS_Surveys/Crop_Progress_and_Condition/) | Typical calendar | **Verified as free.** The old `fcdate10.pdf` link is dead (404); the 2010 handbook is on [USDA NAL ESMIS](https://esmis.nal.usda.gov/publication/usual-planting-and-harvesting-dates-us-field-crops). Prefer actual COBS/Kelley dates when replaying a year. |
| SHE [state map](https://soilhealthexchange.com/funding/states) | Funding demo | **Verified:** Iowa 63 = 17 state or regional + 46 national (the 46 are in every state). Geography only; not eligibility. |
| FarmX profile and ~20–30 voice logs | My Farm / FieldLog-shaped demo | **Not started.** Must be invented and labeled synthetic. Needs the real FieldLog / My Farm schema so we do not redo the work. |

## Demo scenarios

1. **Replay a season.** “What happened on these plots in year X?” — COBS dates, weather, and measured yields, cited, with the county benchmark beside them.
2. **Compare systems, not single practices.** Conventional tillage + fixed N versus no-till / rye / test-guided N, using Kelley’s published treatment design (and papers, not invented plot yields). Say when several practices change together. A TerraValue twin run, if used, is modeled.
3. **Funding matches for an Iowa corn–soy farm.** Filter the 63 geographic matches by practice and deadline; show program text and the official link. Do not say “you qualify.”
4. **Voice log with read-back.** A short scripted entry (“sprayed 32 oz glyphosate on the north field today”) parsed into a FieldLog-shaped record, numbers and field name read back. Labeled synthetic.

## Caveats

- Research plots are not a farm. Show plot, county, and simulated numbers as three different things.
- COBS cannot demonstrate till versus no-till; everything there was no-till. COBS also bundles other differences (for example stover removal on continuous corn).
- Kelley plot data is not in hand. Until it is, do not invent tile-loss or yield deltas.
- The 63 Iowa programs are a map count, not a qualification list. Programs change; check deadlines on demo day.
- Synthetic logs will look cleaner than real FieldLog entries, so voice quality will be overstated.
- Get Saurav’s okay before showing FarmX outside the team. Cite COBS under CC BY 4.0. Do not mix FarmX with real farm records.

## Two-week build (local only)

**Week 1.** Download SSURGO, CDL, CSB, and USC00130200; retry the COBS EDI packages and store them with citations. Confirm Boone yields in Quick Stats. Draw 3–4 CSB-based fields in Boone, attach soils and a CDL rotation, and write a short synthetic FarmX profile. Pull the 63 Iowa program pages for a state/practice/deadline filter.

**Week 2.** If COBS files come in, build one season-replay notebook (operations + weather + plot yields). Script the four demo scenes above; use Kelley only from published descriptions/papers. Write 20–30 synthetic voice lines to the FieldLog schema if we have it, otherwise a clearly temporary stand-in. Typed questions first; voice can sit on top. No emails, posts, or access requests unless you ask us to send them.

## Asks for Saurav and Mithun (draft only, not sent)

1. Can you share the FieldLog (and My Farm) schema, including `/quick-log/voice-parse` inputs and outputs, so FarmX matches the product? A few anonymized voice examples would help, even failures.
2. Are you comfortable using public COBS data (CC BY 4.0, cited) and published Kelley papers in a clearly labeled synthetic Boone demo? Any concern if this is shown outside the team?
3. For the practice compare, is it acceptable to use Kelley’s treatment design and papers only until plot-level data is public, and to call TerraValue’s twin for modeled scenarios, labeled as modeled?
4. Which 15–20 Iowa-relevant programs should we hand-encode first? We will not have the assistant declare eligibility from prose.
5. Should FarmX live in a FieldLog / My Farm test account, and are those two profiles one system or two?

## Sources (all URLs checked or attempted Nov 17, 2026)

- COBS listing: https://silph.biorenew.iastate.edu/available-datasets/cobs
- COBS management dataset: https://silph.biorenew.iastate.edu/dataset/COBS-experiment-management
- COBS plants dataset: https://silph.biorenew.iastate.edu/dataset/cobs-plants
- COBS management metadata PDF: https://silph.biorenew.iastate.edu/files/2026-09/COBS_experiment_management_metadata_v1.0.pdf
- COBS plants metadata PDF: https://silph.biorenew.iastate.edu/files/2026-09/COBS_plants_metadata_v1.0.1.pdf
- COBS management DOI (resolves to EDI `edi.2449.1`): https://doi.org/10.6073/pasta/1fcda1d6564931b1dd4f15b05cf5cfa4
- COBS plants DOI (resolves to EDI `edi.2457.1`): https://doi.org/10.6073/pasta/45f4da1f677d3194502109880a53da92
- EDI package pages: https://portal.edirepository.org/nis/mapbrowse?packageid=edi.2449.1 and https://portal.edirepository.org/nis/mapbrowse?packageid=edi.2457.1
- CC BY 4.0: https://creativecommons.org/licenses/by/4.0/
- USDA ARS project 443545, FY2025: https://www.ars.usda.gov/research/project/?accnNo=443545&fy=2025
- USDA ARS project 443545, FY2024 (treatment write-up): https://www.ars.usda.gov/research/project/?accnNo=443545&fy=2024
- LTAR Ames / Kelley site paper: https://doi.org/10.1002/jeq2.20646 (PMC full text was blocked: https://pmc.ncbi.nlm.nih.gov/articles/PMC11650521/)
- LTAR data access: https://ltar.ars.usda.gov/data/data-access/
- Transforming Drainage (different Boone site; not used as Kelley data): https://doi.org/10.15482/usda.adc/1521092 and https://datateam.agron.iastate.edu/td/dl/
- NOAA GHCND USC00130200: https://www.ncdc.noaa.gov/cdo-web/datasets/GHCND/stations/GHCND:USC00130200/detail
- IEM station page (county = Boone): https://mesonet.agron.iastate.edu/sites/site.php?network=NCDC81&station=USC00130200
- ISU county yield table (403, bot protection): https://www.extension.iastate.edu/agdm/crops/pdf/a1-12.pdf
- ISU blog on the 2025 county yield release: https://blogs.extension.iastate.edu/agdm/2026/05/06/2025-county-level-yields-from-usda-national-agricultural-statistics-service/
- NASS Quick Stats: https://quickstats.nass.usda.gov/
- Iowa NASS county-estimate PDFs (still listing 2022–2023 as of this check): https://www.nass.usda.gov/Statistics_by_State/Iowa/Publications/County_Estimates/ and https://data.nass.usda.gov/Statistics_by_State/Iowa/Publications/County_Estimates/
- Third-party yield table only (not used as confirmation): https://www.argenfields.com/us-farmland-data/iowa/boone-county
- farmdoc daily 2025 county-yield overview (no Boone figures): https://farmdocdaily.illinois.edu/2026/05/2025-nass-county-corn-soybean-and-winter-wheat-yields.html
- SSURGO: https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo
- Web Soil Survey (502 during this check): https://websoilsurvey.nrcs.usda.gov/ and https://websoilsurvey.sc.egov.usda.gov/App/WebSoilSurvey.aspx
- Soil Data Access: https://sdmdataaccess.nrcs.usda.gov/
- CDL national downloads: https://www.nass.usda.gov/Research_and_Science/Cropland/Release/index.php
- Crop Sequence Boundaries: https://www.nass.usda.gov/Research_and_Science/Crop-Sequence-Boundaries/index.php
- Dead planting-handbook URL: https://www.nass.usda.gov/Publications/Todays_Reports/reports/fcdate10.pdf
- Planting handbook on ESMIS: https://esmis.nal.usda.gov/publication/usual-planting-and-harvesting-dates-us-field-crops and https://esmis.nal.usda.gov/sites/default/release-files/vm40xr56k/dv13zw65p/w9505297d/planting-10-29-2010.pdf
- NASS Crop Progress: https://www.nass.usda.gov/Surveys/Guide_to_NASS_Surveys/Crop_Progress_and_Condition/
- SHE funding by state: https://soilhealthexchange.com/funding/states
