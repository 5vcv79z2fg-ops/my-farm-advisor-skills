# Field comparison EDA — Assignment 2

Field-level exploratory analysis for an Australian grain enterprise: five years
of paddock boundaries (2022–2026) turned into one stable field-year panel, then
compared across three management regions.

The analysis of that panel — rotation, climate and NDVI season behaviour — is
[ANALYSIS.md](ANALYSIS.md), built from the committed example tables by
`src/build_analysis.py`. A self-contained one-page version with the figures
embedded is [eda_report.html](eda_report.html), built from the same computed
findings by `src/build_report.py`. Method and conventions live in
[GUIDE.md](GUIDE.md); local operating rules are in [AGENTS.md](AGENTS.md); the
workflow manifest is [SKILL.md](SKILL.md); the audit record is
[examples/qc_summary.md](examples/qc_summary.md).

## Reviewing this branch

The branch carries a merge from `origin/main` so the assignment work sits on
current upstream code. That merge brings unrelated `data-pipeline` work, which
makes a plain branch-to-branch compare look far larger than this assignment is.

**Scope the diff to this directory when reviewing:**

```bash
git diff --stat assignment-1..assignment-2 -- my-farm-advisor/eda/
```

The assignment is a sequence of small commits scoped to this directory — panel
and boundary QC, SILO climate metrics, figures, the guide, per-farm growing
seasons, the pipeline staging export, and the analysis section. Two of those
commits also carry pipeline-side changes (zone naming and the SILO weather
stage); those are called out in their messages.

## What is here

| Path | What it is |
|---|---|
| `src/common.py` | field identity, merge crosswalk, region mapping, geometry |
| `src/build_panel.py` | builds the field-year panel and rotation spine |
| `src/qc_boundaries.py` | boundary change and area diagnostics |
| `src/fetch_weather.py` | SILO client and seasonal metrics |
| `src/make_figures.py` | all five figures |
| `src/export_ndvi_examples.py` | NDVI tables from the runtime tree → `examples/` |
| `src/build_analysis.py` | computes every number in `ANALYSIS.md`; `--check` is the drift test |
| `src/analysis_template.md` | the analysis prose, with computed values substituted in |
| `src/build_report.py` | builds the self-contained `eda_report.html` from the same findings; `--check` is the drift test |
| [ANALYSIS.md](ANALYSIS.md) | rotation, climate and NDVI season analysis |
| [eda_report.html](eda_report.html) | one-page summary, five figures embedded, no external assets |
| [examples/panel_field_year.csv](examples/panel_field_year.csv) | 165 rows, one per field-year |
| [examples/rotation_spine.csv](examples/rotation_spine.csv) | one row per field, crop by year |
| [examples/ndvi_field_year.csv](examples/ndvi_field_year.csv) | 165 rows, one per field-year, NDVI season summary |
| [examples/ndvi_monthly.csv](examples/ndvi_monthly.csv) | 1,317 rows, one per field-month |
| [examples/qc_boundaries.csv](examples/qc_boundaries.csv) | per field-year boundary diagnostics |
| [examples/qc_crosswalk.csv](examples/qc_crosswalk.csv) | three retired sub-paddocks mapped to parents |
| [examples/weather_seasonal.csv](examples/weather_seasonal.csv) | one row per farm-year |
| `examples/figures/` | figure 1–5 |

## Headline numbers

| Check | Value |
|---|---:|
| Canonical fields | 33 |
| Years | 2022–2026 |
| Observed field-years | 155 |
| Not-managed field-years | 10 |
| Region split | 16 / 11 / 6 |
| Changed boundaries | 3, all 2025 |
| Farm-years of climate | 30 |
| NDVI paddock-months observed | 1,209 of 1,240 (2.5 % missing) |
| Wheat + canola share of cropped field-years | 80.6 % |
| Season integral, 2022 | Harden 3.62, Trungley Hall 1.38, lease country 1.35 |

Field counts by year: 30, 30, 32, 33, 30. Every observed field-year carries a
crop and a variety. The 2022 row is the analysis's headline: the wettest season
on record produced one of Harden's best canopies and the weakest on the western
blocks — see [ANALYSIS.md](ANALYSIS.md) for the evidence and the limits.

## Conventions that matter when reading the output

- **Absent is not missing.** A paddock not farmed in a year is `not_managed`,
  never imputed.
- **Two area measures.** `area_ha_geom` is the paddock footprint,
  `area_ha_reported` is the cropped area. Spatial statistics use geometry,
  crop-area statistics use reported area.
- **Merges are explicit.** Three sub-paddocks retired in 2025 are mapped to
  their surviving parents by ID, and their pre-2025 crops are kept in
  `crop_mix`.
- **Growing seasons differ.** Kanoona runs 1 April – 30 November (244 days);
  the other farms run 1 April – 15 November (229 days). Cross-region
  comparisons use the common window, which is complete for all 30 farm-years.
- **Grower corrections are overrides.** Crop-label and variety fixes live in
  `src/common.py` as documented maps, not hand edits to the exported CSVs, so
  a rebuild reproduces them and `variety_source` keeps the provenance.

## Reproducing

```bash
python src/build_panel.py       # panel and rotation spine
python src/qc_boundaries.py     # boundary QC and crosswalk
python src/fetch_weather.py --offline   # seasonal metrics from the SILO cache
python src/make_figures.py      # figures 1-5
python src/export_ndvi_examples.py      # runtime NDVI tables -> examples/
python src/build_analysis.py    # rewrites ANALYSIS.md
python src/build_analysis.py --check    # fails if ANALYSIS.md is stale
python src/build_report.py      # rewrites eda_report.html
python src/build_report.py --check      # fails if the HTML page is stale
```

Raw paddock layers, the daily weather cache and the NDVI pipeline tables live
outside the repository in the untracked runtime tree; `src/common.py` resolves
its location from the environment. Only derived tables and figures are
committed. Every step except `export_ndvi_examples.py` reads `examples/` alone,
so the analysis builds offline.

## Data privacy

No legal entity name, credential or raw grower layer is committed. Farm and
paddock names appear because they are the analysis unit. The SILO address is
read from the environment at run time and is written to no file.
