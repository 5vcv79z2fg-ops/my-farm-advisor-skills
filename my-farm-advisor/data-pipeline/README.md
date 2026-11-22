# Data Pipeline Runtime Setup

Real-farm field polygons per country (CSB GeoParquet pruning, Overpass rules,
repository contract, new-country checklist):
[references/field-boundary-sources.md](references/field-boundary-sources.md).
Soil provider selection by boundary `country`:
[../soil/soil-routing.md](../soil/soil-routing.md).
Per-year crop rotation/crop-type sources by country (CDL, AAFC ACI, and the
keyless WorldCereal default-model path for every other country):
[references/crop-rotation-sources.md](references/crop-rotation-sources.md).
The best-available-data ladder and the keyless RDM regional-reference API
(`scripts/ingest/download_rdm_reference.py`):
[references/worldcereal-rdm.md](references/worldcereal-rdm.md).
- Field-scale maps (any figure drawing one field polygon) use the field-local
  UTM frame, origin-at-SW-corner axes and boxed north arrow, with optional
  viewport rotation — [references/field-map-frame.md](references/field-map-frame.md).
- Every map renders over keyless **Esri World Imagery** warped into the same rotated frame —
  [references/basemap.md](references/basemap.md).
## Example renders

Vision-QC'd sample figures ship under [`examples/`](examples/) — one
subfolder per output family (`field-maps/`, `soil-maps/`, `maturity/`,
`field-components/`, `weather-eda/`, `worldcereal/`). PNGs only; regenerated
runtime artifacts stay outside the repo under `DATA_PIPELINE_DATA_ROOT`
(see `ASSET_POLICY.md` at the repo root).


## Global parity in one paragraph

Any field, any country, **zero accounts**: boundaries from CSB (US) or
OSM/Overpass (rules per country); profile soil from SSURGO (US), the Ontario
detailed survey (CA), Australia's Soil Atlas via Gaia-DAP (AU), or SoilGrids
250 m as the global floor; rotation history from CDL (US), AAFC ACI (CA), or
the WorldCereal default crop models applied per year on keyless Microsoft
Planetary Computer Sentinel-2 (`scripts/ingest/apply_worldcereal.py`,
regional reference samples/parcels from the keyless WorldCereal RDM REST API
(`scripts/ingest/download_rdm_reference.py`, EWOC-legend joined — rung 2 of the
best-available-data ladder), verification figures via `scripts/render_worldcereal.py`, plus a USDA CDL
comparator panel via `scripts/render_worldcereal_cdl.py` — in-field class
shares and an explicit agree/disagree verdict); terrain from the
resolution-ranked DEM resolver (USGS 3DEP/TNM where mapped — up to 1 m — with
Copernicus 30 m, NASADEM, and ALOS as the global floor); weather from NASA
POWER public Zarr; admin boundaries and choropleths from geoadmin plus GADM
for any country via `--gadm-iso`. Every provider journals its
URL, sha256, and version. The one optional upgrade (Sentinel-1 fidelity in
WorldCereal runs, `--backend cdse`) is genuinely optional — the default path
never asks you to sign up for anything.

Per-field composite figures (terrain hillshade + contours, soil polygons, and
per-year crop class over the boundary) render from the same archive with
`scripts/render_field_maps.py`.

This subskill ships the scripts that build the data-pipeline reports and
posters. Each runtime host creates its own virtualenv inside the data tree on
first run; the scripts auto-bootstrap that environment before continuing.

## Quick start

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/ingest/bootstrap_farm_from_county.py \
  --state-fips 17 \
  --county-name DeKalb \
  --count 5 \
  --seed 77 \
  --grower-slug il-dekalb-grower \
  --farm-slug dekalb-demo-farm \
  --farm-name "DeKalb Demo Farm" \
  --run-pipeline \
  --force
```

For a first run that also initializes shared data and seeds fields for a grower in a state, use the installer directly from the checkout:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh \
  --prepare-shared-data \
  --seed-grower-slug acme-grower \
  --seed-state Illinois \
  --seed-field-count 12 \
  --seed-farm-name "Acme Illinois Farm"
```

