# Australian pipeline — adaptation guide

How the US-shaped farm pipeline is adapted to an Australian grain enterprise:
SLGA/ASF soil, Copernicus terrain, Sentinel-2 L2A, SILO weather and the
grower's own crop records. Use this guide to run the AU stages, to debug a
rerun, or to port the branch to another Australian grower.

## When to use

Load this subskill when the task involves:

- Running or rerunning the AU stage list (`src/scripts/au/`).
- Adding an Australian farm, zone or season window.
- Explaining why an AU poster, card or zone report is wrong or empty.
- Debugging NDVI gaps, SILO weather, soil slabs or texture sheets.
- Porting the branch to a new grower with the same source stack.

Do not use the US step list (`run_farm_pipeline.py`) for AU data: it assumes
SSURGO, CDL and a corn/soy season. Do not fall back to WorldCereal or NASA
POWER; the AU branch never uses either.

## Prerequisites

| Item | Requirement |
|---|---|
| Runtime root | `DATA_PIPELINE_DATA_ROOT` absolute and outside the checkout |
| Runtime source | `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src` — the live copy, not the checkout |
| Virtualenv | `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv` |
| Grower slug | `--grower-slug`, e.g. `au-grain-grower` |
| Farm layout | `growers/<grower>/farms/<farm>/` with boundaries and `manifests/field-names.csv` |
| SILO address | `SILO_EMAIL` in the environment only, never in a file |

Sync the committed source into the runtime before a run. Editing
`src/scripts/au/` in the checkout does not change what executes until the
runtime copy is refreshed (`scripts/install.sh` or the bootstrap step).

## Stage order

`au/run_au_pipeline.py` runs the per-farm stages in input order, then the
zone stages. Each stage is idempotent and can be run alone with `--only`.

| Stage | Script | Output |
|---|---|---|
| `weather` | `ingest_au_weather.py` | `derived/tables/weather_daily.csv` per farm |
| `soil-ingest` | `ingest_au_soil.py` | 0–100 cm rasters, 0–30 cm blends, profile tables (opt-in) |
| `ndvi` | `build_au_ndvi.py` | monthly stats, field-year summary, NDVI rasters |
| `cards` | `render_au_ndvi_cards.py` | one NDVI card per paddock |
| `soil-maps` | `render_au_soil_maps.py` | one 0–30 cm soil sheet per paddock |
| `field-posters` | `render_au_field_posters.py` | one poster per paddock |
| `texture` | `render_au_texture_triangles.py` | one texture sheet per farm |
| `soil-cards` | `render_au_soil_cards.py` | one 0–100 cm profile card per paddock |
| `zone-posters` | `render_au_zone_posters.py` | one farm-row × year-column poster per zone |
| `zone-reports` | `build_au_zone_reports.py` | Markdown + self-contained HTML per zone |

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/au/run_au_pipeline.py \
  --grower-slug au-grain-grower \
  --farms kanoona,caroleese,tullokan,brentwood,woodlands,rio \
  [--only ndvi,cards] [--with-soil-ingest] [--skip-zone-stages] [--force]
```

Common narrow runs:

```bash
# Recompute only NDVI after a scene download
... run_au_pipeline.py --only ndvi

# Rebuild zone posters and reports without touching farm stages
... run_au_pipeline.py --skip-farm-stages --only zone-posters,zone-reports

