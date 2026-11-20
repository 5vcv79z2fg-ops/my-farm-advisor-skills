#!/usr/bin/env python3
"""Render one zone poster: farm rows by year columns, plus a ranking strip.

Three zones, one sheet each:

    Kanoona                   16 paddocks, one farm
    Caroleese-Tullokan        11 paddocks, two farms
    Lease-country              6 paddocks, three farms

The year grid is the point of the poster. Rotation here is per paddock, not
per zone, so each year is a different patchwork of wheat, canola and pasture -
and on the lease blocks the *membership itself* changes, because paddocks that
were not managed in a year are absent from the crop record. All ten absent
field-years in the panel sit in the lease zone, so a single five-year average
would quietly compare different sets of paddocks. Side by side, the gaps are
visible instead of averaged away.

Rows are farms because a zone is not always one block of land: the lease
country spans 25 km between Brentwood/Rio and Woodlands, so one shared frame
would leave most of every panel as empty basemap. The frame is constant along a
row (the years compare) and the colour scale is constant everywhere (the rows
compare).

Honesty rules baked into the layout:

  * one fixed colour scale for every panel, derived from every painted value in
    all three zones - never per-panel autoscaling, which would make all five
    years look alike. A ``not managed`` year is hatched grey, not coloured, so
    its NDVI cannot stretch the legend;
  * colour is the mean of the observed monthly medians (NDVI units), not the
    NDVI-month integral, because 2022 is missing months at Kanoona (October)
    and around Temora. The integral is kept for the ranking strip, where the
    observed-season count travels with it;
  * months observed are printed on every panel and counted over managed
    paddocks only, and ``not managed`` is hatched separately from
    ``no usable scene``.
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
from matplotlib.colors import Normalize, to_rgba  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import matplotlib.patheffects as _pe  # noqa: E402

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import basemap as _bm  # noqa: E402
import field_frame as _fflib  # noqa: E402
from field_frame import decorate, frame_for  # noqa: E402
from lib.au_common import (  # noqa: E402
    FARM_NAME_BY_SLUG,
    FARM_ORDER,
    ZONE_LABELS,
    load_field_names,
    season_window,
    zone_for_farm,
)
from lib.paths import (  # noqa: E402
    DATA_ROOT,
    farm_boundary_path,
    farm_tables_dir,
)

ZONES = ("Kanoona", "Caroleese-Tullokan", "Lease-country")
YEARS = (2022, 2023, 2024, 2025, 2026)
OFFLINE = os.environ.get("AG_OFFLINE") == "1"
GREY = "#d4d4d8"


def _zone_frames(grower: str) -> pd.DataFrame:
    """Every paddock, tagged with its zone, farm and readable name."""
    import geopandas as gpd

    parts = []
    for farm_slug in FARM_ORDER:
        boundary = farm_boundary_path(grower, farm_slug)
        if not boundary.exists():
            continue
        frame = gpd.read_file(boundary)
        names = load_field_names(grower, farm_slug)
        lookup = {
            str(row.field_slug): (str(row.paddock_name), str(row.farm), str(row.region))
            for row in names.itertuples(index=False)
        }
        rows = []
        for row in frame.itertuples(index=False):
            slug = str(row.field_id)
            name, farm_name, region = lookup.get(slug, (slug, farm_slug, ""))
            rows.append(
                {
                    "field_slug": slug,
                    "paddock_name": name,
                    "farm_slug": farm_slug,
                    "farm": farm_name,
                    "region": region,
                    "zone": zone_for_farm(farm_name),
                    "geometry": row.geometry,
                }
            )
        parts.append(gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs))
    if not parts:
        raise SystemExit("no farm boundaries found")
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry",
                            crs=parts[0].crs)


def _zone_summaries(grower: str, fields: pd.DataFrame) -> pd.DataFrame:
    tables = []
    for farm_slug in fields["farm_slug"].unique():
        path = farm_tables_dir(grower, str(farm_slug)) / "ndvi_field_year_summary.csv"
        if path.exists():
            tables.append(pd.read_csv(path, dtype={"field_slug": str}))
    if not tables:
        return pd.DataFrame()
    summary = pd.concat(tables, ignore_index=True)
    summary = summary[summary["field_slug"].isin(fields["field_slug"].astype(str))]
    return summary


def _shared_scale(summaries: list[pd.DataFrame]) -> tuple[float, float]:
    """One scale for all three zone posters, so they compare with each other.

    Widened only if a zone falls outside the nominal window - the point is that
    a given colour means the same NDVI on every sheet.
    """
    # only painted values define the scale; a not-managed year is hatched grey,
    # never coloured, so its NDVI must not stretch the legend
    painted = [
        s[s["status"].astype(str) != "not_managed"]
        for s in summaries if not s.empty
    ]
    values = pd.concat([s["season_mean_ndvi"] for s in painted])
    values = pd.to_numeric(values, errors="coerce").dropna()
    lo, hi = 0.20, 0.60
    if values.size:
        lo = min(lo, float(np.floor(values.min() * 20) / 20))
        hi = max(hi, float(np.ceil(values.max() * 20) / 20))
    return lo, hi


def _scale(series: pd.Series) -> tuple[float, float]:
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype="float64")
    if values.size == 0:
        return 0.15, 0.60
    lo, hi = np.percentile(values, [3, 97])
    lo = float(np.floor(lo * 40) / 40)
    hi = float(np.ceil(hi * 40) / 40)
    if hi - lo < 0.1:
        mid = (hi + lo) / 2
        lo, hi = mid - 0.05, mid + 0.05
    return max(lo, 0.0), min(hi, 1.0)


def render_zone(
    *,
    grower: str,
    zone: str,
    fields,
    summary: pd.DataFrame,
    out_png: Path,
    scale: tuple[float, float] | None = None,
) -> Path:
    """One zone sheet: a farm-by-year grid, then the paddock ranking.

    A zone is not always one contiguous block of land. The lease country spans
    25 km between Brentwood/Rio and Woodlands, so a single frame would leave
    three quarters of every panel as empty basemap. Each farm therefore gets its
    own frame, repeated across the five years in its row: the frame is constant
    along a row (so the years compare), while the colour scale is constant
    everywhere (so the rows compare).
    """
    label = ZONE_LABELS.get(zone, zone)
    farm_slugs = [s for s in FARM_ORDER if s in set(fields["farm_slug"].astype(str))]
    if not farm_slugs:
        farm_slugs = sorted(set(fields["farm_slug"].astype(str)))
    start_md, end_md = season_window(farm_slugs[0])
    vmin, vmax = scale or _scale(summary["season_mean_ndvi"])
    norm = Normalize(vmin, vmax)
    cmap = plt.get_cmap("RdYlGn")

    by_key = {
        (str(row.field_slug), int(row.year)): row
        for row in summary.itertuples(index=False)
    }
    order = fields.reset_index(drop=True)

    # ---------------------------------------------------------------- ranking
    # A season that was not managed has no NDVI to rank: count only managed
    # field-years, so "(3/5)" means three cropped seasons, not five panels.
    managed_summary = summary[summary["status"].astype(str) != "not_managed"]
    integral = managed_summary.groupby("field_slug")["season_integral_ndvi_months"].mean()
    observed_years = (
        managed_summary.groupby("field_slug")["season_integral_ndvi_months"].count()
    )
    ranking = pd.DataFrame({"integral": integral, "n": observed_years})
    ranking = ranking.reindex(order["field_slug"].astype(str))
    ranking["name"] = order["paddock_name"].astype(str).to_numpy()
    ranking = ranking.dropna(subset=["integral"]).sort_values("integral")

    # ------------------------------------------------------- one frame per farm
    fig_w = 18.0
    lm_in, rm_in = 1.95, 0.55
    col_gap_in = 0.30
    pw_cap_in = (fig_w - lm_in - rm_in - (len(YEARS) - 1) * col_gap_in) / len(YEARS)
    row_h_cap_in = 3.60

    rows: list[dict] = []
    for farm_slug in farm_slugs:
        sub = order[order["farm_slug"].astype(str) == farm_slug].reset_index(drop=True)
        ff = frame_for(sub, panel_h_in=4.0, panel_w_in=4.0, scale_len=1000.0)
        tb = ff.geometry.total_bounds
        vx0, vx1, vy0, vy1 = _fflib.view_extent(tb[0], tb[2], tb[1], tb[3])
        aspect = (vx1 - vx0) / (vy1 - vy0)  # width / height
        pw_in = float(min(pw_cap_in, row_h_cap_in * aspect))
        rows.append(
            {
                "farm_slug": farm_slug,
                "fields": sub,
                "frame": ff,
                "extent": (vx0, vx1, vy0, vy1),
                "w": pw_in,
                "h": pw_in / aspect,
            }
        )

    # ------------------------------------------------------------- page layout
    top_margin_in = 0.42
    head_h_in = 1.52
    cap_h_in = 0.52
    row_gap_in = 0.34
    cursor_in = top_margin_in + head_h_in
    for row in rows:
        row["cap_top"] = cursor_in
        row["top"] = cursor_in + cap_h_in
        row["bottom"] = row["top"] + row["h"]
        cursor_in = row["bottom"] + row_gap_in
    rank_top_in = cursor_in + 0.06
    rank_h_in = float(max(2.30, 0.215 * len(ranking) + 0.90))
    rank_bot_in = rank_top_in + rank_h_in
    foot_top_in = rank_bot_in + 0.30
    fig_h = foot_top_in + 1.30

    def _fraction(offset_in: float) -> float:
        return 1.0 - offset_in / fig_h

    fig = plt.figure(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("#fbfaf8")
    avail_in = fig_w - lm_in - rm_in

    # ------------------------------------------------------------------ header
    ax_head = fig.add_axes(
        [lm_in / fig_w, _fraction(top_margin_in + head_h_in), avail_in / fig_w,
         head_h_in / fig_h]
    )
    ax_head.axis("off")
    total_ha = float(fields.to_crs(3577).area.sum() / 10_000.0)
    ax_head.text(0.0, 0.94, f"{label} — management zone", fontsize=27,
                 fontweight="bold", color="#0b1220", transform=ax_head.transAxes)
    farms_text = ", ".join(FARM_NAME_BY_SLUG.get(s, s) for s in farm_slugs)
    ax_head.text(
        0.0, 0.66,
        f"{len(fields)} paddocks · {total_ha:,.0f} ha · {farms_text}",
        fontsize=12.6, color="#3f3f46", transform=ax_head.transAxes,
    )
    ax_head.text(
        0.0, 0.26,
        f"Season window {start_md} → {end_md} · Sentinel-2 L2A 10 m, "
        f"one best cloud-screened scene per month · colour = mean of observed "
        f"monthly median NDVI, one fixed scale for all years and zones",
        fontsize=9.8, color="#52525b", transform=ax_head.transAxes,
    )
    ax_head.plot([0, 1], [0.06, 0.06], color=GREY, lw=1.0,
                 transform=ax_head.transAxes, clip_on=False)

    # ------------------------------------------------------- farm × year grid
    for row in rows:
        # left-align every row: the row label must sit beside its first panel,
        # and a narrow farm must not drift away from its own name
        x_in = lm_in
        row_fields = row["fields"]
        farm_label = FARM_NAME_BY_SLUG.get(row["farm_slug"], row["farm_slug"])
        farm_ha = float(row_fields.to_crs(3577).area.sum() / 10_000.0)
        label_mid_in = row["top"] + row["h"] / 2.0
        fig.text(
            (lm_in - 0.18) / fig_w, _fraction(label_mid_in - 0.10),
            farm_label, ha="right", va="center", fontsize=10.8,
            color="#0b1220", fontweight="bold",
        )
        fig.text(
            (lm_in - 0.18) / fig_w, _fraction(label_mid_in + 0.13),
            f"{len(row_fields)} paddock{'s' if len(row_fields) != 1 else ''}"
            f" · {farm_ha:,.0f} ha",
            ha="right", va="center", fontsize=8.6, color="#52525b",
        )

        for index, year in enumerate(YEARS):
            ax = fig.add_axes(
                [x_in / fig_w, _fraction(row["bottom"]), row["w"] / fig_w,
                 row["h"] / fig_h]
            )
            x_in += row["w"] + col_gap_in
            observed = [
                by_key[(slug, year)]
                for slug in row_fields["field_slug"].astype(str)
                if (slug, year) in by_key
            ]
            values, colors, managed, no_scene = [], [], [], []
            for slug in row_fields["field_slug"].astype(str):
                record = by_key.get((slug, year))
                if record is None or str(record.status) == "not_managed":
                    managed.append(False)
                    no_scene.append(False)
                    colors.append(GREY)
                    values.append(np.nan)
                    continue
                managed.append(True)
                value = record.season_mean_ndvi
                if pd.isna(value):
                    no_scene.append(True)
                    colors.append("#f4f4f5")
                    values.append(np.nan)
                else:
                    no_scene.append(False)
                    colors.append(cmap(norm(float(value))))
                    values.append(float(value))

            vx0, vx1, vy0, vy1 = row["extent"]
            if not OFFLINE:
                drew = _bm.draw(ax, row["frame"], (vx0, vx1, vy0, vy1), 620, 620)
            else:
                drew = False
            framed = row_fields.copy()
            framed["geometry"] = row["frame"].geometry.geometry.to_numpy()
            framed = framed.set_crs(row["frame"].epsg, allow_override=True)
            # the colour list mixes a named grey, a hex fill and RGBA tuples
            face_colors = [to_rgba(colour) for colour in colors]
            framed.plot(ax=ax, color=face_colors, edgecolor="#1f2937", linewidth=0.6,
                        alpha=0.88 if drew else 1.0, zorder=2)
            absent = framed[[not m for m in managed]]
            if len(absent):
                absent.plot(ax=ax, facecolor=(1, 1, 1, 0.0), edgecolor="#52525b",
                            hatch="///", linewidth=0.5, zorder=3)
            blind = framed[[bool(no_s) for no_s in no_scene]]
            if len(blind):
                blind.plot(ax=ax, facecolor=(1, 1, 1, 0.35), edgecolor="#a1a1aa",
                           hatch="...", linewidth=0.5, zorder=3)
            framed.boundary.plot(ax=ax, color="#1f2937", linewidth=0.6, zorder=4)

            named = len(framed) <= 8 and row["w"] >= 1.85
            for record, value in zip(framed.itertuples(index=False), values, strict=True):
                point = record.geometry.representative_point()
                if np.isfinite(value):
                    ax.annotate(
                        f"{value:.2f}",
                        (point.x, point.y),
                        fontsize=6.4 if len(framed) > 10 else 7.6,
                        ha="center", va="center", color="#0b1220", zorder=6,
                        path_effects=[_pe.withStroke(linewidth=2.0,
                                                     foreground="#ffffff")],
                    )
                if named:
                    ax.annotate(
                        str(record.paddock_name), (point.x, point.y),
                        xytext=(0, 12 if np.isfinite(value) else 0),
                        textcoords="offset points", fontsize=6.4, ha="center",
                        va="bottom", color="#111827", zorder=6,
                        path_effects=[_pe.withStroke(linewidth=1.8,
                                                     foreground="#ffffff")],
                    )

            ax.set_xlim(vx0, vx1)
            ax.set_ylim(vy0, vy1)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_aspect("equal")
            ax.set_anchor("N")
            ax.set_xlabel("")
            ax.set_ylabel("")
            for spine in ax.spines.values():
                spine.set_color(GREY)
            # in the caption band: inside the frame it always lands on some
            # paddock's value label, and every panel is a different patchwork.
            # A north arrow on a strongly rotated frame points sideways, so the
            # anchor moves left to keep the arrowhead inside this panel.
            ff_row = row["frame"]
            sideways = abs(ff_row.adx) > abs(ff_row.ady)
            decorate(ax, ff_row, arrow_r=0.045, arrow_fs=7.6, callout=False,
                     arrow_xy=(0.785 if sideways else 0.965, 1.085))

            managed = [r for r in observed if str(r.status) != "not_managed"]
            months = [int(r.months_observed) for r in managed
                      if hasattr(r, "months_observed") and pd.notna(r.months_observed)]
            expected = [int(r.months_expected) for r in managed
                        if hasattr(r, "months_expected") and pd.notna(r.months_expected)]
            ax.annotate(
                f"{year}", (0, 1), xycoords="axes fraction", xytext=(0, 26),
                textcoords="offset points", fontsize=11.4, fontweight="bold",
                color="#0b1220", ha="left", va="top",
            )
            if managed and months and expected:
                common = pd.Series(months).mode()
                detail = (
                    f"{len(managed)}/{len(row_fields)} "
                    f"paddock{'s' if len(row_fields) != 1 else ''} · "
                    f"{int(common.iloc[0])}/{int(np.max(expected))} months"
                )
            elif not managed:
                detail = (
                    f"0/{len(row_fields)} "
                    f"paddock{'s' if len(row_fields) != 1 else ''} · not managed"
                )
                ax.annotate(
                    detail, (0, 1), xycoords="axes fraction", xytext=(0, 12),
                    textcoords="offset points", fontsize=7.5, color="#3f3f46",
                    ha="left", va="top",
                )
            else:
                ax.annotate(
                    f"{len(managed)}/{len(row_fields)} paddocks",
                    (0, 1), xycoords="axes fraction", xytext=(0, 12),
                    textcoords="offset points", fontsize=7.5, color="#3f3f46",
                    ha="left", va="top",
                )

    # ----------------------------------------------------------------- ranking
    ax_rank = fig.add_axes(
        [lm_in / fig_w, _fraction(rank_bot_in),
         (fig_w - lm_in - rm_in) / fig_w, rank_h_in / fig_h]
    )
    if ranking.empty:
        ax_rank.axis("off")
    else:
        complete = ranking["n"] >= len(YEARS)
        bars = ax_rank.barh(
            range(len(ranking)), ranking["integral"],
            height=0.66,
            color=["#0f766e" if c else "#99f6e4" for c in complete],
            edgecolor=["#134e4a" if c else "#0f766e" for c in complete],
            linewidth=0.7,
        )
        ax_rank.set_yticks(range(len(ranking)))
        ax_rank.set_yticklabels(
            [f"{n}  ({int(k)}/{len(YEARS)})" for n, k in
             zip(ranking["name"], ranking["n"], strict=True)],
            fontsize=8.4,
        )
        for bar, value in zip(bars, ranking["integral"], strict=True):
            ax_rank.text(value + 0.04, bar.get_y() + bar.get_height() / 2,
                         f"{value:.1f}", va="center", fontsize=7.6, color="#0f172a")
        ax_rank.set_xlabel("mean NDVI-months over the seasons observed", fontsize=8.6)
        ax_rank.tick_params(axis="x", labelsize=7.8)
        ax_rank.grid(axis="x", alpha=0.20, lw=0.6)
        ax_rank.set_xlim(0, float(ranking["integral"].max()) * 1.10)
        for side in ("top", "right"):
            ax_rank.spines[side].set_visible(False)
        ax_rank.set_title(
            "Five-year ranking  ·  bracketed = seasons observed; pale bars had "
            "absent years, so their mean covers the seasons present only",
            fontsize=9.6, loc="left", pad=8, color="#3f3f46",
        )

    # ------------------------------------------------------- scale + legend row
    cax = fig.add_axes(
        [lm_in / fig_w, _fraction(foot_top_in + 0.46), 3.05 / fig_w, 0.15 / fig_h]
    )
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("season mean NDVI — one scale for all years and all zones",
                 fontsize=8.8, labelpad=3)
    cb.ax.tick_params(labelsize=7.4)

    ax_leg = fig.add_axes(
        [(lm_in + 3.45) / fig_w, _fraction(foot_top_in + 0.70), 0.62, 0.30 / fig_h]
    )
    ax_leg.axis("off")
    handles = [
        Patch(facecolor=GREY, edgecolor="#52525b", hatch="///",
              label="not managed that year (absent from the crop record)"),
        Patch(facecolor="#f4f4f5", edgecolor="#a1a1aa", hatch="...",
              label="managed, no scene passed the visibility gate"),
        Line2D([0], [0], marker=">", color="none", markerfacecolor="#111827",
               markeredgecolor="#111827", markersize=9,
               label="true north (per panel)"),
    ]
    ax_leg.legend(handles=handles, loc="center left", frameon=False, fontsize=8.6,
                  ncol=2, handlelength=1.9, columnspacing=1.6)

    fig.text(
        lm_in / fig_w, 0.010,
        "Not managed years are absent from the record, never imputed. Each farm "
        "row keeps its own frame, so paddock sizes compare within a row only; the "
        "colour scale is shared by every panel on all three zone posters. Each panel "
        "prints its year, the paddocks managed and the paddock-months observed, with "
        "true north beside the caption.\n"
        "Sources: Sentinel-2 L2A surface reflectance with SCL cloud/shadow mask; grower crop "
        "records; grower-supplied paddock boundaries.",
        fontsize=6.8, color="#71717a", va="bottom",
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG",
                                                                "au-grain-grower"))
    parser.add_argument("--zone", default=None, choices=ZONES)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    fields = _zone_frames(args.grower_slug)
    out_dir = args.out_dir or (DATA_ROOT / "growers" / args.grower_slug / "derived" / "reports")
    selected = [args.zone] if args.zone else list(ZONES)
    prepared = []
    summaries = []
    for zone in selected:
        subset = fields[fields["zone"] == zone]
        if subset.empty:
            print(f"skip  {zone}: no fields")
            continue
        summary = _zone_summaries(args.grower_slug, subset)
        if summary.empty:
            print(f"skip  {zone}: no NDVI summaries yet")
            continue
        prepared.append((zone, subset, summary))
        summaries.append(summary)

    # The scale is a property of the whole portfolio, not of this invocation:
    # rendering one zone must not silently give it a different scale from the
    # other two sheets.
    all_summaries = []
    for zone in ZONES:
        subset = fields[fields["zone"] == zone]
        if subset.empty:
            continue
        summary = _zone_summaries(args.grower_slug, subset)
        if not summary.empty:
            all_summaries.append(summary)
    scale = _shared_scale(all_summaries or summaries)
    written = []
    for zone, subset, summary in prepared:
        out = out_dir / f"zone_poster_{zone}.png"
        written.append(render_zone(grower=args.grower_slug, zone=zone, fields=subset,
                                   summary=summary, out_png=out, scale=scale))
        print(f"{out}  (scale {scale[0]:.2f}-{scale[1]:.2f})")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