That command installs the runtime source and venv, builds shared geoadmin L0/L1/L2 payloads, shared NASA POWER county weather, GDD, annual corn RM, annual soybean MG, five-year FIPS-average corn RM and soybean MG datasets, and last-five-year CONUS CDL rasters. It then selects a top-crop county in the requested state, samples the requested number of OSM fields, and runs the full farm pipeline so derived tables, field weather, real DEM terrain, soil outputs, CDL history, satellite/NDVI products, reports, cards, posters, and HTML/Markdown farm reports are generated automatically. The generated farm and field reports do not integrate terrain metrics in this change.

If the runtime is already installed, run the equivalent from the runtime source copy:

```bash
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/farm_dashboard.py create \
  --prepare-shared-data \
  --grower-slug acme-grower \
  --state Illinois \
  --field-count 12 \
  --farm-name "Acme Illinois Farm"
```

`DATA_PIPELINE_DATA_ROOT` is required. Set it to an absolute writable path outside the skill checkout before running the installer or any pipeline entrypoint. There is no implicit fallback to a platform workspace path or to a checkout-local `data/` directory.

The installer creates and refreshes the runtime tree under:

- runtime base: `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`
- runtime source copy: `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src`
- default runtime venv: `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv`

Generated outputs, manifests, reports, logs, and downloaded payloads belong under the runtime base, for example `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers` and `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared`. The committed checkout remains the source for installer scripts and baseline `src/` files, but runtime execution happens from the copied source.

Farm weather now uses NASA POWER's public S3 Zarr stores by default at actual field centroids. The default farm weather controls are `--weather-backend zarr`, `--weather-start-year 2021`, `--weather-end-year 2025`, and `--weather-time-standard lst`. The output path and CSV schema stay compatible with existing reports:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/derived/tables/<farm>_weather_2021_2025.csv
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/fields/<field>/weather/daily_weather.csv
```

Run or override those defaults from the runtime source copy:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/run_farm_pipeline.py \
  --grower-slug il-dekalb-grower \
  --farm-slug dekalb-demo-farm \
  --farm-name "DeKalb Demo Farm" \
  --weather-backend zarr \
  --weather-start-year 2021 \
  --weather-end-year 2025 \
  --weather-time-standard lst
```

Use `--weather-backend api` only when explicitly debugging the legacy NASA POWER point API path for small field sets.

Shared county weather for maturity-by-FIPS uses NASA POWER's public S3 Zarr stores by default instead of issuing one `power.larc.nasa.gov` point API request per county grid cell. This avoids API rate-limit failures for L2 geoadmin scopes while preserving the existing output path and schema:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared/weather/nasa-power/<year>/daily_weather_by_fips.parquet
```

For the full shared lower48 baseline, initialize the runtime with multi-year county weather, GDD, corn RM, soybean MG, corn/soybean five-year FIPS averages, and CDL raster outputs. The default shared maturity range is 2021-2025 to match the farm weather and CDL helper defaults; CDL initialization fetches the last five available CONUS rasters by default:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh --prepare-shared-data
```

That install flag runs the equivalent of:

```bash
python scripts/run_maturity_years_by_fips.py \
  --start-year 2021 \
  --end-year 2025 \
  --coverage lower48 \
  --weather-backend zarr \
  --weather-time-standard lst
python scripts/ingest/download_cdl.py \
  --raster-only \
  --cdl-scope conus \
  --cdl-latest-year 2025 \
  --cdl-window-years 5
```

`--prepare-shared-maturity` remains available for weather/GDD/corn/soy maturity only, but it does not prepare CDL rasters.

The maturity runner writes annual files like `shared/corn_maturity/tables/rm_by_fips_2025.parquet` and final five-year average files like `shared/corn_maturity/tables/rm_by_fips_2021_2025_average.parquet` and `shared/soybean_maturity/tables/mg_by_fips_2021_2025_average.parquet`.

For a single annual refresh, run:

```bash
python scripts/run_maturity_by_fips.py \
  --year 2025 \
  --coverage lower48 \
  --weather-backend zarr \
  --weather-time-standard lst
```

Use `--weather-backend api` only when explicitly debugging the legacy NASA POWER point API path for county weather.

## Grower web map

