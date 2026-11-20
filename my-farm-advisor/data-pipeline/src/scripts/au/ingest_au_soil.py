#!/usr/bin/env python3
"""Ingest SLGA/ASF soil for one AU farm into canonical field paths.

The farm-level grids were streamed once per farm by
``eda-field-comparison/src/sample_soil.py`` (210 clips, cached outside the
repo). This stage crops each field's window from those local grids, writes the
canonical per-field rasters the renderers expect, and computes centre-in-
polygon statistics for the profile cards.

Outputs, per field:
    soil/slga_asf/<property>_<depth>.tif
    soil/slga_asf_property_stats.csv
    soil/slga_asf_profile.csv

Per farm:
    derived/tables/slga_asf_property_stats.csv
    derived/tables/slga_asf_profile.csv
    manifests/slga_asf_ingest.json

Usage:
    DATA_PIPELINE_DATA_ROOT=... AG_GROWER_SLUG=au-grain-grower \
    AG_FARM_SLUG=kanoona python au/ingest_au_soil.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import rasterio.windows
from rasterio.features import rasterize

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.au_common import (  # noqa: E402
    DEPTH_THICKNESS_M,
    DEPTH_TOP_M,
    PROPERTY_META,
    SOIL_DEPTHS,
    SOIL_PROPERTIES,
    SOURCE_NAME,
    env_slugs,
    load_field_names,
    load_fields,
    load_inventory,
    profile_from_long,
    soil_raster_dir,
    write_json,
    zone_for_farm,
)
from lib.paths import DATA_ROOT, farm_dir, field_soil_dir  # noqa: E402

PAD_DEG = 0.002


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _crop(src_path: Path, dst_path: Path, geom) -> bool:
    """Write the field's window from a farm grid; False when they do not meet."""
    with rasterio.open(src_path) as src:
        minx, miny, maxx, maxy = geom.bounds
        bbox = (minx - PAD_DEG, miny - PAD_DEG, maxx + PAD_DEG, maxy + PAD_DEG)
        left = max(bbox[0], src.bounds.left)
        bottom = max(bbox[1], src.bounds.bottom)
        right = min(bbox[2], src.bounds.right)
        top = min(bbox[3], src.bounds.top)
        if left >= right or bottom >= top:
            return False
        window = rasterio.windows.from_bounds(left, bottom, right, top, transform=src.transform)
        data = src.read(1, window=window)
        profile = src.profile.copy()
        profile.update(
            height=data.shape[0],
            width=data.shape[1],
            transform=rasterio.windows.transform(window, src.transform),
        )
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **profile) as dst:
            dst.write(data, 1)
    return True


def _zonal(geom, raster_path: Path) -> dict:
    with rasterio.open(raster_path) as ds:
        data = ds.read(1, masked=True)
        mask = rasterize(
            [(geom, 1)],
            out_shape=data.shape,
            transform=ds.transform,
            fill=0,
            all_touched=False,
            dtype="uint8",
        ).astype(bool)
        values = np.asarray(data[mask].compressed(), dtype="float64")
        values = values[np.isfinite(values)]
    if values.size == 0:
        return {"mean": np.nan, "min": np.nan, "max": np.nan, "std": np.nan, "n_pixels": 0}
    return {
        "mean": float(values.mean()),
        "min": float(values.min()),
        "max": float(values.max()),
        "std": float(values.std()),
        "n_pixels": int(values.size),
    }


def _depth_weighted_mean(parts: list[tuple[Path, float]]):
    """Thickness-weighted mean of same-grid slabs -> (array, reference path)."""
    weights = np.array([w for _, w in parts], dtype="float64")
    weights = weights / weights.sum()
    reference = parts[0][0]
    stack = []
    with rasterio.open(reference) as ref:
        transform, crs, shape = ref.transform, ref.crs, (ref.height, ref.width)
    for path, _ in parts:
        with rasterio.open(path) as ds:
            data = ds.read(1, masked=True)
            # promote before filling: filled(nan) is impossible on a uint16 mask
            array = np.asarray(data.astype("float64").filled(np.nan), dtype="float64")
            if array.shape != shape or ds.transform != transform:
                array = np.full(shape, np.nan, dtype="float64")
        stack.append(array)
    stacked = np.stack(stack, axis=0)
    valid = np.isfinite(stacked).any(axis=0)
    blended = np.full(shape, np.nan, dtype="float32")
    with np.errstate(all="ignore"):
        mean = np.nansum(stacked * weights[:, None, None], axis=0) / np.nansum(
            np.where(np.isfinite(stacked), weights[:, None, None], 0.0), axis=0
        )
    blended[valid] = mean[valid].astype("float32")
    return blended, reference


def _write_raster(path: Path, array: np.ndarray, reference: Path) -> None:
    from raster_rules import cog_profile  # pyright: ignore[reportMissingImports]

    with rasterio.open(reference) as ref:
        profile = ref.profile.copy()
    profile.update(dtype=rasterio.float32, count=1, nodata=np.nan, compress="lzw")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **cog_profile(profile, resampling="average")) as dst:
        dst.write(array, 1)


