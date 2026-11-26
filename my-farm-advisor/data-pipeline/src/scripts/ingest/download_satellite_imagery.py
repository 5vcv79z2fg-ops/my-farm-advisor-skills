#!/usr/bin/env python3
"""Download clipped raw satellite TIFFs and NDVI rasters for each field."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import pandas as pd

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from lib.paths import (  # noqa: E402
    DATA_ROOT,
    farm_boundary_path,
    farm_tables_dir,
    field_boundary_path,
    field_satellite_dir,
)
from lib.satellite_imagery import (  # noqa: E402
    LANDSAT_COLLECTION,
    SENTINEL_COLLECTION,
    clip_asset_to_field,
    compute_landsat_ndvi,
    compute_sentinel_ndvi,
    feature_cloud_cover,
    feature_datetime,
    landsat_asset_keys,
    SatelliteSearchError,
    search_features,
    sentinel_asset_keys,
    sign_planetary_computer_href,
)
from reporting_bootstrap import ensure_skill_path  # noqa: E402

_REPO = DATA_ROOT
_DEFAULT_GROWER = os.environ.get("AG_GROWER_SLUG", "default-grower")
_DEFAULT_FARM = os.environ.get("AG_FARM_SLUG", "default-farm")
_DEFAULT_FARM_NAME = os.environ.get("AG_FARM_NAME", "Default Farm")
_DEFAULT_INVENTORY = _REPO / "growers" / _DEFAULT_GROWER / "farms" / _DEFAULT_FARM / "manifests" / "field-inventory.csv"
_FIELD_INVENTORY = Path(os.environ.get("AG_INVENTORY_CSV", str(_DEFAULT_INVENTORY)))

ensure_skill_path("farm-intelligence-reporting")

from pipeline import FieldReportingConfig  # noqa: E402  # pyright: ignore[reportMissingImports]

_SEASON_START_MD = os.environ.get("AG_SEASON_START_MD", "03-01").strip() or "03-01"
_SEASON_END_MD = os.environ.get("AG_SEASON_END_MD", "11-30").strip() or "11-30"


def _season_months() -> tuple[int, ...]:
    """Candidate months inside the requested season window.

    Defaults keep the US behaviour (Mar-Nov). The AU branch narrows the end to
    Nov 15 on the non-Kanoona farms so no scene is selected after the season
    the reports actually describe.
    """
    start = int(_SEASON_START_MD.split("-")[0])
    end = int(_SEASON_END_MD.split("-")[0])
    return tuple(range(start, end + 1))


_GROWING_SEASON_MONTHS = _season_months()


def _season_range(year: int) -> str:
    return f"{year}-{_SEASON_START_MD}T00:00:00Z/{year}-{_SEASON_END_MD}T23:59:59Z"


def _sensors_requested() -> tuple[str, ...]:
    """Sensor families to fetch. US default is both; AU sets ``sentinel`` alone.

    Sentinel-2 stays the single source for the AU branch: its 10 m bands carry
    the field detail, and mixing Landsat 30 m into the same monthly composite
    would silently change the pixel support between months.
    """
    raw = os.environ.get("AG_SENSORS", "sentinel,landsat")
    wanted = tuple(token.strip().lower() for token in raw.split(",") if token.strip())
    unknown = sorted(set(wanted) - {"sentinel", "landsat"})
    if unknown:
        raise SystemExit(f"AG_SENSORS has unknown entries {unknown}; use sentinel/landsat")
    return wanted


def _imagery_years(default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.environ.get("AG_IMAGERY_YEARS", "").strip()
    if not raw:
        return default
    years = tuple(sorted({int(token) for token in raw.split(",") if token.strip()}))
    return years or default


def _field_slug_lookup(inventory_path: Path = _FIELD_INVENTORY) -> dict[str, str]:
    if not inventory_path.exists():
        return {}
    inventory = pd.read_csv(inventory_path)
    if not {"field_id", "field_slug"}.issubset(inventory.columns):
        return {}
    return {
        str(row["field_id"]): str(row["field_slug"])
        for _, row in inventory[["field_id", "field_slug"]].dropna().iterrows()
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _scene_dir(sensor_root: Path, scene_date: datetime, sensor: str) -> Path:
    return (
        sensor_root / f"{scene_date.year}" / f"{sensor}_{scene_date.strftime('%Y%m%d')}"
    )


def _scene_paths(
    sensor_root: Path, scene_date: datetime, sensor: str
) -> dict[str, Path]:
    scene_dir = _scene_dir(sensor_root, scene_date, sensor)
    stamp = scene_date.strftime("%Y%m%d")
    if sensor == "sentinel":
        return {
            "scene_dir": scene_dir,
            "red": scene_dir / f"sentinel_{stamp}_red.tif",
            "nir": scene_dir / f"sentinel_{stamp}_nir.tif",
            "scl": scene_dir / f"sentinel_{stamp}_scl.tif",
            "ndvi": scene_dir / f"sentinel_{stamp}_ndvi.tif",
        }
    return {
        "scene_dir": scene_dir,
        "red": scene_dir / f"landsat_{stamp}_red.tif",
        "nir": scene_dir / f"landsat_{stamp}_nir.tif",
        "qa": scene_dir / f"landsat_{stamp}_qa_pixel.tif",
        "ndvi": scene_dir / f"landsat_{stamp}_ndvi.tif",
    }


def _ensure_field_boundary(
    field_row: pd.Series, grower_slug: str, farm_slug: str, field_slug: str
) -> Path:
    output_path = field_boundary_path(grower_slug, farm_slug, field_slug)
    field_gdf = gpd.GeoDataFrame([field_row], geometry="geometry", crs="EPSG:4326")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    field_gdf.to_file(output_path, driver="GeoJSON")
    return output_path


def _relative_to_repo(path: Path) -> str:
    return str(path.relative_to(_REPO))


def _existing_entry_is_reusable(
    entry: dict[str, Any] | None, expected_id: str, paths: dict[str, Path]
) -> bool:
    if (
        not entry
        or entry.get("scene_id") != expected_id
        or entry.get("status") != "complete"
    ):
        return False
    required = [path for key, path in paths.items() if key != "scene_dir"]
    return all(path.exists() for path in required)


def _normalize_year_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    scenes = normalized.get("scenes")
    if isinstance(scenes, list):
        normalized["scenes"] = scenes
    elif normalized.get("scene_id"):
        normalized["scenes"] = [
            {
                "scene_id": normalized.get("scene_id"),
                "scene_date": normalized.get("scene_date"),
                "cloud_cover": normalized.get("cloud_cover"),
                "status": normalized.get("status", "complete"),
                "raw_tiffs": normalized.get("raw_tiffs", {}),
                "ndvi_tif": normalized.get("ndvi_tif"),
            }
        ]
    else:
        normalized["scenes"] = []
    normalized["scene_count"] = int(
        normalized.get("scene_count", len(normalized["scenes"]))
    )
    normalized.setdefault("coverage_months", [])
    normalized.setdefault("missing_months", [])
    return normalized


def _year_entry_fully_reusable(
    previous_entry: dict[str, Any] | None, *, sensor_root: Path, sensor: str
) -> tuple[bool, list[dict[str, Any]]]:
    if previous_entry is None:
        return False, []
    normalized = _normalize_year_entry(previous_entry)
    scenes = cast(list[dict[str, Any]], normalized.get("scenes", []))
    if normalized.get("status") != "complete" or not scenes:
        return False, []

    reusable: list[dict[str, Any]] = []
    for scene in scenes:
        scene_date_raw = scene.get("scene_date")
        scene_id = str(scene.get("scene_id", "")).strip()
        if not scene_date_raw or not scene_id:
            return False, []
        scene_date = datetime.fromisoformat(str(scene_date_raw))
        scene_paths = _scene_paths(sensor_root, scene_date, sensor)
        if not _existing_entry_is_reusable(scene, scene_id, scene_paths):
            return False, []
        reusable.append(scene)
    return True, reusable


def _scenes_per_month() -> int:
    """Candidate scenes kept per month.

    The STAC ``eo:cloud_cover`` is a 110 km tile statistic, so the tile's
    clearest scene is often the most clouded one over a single paddock. Keeping
    more than one candidate lets the NDVI stage pick by *measured* visible
    fraction inside the paddock; the output still uses one scene per month.
    """
    try:
        value = int(os.environ.get("AG_SCENES_PER_MONTH", "1"))
    except ValueError:
        value = 1
    return max(1, min(value, 4))


def _select_scene_inventory(
    features: list[dict[str, Any]], *, max_scenes_per_year: int,
    per_month: int = 1,
    only_months: set[int] | None = None,
) -> list[dict[str, Any]]:
    sorted_features = sorted(
        features,
        key=lambda feature: (
            feature_cloud_cover(feature),
            -feature_datetime(feature).timestamp(),
        ),
    )
    by_month: dict[int, list[dict[str, Any]]] = {}
    for feature in sorted_features:
        month = feature_datetime(feature).month
        if month not in _GROWING_SEASON_MONTHS:
            continue
        by_month.setdefault(month, []).append(feature)

    selected: list[dict[str, Any]] = []
    for month in _GROWING_SEASON_MONTHS:
        # gap-fill mode targets only the months that have no usable scene; every
        # other month keeps the scene it already has and is not downloaded again
        if only_months is not None and month not in only_months:
            continue
        monthly = by_month.get(month, [])
        # Two items of the same acquisition (different processing baselines)
        # give no second chance at seeing the paddock, so a candidate slot only
        # counts once per calendar date.
        distinct: dict[str, dict[str, Any]] = {}
        for feature in monthly:  # already cloud-ascending
            distinct.setdefault(
                feature_datetime(feature).date().isoformat(), feature
            )
        selected.extend(list(distinct.values())[:per_month])

    return selected[:max_scenes_per_year]


def _month_availability(features: list[dict[str, Any]]) -> dict[str, int]:
    """Distinct acquisition dates available per season month, under the gate.

    Recorded in the manifest so a later run can tell "this month only ever had
    one usable date" from "this month still has candidates to fetch", without
    searching again.
    """
    dates: dict[int, set[str]] = {}
    for feature in features:
        moment = feature_datetime(feature)
        if moment.month not in _GROWING_SEASON_MONTHS:
            continue
        dates.setdefault(moment.month, set()).add(moment.date().isoformat())
    return {str(month): len(values) for month, values in dates.items()}


def _distinct_dates_by_month(scenes: list[dict[str, Any]]) -> dict[int, int]:
    dates: dict[int, set[str]] = {}
    for scene in scenes:
        date = str(scene.get("scene_date", ""))
        if not date:
            continue
        dates.setdefault(int(date[5:7]), set()).add(date)
    return {month: len(values) for month, values in dates.items()}


def _candidates_are_exhausted(
    entry: dict[str, Any] | None, scenes: list[dict[str, Any]]
) -> bool:
    """True when every month already holds every distinct date that exists.

    The per-month cap is deliberately *not* part of the test. Comparing against
    ``min(per_month, available)`` reported "exhausted" while a third date sat
    unfetched - and raising ``AG_SCENES_PER_MONTH`` is the whole point of a
    top-up, so the comparison has to be against availability itself.
    """
    availability = (entry or {}).get("month_candidate_availability") or {}
    if not availability:
        return False
    have = _distinct_dates_by_month(scenes)
    for month, available in availability.items():
        if have.get(int(month), 0) < int(available):
            return False
    return True


def _sentinel_cloud_max(default: float) -> float:
    """Scene-level cloud ceiling for the Sentinel search.

    US default stays 20 %. The AU branch raises it to 70 %: ``eo:cloud_cover``
    describes a 110 x 110 km tile, and a 30 % cloudy tile is routinely clear
    over a 20 ha paddock. Selection still ranks by cloud, and the per-field
    decision is made later on the measured masked-pixel fraction - so a truly
    overcast pass is dropped on evidence, not on tile statistics.
    """
    raw = os.environ.get("AG_SENTINEL_CLOUD_MAX", "").strip()
    return float(raw) if raw else default


def _declared_cloud_cover(feature: dict[str, Any]) -> float | None:
    """Scene-level cloud cover as declared by the provider, or None.

    Reprocessed Sentinel-2 items in the archive sometimes omit
    ``eo:cloud_cover``. Recording ``None`` keeps that distinction; substituting
    the 100.0 default would read as a hopelessly cloudy scene in every
    downstream table.
    """
    value = feature.get("properties", {}).get("eo:cloud_cover")
    if value is None:
        return None
    return round(float(value), 3)


def _download_sentinel_scene(
    feature: dict[str, Any], field_geom: Any, sensor_root: Path
) -> dict[str, Any]:
    scene_date = feature_datetime(feature)
    paths = _scene_paths(sensor_root, scene_date, "sentinel")
    asset_keys = sentinel_asset_keys(feature)
    assets = feature["assets"]
    red_path = clip_asset_to_field(
        sign_planetary_computer_href(
            SENTINEL_COLLECTION, assets[asset_keys["red"]]["href"]
        ),
        field_geom,
        paths["red"],
    )
    nir_path = clip_asset_to_field(
        sign_planetary_computer_href(
            SENTINEL_COLLECTION, assets[asset_keys["nir"]]["href"]
        ),
        field_geom,
        paths["nir"],
    )
    scl_path = clip_asset_to_field(
        sign_planetary_computer_href(
            SENTINEL_COLLECTION, assets[asset_keys["scl"]]["href"]
        ),
        field_geom,
        paths["scl"],
    )
    ndvi_path = compute_sentinel_ndvi(red_path, nir_path, scl_path, paths["ndvi"])
    return {
        "scene_id": feature["id"],
        "scene_date": scene_date.date().isoformat(),
        "cloud_cover": _declared_cloud_cover(feature),
        "status": "complete",
        "raw_tiffs": {
            "red": _relative_to_repo(red_path),
            "nir": _relative_to_repo(nir_path),
            "scl": _relative_to_repo(scl_path),
        },
        "ndvi_tif": _relative_to_repo(ndvi_path),
        "coverage": _coverage_of(red_path, field_geom),
    }


def _download_landsat_scene(
    feature: dict[str, Any], field_geom: Any, sensor_root: Path
) -> dict[str, Any]:
    scene_date = feature_datetime(feature)
    paths = _scene_paths(sensor_root, scene_date, "landsat")
    asset_keys = landsat_asset_keys(feature)
    assets = feature["assets"]
    red_path = clip_asset_to_field(
        sign_planetary_computer_href(
            LANDSAT_COLLECTION, assets[asset_keys["red"]]["href"]
        ),
        field_geom,
        paths["red"],
    )
    nir_path = clip_asset_to_field(
        sign_planetary_computer_href(
            LANDSAT_COLLECTION, assets[asset_keys["nir"]]["href"]
        ),
        field_geom,
        paths["nir"],
    )
    qa_path = clip_asset_to_field(
        sign_planetary_computer_href(
            LANDSAT_COLLECTION, assets[asset_keys["qa"]]["href"]
        ),
        field_geom,
        paths["qa"],
    )
    ndvi_path = compute_landsat_ndvi(red_path, nir_path, qa_path, paths["ndvi"])
    return {
        "scene_id": feature["id"],
        "scene_date": scene_date.date().isoformat(),
        "cloud_cover": _declared_cloud_cover(feature),
        "status": "complete",
        "raw_tiffs": {
            "red": _relative_to_repo(red_path),
            "nir": _relative_to_repo(nir_path),
            "qa_pixel": _relative_to_repo(qa_path),
        },
        "ndvi_tif": _relative_to_repo(ndvi_path),
        "coverage": _coverage_of(red_path, field_geom),
    }


def _coverage_of(tif_path: Path, field_geom) -> dict | None:
    """raster_rules field/pixel overlap stats journalled in the manifest."""
    try:
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import raster_rules as _rr
        return _rr.coverage(tif_path, field_geom)
    except Exception:
        return None


def _load_sensor_manifest(
    path: Path, dataset_name: str, field_id: str, field_slug: str
) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "dataset_name": dataset_name,
        "field_id": field_id,
        "field_slug": field_slug,
        "years": [],
    }


def _download_sensor_archive(
    *,
    field_id: str,
    field_slug: str,
    field_geom: Any,
    grower_slug: str,
    farm_slug: str,
    years: tuple[int, ...],
    sensor: str,
    collection: str,
    cloud_cover_max: float,
    max_scenes_per_year: int,
    force: bool,
    gap_months: dict[int, set[int]] | None = None,
) -> None:
    sensor_root = field_satellite_dir(grower_slug, farm_slug, field_slug) / sensor
    manifest_path = sensor_root / "manifest.json"
    previous_manifest = _load_sensor_manifest(
        manifest_path, sensor, field_id, field_slug
    )
    previous_by_year = {
        int(entry["year"]): _normalize_year_entry(entry)
        for entry in previous_manifest.get("years", [])
        if "year" in entry
    }
    year_entries: list[dict[str, Any]] = []

    for year in years:
        previous_entry = previous_by_year.get(year)
        if gap_months is not None and not gap_months.get(year):
            # nothing missing in this year: carry it over untouched rather than
            # re-searching a season that already has a scene in every month
            if previous_entry is not None:
                year_entries.append(previous_entry)
            continue
        if not force:
            can_reuse_year, reusable_scenes = _year_entry_fully_reusable(
                previous_entry, sensor_root=sensor_root, sensor=sensor
            )
            if can_reuse_year and not _candidates_are_exhausted(
                previous_entry, reusable_scenes
            ):
                # files are reusable but a month still has unfetched dates, so
                # fall through to the search and top the year up
                print(
                    f"top-up {field_id} {sensor} {year} "
                    "(fewer distinct candidate dates than available)"
                )
                can_reuse_year = False
            if can_reuse_year:
                coverage_months = sorted(
                    {
                        int(datetime.fromisoformat(str(scene["scene_date"])).month)
                        for scene in reusable_scenes
                    }
                )
                missing_months = [
                    month
                    for month in _GROWING_SEASON_MONTHS
                    if month not in coverage_months
                ]
                year_entries.append(
                    {
                        "year": year,
                        "status": "complete",
                        "month_candidate_availability": (
                            (previous_entry or {}).get("month_candidate_availability")
                            or {}
                        ),
                        "scene_count": len(reusable_scenes),
                        "coverage_months": coverage_months,
                        "missing_months": missing_months,
                        "coverage_status": "full" if not missing_months else "partial",
                        "reused": True,
                        "api_skipped": True,
                        "scenes": sorted(
                            reusable_scenes,
                            key=lambda scene: str(scene.get("scene_date", "")),
                        ),
                    }
                )
                print(
                    f"skip  {field_id} {sensor} {year} (manifest+files reusable; API skipped)"
                )
                continue

        try:
            features = search_features(
                collection,
                tuple(field_geom.bounds),
                _season_range(year),
                cloud_cover_max,
                limit=max(200, max_scenes_per_year * 30),
            )
        except SatelliteSearchError as exc:
            if previous_entry is not None and previous_entry.get("scenes"):
                # the catalogue is unreachable but this year is already on disk:
                # keep what exists rather than turning a good year into a gap
                year_entries.append(previous_entry)
                print(
                    f"warn  {field_id} {sensor} {year} search failed: {exc} "
                    "(kept existing scenes)"
                )
                continue
            year_entries.append(
                {
                    "year": year,
                    "status": "missing",
                    "reason": "search_failed",
                    "error": str(exc),
                    "scene_count": 0,
                    "scenes": [],
                    "coverage_months": [],
                    "missing_months": list(_GROWING_SEASON_MONTHS),
                }
            )
            print(f"warn  {field_id} {sensor} {year} search failed: {exc}")
            continue
        selected_features = _select_scene_inventory(
            features, max_scenes_per_year=max_scenes_per_year,
            per_month=_scenes_per_month(),
            only_months=(gap_months or {}).get(year),
        )
        # In gap-fill mode the search above only covers the missing months, so
        # the months that already have a scene keep theirs: the manifest is
        # rebuilt from the union, never from the search result alone.
        carried: list[dict[str, Any]] = []
        if gap_months is not None and previous_entry is not None:
            keep = {
                int(str(scene.get("scene_date", "0000-00-00"))[5:7])
                for scene in previous_entry.get("scenes", [])
            } - (gap_months.get(year) or set())
            for scene in previous_entry.get("scenes", []):
                month = int(str(scene.get("scene_date", "0000-00-00"))[5:7])
                if month not in keep:
                    continue
                scene_date = datetime.fromisoformat(str(scene.get("scene_date")))
                paths = _scene_paths(sensor_root, scene_date, sensor)
                if _existing_entry_is_reusable(
                    scene, str(scene.get("scene_id")), paths
                ):
                    carried.append(scene)
        if not selected_features and not carried:
            year_entries.append(
                {
                    "year": year,
                    "status": "missing",
                    "reason": "no_scene_found",
                    "scene_count": 0,
                    "scenes": [],
                    "coverage_months": [],
                    "missing_months": list(_GROWING_SEASON_MONTHS),
                }
            )
            print(f"warn  {field_id} {sensor} {year} no scene found")
            continue

        scenes: list[dict[str, Any]] = list(carried)
        # a gap-fill year was re-examined by definition, so never report it as
        # a straight reuse even when the search found nothing new
        all_reusable = not force and previous_entry is not None and not gap_months
        for feature in selected_features:
            scene_date = feature_datetime(feature)
            scene_paths = _scene_paths(sensor_root, scene_date, sensor)
            previous_scene = None
            if previous_entry is not None:
                previous_scene = next(
                    (
                        scene
                        for scene in previous_entry.get("scenes", [])
                        if scene.get("scene_id") == feature["id"]
                    ),
                    None,
                )
            if not force and _existing_entry_is_reusable(
                previous_scene, feature["id"], scene_paths
            ):
                scenes.append(cast(dict[str, Any], previous_scene))
                print(f"skip  {field_id} {sensor} {year} {feature['id']}")
                continue

            all_reusable = False
            print(f"run   {field_id} {sensor} {year} {feature['id']}")
            try:
                if sensor == "sentinel":
                    scenes.append(
                        _download_sentinel_scene(feature, field_geom, sensor_root)
                    )
                else:
                    scenes.append(
                        _download_landsat_scene(feature, field_geom, sensor_root)
                    )
            except Exception as exc:
                print(
                    f"warn  {field_id} {sensor} {year} {feature['id']} skipped: {exc}"
                )

        coverage_months = sorted(
            {
                int(datetime.fromisoformat(str(scene["scene_date"])).month)
                for scene in scenes
            }
        )
        missing_months = [
            month for month in _GROWING_SEASON_MONTHS if month not in coverage_months
        ]
        year_entries.append(
            {
                "year": year,
                "status": "complete" if scenes else "missing",
                "month_candidate_availability": _month_availability(features),
                "scene_count": len(scenes),
                "coverage_months": coverage_months,
                "missing_months": missing_months,
                "coverage_status": "full" if not missing_months else "partial",
                "reused": all_reusable,
                "scenes": sorted(
                    scenes, key=lambda scene: str(scene.get("scene_date", ""))
                ),
            }
        )

    manifest = {
        "dataset_name": sensor,
        "field_id": field_id,
        "field_slug": field_slug,
        "grower_slug": grower_slug,
        "farm_slug": farm_slug,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "years": sorted(year_entries, key=lambda entry: entry["year"]),
    }
    _write_json(manifest_path, manifest)


def _gap_months(grower_slug: str, farm_slug: str) -> dict[str, dict[int, set[int]]]:
    """Months inside a managed season that already have no usable scene.

    Read from the NDVI stage's own table, so the downloader chases exactly the
    holes the report prints instead of re-downloading seasons that are whole.
    A field with no gaps is absent from the mapping and is left untouched.
    """
    path = farm_tables_dir(grower_slug, farm_slug) / "ndvi_field_year_summary.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype={"field_slug": str})
    gaps: dict[str, dict[int, set[int]]] = {}
    for row in frame.itertuples(index=False):
        if str(getattr(row, "status", "")) != "observed":
            continue
        raw = str(getattr(row, "missing_months", "") or "")
        months: set[int] = set()
        for token in raw.replace('"', "").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = float(token)
            except ValueError:
                continue
            # a single month in an otherwise empty column is written as "11.0"
            if value.is_integer():
                months.add(int(value))
        if months:
            gaps.setdefault(str(row.field_slug), {})[int(row.year)] = months
    return gaps


def main() -> None:
    print("=" * 60)
    print("Satellite imagery download - raw TIFFs first")
    print("=" * 60)

    force = os.environ.get("AG_FORCE") == "1"
    fill_gaps = os.environ.get("AG_FILL_GAPS") == "1"
    config = FieldReportingConfig(
        farm_name=_DEFAULT_FARM_NAME,
        field_boundary_path=str(farm_boundary_path(_DEFAULT_GROWER, _DEFAULT_FARM)),
    )
    imagery_years = _imagery_years(config.imagery_years)
    sensors = _sensors_requested()
    print(f"sensors: {', '.join(sensors)} | years: {imagery_years}")
    print(f"season window: {_SEASON_START_MD} -> {_SEASON_END_MD} (months {_GROWING_SEASON_MONTHS})")
    gap_map: dict[str, dict[int, set[int]]] = {}
    if fill_gaps:
        gap_map = _gap_months(_DEFAULT_GROWER, _DEFAULT_FARM)
        targeted = sum(len(months) for years in gap_map.values()
                       for months in years.values())
        print(
            f"gap-fill: {len(gap_map)} fields, {targeted} field-months, "
            f"up to {_scenes_per_month()} candidate dates per month"
        )
    field_slugs = _field_slug_lookup()
    fields = gpd.read_file(_REPO / config.field_boundary_path).to_crs("EPSG:4326")

    for _, field in fields.iterrows():
        field_id = str(field["field_id"])
        field_slug = field_slugs.get(field_id)
        if not field_slug:
            print(f"skip  {field_id} no field slug")
            continue
        _ensure_field_boundary(field, _DEFAULT_GROWER, _DEFAULT_FARM, field_slug)
        if "sentinel" in sensors:
            _download_sensor_archive(
                field_id=field_id,
                field_slug=field_slug,
                field_geom=field.geometry,
                grower_slug=_DEFAULT_GROWER,
                farm_slug=_DEFAULT_FARM,
                years=imagery_years,
                sensor="sentinel",
                collection=SENTINEL_COLLECTION,
                cloud_cover_max=_sentinel_cloud_max(config.sentinel_cloud_cover_max),
                max_scenes_per_year=9 * _scenes_per_month(),
                force=force,
                gap_months=(gap_map.get(field_slug, {}) if fill_gaps else None),
            )
        if "landsat" in sensors:
            _download_sensor_archive(
                field_id=field_id,
                field_slug=field_slug,
                field_geom=field.geometry,
                grower_slug=_DEFAULT_GROWER,
                farm_slug=_DEFAULT_FARM,
                years=imagery_years,
                sensor="landsat",
                collection=LANDSAT_COLLECTION,
                cloud_cover_max=config.landsat_cloud_cover_max,
                max_scenes_per_year=9,
                force=force,
            )

    print("\n✓ Raw satellite TIFF download complete")


if __name__ == "__main__":
    main()
