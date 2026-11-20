#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportArgumentType=false, reportCallIssue=false, reportGeneralTypeIssues=false
# ruff: noqa: I001
"""Generate canonical per-field NDVI cards from composite and rollup assets."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.warp import Resampling, reproject

matplotlib.use("Agg")

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths  # noqa: E402

_RUNTIME_PATHS = resolve_runtime_paths()
_REPO = _RUNTIME_PATHS.runtime_base
_SCRIPTS = _RUNTIME_PATHS.runtime_scripts
_LIB = _RUNTIME_PATHS.runtime_scripts / "lib"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_LIB))
_DEFAULT_GROWER = os.environ.get("AG_GROWER_SLUG", "default-grower")
_DEFAULT_FARM = os.environ.get("AG_FARM_SLUG", "default-farm")
_DEFAULT_FARM_NAME = os.environ.get("AG_FARM_NAME", "Default Farm")
_DEFAULT_INVENTORY = _REPO / "growers" / _DEFAULT_GROWER / "farms" / _DEFAULT_FARM / "manifests" / "field-inventory.csv"
_FIELD_INVENTORY = Path(os.environ.get("AG_INVENTORY_CSV", str(_DEFAULT_INVENTORY)))

from reporting_bootstrap import ensure_skill_path  # noqa: E402


ensure_skill_path("farm-intelligence-reporting")

from pipeline import (  # noqa: E402
    FieldReportingConfig,
    build_step_manifest,
    load_manifest,
    step_is_stale,
)  # pyright: ignore[reportMissingImports]
from paths import (  # noqa: E402
    farm_boundary_path,
    field_dir,
    field_boundary_path,
    field_feature_path,
    field_manifest_dir,
    field_satellite_dir,
    field_summary_path,
    field_tables_dir,
    shared_cdl_conus_raster_path,
    shared_cdl_raster_dir,
)  # pyright: ignore[reportMissingImports]
from satellite_imagery import write_single_band_raster  # noqa: E402

_SCRIPT = Path(__file__)
_CROP_CARD_SPECS = {
    "corn": {
        "label": "Corn average NDVI",
        "crop_name": "Corn",
        "rollup": "ndvi_corn_rollup.tif",
    },
    "soybean": {
        "label": "Soybean average NDVI",
        "crop_name": "Soybeans",
        "rollup": "ndvi_soybean_rollup.tif",
    },
}
_PEAK_CARD_SPECS = {
    "corn_peak_95": {
        "label": "Corn 95th %ile peak NDVI",
        "crop_name": "Corn",
        "crop_code": 1,
        "doy_window": (180, 240),
        "output_png": "ndvi_corn_peak_95.png",
        "output_tif": "ndvi_corn_peak_95.tif",
    },
    "soybean_peak_95": {
        "label": "Soybean 95th %ile peak NDVI",
        "crop_name": "Soybeans",
        "crop_code": 5,
        "doy_window": (190, 250),
        "output_png": "ndvi_soybean_peak_95.png",
        "output_tif": "ndvi_soybean_peak_95.tif",
    },
}
_PEAK_CROP_MIN_PIXELS = 25
_PEAK_CROP_MIN_FRACTION = 0.05
# All-vegetation fallback cards use their own filenames so a PNG never claims
# to show crop-specific NDVI when it actually shows every field pixel. Both
# crop slots share one canonical all-vegetation card, so reports can dedupe it.
_ALL_VEG_AVG_PNG = "ndvi_all_vegetation.png"
_ALL_VEG_PEAK_PNG = "ndvi_all_vegetation_peak_95.png"


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


def _field_root(field_slug: str) -> Path:
    return field_dir(_DEFAULT_GROWER, _DEFAULT_FARM, field_slug)


def _sensor_manifest_paths(field_slug: str) -> list[Path]:
    satellite_root = field_satellite_dir(_DEFAULT_GROWER, _DEFAULT_FARM, field_slug)
    return [
        satellite_root / "sentinel" / "manifest.json",
        satellite_root / "landsat" / "manifest.json",
    ]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _field_array_stats(
    raster_path: Path, boundary_path: Path
) -> tuple[np.ndarray | None, float | None, tuple[float, float] | None]:
    boundary = gpd.read_file(boundary_path)
    with rasterio.open(raster_path) as src:
        boundary_proj = boundary.to_crs(src.crs)
        import raster_rules as _rr
        clipped, _, _nm = _rr.outer_crop(src, list(boundary_proj.geometry))
    array = clipped[0].astype(float)
    array[_nm] = np.nan  # outside the field polygon
    valid = array[np.isfinite(array)]
    if valid.size == 0:
        return None, None, None
    return (
        array,
        float(np.nanmean(valid)),
        (float(np.nanmin(valid)), float(np.nanmax(valid))),
    )


def _read_resampled_like(
    source_path: Path, reference_path: Path, *, resampling: Resampling
) -> np.ndarray:
    with rasterio.open(reference_path) as ref_src, rasterio.open(source_path) as src:
        destination = np.full((ref_src.height, ref_src.width), np.nan, dtype="float32")
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=ref_src.transform,
            dst_crs=ref_src.crs,
            resampling=resampling,
        )
    return destination


def _shared_cdl_raster_path(year: int, state_fips: str = "19") -> Path:
    state_path = shared_cdl_raster_dir() / f"CDL_{year}_{state_fips}.tif"
    if state_path.exists():
        return state_path
    return shared_cdl_conus_raster_path(year)


def _field_state_fips_lookup() -> dict[str, str]:
    boundary_path = farm_boundary_path(_DEFAULT_GROWER, _DEFAULT_FARM)
    if not boundary_path.exists():
        return {}
    fields = gpd.read_file(boundary_path)
    if "field_id" not in fields.columns or "state_fips" not in fields.columns:
        return {}
    lookup: dict[str, str] = {}
    for _, row in fields[["field_id", "state_fips"]].dropna().iterrows():
        state_fips = str(row["state_fips"]).strip()
        if not state_fips:
            continue
        lookup[str(row["field_id"])] = state_fips.zfill(2)
    return lookup


def _collect_peak_scene_rows(
    field_slug: str, *, crop_code: int, doy_window: tuple[int, int], state_fips: str
) -> list[dict[str, Any]]:
    start_doy, end_doy = doy_window
    rows: list[dict[str, Any]] = []
    for manifest_path in _sensor_manifest_paths(field_slug):
        if not manifest_path.exists():
            continue
        sensor = manifest_path.parent.name
        manifest = _load_json(manifest_path)
        for year_entry in manifest.get("years", []):
            year = year_entry.get("year")
            if year is None:
                continue
            cdl_raster = _shared_cdl_raster_path(int(year), state_fips=state_fips)
            if not cdl_raster.exists():
                continue
            for scene in year_entry.get("scenes", []):
                ndvi_tif = scene.get("ndvi_tif")
                scene_date_raw = scene.get("scene_date")
                if not ndvi_tif or not scene_date_raw:
                    continue
                scene_date = pd.Timestamp(scene_date_raw)
                doy = int(scene_date.dayofyear)
                if doy < start_doy or doy > end_doy:
                    continue
                ndvi_path = _REPO / str(ndvi_tif)
                if not ndvi_path.exists():
                    continue
                rows.append(
                    {
                        "sensor": sensor,
                        "scene_date": scene_date,
                        "year": int(year),
                        "crop_code": crop_code,
                        "cdl_raster": cdl_raster,
                        "ndvi_path": ndvi_path,
                    }
                )
    return sorted(rows, key=lambda row: (row["scene_date"], row["sensor"]))


def _select_peak_reference(scene_rows: list[dict[str, Any]]) -> Path | None:
    """Pick the highest-resolution scene as the stack reference grid.

    Scene rows are date-sorted, and same-day Landsat rows sort before Sentinel,
    so using the first row silently downsamples the whole peak card to Landsat
    resolution. Choose the smallest pixel area instead, preferring Sentinel.
    """
    best_path: Path | None = None
    best_key: tuple[float, int] | None = None
    for row in scene_rows:
        ndvi_path = Path(row["ndvi_path"])
        with rasterio.open(ndvi_path) as src:
            pixel_area = abs(
                src.transform.a * src.transform.e - src.transform.b * src.transform.d
            )
        sensor_rank = 0 if str(row.get("sensor", "")).lower() == "sentinel" else 1
        key = (pixel_area, sensor_rank)
        if best_key is None or key < best_key:
            best_key = key
            best_path = ndvi_path
    return best_path


def _field_mask(reference_path: Path, boundary_path: Path) -> np.ndarray:
    boundary = gpd.read_file(boundary_path)
    with rasterio.open(reference_path) as src:
        boundary_proj = boundary.to_crs(src.crs)
        return geometry_mask(
            boundary_proj.geometry,
            transform=src.transform,
            invert=True,
            out_shape=(src.height, src.width),
        )


def _field_pixel_count(reference_path: Path, boundary_path: Path) -> int:
    return int(_field_mask(reference_path, boundary_path).sum())


def _compute_peak_percentile_array(
    scene_rows: list[dict[str, Any]],
    percentile: float = 95.0,
    *,
    mask_to_crop: bool = True,
    boundary_path: Path | None = None,
) -> tuple[
    np.ndarray | None,
    float | None,
    tuple[float, float] | None,
    Path | None,
    list[dict[str, Any]],
]:
    if not scene_rows:
        return None, None, None, None, []

    reference_path = _select_peak_reference(scene_rows)
    if reference_path is None:
        return None, None, None, None, []
    field_mask = (
        _field_mask(reference_path, boundary_path)
        if boundary_path is not None and boundary_path.exists()
        else None
    )
    masked_arrays: list[np.ndarray] = []
    used_rows: list[dict[str, Any]] = []

    for row in scene_rows:
        ndvi_path = Path(row["ndvi_path"])
        ndvi_array = _read_resampled_like(
            ndvi_path, reference_path, resampling=Resampling.bilinear
        )
        valid_mask = np.isfinite(ndvi_array)
        if field_mask is not None:
            valid_mask &= field_mask
        if mask_to_crop:
            crop_code = int(row["crop_code"])
            cdl_array = _read_resampled_like(
                Path(row["cdl_raster"]), reference_path, resampling=Resampling.nearest
            )
            valid_mask &= np.isfinite(cdl_array) & (
                np.rint(cdl_array).astype("int32") == crop_code
            )
        masked = np.where(valid_mask, ndvi_array, np.nan).astype("float32")
        if np.isfinite(masked).any():
            masked_arrays.append(masked)
            used_rows.append(row)

    if not masked_arrays:
        return None, None, None, reference_path, []

    stacked = np.stack(masked_arrays, axis=0)
    flat = stacked.reshape(stacked.shape[0], -1)
    valid_cols = np.any(np.isfinite(flat), axis=0)
    peak_flat = np.full(flat.shape[1], np.nan, dtype="float32")
    if valid_cols.any():
        peak_flat[valid_cols] = np.nanpercentile(
            flat[:, valid_cols], percentile, axis=0
        ).astype("float32")
    peak_array = peak_flat.reshape(stacked.shape[1:])
    valid = peak_array[np.isfinite(peak_array)]
    if valid.size == 0:
        return None, None, None, reference_path, used_rows
    return (
        peak_array,
        float(np.nanmean(valid)),
        (float(np.nanmin(valid)), float(np.nanmax(valid))),
        reference_path,
        used_rows,
    )


def _placeholder_card(
    output_path: Path, title: str, message: str, detail: str | None = None
) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    fig.patch.set_facecolor("#fafaf9")
    ax.axis("off")
    ax.text(
        0.04,
        0.90,
        title,
        fontsize=14,
        fontweight="bold",
        color="#0f172a",
        transform=ax.transAxes,
    )
    ax.text(
        0.5,
        0.60,
        "NDVI card unavailable",
        ha="center",
        va="center",
        fontsize=15,
        fontweight="bold",
        color="#334155",
        transform=ax.transAxes,
    )
    ax.text(
        0.5,
        0.40,
        message,
        ha="center",
        va="center",
        fontsize=10,
        color="#64748b",
        transform=ax.transAxes,
        wrap=True,
    )
    if detail:
        ax.text(
            0.5,
            0.22,
            detail,
            ha="center",
            va="center",
            fontsize=9,
            color="#94a3b8",
            transform=ax.transAxes,
            wrap=True,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path, dpi=170, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    plt.close(fig)


def _crop_to_valid(array: np.ndarray, pad: int = 2) -> np.ndarray:
    """Crop an array to the bounding box of valid pixels plus padding.

    Sparse QA cards can contain only a handful of crop pixels in a full-field
    grid. Rendering the whole grid makes them invisible, so crop the view.
    """
    valid = np.isfinite(array)
    if not valid.any():
        return array
    rows = np.where(valid.any(axis=1))[0]
    cols = np.where(valid.any(axis=0))[0]
    row_start = max(int(rows[0]) - pad, 0)
    row_stop = min(int(rows[-1]) + pad + 1, array.shape[0])
    col_start = max(int(cols[0]) - pad, 0)
    col_stop = min(int(cols[-1]) + pad + 1, array.shape[1])
    return array[row_start:row_stop, col_start:col_stop]


def _render_raster_card(
    output_path: Path,
    title: str,
    subtitle_lines: list[str],
    array: np.ndarray | None,
    mean_ndvi: float | None,
    value_range: tuple[float, float] | None,
    fallback_message: str,
    *,
    vmin: float = -0.2,
    vmax: float = 1.0,
) -> bool:
    if array is None or mean_ndvi is None or value_range is None:
        _placeholder_card(output_path, title, fallback_message)
        return False

    fig = plt.figure(figsize=(8, 5))
    fig.patch.set_facecolor("#fafaf9")
    gs = fig.add_gridspec(2, 2, height_ratios=[0.34, 1.0], width_ratios=[1.0, 0.04])
    title_ax = fig.add_subplot(gs[0, :])
    image_ax = fig.add_subplot(gs[1, 0])
    color_ax = fig.add_subplot(gs[1, 1])

    title_ax.axis("off")
    title_ax.text(0.0, 0.82, title, fontsize=14, fontweight="bold", color="#0f172a")
    title_ax.text(
        0.0,
        0.10,
        "\n".join(subtitle_lines),
        fontsize=9.5,
        color="#475569",
        va="bottom",
    )

    image = image_ax.imshow(
        np.ma.masked_invalid(array), cmap="RdYlGn", vmin=vmin, vmax=vmax
    )
    image_ax.axis("off")
    image_ax.text(
        0.02,
        0.03,
        f"Mean {mean_ndvi:.3f} | Range {value_range[0]:.2f} to {value_range[1]:.2f}",
        transform=image_ax.transAxes,
        fontsize=8.5,
        color="#0f172a",
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "#f8fafc",
            "edgecolor": "#cbd5e1",
        },
    )
    fig.colorbar(
        image, cax=color_ax, label="NDVI",
        ticks=[t for t in (0.2, 0.4, 0.6, 0.8) if vmin <= t <= vmax],
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path, dpi=170, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    plt.close(fig)
    return True


def _sensor_rank(sensor: str) -> int:
    return 0 if sensor == "sentinel" else 1


def _select_current_season_scenes(
    scene_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = {}
    for row in scene_rows:
        scene_date = row.get("scene_date")
        if scene_date is None:
            continue
        month = int(scene_date.month)
        current = selected.get(month)
        if current is None:
            selected[month] = row
            continue
        current_score = (
            _sensor_rank(str(current.get("sensor", ""))),
            float(current.get("cloud_cover") or 999.0),
            str(current.get("scene_date")),
        )
        candidate_score = (
            _sensor_rank(str(row.get("sensor", ""))),
            float(row.get("cloud_cover") or 999.0),
            str(row.get("scene_date")),
        )
        if candidate_score < current_score:
            selected[month] = row
    return sorted(selected.values(), key=lambda row: row["scene_date"])


def _collect_all_season_scene_rows(
    field_slug: str, boundary_path: Path, *, state_fips: str
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []

    for manifest_path in _sensor_manifest_paths(field_slug):
        if not manifest_path.exists():
            continue
        sensor = manifest_path.parent.name
        manifest = _load_json(manifest_path)
        for year_entry in manifest.get("years", []):
            year = year_entry.get("year")
            if year is None:
                continue
            year_int = int(year)
            cdl_raster = _shared_cdl_raster_path(year_int, state_fips=state_fips)
            if not cdl_raster.exists():
                continue
            for scene in year_entry.get("scenes", []):
                ndvi_tif = scene.get("ndvi_tif")
                scene_date_raw = scene.get("scene_date")
                if not ndvi_tif or not scene_date_raw:
                    continue
                ndvi_path = _REPO / str(ndvi_tif)
                if not ndvi_path.exists() or not boundary_path.exists():
                    continue
                all_rows.append(
                    {
                        "sensor": sensor,
                        "scene_date": pd.Timestamp(scene_date_raw),
                        "cloud_cover": float(scene.get("cloud_cover") or 999.0),
                        "ndvi_path": ndvi_path,
                        "cdl_raster": cdl_raster,
                        "month": pd.Timestamp(scene_date_raw).month,
                        "year": year_int,
                    }
                )
    return all_rows


def _scene_metric_summary(
    ndvi_path: Path,
    cdl_raster: Path,
    boundary_path: Path,
    crop_code: int | None,
) -> dict[str, float] | None:
    boundary = gpd.read_file(boundary_path)
    with rasterio.open(ndvi_path) as src:
        boundary_proj = boundary.to_crs(src.crs)
        ndvi_array = src.read(1).astype("float32")
        field_mask = geometry_mask(
            boundary_proj.geometry,
            transform=src.transform,
            invert=True,
            out_shape=(src.height, src.width),
        )
    valid_mask = field_mask & np.isfinite(ndvi_array)
    if crop_code is not None:
        cdl_array = _read_resampled_like(
            cdl_raster, ndvi_path, resampling=Resampling.nearest
        )
        valid_mask &= np.isfinite(cdl_array) & (
            np.rint(cdl_array).astype("int32") == crop_code
        )
    values = ndvi_array[valid_mask]
    if values.size == 0:
        return None
    return {
        "mean_ndvi": float(np.nanmean(values)),
        "p95_ndvi": float(np.nanpercentile(values, 95)),
        "p05_ndvi": float(np.nanpercentile(values, 5)),
    }


def _select_monthly_scene_rows(
    scene_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in scene_rows:
        by_year.setdefault(int(row["year"]), []).append(row)
    selected: list[dict[str, Any]] = []
    for year in sorted(by_year):
        selected.extend(_select_current_season_scenes(by_year[year]))
    return selected


def _render_current_cumulative_card(
    output_path: Path,
    scene_rows: list[dict[str, Any]],
    join_df: pd.DataFrame,
    boundary_path: Path,
) -> tuple[bool, str | None, dict[str, list[int]], str]:
    if not scene_rows:
        _placeholder_card(
            output_path,
            "Cumulative NDVI by year",
            "No usable cumulative NDVI scenes are available for this field.",
            detail="The chart will populate after seasonal scenes are cached.",
        )
        return False, "no cumulative scenes", {}, "none"

    frame = pd.DataFrame(scene_rows)
    crop_lookup = (
        {
            int(row["year"]): str(row["crop_name"])
            for _, row in join_df[["year", "crop_name"]].dropna().iterrows()
        }
        if {"year", "crop_name"}.issubset(join_df.columns)
        else {}
    )
    frame["crop_name"] = [crop_lookup.get(int(year)) for year in frame["year"].tolist()]
    crop_frame = frame[frame["crop_name"].isin(["Corn", "Soybeans"])].copy()
    all_vegetation = crop_frame.empty
    if all_vegetation:
        # These fields have no corn/soybean years in the CDL window (for
        # example wheat or pasture). Show every seasonal scene without a crop
        # mask instead of leaving the card empty.
        frame = frame.copy()
        frame["crop_name"] = "All vegetation"
        panel_crops = ["All vegetation"]
        basis = "all_vegetation"
    else:
        frame = crop_frame
        panel_crops = [
            crop for crop in ("Corn", "Soybeans") if crop in set(frame["crop_name"])
        ]
        basis = "crop"

    crop_code_lookup = {"Corn": 1, "Soybeans": 5}
    metric_rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        crop_name = str(row["crop_name"])
        crop_code = None if all_vegetation else crop_code_lookup.get(crop_name)
        if crop_code is None and not all_vegetation:
            continue
        metrics = _scene_metric_summary(
            Path(cast(Any, row["ndvi_path"])),
            Path(cast(Any, row["cdl_raster"])),
            boundary_path=boundary_path,
            crop_code=crop_code,
        )
        if metrics is None:
            continue
        metric_rows.append(
            {
                **row.to_dict(),
                **metrics,
            }
        )

    frame = pd.DataFrame(metric_rows)
    if frame.empty:
        _placeholder_card(
            output_path,
            "Cumulative NDVI by year",
            "No usable NDVI pixels were available for cumulative chart metrics.",
            detail="The chart will populate after seasonal NDVI scenes overlap valid field pixels.",
        )
        return False, "no usable NDVI pixels", {}, basis

    frame = frame.sort_values(by=["crop_name", "year", "scene_date"]).copy()
    frame["day_of_year"] = frame["scene_date"].dt.dayofyear
    year_groups: list[pd.DataFrame] = []
    crop_years: dict[str, list[int]] = {crop: [] for crop in panel_crops}
    for key, group in frame.groupby(["crop_name", "year"], sort=True):
        crop_name = str(cast(tuple[object, object], key)[0])
        year = int(cast(tuple[object, object], key)[1])
        ordered = group.sort_values("scene_date").copy()
        ordered["mean_cumulative"] = ordered["mean_ndvi"].cumsum()
        ordered["p95_cumulative"] = ordered["p95_ndvi"].cumsum()
        ordered["p05_cumulative"] = ordered["p05_ndvi"].cumsum()
        year_groups.append(ordered)
        crop_years.setdefault(crop_name, []).append(year)

    fig = plt.figure(figsize=(12, 5.9))
    fig.patch.set_facecolor("#fafaf9")
    gs = fig.add_gridspec(2, 2, height_ratios=[0.34, 1.0], hspace=0.06, wspace=0.18)
    title_ax = fig.add_subplot(gs[0, :])
    if len(panel_crops) == 1:
        crop_axes = {panel_crops[0]: fig.add_subplot(gs[1, :])}
    else:
        crop_axes = {
            panel_crops[0]: fig.add_subplot(gs[1, 0]),
            panel_crops[1]: fig.add_subplot(gs[1, 1]),
        }

    title_ax.axis("off")
    title_ax.text(
        0.0,
        0.82,
        (
            "Cumulative NDVI by crop and year"
            if basis == "crop"
            else "Cumulative NDVI by year (all vegetation)"
        ),
        fontsize=16,
        fontweight="bold",
        color="#0f172a",
    )
    subtitle = (
        "Each subplot shows monthly best-available seasonal scenes grouped by dominant crop year, "
        "with solid mean lines and shaded 5th-95th percentile envelopes for each observed year."
        if basis == "crop"
        else "No corn or soybean years were available for this field, so all seasonal scenes are shown "
        "without a crop mask, using solid mean lines and shaded 5th-95th percentile envelopes."
    )
    title_ax.text(
        0.0,
        0.10,
        subtitle,
        fontsize=10.5,
        color="#475569",
        va="bottom",
        wrap=True,
    )

    palette = ["#0f766e", "#2563eb", "#ea580c", "#7c3aed", "#ca8a04"]
    populated_groups = [group for group in year_groups if not group.empty]
    if populated_groups:
        x_min = min(int(group["day_of_year"].min()) for group in populated_groups)
        x_max = max(int(group["day_of_year"].max()) for group in populated_groups)
        y_max = max(float(group["p95_cumulative"].max()) for group in populated_groups)
    else:
        x_min, x_max, y_max = 1, 366, 1.0
    for crop_name, ax in crop_axes.items():
        crop_groups = [
            group
            for group in year_groups
            if str(group["crop_name"].iloc[0]) == crop_name
        ]
        ax.set_facecolor("#ffffff")
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("Day of year", fontsize=10)
        ax.set_ylabel("Cumulative NDVI", fontsize=10)
        if basis == "crop":
            ax.set_title(crop_name, fontsize=12.5, fontweight="bold", loc="left")
        ax.tick_params(axis="both", labelsize=9)
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(0.0, max(y_max * 1.05, 1.0))
        if not crop_groups:
            ax.text(
                0.5,
                0.5,
                f"No {crop_name.lower().replace('soybeans', 'soybean')} seasonal curves available",
                ha="center",
                va="center",
                transform=ax.transAxes,
                color="#64748b",
                fontsize=10,
            )
            continue
        for idx, group in enumerate(crop_groups):
            year = int(group["year"].iloc[0])
            color = palette[idx % len(palette)]
            ax.plot(
                group["day_of_year"],
                group["mean_cumulative"],
                linewidth=2.1,
                marker="o",
                markersize=4.5,
                color=color,
                label=str(year),
            )
            ax.fill_between(
                group["day_of_year"],
                group["p05_cumulative"],
                group["p95_cumulative"],
                color=color,
                alpha=0.18,
            )
        ax.legend(title="Year", fontsize=9, title_fontsize=9, loc="upper left")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path, dpi=170, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    plt.close(fig)
    return (
        True,
        None,
        {key: sorted(set(value)) for key, value in crop_years.items() if value},
        basis,
    )


def _join_rows(field_slug: str) -> pd.DataFrame:
    join_csv = (
        field_tables_dir(_DEFAULT_GROWER, _DEFAULT_FARM, field_slug)
        / "ndvi_year_crop_join.csv"
    )
    if not join_csv.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(join_csv)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _crop_years(join_df: pd.DataFrame, crop_name: str) -> list[int]:
    if (
        join_df.empty
        or "crop_name" not in join_df.columns
        or "year" not in join_df.columns
    ):
        return []
    years = (
        join_df.loc[join_df["crop_name"] == crop_name, "year"]
        .dropna()
        .astype(int)
        .tolist()
    )
    return sorted(years)


def _card_outputs(field_slug: str) -> dict[str, Path]:
    return {
        "corn": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_corn.png"
        ),
        "soybean": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_soybean.png"
        ),
        "all_vegetation": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, _ALL_VEG_AVG_PNG
        ),
        "corn_peak_95": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_corn_peak_95.png"
        ),
        "soybean_peak_95": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_soybean_peak_95.png"
        ),
        "all_vegetation_peak_95": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, _ALL_VEG_PEAK_PNG
        ),
        "corn_peak_95_crop_only": field_feature_path(
            _DEFAULT_GROWER,
            _DEFAULT_FARM,
            field_slug,
            "ndvi_corn_peak_95_crop_only.png",
        ),
        "soybean_peak_95_crop_only": field_feature_path(
            _DEFAULT_GROWER,
            _DEFAULT_FARM,
            field_slug,
            "ndvi_soybean_peak_95_crop_only.png",
        ),
        "corn_peak_95_tif": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_corn_peak_95.tif"
        ),
        "soybean_peak_95_tif": field_feature_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_soybean_peak_95.tif"
        ),
        "current_season_cumulative": field_feature_path(
            _DEFAULT_GROWER,
            _DEFAULT_FARM,
            field_slug,
            "ndvi_current_season_cumulative.png",
        ),
        "summary": field_summary_path(
            _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_card_summary.json"
        ),
    }


def main() -> None:
    print("=" * 60)
    print("NDVI cards - canonical field assets")
    print("=" * 60)

    config = FieldReportingConfig(
        farm_name=_DEFAULT_FARM_NAME,
        field_boundary_path=str(farm_boundary_path(_DEFAULT_GROWER, _DEFAULT_FARM)),
        grower_slug=_DEFAULT_GROWER,
        farm_slug=_DEFAULT_FARM,
    )
    force = os.environ.get("AG_FORCE") == "1"
    fields = gpd.read_file(_REPO / config.field_boundary_path)
    field_slugs = _field_slug_lookup()
    field_state_fips = _field_state_fips_lookup()

    for _, field in fields.iterrows():
        field_id = str(field["field_id"])
        field_slug = field_slugs.get(field_id)
        if not field_slug:
            print(f"skip  {field_id} (no field slug)")
            continue
        state_fips = field_state_fips.get(field_id, "19")

        manifest_dir = field_manifest_dir(_DEFAULT_GROWER, _DEFAULT_FARM, field_slug)
        boundary_path = field_boundary_path(_DEFAULT_GROWER, _DEFAULT_FARM, field_slug)
        join_csv = (
            field_tables_dir(_DEFAULT_GROWER, _DEFAULT_FARM, field_slug)
            / "ndvi_year_crop_join.csv"
        )
        outputs = _card_outputs(field_slug)

        input_paths: list[Path] = [boundary_path, join_csv]
        join_df = _join_rows(field_slug)
        for spec in _CROP_CARD_SPECS.values():
            rollup_path = field_feature_path(
                _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, str(spec["rollup"])
            )
            if rollup_path.exists():
                input_paths.append(rollup_path)
        peak_scene_rows_by_key: dict[str, list[dict[str, Any]]] = {}
        for peak_key, spec in _PEAK_CARD_SPECS.items():
            scene_rows = _collect_peak_scene_rows(
                field_slug,
                crop_code=int(spec["crop_code"]),
                doy_window=cast(tuple[int, int], spec["doy_window"]),
                state_fips=state_fips,
            )
            peak_scene_rows_by_key[peak_key] = scene_rows
            input_paths.extend(Path(row["ndvi_path"]) for row in scene_rows)
            input_paths.extend(Path(row["cdl_raster"]) for row in scene_rows)
        seasonal_scene_rows = _collect_all_season_scene_rows(
            field_slug, boundary_path, state_fips=state_fips
        )
        selected_scene_rows = _select_monthly_scene_rows(seasonal_scene_rows)
        input_paths.extend(_sensor_manifest_paths(field_slug))
        input_paths.extend(Path(row["ndvi_path"]) for row in selected_scene_rows)

        manifest = build_step_manifest(
            step_name=f"ndvi_card_render_{field_id}",
            input_paths=input_paths,
            # Keep the staleness check on stable outputs only. Card filenames
            # are basis-dependent and stale crop-named PNGs get deleted, so
            # tracking every possible path would force a rerun on every pass.
            output_paths=[outputs["summary"], outputs["current_season_cumulative"]],
            code_paths=[_SCRIPT],
            config=config,
        )
        prior = load_manifest(manifest_dir / f"ndvi_card_render_{field_id}.json")
        if not force and not step_is_stale(manifest, prior):
            print(f"skip  {field_id}")
            continue

        print(f"run   {field_id}")
        summary_payload: dict[str, Any] = {
            "field_id": field_id,
            "field_slug": field_slug,
            "cards": {},
        }

        avg_all_veg_used = False
        for card_key, spec in _CROP_CARD_SPECS.items():
            crop_name = str(spec["crop_name"])
            crop_lower = crop_name.lower().replace("soybeans", "soybean")
            rollup_path = field_feature_path(
                _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, str(spec["rollup"])
            )
            fallback_path = field_feature_path(
                _DEFAULT_GROWER, _DEFAULT_FARM, field_slug, "ndvi_all_years_rollup.tif"
            )
            crop_years = _crop_years(join_df, crop_name)
            array = None
            mean_ndvi = None
            value_range = None
            reason = None
            basis = "crop"
            display_title = str(spec["label"])
            source_path = rollup_path
            if rollup_path.exists() and boundary_path.exists():
                try:
                    array, mean_ndvi, value_range = _field_array_stats(
                        rollup_path, boundary_path
                    )
                except Exception as exc:
                    reason = f"failed to read rollup raster: {exc}"
            elif crop_years:
                reason = f"missing rollup raster for {crop_lower} years"
            elif fallback_path.exists() and boundary_path.exists():
                try:
                    array, mean_ndvi, value_range = _field_array_stats(
                        fallback_path, boundary_path
                    )
                    basis = "all_vegetation"
                    display_title = "Average NDVI — all vegetation"
                    source_path = fallback_path
                    reason = (
                        f"no {crop_lower} years in the crop window; "
                        "using the all-year NDVI fallback"
                    )
                except Exception as exc:
                    reason = f"failed to read all-year NDVI fallback: {exc}"
            else:
                reason = (
                    f"no {crop_lower} years in the available crop history window "
                    "and no all-year NDVI fallback"
                )

            subtitle_lines = []
            if basis == "all_vegetation":
                subtitle_lines.append(
                    f"No {crop_lower} years in the available crop window"
                )
                subtitle_lines.append("Showing all-vegetation NDVI for every NDVI year")
            elif crop_years:
                subtitle_lines.append(
                    f"Years included: {', '.join(str(year) for year in crop_years)} ({len(crop_years)} total)"
                )
            else:
                subtitle_lines.append(
                    "Years included: none in the current 5-year crop window"
                )
            subtitle_lines.append(
                f"Source raster: {source_path.name if source_path.exists() else 'not available'}"
            )

            png_output = (
                outputs["all_vegetation"]
                if basis == "all_vegetation"
                else outputs[card_key]
            )
            if basis == "all_vegetation":
                avg_all_veg_used = True
                # Remove the misleading crop-named PNG left by older runs.
                outputs[card_key].unlink(missing_ok=True)

            rendered = _render_raster_card(
                png_output,
                display_title,
                subtitle_lines,
                array,
                mean_ndvi,
                value_range,
                fallback_message=(
                    f"This field does not yet have a usable {crop_lower} NDVI rollup in the "
                    "canonical cache."
                ),
            )
            summary_payload["cards"][card_key] = {
                "status": "available" if rendered else "unavailable",
                "title": display_title,
                "basis": basis,
                "crop_name": crop_name,
                "years": crop_years,
                "year_count": len(crop_years),
                "output_png": str(png_output.relative_to(_REPO)),
                "rollup_tif": str(source_path.relative_to(_REPO))
                if source_path.exists()
                else None,
                "reason": reason,
                "mean_ndvi": mean_ndvi,
            }

        if not avg_all_veg_used:
            outputs["all_vegetation"].unlink(missing_ok=True)

        # Both peak slots fall back to one shared all-vegetation stack so the
        # report can show a single honest card instead of two duplicates. The
        # union of the crop peak windows is the widest sensible all-veg window.
        peak_union_rows: list[dict[str, Any]] = []
        seen_peak_paths: set[str] = set()
        for rows in peak_scene_rows_by_key.values():
            for row in rows:
                row_key = str(row["ndvi_path"])
                if row_key not in seen_peak_paths:
                    seen_peak_paths.add(row_key)
                    peak_union_rows.append(row)
        peak_union_rows.sort(key=lambda row: (row["scene_date"], row["sensor"]))
        peak_union_window = (
            min(int(spec["doy_window"][0]) for spec in _PEAK_CARD_SPECS.values()),
            max(int(spec["doy_window"][1]) for spec in _PEAK_CARD_SPECS.values()),
        )
        peak_all_veg_cache: dict[str, Any] | None = None
        peak_all_veg_used = False

        for peak_key, spec in _PEAK_CARD_SPECS.items():
            peak_array = None
            peak_mean = None
            peak_range = None
            reference_path = None
            used_rows: list[dict[str, Any]] = []
            reason = None
            basis = "crop"
            display_title = str(spec["label"])
            crop_lower = str(spec["crop_name"]).lower().replace("soybeans", "soybean")
            crop_pixels = 0
            field_pixels = 0
            coverage = 0.0
            crop_only_array = None
            crop_only_mean = None
            crop_only_range = None
            crop_only_rows: list[dict[str, Any]] = []
            scene_rows = peak_scene_rows_by_key[peak_key]
            if scene_rows:
                peak_array, peak_mean, peak_range, reference_path, used_rows = (
                    _compute_peak_percentile_array(
                        scene_rows, percentile=95.0, boundary_path=boundary_path
                    )
                )
                if reference_path is not None and boundary_path.exists():
                    field_pixels = _field_pixel_count(reference_path, boundary_path)
                if peak_array is not None and reference_path is not None:
                    crop_pixels = int(np.isfinite(peak_array).sum())
                    coverage = crop_pixels / field_pixels if field_pixels else 0.0
                    if (
                        crop_pixels < _PEAK_CROP_MIN_PIXELS
                        or coverage < _PEAK_CROP_MIN_FRACTION
                    ):
                        # A handful of crop pixels is not a usable crop card (for
                        # example a pasture field with one sliver soybean year).
                        # Keep the crop-masked array as a QA asset, then fall back
                        # to an all-vegetation card for the main report.
                        crop_only_array = peak_array
                        crop_only_mean = peak_mean
                        crop_only_range = peak_range
                        crop_only_rows = list(used_rows)
                        peak_array = None
                        reason = (
                            f"only {crop_pixels} matching {crop_lower} pixels "
                            f"({coverage:.1%} of field) in the peak window; "
                            "using the all-vegetation peak fallback"
                        )
                if peak_array is None:
                    # No usable crop pixels in the peak window (for example a
                    # wheat or pasture field). Show one real all-vegetation map.
                    if peak_all_veg_cache is None:
                        (
                            all_veg_array,
                            all_veg_mean,
                            all_veg_range,
                            all_veg_ref,
                            all_veg_rows,
                        ) = _compute_peak_percentile_array(
                            peak_union_rows,
                            percentile=95.0,
                            mask_to_crop=False,
                            boundary_path=boundary_path,
                        )
                        peak_all_veg_cache = {
                            "array": all_veg_array,
                            "mean": all_veg_mean,
                            "range": all_veg_range,
                            "reference": all_veg_ref,
                            "rows": all_veg_rows,
                        }
                    peak_array = peak_all_veg_cache["array"]
                    peak_mean = peak_all_veg_cache["mean"]
                    peak_range = peak_all_veg_cache["range"]
                    reference_path = peak_all_veg_cache["reference"]
                    used_rows = list(peak_all_veg_cache["rows"])
                    if peak_array is not None:
                        basis = "all_vegetation"
                        display_title = "95th %ile peak NDVI — all vegetation"
                        if reason is None:
                            reason = (
                                f"no {crop_lower} pixels in the peak window; "
                                "using the all-vegetation peak fallback"
                            )
                    else:
                        reason = "peak-window scenes were available but no valid NDVI pixels overlapped the field"
            else:
                reason = "no peak-window scenes were available for this crop"

            tif_output = outputs[f"{peak_key}_tif"]
            if peak_array is not None and reference_path is not None:
                write_single_band_raster(tif_output, peak_array, reference_path)

            if basis == "all_vegetation":
                peak_all_veg_used = True
                png_output = outputs["all_vegetation_peak_95"]
                # Remove the misleading crop-named PNG from older runs.
                outputs[peak_key].unlink(missing_ok=True)
            else:
                png_output = outputs[peak_key]

            qa_output = outputs[f"{peak_key}_crop_only"]
            crop_only_output_png = None
            if (
                basis == "all_vegetation"
                and crop_only_array is not None
                and crop_only_mean is not None
                and crop_only_range is not None
                and crop_pixels > 0
            ):
                qa_rendered = _render_raster_card(
                    qa_output,
                    f"{spec['label']} — sparse crop pixels (QA)",
                    [
                        (
                            f"Only {crop_pixels} matching {crop_lower} pixels "
                            f"({coverage:.1%} of field) in the crop-masked peak window"
                        ),
                        (
                            "Years included: "
                            + ", ".join(
                                str(year)
                                for year in sorted(
                                    {int(row["year"]) for row in crop_only_rows}
                                )
                            )
                        ),
                        "View cropped to crop-pixel extent; QA asset only, excluded from farm reports",
                    ],
                    _crop_to_valid(crop_only_array),
                    crop_only_mean,
                    crop_only_range,
                    fallback_message=(
                        f"No crop-masked peak NDVI pixels were available for {crop_lower}."
                    ),
                    vmin=0.3,
                    vmax=0.9,
                )
                if qa_rendered:
                    crop_only_output_png = str(qa_output.relative_to(_REPO))
            else:
                qa_output.unlink(missing_ok=True)

            window = (
                peak_union_window if basis == "all_vegetation" else spec["doy_window"]
            )
            subtitle_lines = [
                (
                    f"Peak window DOY {window[0]}-{window[1]} | "
                    f"Scenes used: {len(used_rows)}"
                ),
                (
                    "Years included: "
                    + (
                        ", ".join(
                            str(year)
                            for year in sorted({int(row["year"]) for row in used_rows})
                        )
                        if used_rows
                        else "none"
                    )
                ),
                (
                    "Per-pixel 95th percentile across peak-window NDVI scenes clipped to matching CDL crop pixels"
                    if basis == "crop"
                    else "No usable crop pixels were found, so the 95th percentile is computed across all field pixels"
                ),
            ]

            rendered = _render_raster_card(
                png_output,
                display_title,
                subtitle_lines,
                peak_array,
                peak_mean,
                peak_range,
                fallback_message=(
                    f"This field does not yet have usable peak-window NDVI scenes for {crop_lower} pixels."
                ),
                vmin=0.3,
                vmax=0.9,
            )
            summary_payload["cards"][peak_key] = {
                "status": "available" if rendered else "unavailable",
                "title": display_title,
                "basis": basis,
                "crop_name": spec["crop_name"],
                "doy_window": list(cast(tuple[int, int], window)),
                "scene_count": len(used_rows),
                "years": sorted({int(row["year"]) for row in used_rows}),
                "sources": sorted({str(row["sensor"]) for row in used_rows}),
                "crop_pixel_count": crop_pixels,
                "field_pixel_count": field_pixels,
                "crop_coverage": round(crop_pixels / field_pixels, 4)
                if field_pixels
                else None,
                "output_png": str(png_output.relative_to(_REPO)),
                "crop_only_output_png": crop_only_output_png,
                "output_tif": str(tif_output.relative_to(_REPO))
                if tif_output.exists()
                else None,
                "reason": reason,
                "mean_ndvi": peak_mean,
            }

        if not peak_all_veg_used:
            outputs["all_vegetation_peak_95"].unlink(missing_ok=True)

        (
            rendered_current,
            current_reason,
            cumulative_crop_years,
            cumulative_basis,
        ) = _render_current_cumulative_card(
            outputs["current_season_cumulative"],
            selected_scene_rows,
            join_df,
            boundary_path,
        )
        summary_payload["cards"]["current_season_cumulative"] = {
            "status": "available" if rendered_current else "unavailable",
            "title": (
                "Cumulative NDVI by crop and year"
                if cumulative_basis == "crop"
                else "Cumulative NDVI by year (all vegetation)"
            ),
            "basis": cumulative_basis,
            "scene_count": len(selected_scene_rows),
            "crop_years": {
                crop_name.lower().replace("beans", "bean"): years
                for crop_name, years in cumulative_crop_years.items()
            },
            "sources": sorted({str(row["sensor"]) for row in selected_scene_rows}),
            "output_png": str(outputs["current_season_cumulative"].relative_to(_REPO)),
            "reason": current_reason,
        }

        outputs["summary"].parent.mkdir(parents=True, exist_ok=True)
        outputs["summary"].write_text(
            json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8"
        )
        manifest.status = "complete"
        manifest.write(manifest_dir / f"ndvi_card_render_{field_id}.json")

    print("\n✓ NDVI card generation complete")


if __name__ == "__main__":
    main()