`scripts/reporting/generate_grower_web_map.py` builds one lightweight interactive HTML map per grower. It reads every farm boundary GeoJSON under the grower, embeds the polygons inline, and renders them with Leaflet on an online Esri World Imagery basemap. No imagery, rasters, or base64 payloads are embedded, so files stay small (tens of KiB for a few fields). Click a field to see grower, farm, field name, area, crop, county/state, and field ID; the sidebar list zooms to individual fields.

Output:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/reports/<grower>_grower_fields_map.html
```

Run it from the runtime source copy for one grower, several growers, or every grower:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py \
  --grower-slug il-dekalb-grower \
  --grower-slug ia-story-grower
```

Add `--dry-run` to print resolved grower/field counts and output paths without writing files, or `--output-dir <path>` to override the destination. Field labels use `<farm display name> — Field <n>` unless `field.json` supplies a `label` or a meaningful `display_name`. Ordinals keep labels stable across reruns even when acreage is recomputed.

## Boundary QC and repair

`scripts/ingest/repair_field_boundaries.py` audits and repairs field boundaries with CDL evidence. It never writes unless `--apply` is passed, and every applied change copies the farm boundary and inventory into `derived/boundary_backups/<timestamp>/`, moves replaced field directories into `derived/retired_fields/<timestamp>/`, and records `manifests/boundary_repair.json`. CDL QC reports land in `manifests/boundary_qc.json`.

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"

# Audit: per-field corn/soy/pasture/developed composition and suspect flags
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" scripts/ingest/repair_field_boundaries.py \
  --grower-slug il-dekalb-grower --farm-slug dekalb-north-farm qc

# Split a merged OSM way along its CDL corn/soy interface
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" scripts/ingest/repair_field_boundaries.py \
  --grower-slug il-dekalb-grower --farm-slug dekalb-north-farm \
  split --field-id osm-838357183 --apply

# Replace fields with different OSM farmland ways
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" scripts/ingest/repair_field_boundaries.py \
  --grower-slug ia-story-grower --farm-slug story-central-farm \
  replace --map osm-1193879887=1193879914 --map osm-1195895933=1195895919 --apply
```

QC flags are `tiny_field`, `low_crop`, `pasture_dominated`, `developed_dominated`, `merged_field`, and `no_cdl`. `merged_field` means the latest CDL year has both a large corn component and a large soy component, which is the signature of two OSM ways or two real fields merged into one polygon. Split IDs use `-a`/`-b` suffixes and carry `parent_field_id`, `split_method`, `split_year`, and `split_crop_hint`; replacements carry `replaces`.

After a repair, rerun the farm pipeline without `--force`. Delete the farm weather cache first (`derived/tables/<farm>_weather_<start>_<end>.csv`) because weather silently re-stages cached rows for the old field set; soil and CDL detect missing field IDs and refresh on their own, and DEM, SoilGrids, and satellite steps skip unchanged fields through per-field manifests.

## Default DEM terrain package

Normal farm pipeline initialization and add-field runs that invoke `scripts/run_farm_pipeline.py` include real DEM terrain by default. The pipeline calls `scripts/ingest/download_dem_terrain.py` immediately after field boundary download, passes live real-source permission through `--allow-live-downloads`, and writes elevation, slope, aspect, hillshade, curvature, wetness, depression, relative elevation, and erosion-proxy products under the runtime field tree. Generated farm and field reports do not integrate terrain metrics in this change.

Use `--skip-dem-terrain` only as an explicit operator override when a run must omit DEM terrain. The `scripts/run_farm_pipeline.py --structure-test` path remains a no-DEM, no-download structure check and must not import DEM-only dependencies or contact live services.

The direct DEM CLI remains available from the runtime source copy for focused dry runs, package inspection, or operator-led terrain retries. Direct CLI dry-run mode plans field paths and sources only. It does not write rasters, download DEM tiles, or require live provider services.

Safe temp-root install and dry-run check. This creates the runtime venv before invoking `.venv/bin/python`:

```bash
tmp_root="$(mktemp -d)"
export DATA_PIPELINE_DATA_ROOT="$tmp_root"
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/ingest/download_dem_terrain.py \
  --grower il-dekalb-grower \
  --farm dekalb-demo-farm \
  --context-meters 20 \
  --dry-run
