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

## Commands

Run from this directory with the project virtual environment:

```bash
python src/build_panel.py       # panel_field_year.csv, rotation_spine.csv
python src/qc_boundaries.py     # qc_boundaries.csv, qc_crosswalk.csv
python src/fetch_weather.py     # requires SILO_EMAIL, writes weather tables
python src/fetch_weather.py --offline   # rebuild from cache, no email, no network
python src/make_figures.py      # figures/fig01..fig04
python src/make_figures.py --only 4
```

`fetch_weather.py --offline` rebuilds every derived weather table from the
raw SILO cache. Use it for any change to the season definition; only a cache
miss requires the email address.

`make_figures.py` skips figure 2 with a message if the weather tables are
absent, so the panel and boundary work can proceed before climate access is
arranged.

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

## Weather

SILO point data at 0.05° is the primary source. SILO identifies callers by
email address, supplied as `SILO_EMAIL`; there is no API key. Keep the address
at runtime and out of every committed file.

The enterprise sows mid-April to mid/late May and harvests mid/late November
to mid/late December, so the **primary season is 1 April – 30 November**
(244 days). A 31 October cut-off would discard grain fill and every November
heat day. A secondary **1 April – 31 October** window (214 days) is carried
alongside because it is the only window complete for all five years,
including 2026.

Stage sub-windows: sowing 1 April – 31 May (rainfall and establishment frost),
flowering 1 September – 31 October (frost), November alone (heat). GDD base is
0 °C for wheat and pasture, 5 °C for canola and legumes; frost is Tmin ≤ 2 °C
and heat is Tmax ≥ 30 °C.

Because sowing runs from mid-April to mid-May, a 1 April start is a **ceiling,
not a sowing date**. Moving the start to 1 May removes roughly 490 GDD across
the season, so never read the window total as what a paddock actually
received.


## Outputs

`fig01_field_area.png` field size by region, `fig02_seasonal_rainfall.png`
seasonal rainfall, `fig03_crop_mix.png` crop area share, `fig04_paddock_map.png`
overview plus one panel per region coloured by the latest crop.

## Limits

Weather is per farm centroid, not per field. A partial season is labelled as
such. Differences between regions are associations, not causal effects: the
regions differ in field size, crop mix and management at the same time.
