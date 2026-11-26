#!/usr/bin/env python3
"""Render the 0-30 cm soil-property map sheet for one AU paddock.

One PNG per field: terrain relief under the field, then clay, pH, CEC and
organic carbon as 0-30 cm thickness-weighted means, plus a text panel carrying
the paddock's own numbers so the map reads without the CSV.

The shared SoilGrids renderer (``reporting/generate_soil_grid_maps.py``)
hardcodes 0-5 cm labels and ISRIC band names, so this stage composes the sheet
itself from the same locked pieces: ``lib.field_frame`` for the frame contract,
``lib.basemap`` for the underlay, ``lib.palettes`` for the ramps.
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
import rasterio  # noqa: E402
from matplotlib.colors import LightSource, Normalize  # noqa: E402

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import basemap as _bm  # noqa: E402
import field_frame as _fflib  # noqa: E402
import palettes  # noqa: E402
from field_frame import (  # noqa: E402
    crs_note,
    decorate,
    frame_for,
    reproject_into_frame,
)
from paths import farm_dir, field_boundary_path, field_dir, field_feature_path  # noqa: E402

_OFFLINE = os.environ.get("AG_OFFLINE") == "1"

# band key -> (ISRIC palette stem, label, unit, raster stem)
PANELS = (
    ("clay", "clay", "Clay 0–30 cm", "%", "clay"),
    ("phcac2", "phcac2", "pH (CaCl$_2$) 0–30 cm", "pH", "phcalciumchloride"),
    ("cec", "cec", "CEC 0–30 cm", "meq/100 g", "cec"),
    ("soc", "soc", "Organic carbon 0–30 cm", "%", "carbon"),
)


def _soil_dir(grower: str, farm: str, slug: str) -> Path:
    return field_dir(grower, farm, slug) / "soil" / "slga_asf"


def _dem_path(grower: str, farm: str, slug: str) -> Path:
    return field_dir(grower, farm, slug) / "terrain" / "dem" / "dem_clipped.tif"


def _field_numbers(grower: str, farm: str, slug: str) -> dict:
    path = field_dir(grower, farm, slug) / "soil" / "slga_asf_profile.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype={"field_slug": str})
    if frame.empty:
        return {}
    return frame.iloc[0].to_dict()


def _panel(ax, ff, path: Path, ff_lib, *, code: str | None, label: str, unit: str):
    if not path.exists():
        ax.text(0.5, 0.5, f"no {label.split(' ')[0].lower()} raster",
                ha="center", va="center", transform=ax.transAxes, fontsize=8)
        ax.set_xticks([])
        ax.set_yticks([])
        return None
    with rasterio.open(path) as ds:
        arr, ext = ff_lib.reproject_into_frame(
            ds, ff, dst_res=max(float(ds.res[0]), 5.0)
        )
    ny, nx = arr.shape
    if not _OFFLINE:
        _bm.draw(ax, ff, ext, ny, nx)
    inside = np.asarray(arr.compressed(), dtype="float64")
    cmap, norm, ticks = palettes.soil_scale(code or "soc", inside)
    ticks = palettes.readable_ticks(norm, ticks)
    im = ax.imshow(arr, cmap=cmap, norm=norm, alpha=0.86, zorder=1,
                   extent=[ext[0], ext[1], ext[2], ext[3]], origin="upper")
    ff.geometry.boundary.plot(ax=ax, color="#111827", lw=1.1, zorder=4)
    # the rotation note would run off a narrow panel; the footer carries it
    decorate(ax, ff, arrow_r=0.085, arrow_fs=11.0, callout=False,
             arrow_xy=(0.89, 0.78))
    mean_v = float(np.nanmean(inside)) if inside.size else float("nan")
    # Print the observed spread next to the mean: a uniformly acid paddock is
    # a flat map, not a broken one, and the numbers say so.
    if inside.size:
        title = (f"{label} · mean {mean_v:.2f} {unit} "
                 f"({inside.min():.2f}–{inside.max():.2f})")
    else:
        title = f"{label} · mean {mean_v:.2f} {unit}".strip()
    ax.set_title(title, fontsize=9.4, pad=6)
    cb = ax.figure.colorbar(im, ax=ax, orientation="horizontal", fraction=0.048,
                            pad=0.17, aspect=32,
                            **({"ticks": ticks} if ticks else {}))
    cb.set_label(unit, fontsize=7.0)
    cb.ax.tick_params(labelsize=6.4)
    return im


def render_field(
    *,
    grower: str,
    farm: str,
    slug: str,
    name: str,
    out_png: Path,
    caption_extra: str = "",
) -> Path:
    import geopandas as gpd

    boundary = gpd.read_file(field_boundary_path(grower, farm, slug)).to_crs(4326)
    ff = frame_for(boundary, panel_h_in=2.7, panel_w_in=2.7)
    soil = _soil_dir(grower, farm, slug)
    numbers = _field_numbers(grower, farm, slug)

    fig = plt.figure(figsize=(16.0, 10.6))
    fig.patch.set_facecolor("#fafaf9")
    grid = fig.add_gridspec(2, 3, hspace=0.26, wspace=0.26,
                            left=0.045, right=0.975, top=0.925, bottom=0.075)

    ax_dem = fig.add_subplot(grid[0, 0])
    dem = _dem_path(grower, farm, slug)
    if dem.exists():
        with rasterio.open(dem) as ds:
            arr, ext = reproject_into_frame(ds, ff, dst_res=max(float(ds.res[0]), 5.0))
        ny, nx = arr.shape
        if not _OFFLINE:
            _bm.draw(ax_dem, ff, ext, ny, nx)
        finite = np.asarray(arr.compressed(), dtype="float64")
        lo, hi = (np.percentile(finite, [1, 99]) if finite.size else (0.0, 1.0))
        hs = np.ma.masked_invalid(
            LightSource().hillshade(np.ma.filled(arr, np.nan), vert_exag=2.0)
        )
        ax_dem.imshow(hs, cmap="gray", vmin=0, vmax=1, alpha=0.55, zorder=0,
                      extent=[ext[0], ext[1], ext[2], ext[3]], origin="upper")
        im = ax_dem.imshow(arr, cmap=palettes.dem_scale(finite)[0],
                           norm=Normalize(lo, hi), alpha=0.62, zorder=1,
                           extent=[ext[0], ext[1], ext[2], ext[3]], origin="upper")
        cb = fig.colorbar(im, ax=ax_dem, orientation="horizontal", fraction=0.048,
                          pad=0.17, aspect=32)
        cb.set_label("elevation (m)", fontsize=7.0)
        cb.ax.tick_params(labelsize=6.4)
        relief = float(np.nanmax(finite) - np.nanmin(finite)) if finite.size else float("nan")
        ax_dem.set_title(f"Terrain · relief {relief:.0f} m", fontsize=9.4, pad=6)
        ff.geometry.boundary.plot(ax=ax_dem, color="#111827", lw=1.1, zorder=4)
        decorate(ax_dem, ff, arrow_r=0.085, arrow_fs=11.0, callout=False,
                 arrow_xy=(0.89, 0.78))
    else:
        ax_dem.text(0.5, 0.5, "no DEM", ha="center", transform=ax_dem.transAxes)
        ax_dem.set_xticks([])
        ax_dem.set_yticks([])

    for ax, (code, _stem, label, unit, file_stem) in zip(
        [fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[0, 2]),
         fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 1])],
        PANELS,
        strict=True,
    ):
        _panel(ax, ff, soil / f"{file_stem}_0_30cm.tif", _fflib,
               code=code, label=label, unit=unit)

    ax_txt = fig.add_subplot(grid[1, 2])
    ax_txt.axis("off")
    texture = str(numbers.get("texture_usda_0_30", "") or "unknown")
    lines = [
        f"Texture (0–30 cm): {texture}",
        "",
        f"Clay        {numbers.get('CLAY_0_30', float('nan')):.1f} %",
        f"Silt        {numbers.get('SILT_0_30', float('nan')):.1f} %",
        f"Sand        {numbers.get('SAND_0_30', float('nan')):.1f} %",
        f"pH (CaCl2)  {numbers.get('PHCALCIUMCHLORIDE_0_30', float('nan')):.2f}",
        f"CEC         {numbers.get('CEC_0_30', float('nan')):.1f} meq/100 g",
        f"Org. carbon {numbers.get('CARBON_0_30', float('nan')):.2f} %",
        "",
        f"Carbon stock 0–30 cm   {numbers.get('carbon_stock_t_ha_0_30', float('nan')):.0f} t/ha",
        f"Carbon stock 0–100 cm  {numbers.get('carbon_stock_t_ha_0_100', float('nan')):.0f} t/ha",
        "",
        "Source: SLGA/ASF (0–5, 5–15, 15–30 cm slabs,",
        "thickness-weighted). Polygon statistics.",
    ]
    ax_txt.text(0.0, 0.96, "\n".join(lines), va="top", ha="left",
                fontsize=8.2, family="monospace", color="#0f172a",
                transform=ax_txt.transAxes)
    ax_txt.set_title("Paddock values", fontsize=9.4, loc="left", pad=6)

    fig.suptitle(
        f"{name} — {farm.replace('-', ' ').title()} · soil properties (0–30 cm)",
        fontsize=17, fontweight="bold", x=0.045, y=0.985, ha="left",
    )
    fig.text(0.045, 0.012, crs_note(ff, full=bool(ff.rotated)) + (
        f"  |  {caption_extra}" if caption_extra else ""
    ), fontsize=7.2, color="#475569")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=165, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG", ""))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG", ""))
    parser.add_argument("--field", default=None, help="single field slug; default all")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.grower_slug or not args.farm_slug:
        raise SystemExit("AG_GROWER_SLUG and AG_FARM_SLUG are required")

    names_path = farm_dir(args.grower_slug, args.farm_slug) / "manifests" / "field-names.csv"
    names = pd.read_csv(names_path, dtype={"field_slug": str, "field_id": str})
    if args.field:
        names = names[names["field_slug"].astype(str) == str(args.field)]
    if names.empty:
        raise SystemExit("no fields to render")

    for row in names.itertuples(index=False):
        out = (
            args.out_dir / f"soil_map_{row.paddock_name.replace(' ', '_')}.png"
            if args.out_dir
            else field_feature_path(args.grower_slug, args.farm_slug,
                                    str(row.field_slug), "soil_map_0_30.png")
        )
        render_field(grower=args.grower_slug, farm=args.farm_slug,
                     slug=str(row.field_slug), name=str(row.paddock_name), out_png=out)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