```

For a no-network full-package smoke, use offline fixtures only as a direct CLI test-only synthetic path. These packages are not real grower DEM evidence, are not valid farm validation, and are marked with `synthetic_fixture=true`, `synthetic://...` source URLs, and warnings in the manifest/source reference. Synthetic fixture mode is unreachable from default farm-pipeline orchestration because `run_farm_pipeline.py` does not expose fixture flags. The CLI refuses fixture writes unless the deliberately named `--allow-synthetic-fixtures` test override is present. Prefer a temporary or disposable runtime root for this smoke:

```bash
tmp_root="$(mktemp -d)"
export DATA_PIPELINE_DATA_ROOT="$tmp_root"
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/ingest/download_dem_terrain.py \
  --grower il-dekalb-grower \
  --farm dekalb-demo-farm \
  --context-meters 20 \
  --offline-fixtures \
  --allow-synthetic-fixtures \
  --limit-fields 1
```

Focused direct CLI runs for real grower DEM verification must use cached real source DEM rasters or live provider access. Live DEM provider discovery or downloads are disabled in direct CLI runs unless you opt in. Add `--allow-live-downloads` only when the operator expects network access, provider availability, and downloaded DEM cache writes under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared/dem/`:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/ingest/download_dem_terrain.py \
  --grower il-dekalb-grower \
  --farm dekalb-demo-farm \
  --context-meters 20 \
  --allow-live-downloads
```

Default farm orchestration already invokes real DEM terrain with live real-source controls after field boundaries. Use `--dem-context-meters` and `--dem-source-policy` to tune that default path, or `--skip-dem-terrain` when the operator explicitly chooses to omit DEM terrain for a run.

