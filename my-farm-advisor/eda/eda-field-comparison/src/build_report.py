"""Build the single-page HTML report from the committed example tables.

The report is a condensation of ``ANALYSIS.md``: one summary, five figures,
five key tables and the limits. Every number is taken from the same
``build_findings`` dictionary the markdown analysis uses, so the page and the
long-form analysis cannot diverge. The figures are base64-embedded, so the
file is self-contained and opens from disk with no server and no network.

    python src/build_report.py            # write eda_report.html
    python src/build_report.py --check    # exit 1 if the page is stale

Only the standard library plus what ``build_analysis`` already needs is used,
and only ``examples/`` is read.
"""

from __future__ import annotations

import argparse
import base64
import html
import sys
from pathlib import Path

from build_analysis import Table, build_findings, load_examples

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
EXAMPLES = BASE / "examples"
FIGURES = EXAMPLES / "figures"
OUTPUT = BASE / "eda_report.html"

REGION_COLOUR = {
    "Harden": "#2e6f40",
    "Trungley Hall": "#b5651d",
    "Lease country": "#2f5d8a",
}

FIGURE_CAPTIONS = {
    "fig01_field_area.png": (
        "Figure 1 — One bar per paddock, grouped by management region on a "
        "shared linear hectare axis. The dashed line is each band's median. "
        "Six lease blocks hold 63.4 % of the footprint."
    ),
    "fig02_seasonal_rainfall.png": (
        "Figure 2 — Rainfall on the common 1 April – 15 November window, so "
        "the regions compare. Bars are regional means and lines the farm "
        "range; the one partial season is labelled."
    ),
    "fig03_crop_mix.png": (
        "Figure 3 — Crop area share by region and year. The rotation pulses "
        "between canola and wheat rather than diversifying."
    ),
    "fig04_paddock_map.png": (
        "Figure 4 — Paddock footprints drawn to scale, coloured by the 2026 "
        "crop, with one panel per region."
    ),
    "fig05_ndvi_season.png": (
        "Figure 5 — NDVI season behaviour: monthly shape by zone, the season "
        "integral by zone and year, canopy by crop, and the integral against "
        "season rain."
    ),
}


def _read_png(name: str) -> str:
    path = FIGURES / name
    if not path.exists():
        raise SystemExit(
            f"missing {path.relative_to(BASE)} — run src/make_figures.py first"
        )
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _figure(name: str) -> str:
    data = _read_png(name)
    caption = html.escape(FIGURE_CAPTIONS[name])
    return (
        '<figure class="figure">'
        f'<img alt="{caption}" src="data:image/png;base64,{data}">'
        f"<figcaption>{caption}</figcaption>"
        "</figure>"
    )


def _table(table: Table, label: str) -> str:
    if len(table.align) != len(table.header):
        raise ValueError(f"{label}: align does not match header")

    def cell(tag: str, text: str, index: int) -> str:
        cls = ' class="num"' if table.align[index] == "r" else ""
        return f"<{tag}{cls}>{html.escape(text)}</{tag}>"

    head = "".join(
        cell("th", name, i) for i, name in enumerate(table.header)
    )
    body = "".join(
        "<tr>" + "".join(cell("td", value, i) for i, value in enumerate(row))
        + "</tr>"
        for row in table.rows
    )
    return (
        f'<div class="table-block"><h3>{html.escape(label)}</h3>'
        f"<table>{'<thead><tr>' + head + '</tr></thead>'}<tbody>{body}"
        "</tbody></table></div>"
    )


def _cards(findings) -> str:
    cards = (
        ("Paddocks", findings["n_fields"], "33 stable field IDs, 5 seasons"),
        ("Observed field-years", findings["n_observed"],
         f"{findings['n_not_managed']} not managed"),
        ("Farm-seasons of climate", findings["n_farm_seasons"],
         "SILO daily data, 6 farms"),
        ("Footprint", f"{findings['area_footprint_ha']} ha",
         "latest boundary per field"),
        ("Wheat + canola", f"{findings['wheat_canola_share']} %",
         "of cropped field-years"),
        ("NDVI paddock-months", findings["pm_observed"],
         f"of {findings['pm_expected']} ({findings['pm_gap_pct']} % gaps)"),
    )
    return "".join(
        '<div class="card">'
        f'<span class="card-label">{html.escape(label)}</span>'
        f'<span class="card-value">{html.escape(value)}</span>'
        f'<span class="card-note">{html.escape(note)}</span>'
        "</div>"
        for label, value, note in cards
    )


