#!/usr/bin/env python3
"""Build the three zone reports in Markdown and self-contained HTML.

One report per management zone, assembled from the same tables the posters and
cards read, so a number in the report and a colour on the poster cannot drift
apart:

    derived/reports/zone_report_<zone>.md      relative image link
    derived/reports/zone_report_<zone>.html    poster embedded as base64

The HTML carries no external references - no CDN, no fonts, no map tiles - so
it opens and prints offline from any checkout. Both formats are generated from
committed code and runtime CSVs; nothing is hand-edited.
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_AU_DIR = Path(__file__).resolve().parent
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib", _AU_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.au_common import (  # noqa: E402
    FARM_NAME_BY_SLUG,
    ZONE_LABELS,
    season_window,
)
from lib.paths import DATA_ROOT, farm_dir, farm_tables_dir  # noqa: E402
from render_au_zone_posters import ZONES, _zone_frames, _zone_summaries  # noqa: E402

YEARS = (2022, 2023, 2024, 2025, 2026)

PADDOCK_COLUMNS = (
    ("paddock_name", "Paddock"),
    ("farm_label", "Farm"),
    ("area_ha", "ha"),
    ("years_observed", "Seasons"),
    ("ndvi_mean", "Mean season NDVI"),
    ("ndvi_2022", "2022"),
    ("ndvi_2023", "2023"),
    ("ndvi_2024", "2024"),
    ("ndvi_2025", "2025"),
    ("ndvi_2026", "2026"),
    ("texture_usda_0_30", "Texture 0–30"),
    ("CLAY_0_30", "Clay %"),
    ("PHCALCIUMCHLORIDE_0_30", "pH"),
    ("CEC_0_30", "CEC"),
    ("CARBON_0_30", "OC %"),
    ("carbon_stock_t_ha_0_100", "C stock 0–100 t/ha"),
)

FORMATS = {
    "CLAY_0_30": "{:.1f}",
    "PHCALCIUMCHLORIDE_0_30": "{:.2f}",
    "CEC_0_30": "{:.1f}",
    "CARBON_0_30": "{:.2f}",
    "carbon_stock_t_ha_0_100": "{:.0f}",
    "area_ha": "{:.1f}",
    "ndvi_mean": "{:.3f}",
    "ndvi_2022": "{:.3f}",
    "ndvi_2023": "{:.3f}",
    "ndvi_2024": "{:.3f}",
    "ndvi_2025": "{:.3f}",
    "ndvi_2026": "{:.3f}",
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _texture_sheets(grower: str, farm_slugs) -> list[tuple[str, Path]]:
    """The per-farm texture sheet, one per farm in the zone, in farm order."""
    sheets = []
    for slug in farm_slugs:
        path = (farm_dir(grower, str(slug)) / "derived" / "figures" / "soil"
                / f"soil_texture_triangle_{slug}.png")
        if path.exists():
            sheets.append((FARM_NAME_BY_SLUG.get(str(slug), str(slug)), path))
    return sheets


def _soil_profiles(grower: str, farm_slugs) -> pd.DataFrame:
    frames = []
    for slug in farm_slugs:
        path = farm_tables_dir(grower, str(slug)) / "slga_asf_profile.csv"
        if path.exists():
            frames.append(pd.read_csv(path, dtype={"field_id": str, "field_slug": str}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_table(grower: str, zone: str, fields, summary: pd.DataFrame) -> pd.DataFrame:
    farm_slugs = list(fields["farm_slug"].unique())
    soil = _soil_profiles(grower, farm_slugs)
    table = fields.copy()
    table["area_ha"] = table.to_crs(3577).area / 10_000.0
    table["farm_label"] = table["farm"]

    # A not-managed paddock-year has no crop and its NDVI is context only. It
    # must not appear as a season, in the five-year mean, or in a year column.
    managed = summary[summary["status"].astype(str) != "not_managed"]
    per_year = managed.pivot_table(index="field_slug", columns="year",
                                   values="season_mean_ndvi", aggfunc="first")
    per_year.columns = [f"ndvi_{int(c)}" for c in per_year.columns]
    stats = managed.groupby("field_slug").agg(
        years_observed=("season_mean_ndvi", "count"),
        ndvi_mean=("season_mean_ndvi", "mean"),
    )
    table = table.merge(per_year, left_on="field_slug", right_index=True, how="left")
    table = table.merge(stats, left_on="field_slug", right_index=True, how="left")

    keep = ["field_slug"] + [c for c, _ in PADDOCK_COLUMNS if c in table.columns]
    table = table[keep]
    if not soil.empty:
        soil = soil.drop(columns=[c for c in ("paddock_name", "farm", "region")
                                  if c in soil.columns])
        table = table.merge(soil, on="field_slug", how="left")
    for key in FORMATS:
        if key in table.columns:
            table[key] = pd.to_numeric(table[key], errors="coerce")
    return table.sort_values(["farm_label", "paddock_name"]).reset_index(drop=True)


def _absent_keys(summary: pd.DataFrame) -> set[tuple[str, int]]:
    return {
        (str(row.field_slug), int(row.year))
        for row in summary.itertuples(index=False)
        if str(row.status) == "not_managed"
    }


def _cells(row, absent: set[tuple[str, int]]) -> list[str]:
    """Formatted cells; an absent crop year reads as words, never as NDVI."""
    out = []
    for key, _ in PADDOCK_COLUMNS:
        if key.startswith("ndvi_") and key[5:].isdigit():
            if (str(row.field_slug), int(key[5:])) in absent:
                out.append("not managed")
                continue
        out.append(_fmt(getattr(row, key, None), key))
    return out


def _fmt(value, key: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if key in FORMATS:
        try:
            return FORMATS[key].format(float(value))
        except (TypeError, ValueError):
            return "—"
    return str(value)


def _markdown(grower: str, zone: str, fields, summary: pd.DataFrame,
              table: pd.DataFrame, poster: Path,
              sheets: list[tuple[str, Path]] | None = None) -> str:
    label = ZONE_LABELS.get(zone, zone)
    window = season_window(fields["farm_slug"].iloc[0])
    total_ha = float(table["area_ha"].sum())
    farms = ", ".join(sorted(table["farm_label"].unique()))

    lines = [
        f"# {label} — management zone",
        "",
        f"**{len(table)} paddocks · {total_ha:,.0f} ha · {farms}**",
        "",
        f"Season window {window[0]} → {window[1]} · Sentinel-2 L2A 10 m · "
        f"one best cloud-screened scene per month · generated {_now()}",
        "",
        f"![{label} zone poster]({poster.name})",
        "",
        "## Season by season",
        "",
        "| Year | Paddocks managed | Paddock-months observed | Zone mean season NDVI | Crops present |",
        "|---|---|---|---|---|",
    ]
    for year in YEARS:
        rows = summary[
            (summary["year"] == year)
            & (summary["status"].astype(str) != "not_managed")
        ]
        crops = sorted({str(c) for c in rows["crop"].dropna() if str(c) not in ("", "nan")})
        mean = rows["season_mean_ndvi"].mean()
        # count every paddock-month, not the median paddock: six paddocks short
        # of a month must not read as "8/8"
        months = pd.to_numeric(rows["months_observed"], errors="coerce").sum()
        expected = pd.to_numeric(rows["months_expected"], errors="coerce").sum()
        month_text = f"{int(months)}/{int(expected)}" if expected else "—"
        lines.append(
            f"| {year} | {len(rows)}/{len(table)} | {month_text} | "
            f"{_fmt(mean, 'ndvi_mean')} | {', '.join(crops) if crops else '—'} |"
        )

    lines += ["", "## Paddocks", ""]
    headers = [title for _, title in PADDOCK_COLUMNS]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    absent = _absent_keys(summary)
    for row in table.itertuples(index=False):
        lines.append("| " + " | ".join(_cells(row, absent)) + " |")

    soil = table.dropna(subset=["CLAY_0_30"]) if "CLAY_0_30" in table.columns else table.iloc[:0]
    if not soil.empty:
        lines += ["", "## Soil (0–30 cm unless stated)", ""]
        lines.append(f"- Paddocks with SLGA/ASF coverage: {len(soil)}")
        lines.append(f"- Clay: {soil['CLAY_0_30'].mean():.1f} % "
                     f"(range {soil['CLAY_0_30'].min():.1f}–{soil['CLAY_0_30'].max():.1f})")
        lines.append(f"- pH (CaCl₂): {soil['PHCALCIUMCHLORIDE_0_30'].mean():.2f} "
                     f"(range {soil['PHCALCIUMCHLORIDE_0_30'].min():.2f}–"
                     f"{soil['PHCALCIUMCHLORIDE_0_30'].max():.2f})")
        lines.append(f"- CEC: {soil['CEC_0_30'].mean():.1f} meq/100 g")
        lines.append(f"- Organic carbon: {soil['CARBON_0_30'].mean():.2f} %")
        lines.append(f"- Carbon stock 0–100 cm: {soil['carbon_stock_t_ha_0_100'].mean():.0f} t/ha")
        textures = soil["texture_usda_0_30"].value_counts()
        lines.append("- Texture: " + ", ".join(f"{k} ({v})" for k, v in textures.items()))
        for farm_label, sheet in sheets or []:
            rel = os.path.relpath(sheet, poster.parent).replace(os.sep, "/")
            lines += ["", f"**{farm_label} texture**", "", f"![{farm_label} soil texture]({rel})"]

    managed_summary = summary[summary["status"].astype(str) != "not_managed"]
    gaps = managed_summary[
        managed_summary["months_observed"] < managed_summary["months_expected"]
    ]
    absent = summary[summary["status"] == "not_managed"]
    lines += [
        "",
        "## Data notes",
        "",
        "- Colour on the poster is the mean of the observed monthly median NDVI, one "
        "fixed scale shared by all five years and all three zone posters.",
        "- Months that failed the paddock-visibility gate are reported, never filled. "
        "The season integral counts only observed months.",
        "- Paddocks absent from the crop record in a year are drawn as *not managed*; "
        "they are not imputed and they are excluded from that year's zone mean.",
        "- NDVI is not a yield measure. It reads canopy greenness at the overpass and "
        "is affected by grazing, sowing date and stubble.",
        "",
    ]
    if absent.empty:
        lines.append("- Every paddock in this zone is present in all five crop records.")
    else:
        pairs = ", ".join(
            f"{row.paddock_name} {int(row.year)}" for row in absent.itertuples(index=False)
        )
        lines.append(f"- Paddocks absent from the crop record: {pairs}.")
    if not gaps.empty:
        lines.append(
            f"- Incomplete seasons: {len(gaps)} of {len(managed_summary)} managed "
            f"field-years have at "
            "least one month without a usable scene."
        )
    lines += [
        "",
        "Sources: Sentinel-2 L2A surface reflectance (SCL cloud/shadow mask); "
        "SLGA/ASF soil property grids (five depth slabs, 0–100 cm); "
        "grower crop and variety records; grower-supplied paddock boundaries. "
        "Terrain from Copernicus GLO-30.",
        "",
    ]
    return "\n".join(lines)


HTML_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 42px 54px 64px; background: #fbfaf8; color: #18181b;
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
h1 { font-size: 34px; margin: 0 0 6px; letter-spacing: -0.4px; }
h2 { font-size: 20px; margin: 40px 0 12px; padding-bottom: 6px;
     border-bottom: 1px solid #e4e4e7; }
.sub { color: #52525b; font-size: 15px; margin: 0 0 4px; }
.meta { color: #71717a; font-size: 13px; margin: 0 0 26px; }
figure { margin: 26px 0 8px; }
/* every generated figure is wide: scale to the page, keep aspect, allow zoom */
figure img { width: 100%; height: auto; border: 1px solid #e4e4e7; border-radius: 4px;
             background: #fff; }
figcaption { color: #71717a; font-size: 12.5px; margin-top: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 10px 0 4px; }
th, td { padding: 5px 8px; text-align: right; border-bottom: 1px solid #ededf0;
         white-space: nowrap; }
th { text-align: right; font-weight: 600; color: #27272a; background: #f4f4f5;
     position: sticky; top: 0; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2),
th:nth-child(3), td:nth-child(3) { text-align: left; }
tbody tr:nth-child(even) { background: #fcfcfd; }
ul { padding-left: 20px; }
li { margin: 4px 0; }
dl.kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 12px; margin: 18px 0 6px; }
dl.kpi div { background: #fff; border: 1px solid #e4e4e7; border-radius: 6px;
             padding: 12px 14px; }
dl.kpi dt { color: #71717a; font-size: 11.5px; text-transform: uppercase;
            letter-spacing: 0.4px; }
dl.kpi dd { margin: 4px 0 0; font-size: 19px; font-weight: 650; }
footer { margin-top: 48px; padding-top: 14px; border-top: 1px solid #e4e4e7;
         color: #71717a; font-size: 12px; }
@media print {
  body { padding: 0; background: #fff; }
  figure img { border: none; }
  h2 { break-after: avoid; }
  tr, figure { break-inside: avoid; }
}
"""


