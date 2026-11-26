"""Export the pipeline's NDVI tables to committed examples.

The NDVI stage writes its tables into the runtime tree, which is never
committed. The analysis section reads only `examples/`, so the two tables it
needs are copied down here with the runtime-only columns dropped - a composite
GeoTIFF path and the scene identifier are provenance for the rasters, not
something a reader of the analysis can open.

This is the only script in this folder that needs the runtime; everything
downstream of it runs from the committed CSVs alone.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from common import CROP_GROUP, CROP_LABEL_CORRECTIONS

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent / "examples"
GROWER = "au-grain-grower"
FARMS = ("kanoona", "caroleese", "tullokan", "brentwood", "woodlands", "rio")

FIELD_YEAR_COLUMNS = [
    "field_slug", "paddock_name", "farm", "region", "year", "status",
    "crop", "crop_group", "use_type", "season_start", "season_end",
    "months_expected", "months_observed", "missing_months",
    "season_mean_ndvi", "season_integral_ndvi_months", "peak_month",
    "peak_ndvi", "scenes",
]

MONTHLY_COLUMNS = [
    "field_slug", "paddock_name", "farm", "region", "year", "month",
    "scene_date", "cloud_cover", "ndvi_median", "ndvi_p10", "ndvi_p90",
    "valid_fraction", "n_candidates", "n_usable_candidates", "used_in_season",
    "drop_reason",
]


def _runtime_root() -> Path:
    explicit = os.environ.get("AU_PADDOCKS_RUNTIME", "").strip()
    if explicit:
        return Path(explicit)
    shared = os.environ.get("DATA_PIPELINE_DATA_ROOT", "").strip()
    if shared:
        return Path(shared) / "data-pipeline"
    return Path.home() / "my-farm-advisor-runtime" / "data-pipeline"


def _read(rel: str) -> pd.DataFrame:
    root = _runtime_root() / "growers" / GROWER / "farms"
    frames = []
    for farm in FARMS:
        path = root / farm / "derived" / "tables" / rel
        if not path.exists():
            print(f"skip  {farm} {rel} (absent)")
            continue
        frames.append(pd.read_csv(path, dtype={"field_slug": str}))
    if not frames:
        raise SystemExit(f"no {rel} under {root}")
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    EXAMPLES.mkdir(parents=True, exist_ok=True)

    field_year = _read("ndvi_field_year_summary.csv")
    missing = [c for c in FIELD_YEAR_COLUMNS if c not in field_year.columns]
    if missing:
        raise SystemExit(f"field-year table is missing {missing}")
    field_year = field_year[FIELD_YEAR_COLUMNS].sort_values(
        ["region", "farm", "field_slug", "year"], kind="stable"
    )
    # The NDVI stage labels rows from its own copy of the shapefile attributes.
    # Apply the same grower corrections the panel uses, so the two committed
    # tables cannot disagree about a crop class.
    field_year["crop"] = field_year["crop"].replace(CROP_LABEL_CORRECTIONS)
    corrected_group = field_year["crop"].map(CROP_GROUP)
    field_year["crop_group"] = corrected_group.fillna(field_year["crop_group"])
    out = EXAMPLES / "ndvi_field_year.csv"
    field_year.to_csv(out, index=False, float_format="%.4f")

    monthly = _read("ndvi_monthly_stats.csv")
    missing = [c for c in MONTHLY_COLUMNS if c not in monthly.columns]
    if missing:
        raise SystemExit(f"monthly table is missing {missing}")
    monthly = monthly[MONTHLY_COLUMNS].sort_values(
        ["region", "farm", "field_slug", "year", "month"], kind="stable"
    )
    out_m = EXAMPLES / "ndvi_monthly.csv"
    monthly.to_csv(out_m, index=False, float_format="%.4f")

    observed = field_year[field_year["status"] == "observed"]
    print(f"field-years        : {len(field_year)} "
          f"({len(observed)} observed, {len(field_year) - len(observed)} not managed)")
    print(f"fields             : {field_year['field_slug'].nunique()}")
    print(f"monthly rows       : {len(monthly)} "
          f"({int(monthly['used_in_season'].sum())} in season)")
    print(f"wrote {out}")
    print(f"wrote {out_m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