To persist the default data root for future login sessions, write the user environment file and still export the variable in the current shell before running commands:

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/environment.d"
cat > "${XDG_CONFIG_HOME:-$HOME/.config}/environment.d/60-my-farm-advisor.conf" <<'EOF'
DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
EOF
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
```

The `environment.d` file applies to future sessions only. It does not update an already-running shell.

## Australian branch (`src/scripts/au/`)

The US step list is SSURGO/CDL/corn-soy shaped, so the AU stages live beside it
in `src/scripts/au/` and read the same canonical runtime tree. They source
SLGA/ASF soil property grids (five slabs to 100 cm), Copernicus GLO-30 terrain,
Sentinel-2 L2A, SILO weather and the grower's own crop records — never
WorldCereal, and never NASA POWER.

`au/run_au_pipeline.py` is the entrypoint. It runs the per-farm stages in input
order, then the zone-level stages:

| stage | script | output |
| --- | --- | --- |
| `weather` | `ingest_au_weather.py` | per-farm daily SILO table (`derived/tables/weather_daily.csv`) |
| `soil-ingest` | `ingest_au_soil.py` | per-field 0–100 cm rasters, 0–30 cm blends, profile tables |
| `ndvi` | `build_au_ndvi.py` | monthly stats, per-field-year summary, NDVI rasters |
| `cards` | `render_au_ndvi_cards.py` | one NDVI card per paddock |
| `soil-maps` | `render_au_soil_maps.py` | one 0–30 cm soil-property sheet per paddock |
| `field-posters` | `render_au_field_posters.py` | one field poster per paddock |
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

Growing seasons are per farm: Kanoona 1 Apr – 30 Nov, the Trungley Hall and
lease blocks 1 Apr – 15 Nov (`lib/au_common.py:SEASON_WINDOW`). Reports print
them as `01 Apr – 30 Nov` through `growing_season()`, one formatter for the
poster, the NDVI card and the zone report. One climate source per report, SILO
only.

**Weather.** Climate is a property of the farm, not the paddock: SILO delivers
one 0.05° grid point per farm, and all of that farm's paddocks share it. The
`weather` stage normalises the cached SILO response for
`<Farm>_<lat>_<lon>.csv` (discovered from the farm's snapped centroid, with a
name fallback) into `farms/<farm>/derived/tables/weather_daily.csv`, and the
zone reports draw one three-panel row per farm — temperature by day-of-year
with frost markers, cumulative GDD, cumulative precipitation — as
`zone_weather_<Zone>_<farm>.png`. They never go on the 33 paddock posters:
sixteen identical charts is noise, not evidence. GDD uses base 0 °C for every
crop here, because the rotation mixes wheat, barley, canola and legumes on the
same farm; the base is printed on the axis. The cache is refreshed by
`eda/eda-field-comparison/src/fetch_weather.py`, the only code that talks to
the SILO API.

**Cloud and scene choice.** The STAC search gate is `eo:cloud_cover < 70 %`
(`AG_SENTINEL_CLOUD_MAX`), but that figure describes a 110 × 110 km tile, so it
only decides what is worth downloading. Each downloaded candidate is then
measured against the exact paddock polygon, and the month uses the candidate
with the highest measured `valid_fraction`, provided it clears
`MIN_VALID_FRACTION = 0.50`; otherwise the month is reported as a gap.
`AG_SCENES_PER_MONTH` caps candidates per month and counts **distinct
acquisition dates** — two processing baselines of the same pass are one chance
to see the paddock, not two, so the manifest records
`month_candidate_availability` and later runs top the year up with dates that
were never fetched instead of re-downloading what is on disk.

**Gap top-up.** `AG_FILL_GAPS=1` makes the downloader read
`ndvi_field_year_summary.csv` and re-examine only the months the report prints
as gaps, carrying every other month's scenes over untouched. That is how the
first AU audit's 42 missing managed paddock-months were worked down to 31: the
9 October 2022 months that were capped at two candidates, and four November
months, were download-limit gaps rather than cloud. The remaining 31 have every
available candidate on disk and fail the 50 % paddock gate, so they are
reported as gaps and nothing is imputed. The gate is a *candidate* filter, not
the quality decision: one November 2022 pass sits at 74 % tile cloud and is the
only date in that fortnight, so it is fetched (`AG_SENTINEL_CLOUD_MAX=90`) and
then judged on the paddock's own pixels like every other candidate.

**Honest gaps.** A year with no crop record is `not_managed`; a month with no
scene passing the visibility gate is `no usable scene`. Both are printed as
text, never left as `nan` and never interpolated. Carbon stock is a slab-by-slab
sum (`t C/ha`), not the product of depth-weighted means.

**Zones.** Three management zones group the six farms
(`lib/au_common.py:ZONE_BY_FARM`): **Harden** (Kanoona); **Trungley Hall**
(Caroleese + Tullokan/Dalkeith); and the **lease country** (Brentwood,
Woodlands, Rio). The `region` column in `field-names.csv` and the panel is
derived from that one mapping (`ZONE_REGION`), so a rename cannot leave a stale
label behind; posters and cards print the zone rather than a second copy of the
region name. Counties print as capitalised shire names — `Hilltops Shire`,
`Bland Shire`, `Temora Shire` — through `COUNTY_LABELS`, while the stored GADM
attribute keeps what the source delivered, so a label change never requires
re-clipping a farm. Each zone gets one poster laid out as **rows = farms,
columns = years**. Rows are farms because a zone is
not always one block of land — the lease country spans 25 km between
Brentwood/Rio and Woodlands, so a single shared frame would leave most of every
panel as empty basemap. Each row is framed to its own farm (the frame is
constant along the row, so the years compare) while one colour scale is fixed
across every panel of all three posters (so the rows and zones compare).

Counting is managed-only, because a `not_managed` field-year carries a
contextual NDVI that must never enter a statistic: the colour scale is derived
from painted values alone (an absent year is hatched grey, not coloured), each
panel prints how many paddocks were managed and how many paddock-months were
observed over those managed paddocks, and the five-year ranking counts managed
seasons only, printing e.g. `(3/5)` rather than five. The zone reports apply
the same rule: absent years are excluded from the season table, the zone means,
the per-year columns and the "Seasons" count, and the paddock table prints
`not managed` in that cell instead of an NDVI.

## Running inside an agent CLI

When invoking the pipeline from an agent harness CLI (OpenClaw, Hermes, or plain shell), you can still
activate the environment explicitly, but the entrypoints will install and re-exec
themselves if the runtime venv is missing.

```bash
bash -lc 'export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime && \
  cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src" && \
  "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
    scripts/run_farm_pipeline.py --grower-slug ... --farm-slug ...'
```

This ensures every pipeline step (including geopandas/rasterio operations) uses
the shared environment that lives alongside the replicated scripts.
