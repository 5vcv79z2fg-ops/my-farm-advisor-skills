---
name: eda-field-comparison
description: >
  Multi-year paddock panel analysis for a grain enterprise: stable field
  identity across annual shapefiles, boundary QC, rotation, SILO climate and
  NDVI season behaviour compared across management regions. Use when a grower
  supplies per-year paddock polygons plus crop records and the question is how
  regions compare rather than what is in one field.
---

# Field comparison EDA

Field-level exploratory analysis for an Australian grain operation: five years
of paddock boundaries and crop records turned into one stable field-year panel,
then compared across three management regions.

## When to load

Load this subskill when the task is:

- Building or rebuilding the field-year panel and rotation spine.
- Checking boundary consistency across annual shapefiles.
- Comparing rotation, climate or NDVI behaviour between paddocks, zones or
  seasons.
- Explaining the numbers in `ANALYSIS.md` or `eda_report.html`.
- Corrections from grower records (crop label or variety) that must survive a
  rebuild.

Do not use it for a single year, or for boundaries re-digitised each season
with no stable `paddock_id`.

## Start here

| File | Why |
|---|---|
| [README.md](README.md) | overview, headline numbers, reproduction commands |
| [GUIDE.md](GUIDE.md) | method, conventions, season definitions, weather limits |
| [AGENTS.md](AGENTS.md) | local operating rules and the pre-flight checks |
| [ANALYSIS.md](ANALYSIS.md) | long-form analysis, every number computed |
| [eda_report.html](eda_report.html) | self-contained one-page summary, five figures |
| [examples/qc_summary.md](examples/qc_summary.md) | audit record and grower corrections |

## Pipeline order

```bash
python src/build_panel.py               # panel + rotation spine
python src/qc_boundaries.py             # boundary QC + crosswalk
python src/fetch_weather.py --offline   # climate from the SILO cache
python src/make_figures.py              # figures 1-5
python src/export_ndvi_examples.py      # runtime NDVI -> examples/ (needs runtime)
python src/build_analysis.py            # ANALYSIS.md
python src/build_analysis.py --check    # drift test
python src/build_report.py              # eda_report.html
python src/build_report.py --check      # drift test
```

Only `export_ndvi_examples.py` reads the untracked runtime tree; everything
after it reads the committed `examples/` tables alone and runs offline.

## Guardrails

- Absent is not missing: an unfarmed paddock-year is `not_managed`, never
  imputed.
- Never join years by paddock name; join by `paddock_id`, then apply
  `RETIRED_TO_PARENT`.
- Two area measures: `area_ha_geom` for spatial work, `area_ha_reported` for
  crop-area work.
- Grower corrections belong in the override maps in `src/common.py`, not in the
  exported CSVs, so a rebuild reproduces them.
- Never commit a raw shapefile, weather cache or credential. The SILO address
  lives in `SILO_EMAIL` only.
- After a panel or boundary change, re-run the pre-flight in `AGENTS.md` and
  vision-review any regenerated figure before committing.
