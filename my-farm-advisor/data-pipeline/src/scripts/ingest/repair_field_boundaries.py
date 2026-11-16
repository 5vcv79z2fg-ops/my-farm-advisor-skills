#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Audit and repair farm field boundaries with CDL evidence.

Subcommands:

* ``qc``      report per-field CDL crop composition and flag suspect fields.
* ``split``   split a merged field along the CDL corn/soy interface.
* ``replace`` replace a field with a different OSM farmland way.

The utility never changes data unless ``--apply`` is passed. When applying, it
backs up the farm boundary and inventory, retires replaced field directories
under ``derived/retired_fields/``, and rewrites per-field boundary copies.

CDL class codes: 1 corn, 5 soybeans, 176 grass/pasture, 121-124 developed,
131 barren, 141-143 forest.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import linemerge, split, transform as shapely_transform, unary_union

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "lib"))

from naming import field_slug_from_id  # noqa: E402
from paths import (  # noqa: E402
    farm_boundary_path,
    farm_dir,
    farm_manifest_dir,
    field_boundary_path,
    field_dir,
    shared_cdl_state_raster_path,
)

OVERPASS_URLS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

CORN = 1
SOY = 5
PASTURE = 176
DEVELOPED = (121, 122, 123, 124)
BARREN = 131

_ACRES_PER_M2 = 1 / 4046.8564224
_WGS84 = "EPSG:4326"
_ALBERS = "EPSG:5070"