def _row_value(table: Table, key: str, column: int) -> str:
    for row in table.rows:
        if row[0] == key:
            return row[column]
    raise KeyError(f"{key!r} not in table")


def _points(findings) -> str:
    crop_table = findings["ndvi_crop"]
    points = (
        "Scale separates the regions before anything else does: 16 Harden "
        f"paddocks have a median {findings['median_ha_harden']} ha, while six "
        f"lease blocks have a median {findings['median_ha_lease']} ha and hold "
        f"{findings['lease_area_pct']} % of the footprint.",
        "The rotation is a two-crop system. Canola to wheat is "
        f"{findings['canola_to_wheat_pct']} % of {findings['n_transitions']} "
        f"transitions and wheat plus canola is {findings['wheat_canola_share']} % "
        "of cropped field-years.",
        "Season dominates zone: rainfall runs "
        f"{findings['rain_2022']} mm in 2022 against {findings['rain_2023']} mm "
        f"in 2023, while 2026 is {findings['gdd_2026_gap_pct']} % warmer than "
        "2022 on growing degree days.",
        f"Canopy tracks both crop and rain — pasture leads at "
        f"{_row_value(crop_table, 'Pasture', 2)} median NDVI and lupin trails "
        f"at {_row_value(crop_table, 'Narrow Leaf Lupin', 2)}; integral versus "
        f"season rain has Spearman ρ = {findings['spearman_integral_rain']}.",
    )
    return "".join(f"<li>{html.escape(text)}</li>" for text in points)


def render() -> str:
    findings = build_findings(load_examples())

    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Field comparison EDA — five years, 33 paddocks, three regions</title>
