#!/usr/bin/env python3
"""Render one NDVI season card per AU paddock.

The US card is gated on a CDL raster and speaks in corn/soy terms. This card is
crop-agnostic: the crop label is printed but never drives the layout, because
the rotation changes every year on all six farms.

Panels
    A  month x year heat grid of the monthly median NDVI, annotated. A month
       that failed the paddock-visibility gate stays grey and says why.
    B  the same numbers as season curves, one line per year.
    C  season integral (NDVI-months) per year, with crop and peak month.

Nothing is interpolated: a grey cell is a month with no usable scene, and the
season integral counts only observed months.
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
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.au_common import (  # noqa: E402
    FARM_NAME_BY_SLUG,
    growing_season,
    load_field_names,
    season_months,
)
from lib.paths import (  # noqa: E402
    farm_dir,
    field_feature_path,
    field_summary_path,
    field_tables_dir,
)

MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

YEAR_COLORS = ("#1d4ed8", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#0369a1")


def _crop_label(entry: dict) -> str:
    """Crop for a year, with ``not managed`` kept distinct from a blank crop."""
    if str(entry.get("status") or "") == "not_managed":
        return "not managed"
    crop = entry.get("crop")
    if isinstance(crop, str) and crop.strip():
        return crop.strip()
    return "—"


def _load(grower: str, farm: str, slug: str):
    monthly_path = field_tables_dir(grower, farm, slug) / "ndvi_monthly_stats.csv"
    summary_path = field_summary_path(grower, farm, slug, "ndvi_yearly_summary.json")
    if not monthly_path.exists() or not summary_path.exists():
        return None, None
    monthly = pd.read_csv(monthly_path, dtype={"field_slug": str})
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return monthly, summary


def render_card(
    *,
    grower: str,
    farm: str,
    slug: str,
    name: str,
    out_png: Path,
) -> Path | None:
    monthly, summary = _load(grower, farm, slug)
    if monthly is None or not summary.get("years"):
        return None
    months = season_months(farm)
    years = sorted({int(y["year"]) for y in summary["years"]})
    years_meta = {int(y["year"]): y for y in summary["years"]}
    # not managed: the year keeps its row (the grid must still read as five
    # years) but carries no season, crop or NDVI.
    absent = {
        year for year in years
        if str(years_meta[year].get("status") or "") == "not_managed"
    }

    pivot = monthly.pivot_table(index="year", columns="month", values="ndvi_median")
    used = monthly.pivot_table(index="year", columns="month", values="used_in_season",
                               aggfunc="first")
    masked = monthly.pivot_table(index="year", columns="month", values="valid_fraction",
                                 aggfunc="first")

    fig = plt.figure(figsize=(12.2, 7.6))
    fig.patch.set_facecolor("#fbfaf8")
    outer = fig.add_gridspec(1, 2, width_ratios=[1.15, 1.0], wspace=0.24,
                             left=0.105, right=0.975, top=0.80, bottom=0.12)

    # ---------------------------------------------------------------- panel A
    inner_a = outer[0].subgridspec(1, 2, width_ratios=[1.0, 0.035], wspace=0.03)
    ax_a = fig.add_subplot(inner_a[0, 0])
    cax_a = fig.add_subplot(inner_a[0, 1])
    grid = np.full((len(years), len(months)), np.nan)
    for i, year in enumerate(years):
        if year in absent:
            continue
        for j, month in enumerate(months):
            if year in pivot.index and month in pivot.columns:
                value = pivot.loc[year, month]
                if pd.notna(value):
                    grid[i, j] = float(value)
    norm = Normalize(vmin=0.0, vmax=0.9)
    im = ax_a.imshow(grid, cmap="RdYlGn", norm=norm, aspect="auto")
    for i, year in enumerate(years):
        if year in absent:
            for j, _month in enumerate(months):
                ax_a.add_patch(Rectangle((j - 0.5, i - 0.5), 1.0, 1.0,
                                         facecolor="#ececee", edgecolor="#a1a1aa",
                                         hatch="///", linewidth=0.5, zorder=3))
            ax_a.text((len(months) - 1) / 2, i, "not managed", ha="center",
                      va="center", fontsize=7.2, color="#3f3f46", style="italic",
                      zorder=4)
            continue
        for j, month in enumerate(months):
            value = grid[i, j]
            if np.isfinite(value):
                ax_a.text(j, i, f"{value:.2f}", ha="center", va="center",
                          fontsize=7.4, color="#0b1220")
            else:
                why = "no scene"
                if year in masked.index and month in masked.columns:
                    frac = masked.loc[year, month]
                    if pd.notna(frac):
                        why = f"{frac:.0%} visible"
                ax_a.text(j, i, why, ha="center", va="center", fontsize=5.6,
                          color="#52525b", style="italic")
    ax_a.set_xticks(range(len(months)))
    ax_a.set_xticklabels([MONTH_LABELS[m - 1] for m in months], fontsize=7.6)
    ax_a.set_yticks(range(len(years)))
    ax_a.set_yticklabels(
        [f"{y}\n{_crop_label(years_meta[y])}" for y in years], fontsize=8.4
    )
    for spine in ax_a.spines.values():
        spine.set_color("#d4d4d8")
    ax_a.set_title("Monthly median NDVI — one best scene per month",
                   fontsize=9.4, loc="left", pad=7, fontweight="bold")
    cb = fig.colorbar(im, cax=cax_a)
    cb.set_label("NDVI", fontsize=7)
    cb.ax.tick_params(labelsize=6)

    # ---------------------------------------------------------------- panel B
    inner_b = outer[1].subgridspec(2, 1, height_ratios=[1.25, 1.0], hspace=0.42)
    ax_b = fig.add_subplot(inner_b[0])
    for i, year in enumerate(years):
        if year in absent:
            continue
        series = pivot.loc[year].reindex(months) if year in pivot.index else pd.Series(
            index=months, dtype="float64"
        )
        series = pd.to_numeric(series, errors="coerce").dropna()
        if series.empty:
            continue
        ax_b.plot(series.index, series.values, marker="o", ms=3.4, lw=1.6,
                  color=YEAR_COLORS[i % len(YEAR_COLORS)], label=str(year))
        peak = years_meta[year].get("peak_month")
        if peak in series.index:
            ax_b.scatter([peak], [series.loc[peak]], s=46, facecolor="none",
                         edgecolor=YEAR_COLORS[i % len(YEAR_COLORS)], lw=1.5, zorder=5)
    ax_b.set_xticks(months)
    ax_b.set_xticklabels([MONTH_LABELS[m - 1] for m in months], fontsize=7.4)
    ax_b.set_ylim(0, 0.85)
    ax_b.set_ylabel("NDVI (median)", fontsize=7.6)
    ax_b.tick_params(labelsize=7)
    ax_b.grid(alpha=0.18, lw=0.6)
    plotted = len(years) - len(absent)
    if plotted:
        ax_b.legend(fontsize=7, ncol=min(plotted, len(years)), frameon=False,
                    loc="upper left", handlelength=1.2, columnspacing=1.0)
    ax_b.set_title("Season curves — ring marks the peak month",
                   fontsize=9.4, loc="left", pad=6, fontweight="bold")
    for s in ("top", "right"):
        ax_b.spines[s].set_visible(False)

    # ---------------------------------------------------------------- panel C
    ax_c = fig.add_subplot(inner_b[1])
    integrals = [
        np.nan if y in absent else (years_meta[y].get("season_integral_ndvi_months")
                                    or np.nan)
        for y in years
    ]
    bars = ax_c.bar(range(len(years)), integrals, width=0.62,
                    color=[YEAR_COLORS[i % len(YEAR_COLORS)] for i in range(len(years))],
                    alpha=0.85)
    for bar, year in zip(bars, years, strict=True):
        meta = years_meta[year]
        height = bar.get_height()
        if not np.isfinite(height):
            continue
        observed = int(meta.get("months_observed") or 0)
        expected = int(meta.get("months_expected") or len(months))
        label = f"{height:.1f}"
        if observed < expected:
            label += f"\n{observed}/{expected} mo"
        ax_c.text(bar.get_x() + bar.get_width() / 2, height + 0.06, label,
                  ha="center", va="bottom", fontsize=6.8, color="#27272a")
    for index, year in enumerate(years):
        if year in absent:
            ax_c.text(index, 0.04, "not\nmanaged", ha="center", va="bottom",
                      fontsize=6.8, color="#52525b", style="italic")
    ax_c.set_xticks(range(len(years)))
    # An absent year is already annotated inside the panel; repeating it on the
    # tick label read as two different statements about the same year.
    ax_c.set_xticklabels(
        [str(y) if y in absent else f"{y}\n{_crop_label(years_meta[y])}"
         for y in years],
        fontsize=7.6,
    )
    # An absent year contributes no bar, so autoscaling would drop its slot and
    # push the "not managed" note outside the axes.
    ax_c.set_xlim(-0.55, len(years) - 0.45)
    ax_c.set_ylabel("NDVI-months", fontsize=7.6)
    ax_c.tick_params(labelsize=7)
    ax_c.grid(axis="y", alpha=0.18, lw=0.6)
    ax_c.set_title("Season integral — observed months only",
                   fontsize=9.4, loc="left", pad=6, fontweight="bold")
    for s in ("top", "right"):
        ax_c.spines[s].set_visible(False)

    # ---------------------------------------------------------------- header
    # The poster names the same farm "Brentwood (lease)"; the card used the slug,
    # so a lease block read as if it were owned.
    farm_label = FARM_NAME_BY_SLUG.get(farm, farm.replace("-", " ").title())
    fig.text(0.055, 0.945, f"{name}", fontsize=17, fontweight="bold", color="#0b1220")
    fig.text(0.055, 0.905,
             f"{farm_label}   |   Growing season {growing_season(farm)}   |   "
             f"{years[0]}–{years[-1]}   |   Sentinel-2 L2A, 10 m",
             fontsize=9.2, color="#3f3f46")
    gaps = [y for y in years if y not in absent
            and int(years_meta[y].get("months_observed") or 0)
            < int(years_meta[y].get("months_expected") or len(months))]
    foot = ("Grey cells are months with no usable scene under the paddock-visibility gate "
            "(≥ 50 % of the paddock clear); they are never filled. A hatched year was "
            "not managed.")
    if gaps:
        foot += f"  Incomplete seasons: {', '.join(str(y) for y in gaps)}."
    fig.text(0.055, 0.045, foot, fontsize=7.2, color="#52525b", wrap=True)
    fig.text(0.055, 0.018,
             "Source: Sentinel-2 L2A surface reflectance, SCL cloud/shadow mask. "
             "Statistics over the exact paddock polygon.",
             fontsize=6.6, color="#71717a")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=170, facecolor=fig.get_facecolor())
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
            args.out_dir / f"ndvi_card_{row.paddock_name.replace(' ', '_')}.png"
            if args.out_dir
            else field_feature_path(args.grower_slug, args.farm_slug,
                                    str(row.field_slug), "ndvi_card.png")
        )
        written = render_card(
            grower=args.grower_slug, farm=args.farm_slug, slug=str(row.field_slug),
            name=str(row.paddock_name), out_png=out,
        )
        print(written or f"skip  {row.field_slug} (no NDVI tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