def main() -> int:
    grower, farm = env_slugs()
    inventory = load_inventory(grower, farm)
    names = load_field_names(grower, farm).set_index("field_slug")
    fields = load_fields(grower, farm)
    field_lookup = {
        str(row.field_id): row for row in fields.itertuples(index=False)
    }
    rasters = soil_raster_dir(farm)
    if not rasters.is_dir():
        raise SystemExit(
            f"farm soil grids missing under {rasters}; run "
            "eda-field-comparison/src/sample_soil.py first"
        )

    rows: list[dict] = []
    missing: list[str] = []
    for slug in inventory["field_slug"]:
        record = inventory.loc[inventory["field_slug"] == slug].iloc[0]
        fid = str(record["field_id"])
        if fid not in field_lookup:
            missing.append(f"{slug}: not in farm boundary")
            continue
        geom = field_lookup[fid].geometry
        row = names.loc[slug] if slug in names.index else None
        name = str(row["paddock_name"]) if row is not None else slug
        farm_display = str(row["farm"]) if row is not None else farm
        region = str(row["region"]) if row is not None else zone_for_farm(farm_display)
        for prop in SOIL_PROPERTIES:
            for depth in SOIL_DEPTHS:
                src = rasters / f"{prop.lower()}_{depth.replace('-', '_')}.tif"
                if not src.exists():
                    missing.append(f"{slug}: missing grid {src.name}")
                    continue
                dst = (
                    field_soil_dir(grower, farm, slug)
                    / "slga_asf"
                    / f"{prop.lower()}_{depth.replace('-', '_')}.tif"
                )
                if not _crop(src, dst, geom):
                    missing.append(f"{slug}: no overlap with {src.name}")
                    continue
                stat = _zonal(geom, dst)
                rows.append(
                    {
                        "field_id": fid,
                        "field_slug": slug,
                        "paddock_name": name,
                        "farm": farm_display,
                        "region": region,
                        "property": prop,
                        "attribute": PROPERTY_META[prop]["attribute"],
                        "units": PROPERTY_META[prop]["units"],
                        "depth": depth,
                        "depth_top_m": DEPTH_TOP_M[depth],
                        "depth_thickness_m": DEPTH_THICKNESS_M[depth],
                        **stat,
                        "source": SOURCE_NAME,
                        "doi": PROPERTY_META[prop]["doi"],
                        "accessed_at": _now(),
                    }
                )

    if not rows:
        raise SystemExit("no SLGA rows produced")
    import pandas as pd

    long = pd.DataFrame(rows).sort_values(["field_slug", "property", "depth_top_m"])
    profile = profile_from_long(long)

    # 0-30 cm depth-weighted mean raster per property, so the map sheet and the
    # profile table agree slab for slab rather than reading one 0-5 cm layer.
    for slug, group in long[long["depth"].isin(SOIL_DEPTHS[:3])].groupby("field_slug"):
        for prop, slabs in group.groupby("property"):
            parts = []
            for row in slabs.itertuples(index=False):
                path = (
                    field_soil_dir(grower, farm, slug)
                    / "slga_asf"
                    / f"{prop.lower()}_{row.depth.replace('-', '_')}.tif"
                )
                if path.exists():
                    parts.append((path, DEPTH_THICKNESS_M[row.depth]))
            if not parts:
                continue
            blended = _depth_weighted_mean(parts)
            out = (
                field_soil_dir(grower, farm, slug)
                / "slga_asf"
                / f"{prop.lower()}_0_30cm.tif"
            )
            if blended is not None:
                _write_raster(out, blended[0], blended[1])

    farm_tables = farm_dir(grower, farm) / "derived" / "tables"
    farm_tables.mkdir(parents=True, exist_ok=True)
    long.to_csv(farm_tables / "slga_asf_property_stats.csv", index=False)
    profile.to_csv(farm_tables / "slga_asf_profile.csv", index=False)

    for slug, group in long.groupby("field_slug", sort=True):
        soil = field_soil_dir(grower, farm, slug)
        soil.mkdir(parents=True, exist_ok=True)
        group.to_csv(soil / "slga_asf_property_stats.csv", index=False)
        profile.loc[profile["field_slug"] == slug].to_csv(
            soil / "slga_asf_profile.csv", index=False
        )

    write_json(
        farm_dir(grower, farm) / "manifests" / "slga_asf_ingest.json",
        {
            "grower_slug": grower,
            "farm_slug": farm,
            "source": SOURCE_NAME,
            "properties": list(SOIL_PROPERTIES),
            "depths": list(SOIL_DEPTHS),
            "field_count": int(long["field_slug"].nunique()),
            "row_count": int(len(long)),
            "missing": missing,
            "accessed_at": _now(),
            "runtime_root": str(DATA_ROOT),
        },
    )

    print(
        f"SLGA/ASF: {len(long)} rows | {long['field_slug'].nunique()} fields | "
        f"{len(SOIL_DEPTHS)} depths -> {farm_tables}"
    )
    if missing:
        print(f"  warnings: {len(missing)}")
        for item in missing[:10]:
            print(f"    {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