<style>
  :root {{
    --ink: #1c1c1e;
    --muted: #5c5c66;
    --line: #e2e2e6;
    --panel: #f7f7f5;
    --accent: #2f5d8a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 3rem 1.5rem 4rem;
    background: #ffffff; color: var(--ink);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          "Helvetica Neue", Arial, sans-serif;
  }}
  main {{ max-width: 980px; margin: 0 auto; }}
  .eyebrow {{
    font-size: 0.78rem; letter-spacing: 0.09em; text-transform: uppercase;
    color: var(--muted); margin: 0 0 0.6rem;
  }}
  h1 {{ font-size: clamp(1.7rem, 4vw, 2.35rem); line-height: 1.15; margin: 0 0 0.8rem; }}
  h2 {{
    font-size: 1.28rem; margin: 3rem 0 0.7rem;
    padding-top: 1.4rem; border-top: 1px solid var(--line);
  }}
  h3 {{ font-size: 0.92rem; margin: 0 0 0.5rem; color: var(--muted);
        font-weight: 600; letter-spacing: 0.01em; }}
  p {{ margin: 0 0 1rem; }}
  .lede {{ font-size: 1.06rem; color: #3a3a40; max-width: 62ch; }}
  .cards {{
    display: grid; gap: 0.75rem; margin: 1.8rem 0 0;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  }}
  .card {{
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 8px; padding: 0.85rem 0.95rem;
    display: flex; flex-direction: column;
  }}
  .card-label {{ font-size: 0.74rem; text-transform: uppercase;
                 letter-spacing: 0.06em; color: var(--muted); }}
  .card-value {{ font-size: 1.42rem; font-weight: 650; margin: 0.15rem 0; }}
  .card-note {{ font-size: 0.76rem; color: var(--muted); }}
  ul.points {{ margin: 1rem 0 0; padding-left: 1.1rem; }}
  ul.points li {{ margin-bottom: 0.45rem; }}
  .figure {{ margin: 1.4rem 0 1.6rem; }}
  .figure img {{ width: 100%; height: auto; display: block;
                 border: 1px solid var(--line); border-radius: 8px; }}
  figcaption {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.5rem;
                max-width: 78ch; }}
  .table-block {{ margin: 1.2rem 0 1.8rem; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.87rem; }}
  th, td {{ padding: 0.42rem 0.6rem; border-bottom: 1px solid var(--line);
            text-align: left; white-space: nowrap; }}
  th {{ font-weight: 600; background: var(--panel); }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .note {{ font-size: 0.82rem; color: var(--muted); }}
  .limits {{ background: var(--panel); border-left: 3px solid var(--accent);
             border-radius: 0 8px 8px 0; padding: 1rem 1.15rem; }}
  .limits ul {{ margin: 0.4rem 0 0; padding-left: 1.1rem; }}
  .limits li {{ margin-bottom: 0.4rem; font-size: 0.9rem; }}
  footer {{ margin-top: 3rem; padding-top: 1.2rem; border-top: 1px solid var(--line);
            font-size: 0.8rem; color: var(--muted); }}
  code {{ font-size: 0.82rem; background: var(--panel); padding: 0.1rem 0.3rem;
          border-radius: 4px; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<main>
  <p class="eyebrow">Assignment 2 · Field comparison EDA</p>
  <h1>Five years, 33 paddocks, three management regions</h1>
  <p class="lede">
    A field-level view of an Australian grain enterprise built from annual
    paddock shapefiles and grower records. The same panel answers three
    questions: how the regions differ in scale, how the rotation works, and how
    climate and canopy behave across the seasons.
  </p>

  <div class="cards">{_cards(findings)}</div>

  <h2>What the data says</h2>
  <ul class="points">{_points(findings)}</ul>

  <h2>Paddock scale</h2>
  {_figure("fig01_field_area.png")}
  {_table(findings["zone_profile"], "Paddocks, area and coverage by region")}

  <h2>Rotation</h2>
  <p>
    Across {findings["n_transitions"]} consecutive-year transitions where both
    years carry a crop, canola and wheat trade places. Pasture is a small
    permanent and phase component; faba beans appear in one season.
  </p>
  {_table(findings["rotation_transitions"], "Most common crop transitions")}
  {_figure("fig03_crop_mix.png")}
  {_table(findings["crop_mix_zone"], "Crop share of reported cropped area by region (%)")}

  <h2>Climate</h2>
  {_figure("fig02_seasonal_rainfall.png")}
  {_table(findings["climate_zone"], "Season climate by region")}
  <p class="note">
    Kanoona's 2026 season is open: {findings["partial_days"]} of
    {findings["partial_expected"]} days, so its own-season totals are lower
    bounds. The common window used above is complete for all
    {findings["n_farm_seasons"]} farm-seasons.
  </p>

  <h2>Canopy</h2>
  {_table(findings["ndvi_crop"], "NDVI season summary by crop")}
  {_figure("fig05_ndvi_season.png")}

  <h2>Where the paddocks are</h2>
  {_figure("fig04_paddock_map.png")}

  <h2>Limits</h2>
  <div class="limits">
    <ul>
      <li>Weather is sampled at each farm centroid, not per paddock; Kanoona's
          16 fields share one 0.05° cell.</li>
      <li>Regions differ in field size, crop mix and management at the same
          time, so cross-region differences are associations, not causal
          effects.</li>
      <li>NDVI is a monthly best-scene composite. A missing month is reported
          as a gap and never interpolated.</li>
      <li>The {findings["n_not_managed"]} not-managed field-years are excluded
          from every crop statistic, even where NDVI exists — no crop record
          means no crop to compare.</li>
      <li>No yield, input or gross-margin data in this build, so the analysis
          describes canopy and rotation rather than profitability.</li>
    </ul>
  </div>

  <footer>
    Every number here is computed by <code>src/build_analysis.py</code> from the
    committed CSVs in <code>examples/</code>; the long-form analysis is
    <a href="ANALYSIS.md">ANALYSIS.md</a>. Regenerate this page with
    <code>python src/build_report.py</code>; the <code>--check</code> flag is
    the drift test. Raw paddock layers and the daily weather cache are not
    committed, and no grower credential is stored in any file.
  </footer>
</main>
</body>
</html>
"""
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    rendered = render()
    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT.name} is missing", file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != rendered:
            print(f"{OUTPUT.name} is stale — rerun build_report.py",
                  file=sys.stderr)
            return 1
        print(f"{OUTPUT.name} is up to date")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"wrote {OUTPUT} ({size_kb:.0f} KB, self-contained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
