"""Figures for the field-level EDA.

    fig01_field_area.png         field size distribution by region
    fig02_seasonal_rainfall.png  seasonal rainfall by year and region
    fig03_crop_mix.png           crop area share by region and year
    fig04_paddock_map.png        paddocks coloured by 2026 crop
    fig05_ndvi_season.png        NDVI season shape, zone trajectory, crops, rain

Figures 2 and 5 are skipped with a message if their input tables are absent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon as MplPolygon
from shapely.affinity import scale as shapely_scale

from common import (
    REGIONS,
    YEARS,
    canonical_ids,
    geometry_for,
    load_all,
    short_crop,
)

BASE = Path(__file__).resolve().parents[1]
EXAMPLES = BASE / "examples"
FIG_DIR = EXAMPLES / "figures"

REGION_COLOUR = {
    "Harden": "#2e6f40",
    "Trungley Hall": "#b5651d",
    "Lease country": "#2f5d8a",
}

CROP_COLOUR = {
    "Wheat": "#d9a441",
    "Barley": "#b07b2a",
    "Canola": "#f2d024",
    "Narrow Leaf Lupin": "#7b6fa8",
    "Faba/Broad/Field Bean": "#5d7f5a",
    "Pasture": "#8fbf6f",
    "Not managed": "#c9c9c9",
}

CROP_ORDER = [
    "Wheat",
    "Barley",
    "Canola",
    "Narrow Leaf Lupin",
    "Faba/Broad/Field Bean",
    "Pasture",
]

SKIP_LABELS = {"T1", "T2", "T3", "T4", "T5", "D1", "C1", "C2", "C3"}

MONTH_ABBR = {
    4: "Apr", 5: "May", 6: "Jun", 7: "Jul",
    8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov",
}

plt.rcParams.update(
    {
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
)


def load_panel() -> pd.DataFrame:
    return pd.read_csv(EXAMPLES / "panel_field_year.csv")


def figure_field_area(panel: pd.DataFrame) -> Path:
    observed = panel[panel["status"] == "observed"]
    latest = (
        observed[observed["year"].isin([2025, 2026])]
        .sort_values("year")
        .groupby("field_id")
        .tail(1)
    )
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    rng = np.random.default_rng(7)

    for i, region in enumerate(REGIONS):
        vals = latest.loc[latest["region"] == region, "area_ha_geom"].values
        colour = REGION_COLOUR[region]
        ax.boxplot(
            vals,
            positions=[i],
            vert=False,
            widths=0.45,
            showfliers=False,
            patch_artist=True,
            boxprops=dict(facecolor=colour, alpha=0.18, edgecolor=colour),
            medianprops=dict(color=colour, linewidth=1.6),
            whiskerprops=dict(color=colour, linewidth=1.0),
            capprops=dict(color=colour, linewidth=1.0),
        )
        jitter = rng.normal(0, 0.055, size=len(vals))
        ax.scatter(
            vals,
            i + jitter,
            s=26,
            color=colour,
            alpha=0.85,
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )
        ax.annotate(
            f"n = {len(vals)}",
            xy=(float(np.max(vals)), i),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color=colour,
            fontweight="bold",
        )

    ax.set_yticks(range(len(REGIONS)))
    ax.set_yticklabels(REGIONS)
    ax.set_xscale("log")
    ax.set_xlabel("Field area (ha, log scale)")
    ax.set_title("Field size by management region", loc="left", fontweight="bold")
    ax.grid(axis="x", which="major", color="#e6e6e6", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.set_ylim(-0.6, len(REGIONS) - 0.4)
    fig.tight_layout()
    path = FIG_DIR / "fig01_field_area.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_crop_mix(panel: pd.DataFrame) -> Path:
    observed = panel[panel["status"] == "observed"]
    fig, axes = plt.subplots(1, 3, figsize=(10.4, 3.4), sharey=True)

    for ax, region in zip(axes, REGIONS):
        sub = observed[observed["region"] == region]
        totals = (
            sub.groupby(["year", "crop"])["area_ha_reported"]
            .sum()
            .unstack(fill_value=0)
            .reindex(columns=CROP_ORDER, fill_value=0)
            .reindex(index=list(YEARS), fill_value=0)
        )
        share = totals.div(totals.sum(axis=1).replace(0, np.nan), axis=0) * 100

        bottom = np.zeros(len(YEARS))
        for crop in CROP_ORDER:
            vals = share[crop].fillna(0).values
            if vals.sum() == 0:
                continue
            ax.bar(
                range(len(YEARS)),
                vals,
                bottom=bottom,
                width=0.68,
                color=CROP_COLOUR[crop],
                edgecolor="white",
                linewidth=0.6,
            )
            bottom += vals

        counts = sub.groupby("year").size().reindex(YEARS, fill_value=0)
        for x, year in enumerate(YEARS):
            if counts.get(year, 0) == 0:
                ax.text(
                    x, 52, "not\nmanaged", ha="center", va="center",
                    fontsize=7, color="#888888", style="italic",
                )
            else:
                ax.text(
                    x, 101.5, f"n={counts[year]}", ha="center", va="bottom",
                    fontsize=7, color="#555555",
                )

        ax.set_title(region, loc="left", fontweight="bold", color=REGION_COLOUR[region])
        ax.set_xticks(range(len(YEARS)))
        ax.set_xticklabels([str(y) for y in YEARS])
        ax.set_ylim(0, 108)
        ax.grid(axis="y", color="#ececec", linewidth=0.6)
        ax.set_axisbelow(True)

    axes[0].set_ylabel("Share of cropped area (%)")
    handles = [
        Patch(facecolor=CROP_COLOUR[c], edgecolor="white", label=short_crop(c))
        for c in CROP_ORDER
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=7,
        frameon=False,
        fontsize=8,
        bbox_to_anchor=(0.5, -0.03),
    )
    fig.suptitle(
        "Crop mix by management region and year",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    path = FIG_DIR / "fig03_crop_mix.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _polygons(geom):
    if geom is None:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


def _nice_bar(span_km: float) -> float:
    for candidate in (50, 20, 10, 5, 2, 1):
        if span_km / candidate >= 3:
            return float(candidate)
    return 1.0


def _draw_panel(
    ax,
    items,
    title,
    label_font=5.2,
    show_labels=True,
    colour_by="crop",
    north_arrow=True,
):
    xs0, ys0, xs1, ys1 = [], [], [], []
    for km_geom, crop, name, region in items:
        if colour_by == "region":
            colour = REGION_COLOUR.get(region, "#bbbbbb")
            hatch = None
            alpha = 0.92
        else:
            key = crop if crop else "Not managed"
            colour = CROP_COLOUR.get(key, "#bbbbbb")
            hatch = "///" if key == "Not managed" else None
            alpha = 0.95 if hatch is None else 0.55
        for poly in _polygons(km_geom):
            xs, ys = poly.exterior.xy
            ax.add_patch(
                MplPolygon(
                    list(zip(xs, ys)),
                    closed=True,
                    facecolor=colour,
                    edgecolor="#3a3a3a",
                    linewidth=0.5,
                    hatch=hatch,
                    alpha=alpha,
                )
            )
        b = km_geom.bounds
        xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[2]); ys1.append(b[3])

    minx, miny, maxx, maxy = min(xs0), min(ys0), max(xs1), max(ys1)
    pad = max(maxx - minx, maxy - miny) * 0.09
    # Reserve an empty band at the top so the north arrow can never land on a
    # paddock or a label, whatever the panel extent happens to be.
    top = maxy + pad * (2.9 if north_arrow else 1.2)
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad * 1.8, top)

    if show_labels:
        span_hint = max(maxx - minx, maxy - miny)
        sep = span_hint * 0.085
        step = span_hint * 0.085
        placed: list[tuple[float, float]] = []
        ordered = sorted(
            items, key=lambda t: (-t[0].centroid.y, t[0].centroid.x)
        )
        for km_geom, crop, name, region in ordered:
            if not name:
                continue
            c = km_geom.centroid
            lx, ly = c.x, c.y
            for attempt in range(14):
                if all(
                    abs(lx - px) > sep or abs(ly - py) > sep for px, py in placed
                ):
                    break
                ly -= step
                if attempt % 2 == 1:
                    lx -= sep * 0.9
            placed.append((lx, ly))
            # Keep every label inside the plotted footprint; a label pushed
            # outside the axes reads as a stray mark rather than data.
            lx = min(max(lx, minx), maxx)
            ly = min(max(ly, miny), maxy)
            ax.annotate(
                name,
                (lx, ly),
                fontsize=label_font,
                ha="center",
                va="center",
                color="#111111",
                bbox=dict(boxstyle="round,pad=0.10", fc="white", ec="none", alpha=0.72),
                zorder=6,
            )
            if abs(lx - c.x) > 1e-9 or abs(ly - c.y) > 1e-9:
                ax.annotate(
                    "",
                    xy=(c.x, c.y),
                    xytext=(lx, ly),
                    arrowprops=dict(arrowstyle="-", lw=0.4, color="#777777"),
                    zorder=5,
                )

    span = maxx - minx
    bar = _nice_bar(span)
    # Bottom-right: the corner that stays clear in every panel of this figure.
    x_bar = maxx - bar - pad * 0.15
    y_bar = miny - pad * 0.75
    ax.plot([x_bar, x_bar + bar], [y_bar, y_bar], color="#222222", linewidth=2.0)
    ax.text(x_bar + bar / 2, y_bar + pad * 0.22, f"{bar:.0f} km", ha="center", fontsize=6.2)

    if north_arrow:
        ax.annotate(
            "N",
            xy=(0.968, 0.972),
            xytext=(0.968, 0.905),
            xycoords="axes fraction",
            textcoords="axes fraction",
            ha="center",
            va="center",
            fontsize=7.5,
            fontweight="bold",
            zorder=10,
            arrowprops=dict(arrowstyle="-|>", color="#222222", lw=1.0),
        )

    ax.set_title(title, loc="left", fontweight="bold", fontsize=9)
    ax.set_aspect("equal")
    ax.grid(color="#efefef", linewidth=0.5)
    ax.tick_params(labelsize=6)
    ax.set_axisbelow(True)


def figure_map(panel: pd.DataFrame) -> Path:
    layers = load_all()
    crop_2026 = panel[panel["year"] == 2026].set_index("field_id")["crop"].to_dict()
    name_by_id = (
        panel.drop_duplicates("field_id").set_index("field_id")["paddock_name"].to_dict()
    )
    region_by_id = (
        panel.drop_duplicates("field_id").set_index("field_id")["region"].to_dict()
    )

    items = []
    for cid in canonical_ids(layers):
        geom, _ = geometry_for(cid, layers)
        if geom is None:
            continue
        km_geom = shapely_scale(geom, xfact=1 / 1000.0, yfact=1 / 1000.0, origin=(0, 0))
        items.append(
            (km_geom, crop_2026.get(cid, ""), name_by_id.get(cid, ""),
             region_by_id.get(cid, ""))
        )

    fig = plt.figure(figsize=(11.0, 8.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.45, 1.0], wspace=0.16, hspace=0.22)

    ax_over = fig.add_subplot(gs[0, 0])
    _draw_panel(
        ax_over,
        items,
        "Overview — all 33 paddocks, region colour, drawn to scale",
        show_labels=False,
        colour_by="region",
    )
    for region in REGIONS:
        pts = [g.centroid for g, _, _, r in items if r == region]
        cx = float(np.mean([p.x for p in pts]))
        top_y = max(p.y for p in pts)
        ax_over.text(
            cx, top_y + 2.4, region, fontsize=8.5, fontweight="bold",
            color=REGION_COLOUR[region], ha="center", va="bottom", zorder=9,
            bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="none", alpha=0.85),
        )
    ax_over.set_xlabel("Easting (km, EPSG:3577)", fontsize=7)
    ax_over.set_ylabel("Northing (km, EPSG:3577)", fontsize=7)

    region_axes = {
        "Harden": fig.add_subplot(gs[0, 1]),
        "Trungley Hall": fig.add_subplot(gs[1, 0]),
        "Lease country": fig.add_subplot(gs[1, 1]),
    }
    for region, ax in region_axes.items():
        subset = [it for it in items if it[3] == region]
        _draw_panel(
            ax,
            subset,
            f"{region} — {len(subset)} fields",
            label_font=4.9,
        )
        ax.set_xlabel("Easting (km)", fontsize=6.5)
        ax.set_ylabel("Northing (km)", fontsize=6.5)

    handles = [
        Patch(facecolor=CROP_COLOUR[c], edgecolor="#2b2b2b", linewidth=0.4,
              label=short_crop(c))
        for c in CROP_ORDER
    ]
    handles.append(
        Patch(facecolor=CROP_COLOUR["Not managed"], edgecolor="#2b2b2b",
              linewidth=0.4, hatch="///", label="Not managed 2026")
    )
    fig.legend(
        handles=handles, loc="lower center", ncol=8, frameon=False,
        fontsize=8, bbox_to_anchor=(0.5, 0.005),
        title="2026 crop (region panels; overview is coloured by region)",
        title_fontsize=8,
    )
    fig.suptitle(
        "Paddocks by management region and 2026 crop",
        x=0.01, ha="left", fontweight="bold", fontsize=12,
    )
    fig.tight_layout(rect=(0, 0.045, 1, 0.96))
    path = FIG_DIR / "fig04_paddock_map.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_rainfall() -> Path | None:
    season_path = EXAMPLES / "weather_seasonal.csv"
    if not season_path.exists():
        print("weather_seasonal.csv not found — skipping figure 2")
        return None

    season = pd.read_csv(season_path)
    panel = load_panel()
    region_by_farm = (
        panel.drop_duplicates("farm").set_index("farm")["region"].to_dict()
    )
    season["region"] = season["farm"].map(region_by_farm)
    season = season[season["season_days"] > 180]

    fig, ax = plt.subplots(figsize=(8.0, 3.6))
    width = 0.26
    x = np.arange(len(YEARS))

    farm_counts = season.groupby("region")["farm"].nunique().to_dict()

    for i, region in enumerate(REGIONS):
        sub = season[season["region"] == region]
        means, lows, highs = [], [], []
        for year in YEARS:
            vals = sub.loc[sub["year"] == year, "rain_common_mm"].values
            if len(vals) == 0:
                means.append(np.nan)
                lows.append(np.nan)
                highs.append(np.nan)
            else:
                means.append(float(np.mean(vals)))
                lows.append(float(np.min(vals)))
                highs.append(float(np.max(vals)))
        offset = (i - 1) * width
        ax.bar(
            x + offset,
            means,
            width=width,
            color=REGION_COLOUR[region],
            edgecolor="white",
            linewidth=0.6,
            label=f"{region} ({farm_counts.get(region, 0)} farm"
            + ("s)" if farm_counts.get(region, 0) != 1 else ")"),
        )
        for xi, lo, hi in zip(x + offset, lows, highs):
            if np.isnan(lo) or np.isnan(hi):
                continue
            if hi > lo:
                ax.plot([xi, xi], [lo, hi], color="#333333", linewidth=0.8)

    # Flag any year not complete on the plotted (common) window.
    for year in sorted(season.loc[~season["common_complete"], "year"].unique()):
        idx = list(YEARS).index(year)
        top = float(season.loc[season["year"] == year, "rain_common_mm"].max())
        days = int(season.loc[season["year"] == year, "common_days"].max())
        ax.annotate(
            f"partial\n{days}/229 d\n(lower bound)",
            xy=(idx, top),
            xytext=(idx, top + 45),
            ha="center",
            va="bottom",
            fontsize=6.2,
            color="#8a5a00",
            style="italic",
            arrowprops=dict(arrowstyle="-", lw=0.6, color="#8a5a00"),
        )

    ax.set_xticks(x)
    ax.set_xticklabels([str(y) for y in YEARS])
    ax.set_ylabel("1 Apr – 15 Nov rainfall (mm)")
    ax.set_title(
        "Seasonal rainfall by management region, common window to 15 Nov",
        loc="left",
        fontweight="bold",
        pad=26,
    )
    ax.text(
        0.0,
        1.015,
        "Bars = region mean, lines = farm range. Common 1 Apr – 15 Nov window so "
        "regions are comparable.\n"
        "Kanoona's own season runs to 30 Nov; that extra fortnight is reported "
        "separately.",
        transform=ax.transAxes,
        fontsize=7.0,
        color="#666666",
        style="italic",
        va="bottom",
        linespacing=1.35,
    )
    ax.legend(frameon=False, ncol=3, loc="upper right", fontsize=7.5)
    ax.grid(axis="y", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    path = FIG_DIR / "fig02_seasonal_rainfall.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_ndvi_season(panel: pd.DataFrame) -> Path | None:
    """Season behaviour of the NDVI record: shape, zones, crops, rain."""
    field_year_path = EXAMPLES / "ndvi_field_year.csv"
    monthly_path = EXAMPLES / "ndvi_monthly.csv"
    season_path = EXAMPLES / "weather_seasonal.csv"
    for path in (field_year_path, monthly_path, season_path):
        if not path.exists():
            print(f"{path.name} not found — skipping figure 5")
            return None

    field_year = pd.read_csv(field_year_path, dtype={"field_slug": str})
    monthly = pd.read_csv(monthly_path, dtype={"field_slug": str})
    weather = pd.read_csv(season_path)
    region_by_farm = (
        panel.drop_duplicates("farm").set_index("farm")["region"].to_dict()
    )
    weather["region"] = weather["farm"].map(region_by_farm)

    observed = field_year[field_year["status"] == "observed"]
    used = monthly[monthly["used_in_season"].astype(bool)]
    months = sorted(used["month"].unique())

    fig, axes = plt.subplots(2, 2, figsize=(11.4, 8.2))
    fig.patch.set_facecolor("white")

    # (a) monthly climatology per zone -------------------------------------
    ax = axes[0, 0]
    for region in REGIONS:
        series = (
            used[used["region"] == region]
            .groupby("month")["ndvi_median"].median()
            .reindex(months)
        )
        ax.plot(range(len(months)), series.values, marker="o", markersize=4.4,
                linewidth=1.9, color=REGION_COLOUR[region], label=region)
    pooled = used.groupby("month")["ndvi_median"].median().reindex(months)
    ax.plot(range(len(months)), pooled.values, color="#3f3f46", linewidth=1.4,
            linestyle=(0, (3, 2)), zorder=2, label="All zones")
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels([MONTH_ABBR[int(m)] for m in months], fontsize=8.2)
    ax.set_ylabel("Median NDVI")
    ax.set_ylim(0.05, 0.68)
    ax.grid(axis="y", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_title("Season shape — monthly median NDVI", fontsize=10.2,
                 loc="left", fontweight="bold")

    # (b) zone trajectories of the season integral --------------------------
    ax = axes[0, 1]
    zone_year = observed.pivot_table(
        index="region", columns="year",
        values="season_integral_ndvi_months", aggfunc="median",
    ).reindex(REGIONS)
    x_years = np.arange(len(YEARS))
    ax.axvspan(-0.35, 0.35, color="#f4f1e6", zorder=0)
    for region in REGIONS:
        ax.plot(x_years, zone_year.loc[region, YEARS].values, marker="o",
                markersize=5.0, linewidth=1.9, color=REGION_COLOUR[region],
                label=region)
    ax.set_xticks(x_years)
    ax.set_xticklabels([str(y) for y in YEARS], fontsize=8.2)
    ax.set_ylabel("NDVI-months")
    ax.set_ylim(1.2, 3.9)
    ax.grid(axis="y", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_title("Season integral by zone — median of paddocks", fontsize=10.2,
                 loc="left", fontweight="bold")
    ax.annotate("2022: wettest season", xy=(0.0, 1.28), xytext=(0.45, 1.32),
                fontsize=7.8, color="#6b6b6b", va="bottom")

    # (c) crop ordering ----------------------------------------------------
    ax = axes[1, 0]
    by_crop = observed.groupby("crop").agg(
        mean=("season_mean_ndvi", "median"), n=("year", "size")
    ).sort_values("mean")
    positions = np.arange(len(by_crop))
    ax.barh(positions, by_crop["mean"].values,
            color=[CROP_COLOUR[c] for c in by_crop.index],
            edgecolor="#3f3f46", linewidth=0.5, height=0.68)
    for y, (crop, row) in enumerate(by_crop.iterrows()):
        ax.text(row["mean"] + 0.008, y, f"{row['mean']:.2f}  (n={int(row['n'])})",
                va="center", fontsize=7.9, color="#27272a")
    ax.set_yticks(positions)
    ax.set_yticklabels([short_crop(c) for c in by_crop.index], fontsize=8.4)
    ax.set_xlabel("Median season mean NDVI")
    ax.set_xlim(0, 0.56)
    ax.grid(axis="x", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_title("Canopy by crop — all zones, all seasons", fontsize=10.2,
                 loc="left", fontweight="bold")

    # (d) season integral against season rain ------------------------------
    ax = axes[1, 1]
    joined = observed.merge(
        weather[["farm", "year", "rain_season_mm"]], on=["farm", "year"],
        how="left",
    )
    for region in REGIONS:
        sub = joined[joined["region"] == region]
        ax.scatter(sub["rain_season_mm"], sub["season_integral_ndvi_months"],
                   s=22, color=REGION_COLOUR[region], alpha=0.62,
                   edgecolor="white", linewidth=0.4, label=region)
    rho = joined["season_integral_ndvi_months"].corr(
        joined["rain_season_mm"], method="spearman")
    ax.text(0.03, 0.955, f"Spearman ρ = {rho:.2f}", transform=ax.transAxes,
            fontsize=8.4, color="#3f3f46", va="top",
            bbox=dict(boxstyle="round,pad=0.26", fc="white", ec="#e4e4e7"))
    ax.set_xlabel("Growing-season rain (mm)")
    ax.set_ylabel("Season integral (NDVI-months)")
    ax.set_xlim(140, 800)
    ax.set_ylim(0.9, 4.7)
    ax.grid(color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_title("Integral against season rain — one point per paddock-year",
                 fontsize=10.2, loc="left", fontweight="bold")

    handles = [Line2D([], [], color=REGION_COLOUR[r], marker="o",
                      markersize=4.6, linewidth=1.9, label=r) for r in REGIONS]
    handles.append(Line2D([], [], color="#3f3f46", linewidth=1.4,
                          linestyle=(0, (3, 2)), label="All zones"))
    fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(0.995, 0.952),
               ncol=4, frameon=False, fontsize=8.4, handlelength=1.6,
               columnspacing=1.6)

    fig.suptitle(
        "NDVI season behaviour: one broad peak, a zonal gradient, and a wet-year exception",
        x=0.012, ha="left", fontweight="bold", fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.935), w_pad=1.6, h_pad=1.4)
    path = FIG_DIR / "fig05_ndvi_season.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["1", "2", "3", "4", "5"])
    args = parser.parse_args()

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    panel = load_panel()

    made = []
    if args.only in (None, "1"):
        made.append(figure_field_area(panel))
    if args.only in (None, "2"):
        made.append(figure_rainfall())
    if args.only in (None, "3"):
        made.append(figure_crop_mix(panel))
    if args.only in (None, "4"):
        made.append(figure_map(panel))
    if args.only in (None, "5"):
        made.append(figure_ndvi_season(panel))

    for path in made:
        if path:
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
