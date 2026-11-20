#!/usr/bin/env python3
"""Render one soil-texture sheet per farm: triangle plus paddock composition.

The per-paddock cards answer "what is this paddock"; this sheet answers "where
does this farm sit", which is the question the rotation and input decisions
actually turn on.

Left: the USDA triangle with one point per paddock, sized by area and coloured
by clay, plus a star for the area-weighted farm mean.
Right: the same paddocks as clay/silt/sand composition bars, sorted by clay,
with the texture class named and the farm mean marked.

Points are not labelled on the triangle - sixteen names inside a sandy-loam
cluster would be unreadable - so the bars carry the names and the order.
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
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.au_common import (  # noqa: E402
    FARM_NAME_BY_SLUG,
    load_field_names,
)
from lib.paths import farm_boundary_path, farm_dir, field_dir  # noqa: E402
from reporting.generate_soil_texture_triangles import (  # noqa: E402
    _CLASSES,
    _poly_verts,
    _to_xy,
)

SAND_COLOUR = "#e6cf8b"
SILT_COLOUR = "#b9c6a8"
CLAY_COLOUR = "#a4544f"
INK = "#18181b"
GREY = "#6b7280"


def _field_table(grower: str, farm: str, names: pd.DataFrame) -> pd.DataFrame:
    import geopandas as gpd

    boundary = farm_boundary_path(grower, farm)
    areas = {}
    if boundary.exists():
        frame = gpd.read_file(boundary).to_crs(3577)
        for row in frame.itertuples(index=False):
            areas[str(row.field_id)] = float(row.geometry.area / 10_000.0)

    rows = []
    for record in names.itertuples(index=False):
        slug = str(record.field_slug)
        path = field_dir(grower, farm, slug) / "soil" / "slga_asf_profile.csv"
        if not path.exists():
            continue
        profile = pd.read_csv(path).iloc[0]
        clay = float(profile.get("CLAY_0_30", np.nan))
        silt = float(profile.get("SILT_0_30", np.nan))
        sand = float(profile.get("SAND_0_30", np.nan))
        total = clay + silt + sand
        if not np.isfinite(total) or total <= 0:
            continue
        rows.append(
            {
                "field_slug": slug,
                "paddock_name": str(record.paddock_name),
                "area_ha": areas.get(str(record.field_id), float("nan")),
                "clay": clay,
                "silt": silt,
                "sand": sand,
                "clay_frac": clay / total,
                "silt_frac": silt / total,
                "sand_frac": sand / total,
                "texture": str(profile.get("texture_usda_0_30") or ""),
                "cec": float(profile.get("CEC_0_30", np.nan)),
            }
        )
    return pd.DataFrame(rows)


def render(grower: str, farm: str, out_png: Path) -> Path | None:
    names = load_field_names(grower, farm)
    table = _field_table(grower, farm, names)
    if table.empty:
        print(f"skip  {farm}: no soil profiles")
        return None

    table = table.sort_values("clay").reset_index(drop=True)
    weights = table["area_ha"].fillna(table["area_ha"].mean())
    farm_mean = {
        key: float(np.average(table[key], weights=weights))
        for key in ("clay", "silt", "sand")
    }
    total = sum(farm_mean.values())
    farm_frac = {k: v / total for k, v in farm_mean.items()}

    fig = plt.figure(figsize=(17.0, 11.5))
    fig.patch.set_facecolor("#fbfaf8")
    grid = fig.add_gridspec(
        2, 2, width_ratios=[1.15, 1.0], height_ratios=[0.17, 1.0],
        hspace=0.16, wspace=0.16,
        left=0.055, right=0.965, top=0.955, bottom=0.085,
    )

    farm_label = FARM_NAME_BY_SLUG.get(farm, farm)
    zone = _zone_label(farm_label)

    ax_head = fig.add_subplot(grid[0, :])
    ax_head.axis("off")
    ax_head.text(0.0, 0.78, f"{farm_label} — soil texture", fontsize=26,
                 fontweight="bold", color=INK, transform=ax_head.transAxes)
    ha = float(table["area_ha"].sum())
    ax_head.text(
        0.0, 0.40,
        f"{len(table)} paddock{'s' if len(table) != 1 else ''} · {ha:,.0f} ha · "
        f"0–30 cm thickness-weighted · "
        f"farm mean {_class_of(farm_frac)} "
        f"(clay {farm_mean['clay']:.0f} / silt {farm_mean['silt']:.0f} / "
        f"sand {farm_mean['sand']:.0f} %)",
        fontsize=12.4, color="#3f3f46", transform=ax_head.transAxes,
    )
    ax_head.text(
        0.0, 0.08,
        f"{zone + ' zone · ' if zone else ''}SLGA / ASF 0–30 cm grids, "
        "centre-in-polygon means per paddock · USDA classes, geometry shared "
        "with the per-paddock cards",
        fontsize=9.4, color=GREY, transform=ax_head.transAxes,
    )

    # ------------------------------------------------------------- triangle
    ax = fig.add_subplot(grid[1, 0])
    for _name, (idx, colour) in _CLASSES.items():
        ax.add_patch(MplPolygon(_poly_verts(idx), closed=True, facecolor=colour,
                                edgecolor="#7a4a44", linewidth=0.8, alpha=0.85))
    import matplotlib.patheffects as pe

    # label anchor per class, then nudged clear of the paddock cluster so no
    # class name is struck through by a marker
    point_xy = np.array([
        _to_xy(c, s_, sa) for c, s_, sa in
        zip(table["clay_frac"], table["silt_frac"], table["sand_frac"])
    ])
    offsets = [(0.0, 0.0), (0.0, 0.055), (0.0, 0.10), (0.0, -0.055), (0.0, -0.10)]
    for name, (idx, _) in _CLASSES.items():
        cx, cy = _poly_verts(idx).mean(axis=0)
        best = (cx, cy)
        for dx, dy in offsets:
            trial = np.array([cx + dx, cy + dy])
            if np.min(np.hypot(*(point_xy - trial).T)) >= 0.055:
                best = tuple(trial)
                break
        ax.text(best[0], best[1], name, ha="center", va="center", fontsize=7.0,
                zorder=6, color="#3f2a26", style="italic",
                path_effects=[pe.withStroke(linewidth=2.6, foreground="white")])

    # 10 % grid on all three axes
    sq3 = np.sqrt(3.0)
    for pct in range(10, 100, 10):
        f = pct / 100.0
        ax.plot(*zip(_to_xy(0, 1 - f, f), _to_xy(1, 0, 0)), color="#b08a7a", lw=0.3,
                alpha=0.45)
        ax.plot(*zip(_to_xy(f, 0, 1 - f), _to_xy(1, 0, 0)), color="#b08a7a", lw=0.3,
                alpha=0.45)
        ax.plot([1 - f, 0.5 * (1 - f)], [0, (sq3 / 2) * (1 - f)], color="#b08a7a",
                lw=0.3, alpha=0.45)

    ax.add_patch(MplPolygon([_to_xy(0, 1, 0), _to_xy(0, 0, 1), _to_xy(1, 0, 0)],
                            closed=True, fill=False, edgecolor="#3f2a26", lw=1.6))

    norm = Normalize(table["clay"].min() - 1.0, table["clay"].max() + 1.0)
    cmap = plt.get_cmap("YlOrBr")
    xs, ys = zip(*(_to_xy(c, s, sa) for c, s, sa in
                   zip(table["clay_frac"], table["silt_frac"], table["sand_frac"])))
    sizes = 34 + (table["area_ha"].fillna(10.0) / max(table["area_ha"].max(), 1)) * 210
    ax.scatter(xs, ys, s=sizes, c=table["clay"], cmap=cmap, norm=norm,
               edgecolors="#3f2a26", linewidths=0.7, zorder=7, alpha=0.95)

    mx, my = _to_xy(farm_frac["clay"], farm_frac["silt"], farm_frac["sand"])
    ax.scatter([mx], [my], marker="*", s=520, c="#0f766e", edgecolors="#052e2b",
               linewidths=1.0, zorder=9)
    # the farm mean is stated in the header (class and three fractions); on the
    # triangle it only needs to be the star, so no callout covers a class label

    ax.set_xlim(-0.04, 1.04)
    ax.set_ylim(-0.10, 0.90)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ax.spines.values():
        side.set_visible(False)
    ax.text(0.5, -0.055, "% sand  →      ← % silt", ha="center", fontsize=9.5,
            weight="bold", color="#3f2a26")
    ax.set_title("Where the farm sits in texture space", fontsize=12.5, loc="left",
                 fontweight="bold", pad=10)
    ax.text(0.0, 0.925, "% clay ↑   ·   % sand →   ·   % silt ↙",
            transform=ax.transAxes, fontsize=8.0, color=GREY)
    ax.legend(
        handles=[
            Line2D([0], [0], marker="o", color="none", markerfacecolor="#c98c45",
                   markeredgecolor="#3f2a26", markersize=8,
                   label="paddock — area sets size, clay % sets shade"),
            Line2D([0], [0], marker="*", color="none", markerfacecolor="#0f766e",
                   markeredgecolor="#052e2b", markersize=15,
                   label="area-weighted farm mean"),
        ],
        loc="upper left", frameon=False, fontsize=8.4, handletextpad=0.4,
        labelspacing=0.35, borderaxespad=0.2,
        bbox_to_anchor=(-0.01, 0.86),
    )

    # -------------------------------------------------- composition by paddock
    ax = fig.add_subplot(grid[1, 1])
    ypos = np.arange(len(table))
    sand = table["sand_frac"] * 100.0
    silt = table["silt_frac"] * 100.0
    clay = table["clay_frac"] * 100.0
    ax.barh(ypos, sand, height=0.62, color=SAND_COLOUR, edgecolor="#8a7a45",
            linewidth=0.4)
    ax.barh(ypos, silt, left=sand, height=0.62, color=SILT_COLOUR,
            edgecolor="#5f6b52", linewidth=0.4)
    ax.barh(ypos, clay, left=sand + silt, height=0.62, color=CLAY_COLOUR,
            edgecolor="#6b2f2b", linewidth=0.4)
    for y, c, texture in zip(ypos, clay, table["texture"], strict=True):
        ax.text(102.0, y, f"{c:.0f}%  {texture}", va="center", fontsize=8.0,
                color="#3f2a26", ha="left")
    ax.set_yticks(ypos)
    ax.set_yticklabels(table["paddock_name"], fontsize=8.4)
    ax.set_xlim(0, 148)
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    # pad short lists so one or two paddocks do not stretch into a wall of colour
    pad = 0.3 + max(0.0, (3 - len(table)) / 2.0)
    ax.set_ylim(-0.5 - pad, len(table) - 0.5 + pad)
    ax.set_yticks(np.arange(len(table)))
    ax.set_xlabel("share of fine earth, 0–30 cm (%)", fontsize=9.0)
    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(facecolor=SAND_COLOUR, edgecolor="#8a7a45", label="sand"),
            Patch(facecolor=SILT_COLOUR, edgecolor="#5f6b52", label="silt"),
            Patch(facecolor=CLAY_COLOUR, edgecolor="#6b2f2b", label="clay"),
        ],
        loc="lower right", ncol=3, frameon=False, fontsize=8.4,
        handlelength=1.2, columnspacing=1.2, bbox_to_anchor=(1.0, -0.075),
    )
    ax.set_title("Paddock composition, sorted by clay", fontsize=12.5, loc="left",
                 fontweight="bold", pad=10)
    ax.grid(axis="x", alpha=0.16, lw=0.6)
    ax.axvline(float(farm_mean["sand"]), color="#8a7a45", lw=1.0, ls=":")
    ax.axvline(float(farm_mean["sand"] + farm_mean["silt"]), color="#6b2f2b",
               lw=1.0, ls=":")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.text(0.995, -0.075, "dotted lines = farm mean sand and sand+silt bounds",
            transform=ax.transAxes, ha="right", fontsize=7.6, color=GREY)

    fig.text(
        0.055, 0.014,
        "Texture is the 0–30 cm thickness-weighted mean of the 0–5, 5–15 and 15–30 cm "
        "SLGA/ASF grids. Percentages are normalized to the three particle-size "
        "fractions; a paddock whose fractions sum below 70 % would be dropped, none were.",
        fontsize=7.4, color="#9ca3af",
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png


def _class_of(frac: dict) -> str:
    from lib.au_common import usda_texture

    total = sum(frac.values())
    return usda_texture(frac["sand"] / total * 100.0, frac["clay"] / total * 100.0)


def _zone_label(farm_label: str) -> str | None:
    from lib.au_common import ZONE_LABELS, zone_for_farm

    try:
        zone = zone_for_farm(farm_label)
    except KeyError:
        return None
    label = ZONE_LABELS.get(zone, zone)
    return label if label != farm_label else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG",
                                                                "au-grain-grower"))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG"))
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.farm_slug:
        raise SystemExit("AG_FARM_SLUG or --farm-slug is required")
    out_dir = args.out_dir or (farm_dir(args.grower_slug, args.farm_slug)
                              / "derived" / "figures" / "soil")
    out = out_dir / f"soil_texture_triangle_{args.farm_slug}.png"
    path = render(args.grower_slug, args.farm_slug, out)
    if path is None:
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
