#!/usr/bin/env python3
"""Compose one A3-style field sheet per AU paddock.

The poster is the single page a grower reads: where the paddock is, what the
ground does, what the soil is, and how the season behaved across the five
years. Every panel is drawn natively at poster resolution rather than pasting
the card and map PNGs, so the labels stay legible in print.

Panel sources are the same numbers the standalone card and map sheet use:
``ndvi_monthly_stats.csv``, ``ndvi_yearly_summary.json`` and the SLGA/ASF
0-30 cm rasters.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_AU_DIR = Path(__file__).resolve().parent
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib", _AU_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import basemap as _bm  # noqa: E402
import field_frame as _fflib  # noqa: E402
import palettes  # noqa: E402
import render_au_ndvi_cards as _cards  # noqa: E402
import render_au_soil_maps as _soilmaps  # noqa: E402
from field_frame import crs_note, decorate, frame_for  # noqa: E402
from lib.au_common import (  # noqa: E402
    FARM_NAME_BY_SLUG,
    county_label,
    growing_season,
    load_field_names,
    region_for_farm,
    season_months,
)
from lib.paths import (  # noqa: E402
    farm_dir,
    field_boundary_path,
    field_feature_path,
)

MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
YEAR_COLORS = ("#1d4ed8", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#0369a1")
OFFLINE = os.environ.get("AG_OFFLINE") == "1"


def _fit_title(ax, head: str, tail: str, size: float, min_size: float = 7.6) -> None:
    """Draw a left-aligned panel title that always fits its axes width.

    A character count cannot predict this: a soil panel is as wide as the
    paddock is, so the same title fits on a square block and ran off the page on
    a narrow one. Try one line, then wrap the observed range onto a second line,
    and only then shrink - a wrapped 9 pt title reads better than a 6 pt one.
    Options go up, above the map, so nothing is covered.
    """
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    available = ax.get_window_extent(renderer=renderer).width

    candidates = [f"{head}   {tail}", f"{head}\n{tail}"] if tail else [head]
    title = None
    for text in candidates:
        title = ax.set_title(text, fontsize=size, pad=5, loc="left")
        fig.canvas.draw()
        drawn = title.get_window_extent(renderer=renderer).width
        if drawn <= available or not tail:
            break
    if title is None:
        return
    drawn = title.get_window_extent(renderer=renderer).width
    if drawn > available and drawn > 0:
        title.set_fontsize(max(min_size, size * available / drawn))


def _soil_panel(ax, ff, path: Path, *, code: str, label: str, unit: str,
                fontsize: float = 9.0) -> None:
    if not path.exists():
        ax.text(0.5, 0.5, f"no {label.split(' ')[0].lower()} raster",
                ha="center", va="center", transform=ax.transAxes, fontsize=fontsize)
        ax.set_xticks([])
        ax.set_yticks([])
        return
    import rasterio

    with rasterio.open(path) as ds:
        arr, ext = _fflib.reproject_into_frame(ds, ff, dst_res=max(float(ds.res[0]), 5.0))
    ny, nx = arr.shape
    if not OFFLINE:
        _bm.draw(ax, ff, ext, ny, nx)
    inside = np.asarray(arr.compressed(), dtype="float64")
    cmap, norm, ticks = palettes.soil_scale(code, inside)
    ticks = palettes.readable_ticks(norm, ticks)
    im = ax.imshow(arr, cmap=cmap, norm=norm, alpha=0.86, zorder=1,
                   extent=[ext[0], ext[1], ext[2], ext[3]], origin="upper")
    ff.geometry.boundary.plot(ax=ax, color="#111827", lw=1.2, zorder=4)
    decorate(ax, ff, arrow_r=0.085, arrow_fs=11.0, callout=False,
             arrow_xy=(0.88, 0.86))
    mean_v = float(np.nanmean(inside)) if inside.size else float("nan")
    # The observed spread travels with the mean: a flat panel is a uniform
    # paddock, and the numbers prove it instead of leaving the reader guessing
    # whether the colour scale is broken. It lives on its own line when the
    # panel is narrower than the sentence.
    head = f"{label}   mean {mean_v:.2f} {unit}"
    tail = (f"({inside.min():.2f}–{inside.max():.2f})" if inside.size else "")
    _fit_title(ax, head, tail, fontsize)
    cb = ax.figure.colorbar(im, ax=ax, orientation="horizontal", fraction=0.05,
                            pad=0.12, aspect=26,
                            **({"ticks": ticks} if ticks else {}))
    cb.set_label(unit, fontsize=7.2)
    cb.ax.tick_params(labelsize=6.4)


def _terrain_panel(ax, ff, path: Path, fontsize: float = 9.0) -> None:
    if not path.exists():
        ax.text(0.5, 0.5, "no DEM", ha="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        return
    import rasterio

    with rasterio.open(path) as ds:
        arr, ext = _fflib.reproject_into_frame(ds, ff, dst_res=max(float(ds.res[0]), 5.0))
    ny, nx = arr.shape
    if not OFFLINE:
        _bm.draw(ax, ff, ext, ny, nx)
    finite = np.asarray(arr.compressed(), dtype="float64")
    cmap, norm, relief, _klass = palettes.dem_scale(finite if finite.size else np.array([0.0, 1.0]))
    from matplotlib.colors import LightSource

    hs = np.ma.masked_invalid(
        LightSource().hillshade(np.ma.filled(arr, np.nan), vert_exag=2.0)
    )
    ax.imshow(hs, cmap="gray", vmin=0, vmax=1, alpha=0.55, zorder=0,
              extent=[ext[0], ext[1], ext[2], ext[3]], origin="upper")
    im = ax.imshow(arr, cmap=cmap, norm=norm, alpha=0.62, zorder=1,
                   extent=[ext[0], ext[1], ext[2], ext[3]], origin="upper")
    ff.geometry.boundary.plot(ax=ax, color="#111827", lw=1.2, zorder=4)
    decorate(ax, ff, arrow_r=0.085, arrow_fs=11.0, callout=False,
             arrow_xy=(0.88, 0.86))
    ax.set_title(f"Terrain   relief {relief:.0f} m", fontsize=fontsize, pad=5, loc="left")
    cb = ax.figure.colorbar(im, ax=ax, orientation="horizontal", fraction=0.05,
                            pad=0.12, aspect=26)
    cb.set_label("elevation (m)", fontsize=7.2)
    cb.ax.tick_params(labelsize=6.4)


def render_poster(
    *,
    grower: str,
    farm: str,
    slug: str,
    name: str,
    out_png: Path,
    area_ha: float | None = None,
    county: str = "",
) -> Path | None:
    import geopandas as gpd

    monthly, summary = _cards._load(grower, farm, slug)
    if monthly is None or not summary.get("years"):
        return None

    boundary = gpd.read_file(field_boundary_path(grower, farm, slug))
    if area_ha is None and len(boundary):
        area_ha = float(boundary.to_crs(3577).area.sum() / 10_000.0)
    if not county and len(boundary):
        county = str((boundary.iloc[0].get("county") or ""))
    boundary = boundary.to_crs(4326)
    ff = frame_for(boundary, panel_h_in=3.0, panel_w_in=3.0)

    months = season_months(farm)
    years = sorted({int(y["year"]) for y in summary["years"]})
    meta = {int(y["year"]): y for y in summary["years"]}
    # A year the land was not under this management has no season to show: its
    # NDVI is context, not production, and painting it would read as a crop we
    # ran. The row stays so the five-year grid keeps its meaning.
    absent = {
        year for year in years
        if str(meta[year].get("status") or "") == "not_managed"
    }
    pivot = monthly.pivot_table(index="year", columns="month", values="ndvi_median")
    masked = monthly.pivot_table(index="year", columns="month", values="valid_fraction",
                                 aggfunc="first")

    fig = plt.figure(figsize=(11.7, 16.5))
    fig.patch.set_facecolor("#fbfaf8")
    outer = fig.add_gridspec(
        4, 1, height_ratios=[0.60, 1.78, 1.30, 1.12],
        hspace=0.34, left=0.055, right=0.955, top=0.965, bottom=0.098,
    )

    # ------------------------------------------------------------------ header
    ax_head = fig.add_subplot(outer[0])
    ax_head.axis("off")
    ax_head.text(0.0, 0.92, name, fontsize=25, fontweight="bold", color="#0b1220",
                 transform=ax_head.transAxes)
    sub = " · ".join(
        part for part in (
            FARM_NAME_BY_SLUG.get(farm, farm),
            region_for_farm(FARM_NAME_BY_SLUG.get(farm, farm)),
            county_label(county),
            f"{area_ha:.1f} ha" if area_ha else "",
        ) if part
    )
    ax_head.text(0.0, 0.66, sub, fontsize=11.6, color="#3f3f46",
                 transform=ax_head.transAxes)
    ax_head.text(
        0.0, 0.40,
        f"Seasons {years[0]}–{years[-1]}   |   Growing season "
        f"{growing_season(farm)}   |   "
        f"Sentinel-2 L2A 10 m + SLGA/ASF soil   |   Copernicus GLO-30 terrain",
        fontsize=9.6, color="#52525b", transform=ax_head.transAxes,
    )
    def _crop_label(year: int) -> str:
        entry = meta[year]
        if str(entry.get("status") or "") == "not_managed":
            return "not managed"
        crop = entry.get("crop")
        label = str(crop) if isinstance(crop, str) and crop else "—"
        if entry.get("use_type") == "dual_purpose":
            label += " (dual purpose)"
        return label

    crop_line = "   ".join(f"{y}: {_crop_label(y)}" for y in years)
    ax_head.text(0.0, 0.12, crop_line, fontsize=9.2, color="#1f2937",
                 transform=ax_head.transAxes)
    ax_head.plot([0, 1], [0.02, 0.02], color="#d4d4d8", lw=1.0,
                 transform=ax_head.transAxes, clip_on=False)

    # ------------------------------------------------------------------- maps
    sub_maps = outer[1].subgridspec(1, 3, wspace=0.30)
    ax_terrain = fig.add_subplot(sub_maps[0, 0])
    ax_clay = fig.add_subplot(sub_maps[0, 1])
    ax_ph = fig.add_subplot(sub_maps[0, 2])
    soil = _soilmaps._soil_dir(grower, farm, slug)
    _terrain_panel(ax_terrain, ff, _soilmaps._dem_path(grower, farm, slug), fontsize=10)
    _soil_panel(ax_clay, ff, soil / "clay_0_30cm.tif", code="clay",
                label="Clay 0–30 cm", unit="%", fontsize=10)
    _soil_panel(ax_ph, ff, soil / "phcalciumchloride_0_30cm.tif", code="phcac2",
                label="pH (CaCl$_2$) 0–30 cm", unit="pH", fontsize=10)

    # -------------------------------------------------------------- NDVI grid
    grid_ax = outer[2].subgridspec(1, 2, width_ratios=[1.0, 0.028], wspace=0.02)
    ax_g = fig.add_subplot(grid_ax[0, 0])
    cax = fig.add_subplot(grid_ax[0, 1])
    grid = np.full((len(years), len(months)), np.nan)
    for i, year in enumerate(years):
        if year in absent:
            continue
        for j, month in enumerate(months):
            if year in pivot.index and month in pivot.columns:
                value = pivot.loc[year, month]
                if pd.notna(value):
                    grid[i, j] = float(value)
    norm = Normalize(0.0, 0.9)
    im = ax_g.imshow(grid, cmap="RdYlGn", norm=norm, aspect="auto")
    for i, year in enumerate(years):
        if year in absent:
            for j, _month in enumerate(months):
                ax_g.add_patch(Rectangle((j - 0.5, i - 0.5), 1.0, 1.0,
                                         facecolor="#ececee", edgecolor="#a1a1aa",
                                         hatch="///", linewidth=0.5, zorder=3))
            ax_g.text((len(months) - 1) / 2, i, "not managed", ha="center",
                      va="center", fontsize=8.4, color="#3f3f46", style="italic",
                      zorder=4)
            continue
        for j, _month in enumerate(months):
            value = grid[i, j]
            if np.isfinite(value):
                ax_g.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=9)
            else:
                ax_g.text(j, i, "no data", ha="center", va="center", fontsize=7,
                          color="#52525b", style="italic")
    ax_g.set_xticks(range(len(months)))
    ax_g.set_xticklabels([MONTH_LABELS[m - 1] for m in months], fontsize=8.4)
    ax_g.set_yticks(range(len(years)))
    ax_g.set_yticklabels([str(y) for y in years], fontsize=9.4)
    ax_g.set_title("Monthly median NDVI — one best cloud-screened scene per month",
                   fontsize=11, loc="left", pad=8, fontweight="bold")
    cb = fig.colorbar(im, cax=cax)
    cb.set_label("NDVI", fontsize=8)
    cb.ax.tick_params(labelsize=7)

    # ------------------------------------------------------- curves + integral
    lower = outer[3].subgridspec(1, 2, width_ratios=[1.35, 1.0], wspace=0.24)
    ax_c = fig.add_subplot(lower[0, 0])
    for i, year in enumerate(years):
        if year in absent:
            continue
        series = pivot.loc[year].reindex(months) if year in pivot.index else pd.Series(
            index=months, dtype="float64"
        )
        series = pd.to_numeric(series, errors="coerce").dropna()
        if series.empty:
            continue
        ax_c.plot(series.index, series.values, marker="o", ms=4.2, lw=1.9,
                  color=YEAR_COLORS[i % len(YEAR_COLORS)], label=str(year))
    ax_c.set_xticks(months)
    ax_c.set_xticklabels([MONTH_LABELS[m - 1] for m in months], fontsize=8)
    ax_c.set_ylim(0, 0.85)
    ax_c.set_ylabel("NDVI (median)", fontsize=8.6)
    ax_c.tick_params(labelsize=8)
    ax_c.grid(alpha=0.18, lw=0.6)
    plotted = len(years) - len(absent)
    if plotted:
        ax_c.legend(fontsize=8, ncol=min(plotted, len(years)), frameon=False,
                    loc="upper left")
    ax_c.set_title("Season curves", fontsize=10.4, loc="left", pad=6, fontweight="bold")
    for s in ("top", "right"):
        ax_c.spines[s].set_visible(False)

    ax_i = fig.add_subplot(lower[0, 1])
    integrals = [
        np.nan if year in absent else (meta[year].get("season_integral_ndvi_months")
                                       or np.nan)
        for year in years
    ]
    bars = ax_i.bar(range(len(years)), integrals, width=0.6,
                    color=[YEAR_COLORS[i % len(YEAR_COLORS)] for i in range(len(years))],
                    alpha=0.86)
    for bar, year in zip(bars, years, strict=True):
        height = bar.get_height()
        if not np.isfinite(height):
            continue
        observed = int(meta[year].get("months_observed") or 0)
        expected = int(meta[year].get("months_expected") or len(months))
        label = f"{height:.1f}" + (f"\n{observed}/{expected} mo" if observed < expected else "")
        ax_i.text(bar.get_x() + bar.get_width() / 2, height + 0.06, label,
                  ha="center", va="bottom", fontsize=7.6, color="#27272a")
    for index, year in enumerate(years):
        if year in absent:
            ax_i.text(index, 0.04, "not\nmanaged", ha="center", va="bottom",
                      fontsize=7.4, color="#52525b", style="italic")
    ax_i.set_xticks(range(len(years)))
    ax_i.set_xticklabels([str(y) for y in years], fontsize=8)
    # Pin the year slots: an absent year contributes no bar, so autoscaling
    # would drop its slot and push the "not managed" note outside the axes.
    ax_i.set_xlim(-0.55, len(years) - 0.45)
    ax_i.set_ylabel("NDVI-months", fontsize=8.6)
    ax_i.tick_params(labelsize=8)
    ax_i.grid(axis="y", alpha=0.18, lw=0.6)
    ax_i.set_title("Season integral (observed months)", fontsize=10.4, loc="left",
                   pad=6, fontweight="bold")
    for s in ("top", "right"):
        ax_i.spines[s].set_visible(False)

    # ------------------------------------------------------------------ footer
    numbers = _soilmaps._field_numbers(grower, farm, slug)
    texture = str(numbers.get("texture_usda_0_30", "") or "unknown")
    fig.text(
        0.055, 0.064,
        f"Soil 0–30 cm: {texture}, clay {numbers.get('CLAY_0_30', float('nan')):.0f} %, "
        f"pH {numbers.get('PHCALCIUMCHLORIDE_0_30', float('nan')):.2f}, "
        f"CEC {numbers.get('CEC_0_30', float('nan')):.0f} meq/100 g, "
        f"organic carbon {numbers.get('CARBON_0_30', float('nan')):.2f} %, "
        f"stock {numbers.get('carbon_stock_t_ha_0_100', float('nan')):.0f} t C/ha (0–100 cm)",
        fontsize=8.2, color="#27272a",
    )
    import textwrap

    note_lines = [
        "Grey NDVI cells are months without a scene passing the paddock-visibility "
        "gate; they are reported, never filled.",
        "A hatched year was not managed: no crop, no NDVI, no integral — only the "
        "land's static attributes are shown.",
        crs_note(ff, full=bool(ff.rotated)),
    ]
    note = "\n".join(
        "\n".join(textwrap.wrap(line, width=150)) for line in note_lines
    )
    fig.text(0.055, 0.012, note, fontsize=6.4, color="#71717a", va="bottom")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG", ""))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG", ""))
    parser.add_argument("--field", default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.grower_slug or not args.farm_slug:
        raise SystemExit("AG_GROWER_SLUG and AG_FARM_SLUG are required")

    names = load_field_names(args.grower_slug, args.farm_slug)
    if args.field:
        names = names[names["field_slug"].astype(str) == str(args.field)]
    if names.empty:
        raise SystemExit("no fields to render")
    _ = farm_dir(args.grower_slug, args.farm_slug)

    for row in names.itertuples(index=False):
        out = (
            args.out_dir / f"poster_{row.paddock_name.replace(' ', '_')}.png"
            if args.out_dir
            else field_feature_path(args.grower_slug, args.farm_slug,
                                    str(row.field_slug), "field_poster.png")
        )
        written = render_poster(
            grower=args.grower_slug, farm=args.farm_slug, slug=str(row.field_slug),
            name=str(row.paddock_name), out_png=out,
        )
        print(written or f"skip  {row.field_slug} (no NDVI tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