_US_STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP",
    "72": "PR", "78": "VI",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"expected a JSON object in {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_boundary_geojson(fields: gpd.GeoDataFrame, path: Path) -> None:
    """Write GeoJSON and keep optional properties out of features that lack them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields.to_file(path, driver="GeoJSON")
    payload = _read_json(path)
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        cleaned = {key: value for key, value in properties.items() if value is not None}
        split_year = cleaned.get("split_year")
        if isinstance(split_year, float) and split_year.is_integer():
            cleaned["split_year"] = int(split_year)
        feature["properties"] = cleaned
    _write_json(path, payload)


def _load_fields(grower_slug: str, farm_slug: str) -> gpd.GeoDataFrame:
    path = farm_boundary_path(grower_slug, farm_slug)
    if not path.is_file():
        raise SystemExit(f"farm boundary not found: {path}")
    fields = gpd.read_file(path)
    if fields.empty:
        raise SystemExit(f"farm boundary has no features: {path}")
    if fields.crs is None:
        fields = fields.set_crs(_WGS84)
    return fields


def _state_fips(fields: gpd.GeoDataFrame) -> str:
    if "state_fips" not in fields.columns:
        raise SystemExit("field boundaries have no state_fips column; CDL QC is US-only")
    values = fields["state_fips"].dropna().astype(str).str.zfill(2)
    if values.empty:
        raise SystemExit("field boundaries have no usable state_fips values")
    return str(values.mode().iloc[0])


def _window_for(geom, src):
    minx, miny, maxx, maxy = geom.bounds
    pad = 60.0
    window = from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad, src.transform)
    return window.round_offsets().round_lengths()


def _masked_window(geom, src, *, all_touched: bool = False):
    window = _window_for(geom, src)
    data = src.read(1, window=window)
    transform = src.window_transform(window)
    mask = rasterize(
        [(geom, 1)],
        out_shape=data.shape,
        transform=transform,
        fill=0,
        all_touched=all_touched,
        dtype="uint8",
    ).astype(bool)
    return data, transform, mask


def _largest_component_fraction(array: np.ndarray, total: int) -> float:
    if total <= 0:
        return 0.0
    labels, count = ndimage.label(array.astype("uint8"))
    if count == 0:
        return 0.0
    sizes = ndimage.sum(array, labels, range(1, count + 1))
    return float(np.max(sizes)) / total


def cdl_stats(geom, state_fips: str, year: int) -> dict[str, Any] | None:
    raster_path = shared_cdl_state_raster_path(year, state_fips)
    if not raster_path.is_file():
        return None
    with rasterio.open(raster_path) as src:
        to_raster = Transformer.from_crs(_WGS84, src.crs, always_xy=True)
        geom_r = shapely_transform(to_raster.transform, geom)
        data, _transform, mask = _masked_window(geom_r, src)
        total = int(mask.sum())
        if total == 0:
            return None
        fractions: dict[str, float] = {}
        largest: dict[str, float] = {}
        named = {
            "corn": (CORN,),
            "soy": (SOY,),
            "pasture": (PASTURE,),
            "developed": DEVELOPED,
            "barren": (BARREN,),
        }
        for name, codes in named.items():
            layer = np.isin(data, codes) & mask
            fractions[name] = float(layer.sum()) / total
            largest[name] = _largest_component_fraction(layer, total)
        fractions["crop"] = fractions["corn"] + fractions["soy"]
        fractions["noncrop"] = (
            fractions["pasture"]
            + fractions["developed"]
            + fractions["barren"]
        )
    return {
        "year": year,
        "pixels": total,
        "fractions": {key: round(value, 4) for key, value in fractions.items()},
        "largest_component": {key: round(value, 4) for key, value in largest.items()},
    }


def _component_polygon(data, mask, transform, code: int) -> Polygon | None:
    layer = ((data == code) & mask).astype("uint8")
    labels, count = ndimage.label(layer)
    if count == 0:
        return None
    sizes = ndimage.sum(layer, labels, range(1, count + 1))
    best = int(np.argmax(sizes)) + 1
    component = (labels == best).astype("uint8")
    polygons = [
        shape(geom)
        for geom, value in shapes(component, mask=component, transform=transform)
        if value == 1
    ]
    if not polygons:
        return None
    return max(polygons, key=lambda poly: poly.area)


def _flags(stats: dict[int, dict[str, Any]] | None, area_acres: float) -> list[str]:
    if not stats:
        return ["no_cdl"]
    latest = stats[max(stats)]
    fractions = latest["fractions"]
    largest = latest["largest_component"]
    flags: list[str] = []
    if area_acres < 5:
        flags.append("tiny_field")
    if fractions["pasture"] >= 0.5:
        flags.append("pasture_dominated")
    if fractions["developed"] >= 0.3:
        flags.append("developed_dominated")
    if fractions["crop"] < 0.8:
        flags.append("low_crop")
    if largest["corn"] >= 0.2 and largest["soy"] >= 0.2:
        flags.append("merged_field")
    return flags


def command_qc(args) -> int:
    fields = _load_fields(args.grower_slug, args.farm_slug)
    state_fips = _state_fips(fields)
    years = list(range(args.year - 4, args.year + 1))
    records = []
    suspicious = 0
    print(f"CDL QC: {args.grower_slug}/{args.farm_slug} ({state_fips}), {years[0]}-{years[-1]}")
    print(f"{'field_id':24s} {'acres':>7s}  2025 composition                         flags")
    for _, row in fields.iterrows():
        field_id = str(row["field_id"])
        area_acres = float(row.get("area_acres") or 0.0)
        stats = {
            year: result
            for year in years
            if (result := cdl_stats(row.geometry, state_fips, year)) is not None
        }
        flags = _flags(stats, area_acres)
        if flags:
            suspicious += 1
        latest = stats.get(args.year)
        composition = "no CDL"
        if latest:
            parts = [
                f"{name} {latest['fractions'][name]:.2f}"
                for name in ("corn", "soy", "pasture", "developed")
                if latest["fractions"][name] >= 0.02
            ]
            composition = ", ".join(parts) or "other"
        print(f"{field_id:24s} {area_acres:7.2f}  {composition:40s} {','.join(flags) or 'ok'}")
        records.append(
            {
                "field_id": field_id,
                "area_acres": round(area_acres, 2),
                "years": stats,
                "flags": flags,
            }
        )
    payload = {
        "grower_slug": args.grower_slug,
        "farm_slug": args.farm_slug,
        "state_fips": state_fips,
        "years": years,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "field_count": len(records),
        "suspicious_count": suspicious,
        "fields": records,
    }
    report_path = farm_manifest_dir(args.grower_slug, args.farm_slug) / "boundary_qc.json"
    _write_json(report_path, payload)
    print(f"\n{suspicious}/{len(records)} field(s) flagged. Report: {report_path}")
    return 0


def _split_geometry(
    geom,
    state_fips: str,
    year: int,
    *,
    simplify_m: float,
    extend_m: float,
) -> list[tuple[Polygon, str]]:
    raster_path = shared_cdl_state_raster_path(year, state_fips)
    if not raster_path.is_file():
        raise SystemExit(f"CDL raster not found: {raster_path}")
    to_albers = Transformer.from_crs(_WGS84, _ALBERS, always_xy=True)
    to_wgs84 = Transformer.from_crs(_ALBERS, _WGS84, always_xy=True)
    geom_alb = shapely_transform(to_albers.transform, geom)
    with rasterio.open(raster_path) as src:
        to_raster = Transformer.from_crs(_WGS84, src.crs, always_xy=True)
        geom_r = shapely_transform(to_raster.transform, geom)
        data, transform, mask = _masked_window(geom_r, src)
        raster_crs = src.crs
    corn = _component_polygon(data, mask, transform, CORN)
    soy = _component_polygon(data, mask, transform, SOY)
    if corn is None or soy is None:
        raise SystemExit("could not find both a corn and a soy component in the field")
    # CDL rasters are normally EPSG:5070 already; reproject from the raster CRS
    # only when the source grid uses a different projected CRS.
    if raster_crs is not None and raster_crs.to_string() != _ALBERS:
        raster_to_albers = Transformer.from_crs(raster_crs, _ALBERS, always_xy=True)
        corn = shapely_transform(raster_to_albers.transform, corn)
        soy = shapely_transform(raster_to_albers.transform, soy)

    shared = corn.boundary.intersection(soy.boundary)
    if shared.is_empty:
        raise SystemExit("corn and soy components do not share a boundary; cannot derive a divider")
    pieces = (
        list(shared.geoms) if shared.geom_type == "MultiLineString" else [shared]
    )
    pieces = [piece for piece in pieces if piece.length > 10]
    if not pieces:
        raise SystemExit("no usable shared boundary between corn and soy components")
    core = unary_union(pieces).simplify(simplify_m)
    line = linemerge(core) if core.geom_type == "MultiLineString" else core
    if line.geom_type == "MultiLineString":
        line = max(line.geoms, key=lambda item: item.length)
    if not isinstance(line, LineString):
        raise SystemExit(f"could not derive a single divider line (got {line.geom_type})")

    coords = list(line.coords)
    e0 = _extend(coords[1], coords[0], extend_m)
    e1 = _extend(coords[-2], coords[-1], extend_m)
    divider = LineString([e0, *coords, e1])
    parts = [
        part
        for part in split(geom_alb, divider).geoms
        if part.area * _ACRES_PER_M2 > 1.0
    ]
    if len(parts) != 2:
        raise SystemExit(
            f"split produced {len(parts)} parts; pass a manual --split-line or adjust simplify/extend"
        )
    labelled = []
    for part in parts:
        point = part.representative_point()
        if corn.contains(point):
            crop = "corn"
        elif soy.contains(point):
            crop = "soy"
        else:
            crop = "unknown"
        labelled.append((shapely_transform(to_wgs84.transform, part), crop))
    return labelled


def _extend(p0, p1, distance: float):
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return p1
    return (p1[0] + dx / length * distance, p1[1] + dy / length * distance)


def command_split(args) -> int:
    fields = _load_fields(args.grower_slug, args.farm_slug)
    state_fips = _state_fips(fields)
    matches = fields.index[fields["field_id"].astype(str) == args.field_id].tolist()
    if not matches:
        raise SystemExit(f"field not found: {args.field_id}")
    parent = fields.loc[matches[0]]
    parts = _split_geometry(
        parent.geometry,
        state_fips,
        args.year,
        simplify_m=args.simplify_m,
        extend_m=args.extend_m,
    )
    to_albers = Transformer.from_crs(_WGS84, _ALBERS, always_xy=True)
    new_rows = []
    for index, (geom, crop) in enumerate(parts, start=1):
        suffix = "ab"[index - 1]
        new_id = f"{args.field_id}-{suffix}"
        properties = parent.drop(labels=["geometry"]).to_dict()
        properties.update(
            {
                "field_id": new_id,
                "source": f"{parent.get('source', 'OpenStreetMap/Overpass')} (split from {args.field_id})",
                "parent_field_id": args.field_id,
                "split_method": "cdl-corn-soy-interface",
                "split_year": args.year,
                "area_acres": round(
                    shapely_transform(to_albers.transform, geom).area * _ACRES_PER_M2, 2
                ),
            }
        )
        properties["split_crop_hint"] = crop
        new_rows.append({"properties": properties, "geometry": geom})
        print(f"  {new_id}: {properties['area_acres']:.2f} ac, {crop} side")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write the split.")
        return 0

    rest = fields.drop(index=matches[0])
    new_gdf = gpd.GeoDataFrame(
        [*rest.drop(columns=["geometry"]).to_dict("records"), *[row["properties"] for row in new_rows]],
        geometry=[*rest.geometry.tolist(), *[row["geometry"] for row in new_rows]],
        crs=_WGS84,
    )
    _apply_changes(
        args,
        new_gdf,
        retired_ids=[args.field_id],
        reason=f"split {args.field_id}",
    )
    return 0


def _fetch_osm_ways(way_ids: list[int]) -> dict[int, tuple[Polygon, dict[str, Any]]]:
    query = (
        "[out:json][timeout:120];("
        + "".join(f"way({way_id});" for way_id in way_ids)
        + ");out geom tags;"
    )
    last_error: Exception | None = None
    for endpoint in OVERPASS_URLS:
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    endpoint,
                    data={"data": query},
                    timeout=180,
                    headers={"User-Agent": "my-farm-advisor/1.0", "Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                result: dict[int, tuple[Polygon, dict[str, Any]]] = {}
                for element in payload.get("elements", []):
                    if element.get("type") != "way":
                        continue
                    points = [(point["lon"], point["lat"]) for point in element.get("geometry", [])]
                    if len(points) < 4:
                        continue
                    if points[0] != points[-1]:
                        points.append(points[0])
                    polygon = Polygon(points)
                    if not polygon.is_valid:
                        polygon = polygon.buffer(0)
                    if polygon.is_empty:
                        continue
                    result[int(element["id"])] = (polygon, element.get("tags", {}))
                missing = sorted(set(way_ids) - set(result))
                if missing:
                    raise ValueError(f"OSM response missing ways: {missing}")
                return result
            except Exception as exc:  # noqa: BLE001 - retries across public endpoints
                last_error = exc
                time.sleep(2.0 * attempt)
    raise SystemExit(f"could not fetch OSM ways {way_ids}: {last_error}")


def command_replace(args) -> int:
    fields = _load_fields(args.grower_slug, args.farm_slug)
    mappings: list[tuple[str, int]] = []
    for raw in args.map:
        if "=" not in raw:
            raise SystemExit(f"--map must be OLD_ID=NEW_WAY_ID, got {raw!r}")
        old_id, new_way = raw.split("=", 1)
        try:
            mappings.append((old_id.strip(), int(new_way)))
        except ValueError as exc:
            raise SystemExit(f"invalid OSM way id in --map {raw!r}") from exc

    existing_ids = set(fields["field_id"].astype(str))
    for old_id, _ in mappings:
        if old_id not in existing_ids:
            raise SystemExit(f"field not found: {old_id}")
    new_way_ids = [way_id for _, way_id in mappings]
    if len(set(new_way_ids)) != len(new_way_ids):
        raise SystemExit("duplicate replacement OSM way ids")
    fetched = _fetch_osm_ways(new_way_ids)

    to_albers = Transformer.from_crs(_WGS84, _ALBERS, always_xy=True)
    rows = fields.to_dict("records")
    new_rows = []
    retired: list[str] = []
    for old_id, way_id in mappings:
        index = next(i for i, row in enumerate(rows) if str(row["field_id"]) == old_id)
        parent = rows[index]
        polygon, tags = fetched[way_id]
        properties = dict(parent)
        properties.update(
            {
                "field_id": f"osm-{way_id}",
                "source": "OpenStreetMap/Overpass",
                "crop_name": str(tags.get("crop") or tags.get("landuse") or "farmland"),
                "replaces": old_id,
                "area_acres": round(
                    shapely_transform(to_albers.transform, polygon).area * _ACRES_PER_M2, 2
                ),
            }
        )
        properties.pop("geometry", None)
        new_rows.append({"properties": properties, "geometry": polygon})
        retired.append(old_id)
        print(
            f"  osm-{way_id} replaces {old_id}: {properties['area_acres']:.2f} ac "
            f"({properties['crop_name']})"
        )

    if not args.apply:
        print("\nDry run. Re-run with --apply to write the replacements.")
        return 0

    remaining = [row for row in rows if str(row["field_id"]) not in set(retired)]
    new_gdf = gpd.GeoDataFrame(
        [*remaining, *[row["properties"] for row in new_rows]],
        geometry=[*[row["geometry"] for row in remaining], *[row["geometry"] for row in new_rows]],
        crs=_WGS84,
    )
    _apply_changes(
        args,
        new_gdf,
        retired_ids=retired,
        reason="replace " + ", ".join(retired),
    )
    return 0


def _apply_changes(
    args,
    fields: gpd.GeoDataFrame,
    *,
    retired_ids: list[str],
    reason: str,
) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root = farm_dir(args.grower_slug, args.farm_slug) / "derived" / "boundary_backups" / stamp
    backup_root.mkdir(parents=True, exist_ok=True)
    boundary_path = farm_boundary_path(args.grower_slug, args.farm_slug)
    inventory_path = farm_manifest_dir(args.grower_slug, args.farm_slug) / "field-inventory.csv"
    if boundary_path.is_file():
        shutil.copy2(boundary_path, backup_root / boundary_path.name)
    if inventory_path.is_file():
        shutil.copy2(inventory_path, backup_root / inventory_path.name)

    fields = fields.sort_values("field_id").reset_index(drop=True)
    _write_boundary_geojson(fields, boundary_path)

    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with inventory_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field_id", "field_slug"])
        for field_id in fields["field_id"].astype(str):
            writer.writerow([field_id, field_slug_from_id(field_id)])

    for _, row in fields.iterrows():
        field_id = str(row["field_id"])
        target = field_boundary_path(args.grower_slug, args.farm_slug, field_id)
        _write_boundary_geojson(
            gpd.GeoDataFrame([row], geometry="geometry", crs=fields.crs), target
        )

    archive_root = (
        farm_dir(args.grower_slug, args.farm_slug)
        / "derived"
        / "retired_fields"
        / stamp
    )
    for field_id in retired_ids:
        source = field_dir(args.grower_slug, args.farm_slug, field_id)
        if source.is_dir():
            archive_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(archive_root / field_id))
            print(f"  archived {source} -> {archive_root / field_id}")

    _sync_farm_state(args.grower_slug, args.farm_slug, fields)

    record = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "grower_slug": args.grower_slug,
        "farm_slug": args.farm_slug,
        "operation": reason,
        "retired_field_ids": retired_ids,
        "field_count": len(fields),
        "backup_dir": str(backup_root),
        "archive_dir": str(archive_root),
    }
    _write_json(
        farm_manifest_dir(args.grower_slug, args.farm_slug) / "boundary_repair.json",
        record,
    )
    print(f"\nApplied: {reason}")
    print(f"  fields: {len(fields)}")
    print(f"  backup: {backup_root}")
    print(f"  archive: {archive_root}")


def _sync_farm_state(
    grower_slug: str,
    farm_slug: str,
    fields: gpd.GeoDataFrame,
) -> None:
    farm_json = farm_dir(grower_slug, farm_slug) / "farm.json"
    if not farm_json.is_file() or "state_fips" not in fields.columns:
        return
    values = fields["state_fips"].dropna().astype(str).str.zfill(2)
    if values.empty:
        return
    state = _US_STATE_FIPS.get(str(values.mode().iloc[0]))
    if not state:
        return
    payload = _read_json(farm_json)
    payload["state"] = state
    payload["country"] = payload.get("country", "US")
    _write_json(farm_json, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", required=True)
    parser.add_argument("--farm-slug", required=True)
    parser.add_argument("--year", type=int, default=2025, help="CDL year for QC and split interface")
    parser.add_argument("--apply", action="store_true", help="Write changes; default is dry-run")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("qc", help="Report CDL composition and suspect fields")

    split_parser = subparsers.add_parser("split", help="Split a merged field along the CDL interface")
    split_parser.add_argument("--field-id", required=True)
    split_parser.add_argument("--simplify-m", type=float, default=8.0)
    split_parser.add_argument("--extend-m", type=float, default=120.0)

    replace_parser = subparsers.add_parser("replace", help="Replace a field with an OSM farmland way")
    replace_parser.add_argument(
        "--map",
        action="append",
        required=True,
        metavar="OLD_ID=NEW_WAY_ID",
        help="Repeat for multiple replacements",
    )

    args = parser.parse_args(argv)
    if args.command == "qc":
        return command_qc(args)
    if args.command == "split":
        return command_split(args)
    if args.command == "replace":
        return command_replace(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
