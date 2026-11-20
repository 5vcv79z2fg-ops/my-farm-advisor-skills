"""Export canonical field boundaries as GeoJSON for downstream components.

Two products, both written outside the repository under the runtime tree:

``boundaries/<year>.geojson``
    Every managed field as it existed in that year, in EPSG:4326. Merged
    2025 sub-paddocks are unioned onto their parent, so a field-year always
    carries the geometry that was actually farmed that year. Satellite work
    uses these.

``canonical/<field_id>.geojson``
    One polygon per field, taken from the newest year that has it. Soil and
    terrain are effectively static, so a single footprint is the right unit
    there.

Crop attributes are read from ``examples/panel_field_year.csv``, not from the
shapefile record. The panel resolves the dominant crop by area across a merged
field's parts and carries the grower-confirmed rows; taking the first shapefile
record would label a merged field with whichever part happened to be listed
first.

Usage
-----
    python export_boundaries.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform, unary_union

from common import RUNTIME_ROOT, YEARS, canonical_ids, components_for, load_all

PANEL = Path(__file__).resolve().parents[1] / "examples" / "panel_field_year.csv"

COMPONENTS_DIR = RUNTIME_ROOT / "components"
BOUNDARY_DIR = COMPONENTS_DIR / "boundaries"
CANONICAL_DIR = COMPONENTS_DIR / "canonical"

_TO_WGS84 = Transformer.from_crs("EPSG:3577", "EPSG:4326", always_xy=True).transform


def _feature(geom, props: dict) -> dict:
    return {
        "type": "Feature",
        "properties": props,
        "geometry": mapping(transform(_TO_WGS84, geom)),
    }


def _write(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _geometry(cid: int, year: int, layers: dict):
    """Union of every shapefile part that made up this field in that year."""
    comps = components_for(cid, layers[year])
    if not comps:
        return None, 0
    geom = comps[0].geom if len(comps) == 1 else unary_union([c.geom for c in comps])
    return geom, len(comps)


def main() -> int:
    layers = load_all()
    ids = canonical_ids(layers)
    panel = pd.read_csv(PANEL)
    panel["geometry_source"] = pd.to_numeric(
        panel["geometry_source"], errors="coerce"
    ).astype("Int64")

    lookup = {
        (int(row.field_id), int(row.year)): row
        for row in panel.itertuples(index=False)
        if row.status == "observed"
    }

    written: dict[int, int] = {}
    for year in YEARS:
        features = []
        for cid in ids:
            row = lookup.get((cid, year))
            if row is None:
                continue
            geom, parts = _geometry(cid, year, layers)
            if geom is None:
                # Field absent from this year's shapefile but grower-confirmed.
                src = int(row.geometry_source) if pd.notna(row.geometry_source) else None
                if src is None:
                    continue
                geom, parts = _geometry(cid, src, layers)
                if geom is None:
                    continue
            features.append(
                _feature(
                    geom,
                    {
                        "field_id": str(cid),
                        "paddock_name": row.paddock_name,
                        "farm": row.farm,
                        "region": row.region,
                        "year": int(year),
                        "crop": row.crop,
                        "crop_group": row.crop_group,
                        "variety": row.variety,
                        "use_type": row.use_type,
                        "land_use": row.land_use,
                        "mixed_flag": bool(row.mixed_flag),
                        "area_ha_geom": round(geom.area / 10_000.0, 2),
                        "components": parts,
                    },
                )
            )
        _write(BOUNDARY_DIR / f"{year}.geojson", features)
        written[year] = len(features)

    canonical_count = 0
    for cid in ids:
        rows = panel[(panel.field_id == cid) & (panel.status == "observed")]
        if rows.empty:
            continue
        geom = None
        for year in (2026, 2025, 2024, 2023, 2022):
            geom, _ = _geometry(cid, year, layers)
            if geom is not None:
                break
        if geom is None:
            continue
        newest = rows.sort_values("year").iloc[-1]
        _write(
            CANONICAL_DIR / f"{cid}.geojson",
            [
                _feature(
                    geom,
                    {
                        "field_id": str(cid),
                        "paddock_name": newest.paddock_name,
                        "farm": newest.farm,
                        "region": newest.region,
                        "area_ha_geom": round(geom.area / 10_000.0, 2),
                    },
                )
            ],
        )
        canonical_count += 1

    print(f"runtime     : {COMPONENTS_DIR}")
    for year in YEARS:
        print(f"  {year}: {written[year]:2d} field polygons")
    print(f"  canonical: {canonical_count} static footprints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
