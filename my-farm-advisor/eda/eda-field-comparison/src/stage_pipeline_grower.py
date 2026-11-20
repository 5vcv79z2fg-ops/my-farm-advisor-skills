"""Stage the canonical paddocks into the data-pipeline grower layout.

The pipeline expects one GeoJSON per farm at
``growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson``, with a
hard-required ``field_id`` and ``country`` on every feature. This script writes
the *input* payloads for ``ingest_user_boundaries.py`` from the canonical
footprints, one file per farm, and leaves the ingest itself to that script so
the county gate, inventory and provenance remain single-sourced.

Canonical (newest) footprints are used deliberately: the pipeline's satellite
and reporting legs carry one static boundary per field. The field-year panel
keeps the year-matched geometry for the analysis.

Usage
-----
    python stage_pipeline_grower.py
    # then, per farm:
    DATA_PIPELINE_DATA_ROOT=... python src/scripts/ingest/ingest_user_boundaries.py \
        --geojson <staging>/<farm>.geojson --grower-slug au-grain-grower \
        --farm-slug <farm> --counties-dir .../shared/geoadmin/l2_counties_aus \
        --handoff off
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import geopandas as gpd

HERE = Path(__file__).resolve().parent
GROWER_SLUG = "au-grain-grower"
COUNTRY = "AUS"  # GADM ISO3; the county echo matches on this, not ISO-2.

FARM_SLUG = {
    "Brentwood (lease)": "brentwood",
    "Caroleese": "caroleese",
    "Kanoona": "kanoona",
    "Rio (lease)": "rio",
    "Tullokan / Dalkeith": "tullokan",
    "Woodlands (lease)": "woodlands",
}

REGION_BY_FARM = {
    "Kanoona": "Kanoona",
    "Caroleese": "Caroleese–Tullokan",
    "Tullokan / Dalkeith": "Caroleese–Tullokan",
    "Brentwood (lease)": "Lease country",
    "Woodlands (lease)": "Lease country",
    "Rio (lease)": "Lease country",
}


def _runtime_root() -> Path:
    explicit = os.environ.get("AU_PADDOCKS_RUNTIME", "").strip()
    if explicit:
        return Path(explicit)
    shared = os.environ.get("DATA_PIPELINE_DATA_ROOT", "").strip()
    if shared:
        return Path(shared) / "au-paddocks"
    return Path.home() / "my-farm-advisor-runtime" / "au-paddocks"


RUNTIME_ROOT = _runtime_root()
CANONICAL_DIR = RUNTIME_ROOT / "components" / "canonical"
STAGING_DIR = RUNTIME_ROOT / "components" / "pipeline-staging"
PANEL_CSV = HERE.parent / "examples" / "panel_field_year.csv"


def main() -> int:
    import pandas as pd

    frames = [gpd.read_file(p) for p in sorted(CANONICAL_DIR.glob("*.geojson"))]
    if not frames:
        raise SystemExit(f"no canonical boundaries under {CANONICAL_DIR}")
    fields = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326"
    )
    panel = pd.read_csv(PANEL_CSV, dtype={"field_id": str})
    panel["field_id"] = panel["field_id"].astype(str)

    pipeline_root = Path(
        os.environ.get("DATA_PIPELINE_DATA_ROOT", "").strip()
        or (Path.home() / "my-farm-advisor-runtime")
    )
    grower_dir = pipeline_root / "data-pipeline" / "growers" / GROWER_SLUG

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for farm, group in fields.groupby("farm", sort=True):
        farm_slug = FARM_SLUG[farm]
        features = []
        names = []
        for row in group.itertuples(index=False):
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "field_id": str(row.field_id),
                        "country": COUNTRY,
                        "field_name": row.paddock_name,
                        "paddock_name": row.paddock_name,
                        "farm": farm,
                        "farm_slug": farm_slug,
                        "region": REGION_BY_FARM[farm],
                        "state": "NSW",
                        "area_ha_est": round(row.geometry.area / 10_000.0, 2),
                    },
                    "geometry": row.geometry.__geo_interface__,
                }
            )
            names.append(
                {
                    "field_id": str(row.field_id),
                    "field_slug": str(row.field_id),
                    "paddock_name": row.paddock_name,
                    "farm": farm,
                    "farm_slug": farm_slug,
                    "region": REGION_BY_FARM[farm],
                }
            )
        payload = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": features,
        }
        out = STAGING_DIR / f"{farm_slug}.geojson"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        manifest_dir = grower_dir / "farms" / farm_slug / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(names).sort_values("paddock_name").to_csv(
            manifest_dir / "field-names.csv", index=False
        )

        panel_farm = panel[panel["farm"] == farm]
        crops = panel_farm[
            ["field_id", "year", "status", "crop", "crop_group", "use_type"]
        ].copy()
        crops["field_id"] = crops["field_id"].astype(str)
        crops["field_slug"] = crops["field_id"]
        crops = crops[
            [
                "field_id",
                "field_slug",
                "year",
                "status",
                "crop",
                "crop_group",
                "use_type",
            ]
        ].sort_values(["field_slug", "year"])
        crops.to_csv(manifest_dir / "field-crops.csv", index=False)

        print(f"{farm_slug:10s} {len(features):2d} fields -> {out}")

    print(f"\ngrower {GROWER_SLUG} | {len(fields)} fields | {len(FARM_SLUG)} farms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
