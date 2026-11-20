# Field comparison EDA — Assignment 2

Field-level exploratory analysis for an Australian grain enterprise: five years
of paddock boundaries (2022–2026) turned into one stable field-year panel, then
compared across three management regions.

Method and conventions live in [GUIDE.md](GUIDE.md); local operating rules are
in [AGENTS.md](AGENTS.md); the audit record is
[examples/qc_summary.md](examples/qc_summary.md).

## Reviewing this branch

The branch carries a merge from `origin/main` so the assignment work sits on
current upstream code. That merge brings 63 commits of unrelated `data-pipeline`
work, which makes a plain branch-to-branch compare look far larger than this
assignment is.

**Scope the diff to this directory when reviewing:**

```bash
git diff --stat assignment-1..assignment-2 -- my-farm-advisor/eda/
```

That shows the actual contribution: **20 files, 2,972 lines**. For contrast, the
unscoped compare is 235 files and 44,704 insertions, almost all of it inherited
upstream work.

The assignment itself is six commits:

| Commit | Content |
|---|---|
| `79ff3c9` | field-year panel and boundary QC |
| `a6a97fb` | SILO seasonal climate metrics |
| `b27f81b` | region comparison figures |
| `90fd32b` | guide and EDA index link |
| `93aa296` | figure vision review pass |
| latest | per-farm growing seasons and this review note |

## What is here

| Path | What it is |
|---|---|
| `src/common.py` | field identity, merge crosswalk, region mapping, geometry |
| `src/build_panel.py` | builds the field-year panel and rotation spine |
| `src/qc_boundaries.py` | boundary change and area diagnostics |
| `src/fetch_weather.py` | SILO client and seasonal metrics |
| `src/make_figures.py` | all four figures |
| [examples/panel_field_year.csv](examples/panel_field_year.csv) | 165 rows, one per field-year |
| [examples/rotation_spine.csv](examples/rotation_spine.csv) | one row per field, crop by year |
| [examples/qc_boundaries.csv](examples/qc_boundaries.csv) | per field-year boundary diagnostics |
| [examples/qc_crosswalk.csv](examples/qc_crosswalk.csv) | three retired sub-paddocks mapped to parents |
| [examples/weather_seasonal.csv](examples/weather_seasonal.csv) | one row per farm-year |
| `examples/figures/` | figure 1–4 |

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

Field counts by year: 30, 30, 32, 33, 30. Every observed field-year carries a
crop and a variety.

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

## Reproducing

```bash
python src/build_panel.py       # panel and rotation spine
python src/qc_boundaries.py     # boundary QC and crosswalk
python src/fetch_weather.py --offline   # seasonal metrics from the SILO cache
python src/make_figures.py      # figures 1-4
```

Raw paddock layers and the daily weather cache live outside the repository in
the untracked runtime tree; `src/common.py` resolves its location from the
environment. Only derived tables and figures are committed.

## Data privacy

No legal entity name, credential or raw grower layer is committed. Farm and
paddock names appear because they are the analysis unit. The SILO address is
read from the environment at run time and is written to no file.
