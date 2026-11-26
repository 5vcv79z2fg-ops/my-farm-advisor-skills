# Field comparison EDA

Build a field-year panel from annual paddock shapefiles and compare field
size, boundaries, weather and crop rotation across management regions.

Use this guide when a grower supplies per-year paddock boundaries plus the
crop planted in each paddock, and the question is “how do these regions
compare?” rather than “what is in this one field?”.

## When to use

- Several years of paddock polygons with the same paddock identifiers.
- Crop records per paddock per year, from the farm management system.
- A small number of management regions or farms to compare.
- No parcel-level crop raster is available. This workflow is the substitute
  for a CDL-style product: grower records replace the remote-sensing layer.

Do not use it for a single year, or for boundaries that were re-digitised
from scratch each season without stable identifiers.

## Panels

| Panel | Use |
|---|---|
| `examples/panel_field_year.csv` | one row per field-year, long format |
| `examples/rotation_spine.csv` | one row per field, crop by year |
| `examples/qc_boundaries.csv` | boundary change and area diagnostics |
| `examples/qc_crosswalk.csv` | retired sub-paddocks mapped to parents |
| `examples/qc_summary.md` | plain-language audit record |
| `examples/ndvi_field_year.csv` | one row per field-year, NDVI season summary |
| `examples/ndvi_monthly.csv` | one row per field-month, best-scene NDVI |

## Commands

Run from this directory with the project virtual environment:

```bash
python src/build_panel.py       # panel_field_year.csv, rotation_spine.csv
python src/qc_boundaries.py     # qc_boundaries.csv, qc_crosswalk.csv
python src/fetch_weather.py     # requires SILO_EMAIL, writes weather tables
python src/fetch_weather.py --offline   # rebuild from cache, no email, no network
python src/make_figures.py      # figures/fig01..fig05
python src/make_figures.py --only 4
python src/export_ndvi_examples.py      # pipeline NDVI tables -> examples/
python src/build_analysis.py            # rewrites ANALYSIS.md
python src/build_analysis.py --check    # fails if ANALYSIS.md is stale
python src/build_report.py              # rewrites eda_report.html
python src/build_report.py --check      # fails if the HTML page is stale
```

`fetch_weather.py --offline` rebuilds every derived weather table from the
raw SILO cache. Use it for any change to the season definition; only a cache
miss requires the email address.

`make_figures.py` skips figure 2 or 5 with a message if the tables they need are
absent, so the panel and boundary work can proceed before climate access is
arranged.

`export_ndvi_examples.py` is the one command here that reads the untracked
runtime tree; everything after it reads `examples/` alone. It drops the two
runtime-only columns (`composite_tif`, `scene_id`) so the committed tables hold
data rather than paths into a machine that no longer exists.

## Analysis

[ANALYSIS.md](ANALYSIS.md) answers the assignment's data-analysis question in
three parts: rotation from the panel, climate from the SILO seasonal table, and
NDVI season behaviour from the two NDVI tables. Its numbers are not typed into
the markdown — `src/build_analysis.py` computes them and substitutes them into
`src/analysis_template.md`. The builder fails if a template token has no
computation behind it and if a computed value is never printed, so a changed CSV
cannot leave a stale sentence behind. `--check` is the test to run before
committing an analysis change.

[eda_report.html](eda_report.html) is the one-page version: summary cards, five
key tables, the five figures and the limits, with every image base64-embedded so
the file opens from disk with no server or network. `src/build_report.py` builds
it from the same `build_findings` result as `ANALYSIS.md`, and its `--check` flag
is the drift test for the page.

## Conventions

These are the decisions that the panel depends on. Keep them if the workflow
is reused.

- **Geometry CRS.** Source layers are WGS84; all area and distance work
  happens in EPSG:3577.
- **Two area measures.** `area_ha_geom` is the paddock footprint,
  `area_ha_reported` is the cropped area from the management system. Use
  geometry for spatial statistics and reported area for crop-area statistics.
  Never silently swap one for the other.