# Render without basemap tiles (offline or rate-limited)
AG_OFFLINE=1 ... run_au_pipeline.py --only zone-posters,zone-reports
```

## Environment and configuration

| Variable | Effect |
|---|---|
| `DATA_PIPELINE_DATA_ROOT` | runtime root; required, absolute, external |
| `DATA_PIPELINE_VENV_DIR` | alternate absolute venv path |
| `AG_GROWER_SLUG` | default grower slug for every stage |
| `AG_FARM_SLUG` | farm for a single-farm renderer |
| `AG_OFFLINE=1` | skip contextily basemap downloads; renders use the plain frame |
| `AG_FILL_GAPS=1` | refetch only months reported as gaps, keep everything else |
| `AG_SENTINEL_CLOUD_MAX` | STAC tile-cloud ceiling (AU default 70 %) |
| `AG_SCENES_PER_MONTH` | candidate scenes per month, clamped 1–4 (default 1) |
| `SILO_EMAIL` | SILO caller identity; runtime only, never committed |

Season windows live in `scripts/lib/au_common.py` (`SEASON_WINDOW`,
`DEFAULT_SEASON_WINDOW`): Kanoona 1 Apr – 30 Nov, every other farm 1 Apr –
15 Nov. Zone membership lives in `ZONE_BY_FARM`, and printed names in
`ZONE_REGION` / `ZONE_LABELS`; the `region` column in `field-names.csv` is
derived from that mapping, so a rename cannot leave a stale label.

## Sources and counting rules

- **Soil** — SLGA/ASF property grids, five slabs to 100 cm; carbon is a
  slab-by-slab sum, not the product of depth-weighted means.
- **Terrain** — Copernicus GLO-30.
- **Imagery** — Sentinel-2 L2A via Planetary Computer STAC; one scene per
  month chosen by measured `valid_fraction` inside the paddock, gate 50 %.
- **Weather** — SILO 0.05° grid, one point per farm centroid. A farm's
  paddocks share one series; weather never goes on paddock posters.
- **Crops** — the grower's own records. A year with no record is
  `not_managed`, not missing.

Counting is managed-only everywhere: `not_managed` field-years carry
contextual NDVI that must not enter a statistic. Posters and reports print
`not managed` in that cell and count observed paddock-months only. The zone
colour scale is derived from painted (managed) values, with absent years
hatched grey.

## Gotchas that cost time

- **Stale runtime source.** The checkout is not executable. Re-run the
  installer after editing any stage, or the old code runs and the fix looks
  ineffective.
- **Cache short-circuit.** Weather ingest reuses the cached SILO CSV unless
  `--force`. A plain run will not extend a short cache.
- **STAC page limit.** The search API rejects `limit > 1000` with a bare 400;
  the clamp lives in `lib/satellite_imagery.py`. Candidate caps scale the page
  size, so a large `AG_SCENES_PER_MONTH` can still hit the ceiling.
- **Distinct dates, not items.** `AG_SCENES_PER_MONTH` counts distinct
  acquisition dates. Two processing baselines of one pass are one chance to
  see the paddock.
- **Gap top-up is not a re-download.** `AG_FILL_GAPS=1` reads
  `ndvi_field_year_summary.csv` and fetches only unreached dates; it will not
  overwrite months already on disk.
- **50 % paddock gate is a quality decision.** `AG_SENTINEL_CLOUD_MAX` only
  decides what is worth downloading. The month is judged on the paddock's own
  masked pixels; a month below 50 % is a reported gap, never imputed.
- **Zone posters use rows = farms, columns = years.** The lease country spans
  ~25 km, so one shared frame would leave most panels as empty basemap. Each
  row is framed to its farm; the colour scale is fixed across all posters.
- **Counties are labels, not geometry.** `COUNTY_LABELS` translates the printed
  name (`Hilltops Shire`); the stored GADM attribute keeps the source value, so
  a label change never needs a re-clip.
- **SILO is refreshed through the EDA client.** The raw cache is updated by
  `eda/eda-field-comparison/src/fetch_weather.py`; the AU weather stage only
  normalises what that cache holds.

## Verify a run

1. Each stage exits non-zero on failure; `run_au_pipeline.py` prints `ok` or
   `FAILED` per stage.
2. Zone report and poster counts should match the panel: 33 paddocks,
   155 observed field-years, 10 `not_managed`.
3. NDVI gaps are printed as text, never `nan`: 1,209 of 1,240 managed
   paddock-months in the reference run.
4. The zone-report HTML must open from disk with no external asset.

## Related

- [Pipeline README](../README.md) — runtime contract, install, all stages.
- [Data pipeline AGENTS](../AGENTS.md) — runtime and safety rules.
- [Field comparison EDA](../../eda/eda-field-comparison/GUIDE.md) — panel,
  rotation, SILO seasons and the analysis built on the same crop records.
- [Field map frame](../references/field-map-frame.md) — poster frame contract.
- [Colormap recommendations](../references/colormap-recommendations.md).
