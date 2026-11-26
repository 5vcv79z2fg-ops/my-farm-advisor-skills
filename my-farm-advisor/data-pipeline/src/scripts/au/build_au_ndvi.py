#!/usr/bin/env python3
"""Build per-field NDVI composites and season summaries for an AU farm.

The US stage rolls NDVI up by CDL crop (corn/soy) and gates every card on a
CDL raster. The AU branch has no CDL: the grower's own crop records stand in,
and the card is crop-agnostic. What both share is the unit of work - one
Sentinel-2 scene per month, a seasonal composite per field-year.

For every field-year this writes:

    derived/features/ndvi_year_<year>_composite.tif   pixel-wise median, 10 m
    derived/tables/ndvi_monthly_stats.csv             one row per month
    derived/summaries/ndvi_yearly_summary.json        season integral + peak

Farm rollups:

    derived/tables/ndvi_monthly_stats.csv
    derived/tables/ndvi_field_year_summary.csv

Statistics come from the exact paddock polygon, never the buffered clip, and
a month with no usable scene is reported as missing. Nothing is interpolated:
the season integral counts only the months actually observed.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))
if str(_SCRIPTS_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT / "lib"))

from lib.au_common import (  # noqa: E402
    env_slugs,
    load_field_crops,
    load_field_names,
    load_fields,
    load_inventory,
    season_months,
    season_window,
    write_json,
)
from lib.paths import (  # noqa: E402
    DATA_ROOT,
    farm_tables_dir,
    field_feature_path,
    field_satellite_dir,
    field_summary_path,
    field_tables_dir,
)

# A month enters the season statistics only when the paddock itself is at least
# half visible. This is the honest gate: scene-level cloud cover describes a
# 110 km tile, while this number is measured on the paddock's own pixels.
MIN_VALID_FRACTION = 0.5


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _scene_rows(grower: str, farm: str, field_slug: str, months: tuple[int, ...]):
    """Every candidate scene inside the season window, by month.

    More than one candidate per month is expected: the download stage keeps
    them because the STAC cloud figure describes a whole 110 km tile, not this
    paddock. The caller decides which candidate is usable.
    """
    manifest = field_satellite_dir(grower, farm, field_slug) / "sentinel" / "manifest.json"
    if not manifest.exists():
        return pd.DataFrame()
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for entry in doc.get("years", []):
        if entry.get("status") != "complete":
            continue
        year = int(entry["year"])
        for scene in entry.get("scenes", []):
            date = str(scene.get("scene_date", ""))
            if not date:
                continue
            month = int(date[5:7])
            if month not in months:
                continue
            ndvi_rel = scene.get("ndvi_tif")
            if not ndvi_rel:
                continue
            path = DATA_ROOT / str(ndvi_rel)
            if not path.exists():
                continue
            cloud = float(scene.get("cloud_cover") or 100.0)
            rows.append(
                {
                    "year": year,
                    "month": month,
                    "scene_date": date,
                    "scene_id": scene.get("scene_id", ""),
                    "cloud_cover": cloud,
                    "ndvi_path": path,
                }
            )
    return pd.DataFrame(rows)


def _polygon_mask(dataset, geom) -> np.ndarray:
    return rasterize(
        [(geom, 1)],
        out_shape=(dataset.height, dataset.width),
        transform=dataset.transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)


def _polygon_values(path: Path, geom) -> tuple[np.ndarray, dict]:
    """Polygon statistics plus the fraction of the clip that was masked out.

    The masked fraction is the evidence we actually control: Sentinel-2 SCL
    cloud/shadow pixels are NaN in the NDVI raster, so it reports how much of
    the field's neighbourhood the provider's mask removed, independently of
    whatever scene-level cloud metadata did or did not travel with the item.
    """
    with rasterio.open(path) as ds:
        data = ds.read(1, masked=True)
        mask = _polygon_mask(ds, geom)
        values = np.asarray(data[mask].compressed(), dtype="float64")
        total = int(ds.height * ds.width)
        finite = int(np.isfinite(np.asarray(data.filled(np.nan), dtype="float64")).sum())
    values = values[np.isfinite(values)]
    masked_fraction = 1.0 - (finite / total) if total else np.nan
    if values.size == 0:
        return values, {
            "median": np.nan,
            "p10": np.nan,
            "p90": np.nan,
            "n_px": 0,
            "masked_fraction": masked_fraction,
        }
    return values, {
        "median": float(np.median(values)),
        "p10": float(np.percentile(values, 10)),
        "p90": float(np.percentile(values, 90)),
        "n_px": int(values.size),
        "masked_fraction": masked_fraction,
    }


def _polygon_pixel_count(path: Path, geom) -> int:
    with rasterio.open(path) as ds:
        return int(_polygon_mask(ds, geom).sum())


def _align(path: Path, reference: rasterio.DatasetReader) -> np.ndarray:
    destination = np.full((reference.height, reference.width), np.nan, dtype="float32")
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=reference.transform,
            dst_crs=reference.crs,
            resampling=Resampling.bilinear,
            src_nodata=np.nan,
            dst_nodata=np.nan,
        )
    return destination


def _write_median_composite(output: Path, paths: list[Path]) -> None:
    from raster_rules import cog_profile  # pyright: ignore[reportMissingImports]

    with rasterio.open(paths[0]) as reference:
        stack = [_align(path, reference) for path in paths]
        profile = reference.profile.copy()
    stacked = np.stack(stack, axis=0)
    composite = np.full(stacked.shape[1:], np.nan, dtype="float32")
    valid = np.isfinite(stacked).sum(axis=0) > 0
    with np.errstate(all="ignore"):
        median = np.nanmedian(stacked, axis=0)
    composite[valid] = median[valid].astype("float32")

    profile.update(dtype=rasterio.float32, count=1, nodata=np.nan, compress="lzw")
    output.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output, "w", **cog_profile(profile, resampling="average")) as dst:
        dst.write(composite, 1)


def main() -> int:
    grower, farm = env_slugs()
    months = season_months(farm)
    start_md, end_md = season_window(farm)
    inventory = load_inventory(grower, farm)
    names = load_field_names(grower, farm)
    crops = load_field_crops(grower, farm)
    fields = load_fields(grower, farm)
    geometry = {str(row.field_id): row.geometry for row in fields.itertuples(index=False)}
    name_by_slug = dict(zip(names["field_slug"].astype(str), names["paddock_name"], strict=False))
    region_by_slug = dict(zip(names["field_slug"].astype(str), names["region"], strict=False))
    farm_by_slug = dict(zip(names["field_slug"].astype(str), names["farm"], strict=False))
    crop_lookup = {
        (str(row.field_slug), int(row.year)): row
        for row in crops.itertuples(index=False)
    }

    monthly_rows: list[dict] = []
    summary_rows: list[dict] = []
    missing: list[str] = []

    for slug in inventory["field_slug"].astype(str):
        geom = geometry.get(slug)
        if geom is None:
            missing.append(f"{slug}: no boundary geometry")
            continue
        scenes = _scene_rows(grower, farm, slug, months)
        if scenes.empty:
            missing.append(f"{slug}: no Sentinel scenes")
            continue
        expected_px = _polygon_pixel_count(
            Path(scenes.iloc[0]["ndvi_path"]), geom
        )

        for year, group in scenes.groupby("year", sort=True):
            year = int(year)
            group = group.sort_values(["month", "cloud_cover"])
            year_rows: list[dict] = []
            usable_paths: list[Path] = []
            observed_months: list[int] = []
            for _month, month_rows in group.groupby("month", sort=True):
                evaluated = []
                for row in month_rows.itertuples(index=False):
                    _, stats = _polygon_values(row.ndvi_path, geom)
                    valid_fraction = (
                        stats["n_px"] / expected_px if expected_px else 0.0
                    )
                    evaluated.append((valid_fraction, row, stats))
                usable = [e for e in evaluated if e[0] >= MIN_VALID_FRACTION]
                # one scene per month: the candidate that actually sees this
                # paddock, not the one whose 110 km tile looked clearest
                best = (max(usable, key=lambda e: (e[0], -e[1].cloud_cover))
                        if usable else max(evaluated, key=lambda e: e[0]))
                valid_fraction, row, stats = best
                usable_flag = valid_fraction >= MIN_VALID_FRACTION
                record = {
                    "field_id": None,
                    "field_slug": slug,
                    "paddock_name": name_by_slug.get(slug, slug),
                    "farm": farm_by_slug.get(slug, farm),
                    "region": region_by_slug.get(slug, ""),
                    "year": year,
                    "month": int(row.month),
                    "scene_date": row.scene_date,
                    "scene_id": row.scene_id,
                    "cloud_cover": row.cloud_cover,
                    "ndvi_median": stats["median"] if usable else np.nan,
                    "ndvi_p10": stats["p10"] if usable else np.nan,
                    "ndvi_p90": stats["p90"] if usable else np.nan,
                    "n_px": stats["n_px"],
                    "valid_fraction": round(float(valid_fraction), 4),
                    "masked_fraction": round(float(stats["masked_fraction"]), 4),
                    "n_candidates": len(evaluated),
                    "n_usable_candidates": len(usable),
                    "used_in_season": bool(usable_flag),
                    "drop_reason": ""
                    if usable_flag
                    else f"paddock only {valid_fraction:.0%} visible",
                }
                year_rows.append(record)
                monthly_rows.append(record)
                if usable_flag:
                    observed_months.append(int(row.month))
                    usable_paths.append(row.ndvi_path)

            summary_extra: dict = {}
            if usable_paths:
                composite = field_feature_path(
                    grower, farm, slug, f"ndvi_year_{year}_composite.tif"
                )
                _write_median_composite(composite, usable_paths)
                _, composite_stats = _polygon_values(composite, geom)
                summary_extra = {
                    "composite_median_ndvi": composite_stats["median"],
                    "composite_p10": composite_stats["p10"],
                    "composite_p90": composite_stats["p90"],
                    "composite_tif": str(composite.relative_to(DATA_ROOT)),
                }
            else:
                summary_extra = {
                    "composite_median_ndvi": np.nan,
                    "composite_p10": np.nan,
                    "composite_p90": np.nan,
                    "composite_tif": "",
                }

            medians = pd.Series(
                {int(r["month"]): r["ndvi_median"] for r in year_rows if r["used_in_season"]}
            ).dropna()
            peak_month = int(medians.idxmax()) if not medians.empty else None
            crop = crop_lookup.get((slug, year))

            summary_rows.append(
                {
                    "field_slug": slug,
                    "paddock_name": name_by_slug.get(slug, slug),
                    "farm": farm_by_slug.get(slug, farm),
                    "region": region_by_slug.get(slug, ""),
                    "year": year,
                    "status": getattr(crop, "status", ""),
                    "crop": getattr(crop, "crop", ""),
                    "crop_group": getattr(crop, "crop_group", ""),
                    "use_type": getattr(crop, "use_type", ""),
                    "season_start": start_md,
                    "season_end": end_md,
                    "months_expected": len(months),
                    "months_observed": len(observed_months),
                    "missing_months": ",".join(
                        str(m) for m in months if m not in observed_months
                    ),
                    "season_mean_ndvi": float(medians.mean()) if not medians.empty else np.nan,
                    "season_integral_ndvi_months": float(medians.sum())
                    if not medians.empty
                    else np.nan,
                    "peak_month": peak_month,
                    "peak_ndvi": float(medians.max()) if not medians.empty else np.nan,
                    "max_scene_masked_fraction": float(
                        pd.Series([r["masked_fraction"] for r in year_rows]).max()
                    ),
                    **summary_extra,
                    "scenes": int(len(group)),
                    "scenes": int(len(group)),
                }
            )

    if not summary_rows:
        raise SystemExit("no NDVI summaries produced")

    field_ids = dict(zip(names["field_slug"].astype(str), names["field_id"], strict=False))
    monthly = pd.DataFrame(monthly_rows)
    monthly["field_id"] = monthly["field_slug"].map(field_ids)
    monthly = monthly.sort_values(["field_slug", "year", "month"])
    summary = pd.DataFrame(summary_rows).sort_values(["field_slug", "year"])

    tables = farm_tables_dir(grower, farm)
    monthly.to_csv(tables / "ndvi_monthly_stats.csv", index=False)
    summary.to_csv(tables / "ndvi_field_year_summary.csv", index=False)

    for slug, group in summary.groupby("field_slug", sort=True):
        out = field_tables_dir(grower, farm, slug) / "ndvi_monthly_stats.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        monthly[monthly["field_slug"] == slug].to_csv(out, index=False)
        write_json(
            field_summary_path(grower, farm, slug, "ndvi_yearly_summary.json"),
            {
                "field_slug": slug,
                "paddock_name": name_by_slug.get(slug, slug),
                "farm": farm_by_slug.get(slug, farm),
                "season_window": {"start": start_md, "end": end_md},
                "years": group.drop(columns=["composite_tif"]).to_dict(orient="records"),
                "generated_at": _now(),
            },
        )

    write_json(
        Path(tables).parent / "manifests" / "ndvi_build.json",
        {
            "grower_slug": grower,
            "farm_slug": farm,
            "season_window": {"start": start_md, "end": end_md, "months": list(months)},
            "field_count": int(summary["field_slug"].nunique()),
            "field_years": int(len(summary)),
            "missing": missing,
            "generated_at": _now(),
        },
    )

    print(
        f"NDVI: {len(summary)} field-years | {summary['field_slug'].nunique()} fields | "
        f"{int(summary['scenes'].sum())} scenes -> {tables}"
    )
    gaps = summary[summary["months_observed"] < summary["months_expected"]]
    if not gaps.empty:
        print(f"  gaps: {len(gaps)} field-years with missing months")
        for row in gaps.head(5).itertuples(index=False):
            print(f"    {row.field_slug} {row.year}: missing {row.missing_months}")
    if missing:
        print(f"  warnings: {len(missing)}")
        for item in missing[:5]:
            print(f"    {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