def _table_html(table: pd.DataFrame, summary: pd.DataFrame) -> str:
    head = "".join(f"<th>{html.escape(title)}</th>" for _, title in PADDOCK_COLUMNS)
    body = []
    absent = _absent_keys(summary)
    for row in table.itertuples(index=False):
        cells = "".join(
            f"<td>{html.escape(value)}</td>" for value in _cells(row, absent)
        )
        body.append(f"<tr>{cells}</tr>")
    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _html(grower: str, zone: str, fields, summary: pd.DataFrame,
          table: pd.DataFrame, poster: Path,
          sheets: list[tuple[str, Path]] | None = None) -> str:
    label = ZONE_LABELS.get(zone, zone)
    window = season_window(fields["farm_slug"].iloc[0])
    total_ha = float(table["area_ha"].sum())
    farms = ", ".join(sorted(table["farm_label"].unique()))
    encoded = base64.b64encode(poster.read_bytes()).decode("ascii")

    season_rows = []
    for year in YEARS:
        rows = summary[
            (summary["year"] == year)
            & (summary["status"].astype(str) != "not_managed")
        ]
        crops = sorted({str(c) for c in rows["crop"].dropna()
                        if str(c) not in ("", "nan")})
        months = pd.to_numeric(rows["months_observed"], errors="coerce").sum()
        expected = pd.to_numeric(rows["months_expected"], errors="coerce").sum()
        month_text = f"{int(months)}/{int(expected)}" if expected else "—"
        season_rows.append(
            f"<tr><td>{year}</td><td>{len(rows)}/{len(table)}</td>"
            f"<td>{month_text}</td><td>{_fmt(rows['season_mean_ndvi'].mean(), 'ndvi_mean')}</td>"
            f"<td style='text-align:left'>{html.escape(', '.join(crops) or '—')}</td></tr>"
        )

    soil = table.dropna(subset=["CLAY_0_30"]) if "CLAY_0_30" in table.columns else table.iloc[:0]
    soil_block = ""
    if not soil.empty:
        textures = soil["texture_usda_0_30"].value_counts()
        soil_block = (
            "<h2>Soil (0–30 cm unless stated)</h2><ul>"
            f"<li>Paddocks with SLGA/ASF coverage: {len(soil)}</li>"
            f"<li>Clay: {soil['CLAY_0_30'].mean():.1f} % "
            f"(range {soil['CLAY_0_30'].min():.1f}–{soil['CLAY_0_30'].max():.1f})</li>"
            f"<li>pH (CaCl₂): {soil['PHCALCIUMCHLORIDE_0_30'].mean():.2f} "
            f"(range {soil['PHCALCIUMCHLORIDE_0_30'].min():.2f}–"
            f"{soil['PHCALCIUMCHLORIDE_0_30'].max():.2f})</li>"
            f"<li>CEC: {soil['CEC_0_30'].mean():.1f} meq/100 g</li>"
            f"<li>Organic carbon: {soil['CARBON_0_30'].mean():.2f} %</li>"
            f"<li>Carbon stock 0–100 cm: "
            f"{soil['carbon_stock_t_ha_0_100'].mean():.0f} t/ha</li>"
            "<li>Texture: "
            + ", ".join(f"{html.escape(str(k))} ({v})" for k, v in textures.items())
            + "</li></ul>"
        )
        for farm_label, sheet in sheets or []:
            encoded_sheet = base64.b64encode(sheet.read_bytes()).decode("ascii")
            soil_block += (
                f"<figure><img alt='{html.escape(farm_label)} soil texture sheet' "
                f"src='data:image/png;base64,{encoded_sheet}'>"
                f"<figcaption>{html.escape(farm_label)} — texture triangle with one "
                "point per paddock and the paddock composition bars, 0–30 cm.</figcaption>"
                "</figure>"
            )

    absent = summary[summary["status"] == "not_managed"]
    managed_summary = summary[summary["status"].astype(str) != "not_managed"]
    gaps = managed_summary[
        managed_summary["months_observed"] < managed_summary["months_expected"]
    ]
    if absent.empty:
        absent_line = "Every paddock in this zone is present in all five crop records."
    else:
        absent_line = "Paddocks absent from the crop record: " + ", ".join(
            f"{html.escape(str(r.paddock_name))} {int(r.year)}"
            for r in absent.itertuples(index=False)
        ) + ". They are not imputed and are excluded from that year's zone mean."

    kpis = (
        f"<div><dt>Paddocks</dt><dd>{len(table)}</dd></div>"
        f"<div><dt>Area</dt><dd>{total_ha:,.0f} ha</dd></div>"
        f"<div><dt>Mean season NDVI</dt><dd>{_fmt(table['ndvi_mean'].mean(), 'ndvi_mean')}</dd></div>"
        f"<div><dt>Field-years observed</dt><dd>{int(table['years_observed'].sum())}"
        f"/{len(table) * len(YEARS)}</dd></div>"
    )

    return f"""<!doctype html>
<html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(label)} — management zone</title>
<style>{HTML_STYLE}</style></head><body>
<h1>{html.escape(label)} — management zone</h1>
<p class="sub">{len(table)} paddocks · {total_ha:,.0f} ha · {html.escape(farms)}</p>
<p class="meta">Season window {window[0]} → {window[1]} · Sentinel-2 L2A 10 m ·
one best cloud-screened scene per month · generated {_now()}</p>
<dl class="kpi">{kpis}</dl>
<figure>
  <img alt="{html.escape(label)} zone poster, five seasons side by side"
       src="data:image/png;base64,{encoded}">
  <figcaption>Five seasons side by side on one fixed colour scale. Hatching marks
  paddocks absent from the crop record and paddocks with no usable scene; the
  ranking strip lists the five-year mean season integral with the number of
  seasons observed.</figcaption>
</figure>
<h2>Season by season</h2>
<table><thead><tr><th style="text-align:left">Year</th>
<th style="text-align:left">Paddocks managed</th><th style="text-align:left">Paddock-months observed</th>
<th>Zone mean season NDVI</th><th style="text-align:left">Crops present</th></tr></thead>
<tbody>{''.join(season_rows)}</tbody></table>
<h2>Paddocks</h2>
{_table_html(table, summary)}
{soil_block}
<h2>Data notes</h2>
<ul>
<li>Colour is the mean of the observed monthly median NDVI; one fixed scale is
shared by all five years and by all three zone posters, so colour means the same
NDVI on every sheet.</li>
<li>Months that failed the paddock-visibility gate are reported, never filled. The
season integral counts only observed months.</li>
<li>{absent_line}</li>
<li>{f"Incomplete seasons: {len(gaps)} of {len(managed_summary)} managed field-years have at least one month without a usable scene." if not gaps.empty else "Every managed field-year in this zone has a complete set of monthly scenes."}</li>
<li>NDVI is not a yield measure. It reads canopy greenness at the overpass and is
affected by grazing, sowing date and stubble.</li>
</ul>
<footer>Sources: Sentinel-2 L2A surface reflectance (SCL cloud/shadow mask);
SLGA/ASF soil property grids (five depth slabs, 0–100 cm); grower crop and variety
records; grower-supplied paddock boundaries; Copernicus GLO-30 terrain.
Generated by <code>au/build_au_zone_reports.py</code>.</footer>
</body></html>
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG",
                                                                "au-grain-grower"))
    parser.add_argument("--zone", default=None, choices=ZONES)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    fields = _zone_frames(args.grower_slug)
    out_dir = args.out_dir or (DATA_ROOT / "growers" / args.grower_slug
                               / "derived" / "reports")
    written = []
    for zone in ([args.zone] if args.zone else ZONES):
        subset = fields[fields["zone"] == zone]
        if subset.empty:
            print(f"skip  {zone}: no fields")
            continue
        summary = _zone_summaries(args.grower_slug, subset)
        if summary.empty:
            print(f"skip  {zone}: no NDVI summaries yet")
            continue
        poster = out_dir / f"zone_poster_{zone}.png"
        if not poster.exists():
            print(f"skip  {zone}: poster missing ({poster.name}); run the poster stage first")
            continue
        table = build_table(args.grower_slug, zone, subset, summary)
        sheets = _texture_sheets(args.grower_slug, list(subset["farm_slug"].unique()))

        md_path = out_dir / f"zone_report_{zone}.md"
        md_path.write_text(
            _markdown(args.grower_slug, zone, subset, summary, table, poster, sheets),
            encoding="utf-8",
        )
        html_path = out_dir / f"zone_report_{zone}.html"
        html_path.write_text(
            _html(args.grower_slug, zone, subset, summary, table, poster, sheets),
            encoding="utf-8",
        )
        written.extend([md_path, html_path])
        print(f"{md_path}\n{html_path}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
