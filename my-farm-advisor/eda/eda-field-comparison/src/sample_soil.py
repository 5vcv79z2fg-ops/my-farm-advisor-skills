"""Sample SLGA/ASF soil properties for every canonical paddock, 0-100 cm.

The SLGA grids are national COGs at 3 arc-seconds (~90 m). Streaming one clip
per field per property per depth would mean ~1,155 remote reads of the same
files, so this script clips once per *farm* bounding box and then masks each
paddock out of that small local grid. Six farms x seven properties x five
depths = 210 clips, cached under the runtime tree.

Products
--------
``examples/soil_field_properties.csv``
    Long format, one row per field x property x depth: mean/min/max/std and
    the number of 90 m cells whose centre falls inside the paddock.

``examples/soil_field_profile.csv``
    One row per field. Depth-weighted 0-100 cm and 0-30 cm values, USDA
    texture class from the 0-30 cm sand/clay/silt, and organic-carbon stock
    to 30 and 100 cm (bulk density comes from the same product).

Caveat carried into the report: a 3 arc-second cell is coarse relative to a
7 ha paddock (~9 cells). ``n_pixels`` and ``cell_frac`` are published so a
thin sample is visible rather than implied.

Usage
-----
    python sample_soil.py            # uses the runtime cache, clips what is missing
    python sample_soil.py --force    # re-clip every farm grid
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Geod
from rasterio.features import rasterize

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "my-farm-advisor" / "soil" / "australia-soil" / "src"))

import australia_soil as au  # noqa: E402

EXAMPLES = HERE.parent / "examples"


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
SOIL_DIR = RUNTIME_ROOT / "components" / "soil"

PROPERTIES = (
    "CLAY",
    "SILT",
    "SAND",
    "CEC",
    "PHCALCIUMCHLORIDE",
    "CARBON",
    "BULKDENSITY",
)
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm")

# Farm display name -> filesystem slug. Matches the grower/farm slugs used by
# the data pipeline so a later manifest build does not fork the naming.
FARM_SLUG = {
    "Brentwood (lease)": "brentwood",
    "Caroleese": "caroleese",
    "Kanoona": "kanoona",
    "Rio (lease)": "rio",
    "Tullokan / Dalkeith": "tullokan",
    "Woodlands (lease)": "woodlands",
}

# Pad the farm bbox by at least one 3 arc-second cell so edge cells are whole.
PAD_DEG = 0.017
GEOD = Geod(ellps="WGS84")


def load_canonical() -> gpd.GeoDataFrame:
    frames = []
    for path in sorted(CANONICAL_DIR.glob("*.geojson")):
        frames.append(gpd.read_file(path))
    if not frames:
        raise FileNotFoundError(f"no canonical boundaries under {CANONICAL_DIR}")
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    gdf["field_id"] = gdf["field_id"].astype(str)
    gdf["farm_slug"] = gdf["farm"].map(FARM_SLUG)
    if gdf["farm_slug"].isna().any():
        missing = sorted(gdf.loc[gdf["farm_slug"].isna(), "farm"].unique())
        raise KeyError(f"no slug for farm(s): {missing}")
    gdf["area_m2"] = gdf.geometry.to_crs(3577).area
    return gdf


def clip_farm(farm_slug: str, bbox, force: bool) -> dict[tuple[str, str], Path]:
    """Clip every property/depth for one farm, skipping cached grids."""
    out_dir = SOIL_DIR / "rasters" / farm_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[tuple[str, str], Path] = {}
    for prop in PROPERTIES:
        done = 0
        for depth in DEPTHS:
            out_path = out_dir / f"{prop.lower()}_{depth.replace('-', '_')}.tif"
            paths[(prop, depth)] = out_path
            if out_path.exists() and out_path.stat().st_size > 0 and not force:
                done += 1
                continue
            au.clip_field(bbox, out_path, prop=prop, depth=depth, source="dap")
            done += 1
        print(f"  {prop:20s} {done}/{len(DEPTHS)} depths ready", flush=True)
    return paths


def _cell_area_m2(transform) -> float:
    """True ground area of one raster cell, from the WGS84 ellipsoid."""
    lon, lat = transform * (0.5, 0.5)
    dx, dy = transform.a, transform.e
    lons = [lon, lon + dx, lon + dx, lon]
    lats = [lat, lat + dy, lat + dy, lat]
    area, _ = GEOD.polygon_area_perimeter(lons, lats)
    return abs(area)


def zonal_stats(raster_path: Path, geom, area_m2: float) -> dict:
    """Centre-in-polygon statistics for one paddock on one grid."""
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
        cell_m2 = _cell_area_m2(ds.transform)
        res_deg = abs(ds.transform.a)
    if values.size == 0:
        # Paddock smaller than a cell: fall back to the centre cell and say so.
        with rasterio.open(raster_path) as ds:
            row, col = ds.index(*geom.centroid.coords[0][:2])
            value = float(ds.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
            cell_m2 = _cell_area_m2(ds.transform)
        if not np.isfinite(value):
            return {
                "mean": np.nan,
                "min": np.nan,
                "max": np.nan,
                "std": np.nan,
                "n_pixels": 0,
                "cell_frac": 0.0,
                "cell_deg": res_deg,
                "sampling": "none",
            }
        return {
            "mean": value,
            "min": value,
            "max": value,
            "std": 0.0,
            "n_pixels": 1,
            "cell_frac": round(min(1.0, cell_m2 / area_m2), 3),
            "cell_deg": res_deg,
            "sampling": "centroid_cell",
        }
    return {
        "mean": float(values.mean()),
        "min": float(values.min()),
        "max": float(values.max()),
        "std": float(values.std()),
        "n_pixels": int(values.size),
        "cell_frac": round(min(1.0, values.size * cell_m2 / area_m2), 3),
        "cell_deg": res_deg,
        "sampling": "centre_in_polygon",
    }


def usda_texture(sand: float, clay: float) -> str:
    """USDA texture class from sand and clay percentages (silt = remainder)."""
    if not np.isfinite(sand) or not np.isfinite(clay):
        return ""
    s, c = float(sand), float(clay)
    si = 100.0 - s - c
    if si < 0:
        return ""

    # Coarse sands first: these win over the clay branches at low clay.
    if si + 1.5 * c < 15:
        return "sand"
    if si + 2.0 * c < 30:
        return "loamy sand"

    if c >= 40:
        if si >= 40:
            return "silty clay"
        if s > 45:
            return "sandy clay"
        return "clay"
    if c >= 35:
        if s >= 45:
            return "sandy clay"
        if si >= 40:
            return "silty clay"
        return "clay loam"
    if c >= 27:
        if s >= 45:
            return "sandy clay loam"
        if si >= 40:
            return "silty clay loam"
        return "clay loam"
    if c >= 20:
        if s >= 45:
            return "sandy clay loam"
        if si >= 50:
            return "silt loam"
        return "loam"

    # clay < 20
    if c < 7:
        if si >= 80:
            return "silt"
        if si >= 50:
            return "silt loam"
        return "sandy loam"
    if si >= 80:
        return "silt"
    if si >= 50:
        return "silt loam"
    if s >= 52:
        return "sandy loam"
    return "loam"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-clip cached grids")
    args = parser.parse_args()

    fields = load_canonical()
    print(f"fields: {len(fields)} | canonical reference: {CANONICAL_DIR}")

    rows: list[dict] = []
    for farm_slug, group in fields.groupby("farm_slug", sort=True):
        minx, miny, maxx, maxy = group.total_bounds
        bbox = (minx - PAD_DEG, miny - PAD_DEG, maxx + PAD_DEG, maxy + PAD_DEG)
        print(f"{farm_slug:10s} {len(group):2d} fields | bbox {bbox}")
        grids = clip_farm(farm_slug, bbox, args.force)
        for prop in PROPERTIES:
            for depth in DEPTHS:
                path = grids[(prop, depth)]
                entry = au.resolve_entry(prop, depth)
                meta = au.PROPERTY_URLS[prop]
                for row in group.itertuples(index=False):
                    stat = zonal_stats(path, row.geometry, row.area_m2)
                    rows.append(
                        {
                            "field_id": row.field_id,
                            "paddock_name": row.paddock_name,
                            "farm": row.farm,
                            "region": row.region,
                            "property": prop,
                            "attribute": meta["attribute"],
                            "units": meta["units"],
                            "depth": depth,
                            "depth_top_m": au.DEPTH_LABELS[depth][1],
                            "depth_bottom_m": au.DEPTH_LABELS[depth][2],
                            "depth_thickness_m": au.DEPTH_LABELS[depth][2]
                            - au.DEPTH_LABELS[depth][1],
                            "mean": stat["mean"],
                            "min": stat["min"],
                            "max": stat["max"],
                            "std": stat["std"],
                            "n_pixels": stat["n_pixels"],
                            "cell_frac": stat["cell_frac"],
                            "cell_deg": stat["cell_deg"],
                            "sampling": stat["sampling"],
                            "source": au.SOURCE_NAME,
                            "doi": meta["doi"],
                            "url": entry["dap"],
                            "canonical_url": entry["cog"],
                            "accessed_at": au._utcnow(),
                        }
                    )

    long = pd.DataFrame(rows).sort_values(
        ["farm", "paddock_name", "property", "depth_top_m"]
    )
    long_path = EXAMPLES / "soil_field_properties.csv"
    long.to_csv(long_path, index=False)
    print(f"wrote {long_path} ({len(long)} rows)")

    wide = (
        long.pivot_table(
            index=["field_id", "paddock_name", "farm", "region"],
            columns=["property", "depth"],
            values="mean",
        )
        .sort_index(axis=1)
    )
    wide.columns = [f"{p}_{d}" for p, d in wide.columns]
    wide = wide.reset_index()

    thickness = {d: au.DEPTH_LABELS[d][2] - au.DEPTH_LABELS[d][1] for d in DEPTHS}
    for prop in PROPERTIES:
        cols = [f"{prop}_{d}" for d in DEPTHS]
        w = np.array([thickness[d] for d in DEPTHS])
        wide[f"{prop}_0_100"] = wide[cols].mul(w, axis=1).sum(axis=1) / w.sum()
        cols30 = [f"{prop}_{d}" for d in ("0-5cm", "5-15cm", "15-30cm")]
        w30 = np.array([thickness[d] for d in ("0-5cm", "5-15cm", "15-30cm")])
        wide[f"{prop}_0_30"] = wide[cols30].mul(w30, axis=1).sum(axis=1) / w30.sum()

    wide["texture_usda_0_30"] = [
        usda_texture(s, c)
        for s, c in zip(wide["SAND_0_30"], wide["CLAY_0_30"], strict=True)
    ]
    # Carbon stock is a slab sum, not a product of depth-weighted means:
    # bulk density rises with depth while SOC falls, so averaging first
    # overstates stock by ~2%.
    for label, slabs in (
        ("0_30", ("0-5cm", "5-15cm", "15-30cm")),
        ("0_100", DEPTHS),
    ):
        stock = sum(
            wide[f"CARBON_{d}"] / 100.0
            * wide[f"BULKDENSITY_{d}"]
            * (thickness[d] * 100.0)
            * 100.0
            for d in slabs
        )
        wide[f"carbon_stock_t_ha_{label}"] = stock

    wide["area_ha_geom"] = wide["field_id"].map(
        lambda fid: round(
            fields.loc[fields["field_id"] == fid, "area_m2"].iloc[0] / 10_000.0, 2
        )
    )
    wide = wide.sort_values(["farm", "paddock_name"])
    wide_path = EXAMPLES / "soil_field_profile.csv"
    wide.to_csv(wide_path, index=False)
    print(f"wrote {wide_path} ({len(wide)} fields)")

    pixels = long.groupby("field_id")["n_pixels"].max()
    print(
        f"texture spread : {wide['texture_usda_0_30'].value_counts().to_dict()}\n"
        f"cells per field : median {pixels.median():.0f}, "
        f"min {pixels.min()}, max {pixels.max()}\n"
        f"fields <5 cells : {(pixels < 5).sum()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
