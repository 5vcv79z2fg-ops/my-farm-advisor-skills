# Data audit — paddock boundaries and crop records, 2022–2026

Plain-language record of what was checked before any analysis. Numbers come
from `qc_boundaries.csv`, `qc_crosswalk.csv` and `panel_field_year.csv`.

## Scope

| Item | Value |
|---|---|
| Management regions | 3 — Kanoona, Caroleese–Tullokan, Lease country |
| Farms | 6 |
| Canonical fields | 33 |
| Years | 2022–2026 |
| Observed field-years | 155 |
| Not-managed field-years | 10 |
| Crop coverage | 155 / 155 |
| Variety coverage | 155 / 155 |
| CRS, source | WGS84; reprojected to EPSG:3577 (Australian Albers) for area and distance |

`not_managed` means the paddock was not part of the enterprise that year. It
never means the data are missing.

## Field count by year

| Year | Observed | Not managed |
|---|---:|---:|
| 2022 | 30 | Brentwood ×2, Woodlands |
| 2023 | 30 | Brentwood ×2, Woodlands |
| 2024 | 32 | Woodlands |
| 2025 | 33 | — |
| 2026 | 30 | Rio ×3 |

Region totals, using the latest boundary per field:

| Region | Fields | Geometry (ha) | Reported (ha) |
|---|---:|---:|---:|
| Kanoona | 16 | 386.2 | 384.5 |
| Caroleese–Tullokan | 11 | 701.0 | 706.6 |
| Lease country | 6 | 1,885.2 | 1,814.4 |
| **Total** | **33** | **2,972.4** | **2,905.4** |

## Paddock identity

- Paddock IDs are stable. Every field keeps one ID across all years it is
  present, and names and farm assignments never drift.
- 36 distinct IDs appear across the five files. Three are retired
  sub-paddocks absorbed in 2025, leaving 33 canonical fields.
- No duplicate IDs in any year.

## Boundary consistency

The five files use exactly two boundary versions.

| Version | Years | Kanoona records |
|---|---|---:|
| A | 2022, 2023, 2024 | 19 |
| B | 2025, 2026 | 15–16 |

Within a version the polygons are identical — 0.000% symmetric difference,
0.00 m Hausdorff distance, 0.00 m centroid shift. Nineteen comparisons per
field-year confirm this: 119 of 122 changed-field comparisons are exact.

Only three boundaries change, all in 2025, all from merging a retired
sub-paddock into its parent. Measured against the merged 2024 footprint:

| Field | Ha 2025 | Symmetric difference | Hausdorff | Centroid shift |
|---|---:|---:|---:|---:|
| Copperbella | 30.34 | 5.90% | 115.3 m | 8.3 m |
| Lane Lucerne | 32.01 | 1.10% | 160.6 m | 0.2 m |
| Oat Paddock | 20.31 | 3.25% | 111.7 m | 0.3 m |

The merges are near-exact reconstructions, not re-digitisation: each retired
polygon overlaps its parent by 97.4–97.5%.

## Merge crosswalk

| Retired | Ha | Parent | Ha 2024 | Ha 2025 | Merged | Overlap |
|---|---:|---|---:|---:|---:|---:|
| Hayshed | 5.81 | Lane Lucerne | 26.19 | 32.01 | 32.00 | 97.5% |
| House | 7.30 | Oat Paddock | 12.73 | 20.31 | 20.03 | 97.4% |
| Copperbella Creek | 15.79 | Copperbella | 13.59 | 30.34 | 29.38 | 97.4% |

For 2022–2024 those parents carried two crops. The original records are kept
in `crop_mix` and the area-weighted dominant crop is the `crop` column. Nine
field-years are affected.

## Area attribute versus geometry

Reported area is cropped area. Geometry is the full paddock footprint. The
largest differences are explained by non-cropped land inside the boundary.

| Field | Difference | Explanation |
|---|---:|---|
| Rio South | +92.12 ha (+32.4%) | tree grove and rocky zones inside the boundary |
| Stewarts | +2.20 ha (+18.1%) | tree grove and rocky zones |
| Rio North | −21.07 ha (−4.4%) | reported area exceeds geometry |
| North Gully | −5.30 ha (−4.1%) | reported area exceeds geometry |
| Far Manning | −1.00 ha (−3.9%) | reported area exceeds geometry |
| Copperbella | +0.34 ha (+1.1%) | merged boundary |
| Oat Paddock | +0.31 ha (+1.6%) | merged boundary |

Convention: geometry for spatial statistics, reported area for crop-area
statistics, both shown wherever the distinction matters.

## Crop records

- 7 crop classes: wheat, barley, canola, narrow leaf lupin, field peas,
  faba/broad bean, pasture.
- Crop and variety are present in every observed field-year.
- Pump Lane is permanent pasture for all five years and is flagged
  `permanent_pasture`, so rotation-diversity metrics are reported both with
  and without it.
- 18 distinct five-year sequences across 33 fields. Repeats are genuine
  whole-farm programming, confirmed by the grower.

## Known limitations

- Weather is available at farm centroids, not per field. Kanoona's 16 fields
  sit inside a single 0.05° cell, so they share one series.
- The primary season is 1 April – 30 November (244 days), to match sowing
  through harvest start. 2026 has data only to 19 November, so its primary
  season is 233 of 244 days and every 2026 seasonal figure is a lower bound.
  The 1 April – 31 October window is complete for all five years and is the
  fair comparison for 2026.
- No yield or management input data in this build.
- Three management regions within one enterprise, not three independent
  growers.
