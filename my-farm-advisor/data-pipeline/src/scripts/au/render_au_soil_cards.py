#!/usr/bin/env python3
"""Render the 0-100 cm soil profile card for one AU paddock.

One PNG per paddock, A4 landscape. The card reads top to bottom the way a pit
does - five depth slabs from 0-5 cm to 60-100 cm - and carries both the slabs
as measured and the two derived numbers the reports quote:

    * texture composition by slab, with the USDA class named at each depth;
    * pH (CaCl2) and CEC by slab;
    * organic carbon and bulk density by slab;
    * the USDA texture triangle with one point per slab plus the 0-30 cm star;
    * carbon stock as a slab-by-slab sum, 0-30 cm and 0-100 cm (never the
      product of a depth-weighted carbon mean and a bulk-density mean);
    * a numbers table so the card can be read without the CSV.

The triangle geometry is imported from the shared renderer rather than
retyped, so a class boundary can never disagree between the US and AU cards.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.au_common import (  # noqa: E402
    DEPTH_THICKNESS_M,
    FARM_NAME_BY_SLUG,
    SOIL_DEPTHS,
    ZONE_LABELS,
    load_field_names,
    usda_texture,
    zone_for_farm,
)
from lib.paths import DATA_ROOT, farm_dir, field_dir  # noqa: E402
from reporting.generate_soil_texture_triangles import (  # noqa: E402
    _CLASSES,
    _poly_verts,
    _to_xy,
)

DEPTH_LABELS = {
    "0-5cm": "0–5",
    "5-15cm": "5–15",
    "15-30cm": "15–30",
    "30-60cm": "30–60",
    "60-100cm": "60–100",
}
SAND_COLOUR = "#e6cf8b"
SILT_COLOUR = "#b9c6a8"
CLAY_COLOUR = "#a4544f"
INK = "#18181b"
GREY = "#6b7280"
FAINT = "#e4e4e7"


def _fmt(value, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    return f"{value:.{digits}f}"


def _profile(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype={"field_id": str, "field_slug": str})
    if frame.empty:
        return None
    return frame.iloc[0]


def _slab(row: pd.Series, prop: str, depth: str) -> float:
    key = f"{prop}_{depth}"
    if key not in row:
        return float("nan")
    value = pd.to_numeric(pd.Series([row[key]]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else float("nan")


def _triangle(ax, row: pd.Series) -> None:
    for _name, (idx, colour) in _CLASSES.items():
        ax.add_patch(
            MplPolygon(_poly_verts(idx), closed=True, facecolor=colour,
                       edgecolor="#7a4a44", linewidth=0.6, alpha=0.85)
        )
    plotted = []
    for depth in SOIL_DEPTHS:
        c, si, sa = (_slab(row, "CLAY", depth), _slab(row, "SILT", depth),
                     _slab(row, "SAND", depth))
        total = c + si + sa
        if all(np.isfinite(v) for v in (c, si, sa)) and total >= 70:
            plotted.append(_to_xy(c / total, si / total, sa / total))
    plotted = np.array(plotted) if plotted else np.empty((0, 2))
    for name, (idx, _) in _CLASSES.items():
        cx, cy = _poly_verts(idx).mean(axis=0)
        best = (cx, cy)
        for dy in (0.0, 0.05, 0.09, -0.05, -0.09):
            trial = np.array([cx, cy + dy])
            if plotted.size == 0 or np.min(np.hypot(*(plotted - trial).T)) >= 0.045:
                best = tuple(trial)
                break
        ax.text(best[0], best[1], name, ha="center", va="center", fontsize=6.0,
                zorder=9, color="#3f2a26", style="italic",
                path_effects=[pe.withStroke(linewidth=2.2, foreground="white")])

    ax.add_patch(
        MplPolygon([_to_xy(0, 1, 0), _to_xy(0, 0, 1), _to_xy(1, 0, 0)], closed=True,
                   fill=False, edgecolor="#3f2a26", linewidth=1.3)
    )

    depth_slabs = SOIL_DEPTHS[:3] + SOIL_DEPTHS[3:]
    marks = ("o", "o", "o", "s", "s")
    for depth, marker in zip(depth_slabs, marks, strict=True):
        clay = _slab(row, "CLAY", depth)
        silt = _slab(row, "SILT", depth)
        sand = _slab(row, "SAND", depth)
        total = clay + silt + sand
        if not all(np.isfinite(v) for v in (clay, silt, sand)) or total < 70:
            continue
        x, y = _to_xy(clay / total, silt / total, sand / total)
        filled = depth in SOIL_DEPTHS[:3]
        ax.scatter([x], [y], marker=marker, s=58 if filled else 46,
                   c="#1d4ed8" if filled else "#ffffff",
                   edgecolors="#1d4ed8", linewidths=1.4, zorder=7)

    clay30 = float(row.get("CLAY_0_30", float("nan")))
    silt30 = float(row.get("SILT_0_30", float("nan")))
    sand30 = float(row.get("SAND_0_30", float("nan")))
    total = clay30 + silt30 + sand30
    if all(np.isfinite(v) for v in (clay30, silt30, sand30)) and total >= 70:
        x, y = _to_xy(clay30 / total, silt30 / total, sand30 / total)
        ax.scatter([x], [y], marker="*", s=330, c="#f59e0b", edgecolors="#78350f",
                   linewidths=0.9, zorder=9)

    ax.set_xlim(-0.04, 1.04)
    ax.set_ylim(-0.06, 0.88)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_xlabel("% sand  →   ← % silt", fontsize=7.6, labelpad=2, color="#3f2a26")

    # the five slabs land close together inside sandy loam, so the slab names
    # live in a key rather than as colliding annotations next to the points
    from matplotlib.lines import Line2D

    key = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#1d4ed8",
               markeredgecolor="#1d4ed8", markersize=6, label=f"{DEPTH_LABELS[d]} cm")
        for d in SOIL_DEPTHS[:3]
    ] + [
        Line2D([0], [0], marker="s", color="none", markerfacecolor="#ffffff",
               markeredgecolor="#1d4ed8", markersize=6, label=f"{DEPTH_LABELS[d]} cm")
        for d in SOIL_DEPTHS[3:]
    ] + [
        Line2D([0], [0], marker="*", color="none", markerfacecolor="#f59e0b",
               markeredgecolor="#78350f", markersize=11, label="0–30 cm mean")
    ]
    ax.legend(handles=key, loc="upper left", bbox_to_anchor=(-0.02, 1.02),
              frameon=False, fontsize=6.8, handletextpad=0.5, labelspacing=0.35,
              borderaxespad=0.0)
    ax.set_title("Texture triangle (USDA)", fontsize=11.5, loc="left",
                 fontweight="bold", pad=8)


def render(grower: str, farm: str, slug: str, out_png: Path) -> Path | None:
    row = _profile(field_dir(grower, farm, slug) / "soil" / "slga_asf_profile.csv")
    if row is None:
        print(f"skip  {slug}: no soil profile table")
        return None

    name = str(row.get("paddock_name") or slug)
    farm_label = FARM_NAME_BY_SLUG.get(farm, str(row.get("farm") or farm))
    zone = ZONE_LABELS.get(
        zone_for_farm(str(row.get("farm") or farm_label)), "").split(" (")[0]

    boundary = field_dir(grower, farm, slug) / "boundary" / "field_boundary.geojson"
    area_ha = float("nan")
    if boundary.exists():
        import geopandas as gpd

        area_ha = float(gpd.read_file(boundary).to_crs(3577).area.sum() / 10_000.0)

    depths = list(SOIL_DEPTHS)
    ypos = np.arange(len(depths))

    fig = plt.figure(figsize=(16.0, 11.0))
    fig.patch.set_facecolor("#fbfaf8")
    grid = fig.add_gridspec(
        3, 3, height_ratios=[0.26, 1.0, 1.0],
        hspace=0.46, wspace=0.26,
        left=0.088, right=0.968, top=0.950, bottom=0.075,
    )

    # ------------------------------------------------------------------ header
    ax_head = fig.add_subplot(grid[0, :])
    ax_head.axis("off")
    ax_head.text(0.0, 0.80, name, fontsize=27, fontweight="bold", color=INK,
                 transform=ax_head.transAxes)
    texture = str(row.get("texture_usda_0_30") or "—")
    head_bits = [farm_label]
    if zone and zone != farm_label:
        head_bits.append(zone)
    head_bits += [f"{_fmt(area_ha)} ha", f"0–30 cm {texture}"]
    ax_head.text(0.0, 0.38, "  ·  ".join(head_bits), fontsize=12.5, color="#3f3f46",
                 transform=ax_head.transAxes)
    ax_head.text(
        0.0, 0.11,
        "SLGA / ASF soil property grids, five depth slabs to 100 cm · rasters are "
        "thickness-weighted to 0–30 cm · carbon stock is a slab sum, not a product of means",
        fontsize=9.4, color=GREY, transform=ax_head.transAxes,
    )

    # ----------------------------------------------- 1. composition by depth
    ax = fig.add_subplot(grid[1, 0])
    sand = [_slab(row, "SAND", d) for d in depths]
    silt = [_slab(row, "SILT", d) for d in depths]
    clay = [_slab(row, "CLAY", d) for d in depths]
    ax.barh(ypos, sand, color=SAND_COLOUR, edgecolor="#8a7a45", linewidth=0.5,
            label="sand")
    ax.barh(ypos, silt, left=sand, color=SILT_COLOUR, edgecolor="#5f6b52",
            linewidth=0.5, label="silt")
    ax.barh(ypos, clay, left=np.add(sand, silt), color=CLAY_COLOUR,
            edgecolor="#6b2f2b", linewidth=0.5, label="clay")
    tick_labels = []
    for depth, y, c, si, sa in zip(depths, ypos, clay, silt, sand, strict=True):
        total = c + si + sa
        if np.isfinite(total) and total > 0:
            ax.text(98.0, y, f"{c:.0f}/{si:.0f}/{sa:.0f}", va="center", ha="right",
                    fontsize=7.2, color="#fafaf9")
        tick_labels.append(f"{DEPTH_LABELS[depth]} cm\n{usda_texture(sa, c) or '—'}")
    ax.set_yticks(ypos)
    ax.set_yticklabels(tick_labels, fontsize=8.2)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("% of fine earth", fontsize=8.6)
    ax.set_title("Texture by depth (clay/silt/sand %)", fontsize=11.5, loc="left",
                 fontweight="bold", pad=8)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.175), ncol=3,
              frameon=False, fontsize=8.0, handlelength=1.3, columnspacing=1.3)
    ax.grid(axis="x", alpha=0.18, lw=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    # ----------------------------------------------------- 2. pH and CEC
    ax = fig.add_subplot(grid[1, 1])
    ph = [_slab(row, "PHCALCIUMCHLORIDE", d) for d in depths]
    cec = [_slab(row, "CEC", d) for d in depths]
    ax.plot(ph, ypos, "o-", color="#2f6f4f", lw=1.8, ms=5.5, label="pH (CaCl₂)")
    ax.set_xlabel("pH (CaCl₂)", fontsize=8.6, color="#2f6f4f")
    ax.tick_params(axis="x", labelcolor="#2f6f4f", labelsize=7.8)
    ph_lo = float(np.nanmin(ph)) - 0.4 if np.isfinite(ph).any() else 4.0
    ph_hi = float(np.nanmax(ph)) + 0.4 if np.isfinite(ph).any() else 8.0
    ax.set_xlim(ph_lo, ph_hi)
    ax.set_ylim(len(depths) - 0.5, -0.5)
    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{DEPTH_LABELS[d]} cm" for d in depths], fontsize=8.6)
    ax.grid(axis="y", alpha=0.14, lw=0.6)

    ax2 = ax.twiny()
    ax2.plot(cec, ypos, "s--", color="#7c4a94", lw=1.5, ms=4.8, label="CEC")
    ax2.set_xlabel("CEC (meq/100 g)", fontsize=8.6, color="#7c4a94")
    ax2.tick_params(axis="x", labelcolor="#7c4a94", labelsize=7.8)
    ax2.set_xlim(0, max(5.0, float(np.nanmax(cec) * 1.35)) if np.isfinite(cec).any() else 30)
    ax.set_title("pH and CEC by depth", fontsize=11.5, loc="left", fontweight="bold",
                 pad=28)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax2.spines["top"].set_color("#7c4a94")

    # ------------------------------------------------ 3. carbon & bulk density
    ax = fig.add_subplot(grid[1, 2])
    carbon = [_slab(row, "CARBON", d) for d in depths]
    bd = [_slab(row, "BULKDENSITY", d) for d in depths]
    ax.barh(ypos, carbon, height=0.55, color="#5b4636", alpha=0.85,
            edgecolor="#3a2c21", linewidth=0.5)
    for y, value in zip(ypos, carbon, strict=True):
        if np.isfinite(value):
            ax.text(value + 0.02, y, f"{value:.2f}", va="center", fontsize=7.2,
                    color="#3a2c21")
    ax.set_xlabel("organic carbon (%)", fontsize=8.6, color="#5b4636")
    ax.tick_params(axis="x", labelcolor="#5b4636", labelsize=7.8)
    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{DEPTH_LABELS[d]} cm" for d in depths], fontsize=8.6)
    ax.set_ylim(len(depths) - 0.5, -0.5)
    ax.set_xlim(0, float(np.nanmax(carbon) * 1.45) if np.isfinite(carbon).any() else 2)
    ax.grid(axis="x", alpha=0.16, lw=0.6)

    ax2 = ax.twiny()
    ax2.plot(bd, ypos, "o-", color="#b45309", lw=1.6, ms=4.6)
    ax2.set_xlabel("bulk density (g/cm³)", fontsize=8.6, color="#b45309")
    ax2.tick_params(axis="x", labelcolor="#b45309", labelsize=7.8)
    bd_lo = max(0.5, float(np.nanmin(bd)) - 0.15) if np.isfinite(bd).any() else 0.9
    bd_hi = float(np.nanmax(bd)) + 0.15 if np.isfinite(bd).any() else 1.9
    ax2.set_xlim(bd_lo, bd_hi)
    ax.set_title("Organic carbon and bulk density", fontsize=11.5, loc="left",
                 fontweight="bold", pad=28)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax2.spines["top"].set_color("#b45309")

    # ------------------------------------------------------- 4. triangle
    ax = fig.add_subplot(grid[2, 0])
    _triangle(ax, row)
    ax.set_title("Texture triangle", fontsize=11.5, loc="left", fontweight="bold",
                 pad=8)
    ax.text(0.0, -0.085, "circle = 0–30 cm slabs · square = 30–100 cm · "
                         "star = 0–30 cm thickness-weighted",
            transform=ax.transAxes, fontsize=7.2, color=GREY)

    # ------------------------------------------------------- 5. carbon stock
    ax = fig.add_subplot(grid[2, 1])
    slab_stock = []
    for depth in depths:
        c, b = _slab(row, "CARBON", depth), _slab(row, "BULKDENSITY", depth)
        slab_stock.append(c / 100.0 * b * (DEPTH_THICKNESS_M[depth] * 100.0) * 100.0)
    slab_stock = np.array(slab_stock, dtype="float64")
    running = np.cumsum(np.nan_to_num(slab_stock))
    bars = ax.bar(ypos, slab_stock, width=0.62,
                  color=["#5b4636" if d in SOIL_DEPTHS[:3] else "#a89478"
                         for d in depths],
                  edgecolor="#3a2c21", linewidth=0.5)
    for bar, value in zip(bars, slab_stock, strict=True):
        if np.isfinite(value):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.4,
                    f"{value:.0f}", ha="center", fontsize=7.4, color="#3a2c21")
    ax.plot(ypos, running, "o--", color="#0f766e", lw=1.4, ms=4.2, zorder=5)
    for y, value in zip(ypos, running, strict=True):
        ax.text(y + 0.34, value, f"{value:.0f}", fontsize=7.0, color="#0f766e",
                ha="left", va="center")
    ax.set_xticks(ypos)
    ax.set_xticklabels([DEPTH_LABELS[d] for d in depths], fontsize=8.0)
    ax.set_xlabel("depth slab (cm)", fontsize=8.6)
    ax.set_ylabel("t C/ha in slab", fontsize=8.6)
    ax.set_title("Carbon stock by slab (bars) and cumulative (line)", fontsize=11.5,
                 loc="left", fontweight="bold", pad=8)
    ax.grid(axis="y", alpha=0.18, lw=0.6)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    stock30 = row.get("carbon_stock_t_ha_0_30", float("nan"))
    stock100 = row.get("carbon_stock_t_ha_0_100", float("nan"))
    ax.text(0.02, 0.96,
            f"0–30 cm  {_fmt(stock30, 1)} t/ha\n0–100 cm  {_fmt(stock100, 1)} t/ha",
            transform=ax.transAxes, ha="left", va="top", fontsize=8.2, color="#3a2c21",
            bbox=dict(boxstyle="round,pad=0.35", fc="#f6f1e7", ec="#c8b89c", lw=0.7))

    # ------------------------------------------------------- 6. numbers table
    ax = fig.add_subplot(grid[2, 2])
    ax.axis("off")
    ax.set_title("Paddock numbers", fontsize=11.5, loc="left", fontweight="bold",
                 pad=8)
    lines = [
        ("Texture 0–30 cm", str(row.get("texture_usda_0_30") or "—")),
        ("Clay 0–30 / 0–100 cm",
         f"{_fmt(row.get('CLAY_0_30'))} % / {_fmt(row.get('CLAY_0_100'))} %"),
        ("Silt 0–30 / 0–100 cm",
         f"{_fmt(row.get('SILT_0_30'))} % / {_fmt(row.get('SILT_0_100'))} %"),
        ("Sand 0–30 / 0–100 cm",
         f"{_fmt(row.get('SAND_0_30'))} % / {_fmt(row.get('SAND_0_100'))} %"),
        ("pH 0–30 / 0–100 cm",
         f"{_fmt(row.get('PHCALCIUMCHLORIDE_0_30'), 2)} / "
         f"{_fmt(row.get('PHCALCIUMCHLORIDE_0_100'), 2)}"),
        ("CEC 0–30 / 0–100 cm",
         f"{_fmt(row.get('CEC_0_30'))} / {_fmt(row.get('CEC_0_100'))} meq/100 g"),
        ("Organic carbon 0–30 / 0–100 cm",
         f"{_fmt(row.get('CARBON_0_30'), 2)} % / {_fmt(row.get('CARBON_0_100'), 2)} %"),
        ("Bulk density 0–30 / 0–100 cm",
         f"{_fmt(row.get('BULKDENSITY_0_30'), 2)} / "
         f"{_fmt(row.get('BULKDENSITY_0_100'), 2)} g/cm³"),
        ("Carbon stock 0–30 / 0–100 cm",
         f"{_fmt(stock30, 1)} / {_fmt(stock100, 1)} t/ha"),
    ]
    for index, (label, value) in enumerate(lines):
        y = 0.94 - index * 0.089
        ax.text(0.0, y, label, fontsize=8.6, color=GREY, transform=ax.transAxes,
                va="top")
        ax.text(1.0, y, value, fontsize=8.8, color=INK, transform=ax.transAxes,
                va="top", ha="right")
        ax.plot([0.0, 1.0], [y - 0.042, y - 0.042], color=FAINT, lw=0.8,
                transform=ax.transAxes, clip_on=False)
    ax.text(0.0, 0.04,
            "0–30 and 0–100 cm are thickness-weighted across the slabs.\n"
            "Carbon stock uses each slab's own carbon and bulk density.",
            fontsize=7.4, color=GREY, transform=ax.transAxes, va="top")

    fig.text(
        0.055, 0.016,
        "Source: SLGA / ASF soil property grids (0–100 cm, five slabs), CSIRO, "
        "doi.org/10.25919/hc4s-3130 and related property DOIs. "
        "Texture classes are USDA; the triangle geometry matches the shared renderer.",
        fontsize=7.2, color="#9ca3af",
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG",
                                                                "au-grain-grower"))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG"))
    parser.add_argument("--field-slug", default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.farm_slug:
        raise SystemExit("AG_FARM_SLUG or --farm-slug is required")

    names = load_field_names(args.grower_slug, args.farm_slug)
    slugs = ([args.field_slug] if args.field_slug
             else list(names["field_slug"].astype(str)))
    out_dir = args.out_dir or (farm_dir(args.grower_slug, args.farm_slug)
                              / "derived" / "figures" / "soil_cards")
    written = []
    for slug in slugs:
        out = out_dir / f"soil_profile_card_{slug}.png"
        path = render(args.grower_slug, args.farm_slug, str(slug), out)
        if path is not None:
            written.append(path)
            print(path)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