- **Absent is not missing.** A year in which a paddock was not managed is
  `status = not_managed`. Do not impute it and do not call it a gap in the
  data.
- **Merges are explicit.** When a sub-paddock is retired, map it to its
  surviving parent with a crosswalk rather than by name. Keep the retired
  record's crop in `crop_mix`.
- **Mixed years are preserved.** If a merged field carried two crops before
  the merge, `crop` holds the area-weighted dominant crop and `crop_mix`
  holds both. Do not discard the components.
- **Permanent pasture is flagged.** A paddock that never rotates is
  `land_use = permanent_pasture`. Report rotation diversity with and without
  it.
- **Confirmation is a source.** A grower-confirmed crop for a year with no
  polygon is `crop_source = grower_confirmed` and records the year the
  geometry came from.
- **Corrections are overrides.** When the grower corrects a recorded crop
  label or variety, add a narrow key to `CROP_LABEL_CORRECTIONS` or
  `VARIETY_CORRECTIONS` in `src/common.py` rather than editing the exported
  CSV. Rebuild the panel and record the change in `examples/qc_summary.md`;
  `variety_source` keeps the audit trail. Then re-run
  `src/stage_pipeline_grower.py`, because the AU pipeline keeps its own copy of
  the crop records and will keep printing the old label until it is restaged.

## Weather

SILO point data at 0.05° is the primary source. SILO identifies callers by
email address, supplied as `SILO_EMAIL`; there is no API key. Keep the address
at runtime and out of every committed file.

Growing seasons differ by location. Kanoona, the easternmost farm, runs
**1 April – 30 November** (244 days). Every other farm runs **1 April –
15 November** (229 days). The difference is real management, so it is kept in
the data rather than smoothed away: `season_window`, `season_days` and
`season_expected_days` record each farm's own dates. The pipeline's zone-report
weather charts use each farm's own season and plot GDD base 0 °C for every crop,
labelled on the axis, because the rotation mixes cereals, canola and legumes on
one farm.

Because a two-week head start would flatter Kanoona in any regional total,
every farm also carries a **common window of 1 April – 15 November**
(229 days). That window is complete for all 30 farm-years, including 2026.
Compare regions on `common_*`; describe one farm on `season_*`.
`extension_days`, `rain_extension_mm` and `gdd_base0_extension` isolate what
Kanoona's extra fortnight adds: 15 days and roughly 220–335 GDD₀ in a complete
year.

Stage sub-windows: sowing 1 April – 31 May (rainfall and establishment frost),
flowering 1 September – 31 October (frost). GDD base is 0 °C for wheat and
pasture, 5 °C for canola and legumes; frost is Tmin ≤ 2 °C and heat is
Tmax ≥ 30 °C.

Because sowing runs from mid-April to mid-May, a 1 April start is a **ceiling,
not a sowing date**. Moving the start to 1 May removes roughly 490 GDD across
the season, so never read the window total as what a paddock actually
received.


## Outputs

`fig01_field_area.png` field size by region, `fig02_seasonal_rainfall.png`
seasonal rainfall, `fig03_crop_mix.png` crop area share, `fig04_paddock_map.png`
overview plus one panel per region coloured by the latest crop,
`fig05_ndvi_season.png` monthly NDVI shape by zone, the season integral by zone
and year, canopy by crop, and integral against season rain.
`eda_report.html` collects the five figures and the headline tables into one
self-contained page.

## Limits

Weather is per farm centroid, not per field. A partial season is labelled as
such. Differences between regions are associations, not causal effects: the
regions differ in field size, crop mix and management at the same time.

NDVI is a monthly best-scene composite, so a missing month is reported as a gap
and never interpolated. A missing November raises a season mean by about 0.02
NDVI while removing about 0.19 NDVI-months from the integral; the analysis
quantifies both. The 10 `not_managed` field-years are excluded from every crop
statistic even though NDVI exists for them — no crop record means no crop to
compare.
