#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Generate a lightweight grower-level interactive field map.

The output is one standalone HTML file per grower. Field polygons come from the
pipeline boundary GeoJSON, are embedded inline as GeoJSON, and render on an
online Esri World Imagery basemap through Leaflet. No imagery, rasters, or
base64 payloads are embedded, so the file stays small.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths  # noqa: E402

_RUNTIME_PATHS = resolve_runtime_paths()
_SCRIPTS = _RUNTIME_PATHS.runtime_scripts
_LIB = _RUNTIME_PATHS.runtime_scripts / "lib"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_LIB))

from paths import GROWERS_ROOT, grower_dir, grower_farms_dir, grower_report_path  # noqa: E402

_OSM_IDENTIFIER = re.compile(r"^osm[-_]?\d+$", re.IGNORECASE)

# US state/territory FIPS prefixes. Boundary files carry state_fips but not a
# state abbreviation, and farm.json is not reliable for this (a farm can carry
# a stale or incorrect state). The FIPS code comes from the downloaded polygon.
_US_STATE_FIPS = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
    "60": "AS",
    "66": "GU",
    "69": "MP",
    "72": "PR",
    "78": "VI",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return payload


def _as_float(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _inventory_order(farm_path: Path) -> dict[str, int]:
    """Return field_id -> 0-based order from the farm field inventory, if present."""
    inventory_path = farm_path / "manifests" / "field-inventory.csv"
    if not inventory_path.is_file():
        return {}
    order: dict[str, int] = {}
    try:
        with inventory_path.open(newline="", encoding="utf-8") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                field_id = str(row.get("field_id") or "").strip()
                if field_id and field_id not in order:
                    order[field_id] = index
    except OSError:
        return {}
    return order


def _resolve_field_label(field_meta: dict[str, Any], farm_display: str, ordinal: int) -> str:
    """Pick a stable, human-friendly field label.

    Order: explicit ``label``, a meaningful ``display_name`` (anything that is
    not an OSM identifier), then ``"<farm> — Field N"``. Ordinals are stable
    across reruns and do not change when acreage is recomputed.
    """
    explicit = str(field_meta.get("label") or "").strip()
    if explicit:
        return explicit

    display = str(field_meta.get("display_name") or "").strip()
    slug = str(field_meta.get("field_slug") or "").strip()
    if display and display != slug and not _OSM_IDENTIFIER.match(display):
        return display

    return f"{farm_display} — Field {ordinal}"


def _farm_field_features(
    grower_slug: str,
    grower_display: str,
    farm_path: Path,
) -> list[dict[str, Any]]:
    farm_slug = farm_path.name
    boundary_path = farm_path / "boundary" / "field_boundaries.geojson"
    if not boundary_path.is_file():
        print(f"warning: {grower_slug}/{farm_slug}: missing {boundary_path}", file=sys.stderr)
        return []

    farm_meta_path = farm_path / "farm.json"
    farm_meta = _read_json(farm_meta_path) if farm_meta_path.is_file() else {}
    farm_display = str(farm_meta.get("display_name") or farm_slug).strip() or farm_slug

    boundary = _read_json(boundary_path)
    raw_features = boundary.get("features")
    if not isinstance(raw_features, list):
        print(f"warning: {grower_slug}/{farm_slug}: boundary file has no features", file=sys.stderr)
        return []

    order = _inventory_order(farm_path)
    sortable: list[tuple[int, str, dict[str, Any]]] = []
    for index, feature in enumerate(raw_features):
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        field_id = str(props.get("field_id") or "").strip()
        rank = order.get(field_id, len(order) + index)
        sortable.append((rank, field_id or f"~{index:04d}", feature))
    sortable.sort(key=lambda item: (item[0], item[1]))

    features: list[dict[str, Any]] = []
    for ordinal, (_, _, feature) in enumerate(sortable, start=1):
        props = dict(feature.get("properties") or {})
        field_id = str(props.get("field_id") or f"field-{ordinal}").strip()
        field_meta_path = farm_path / "fields" / field_id / "field.json"
        field_meta = _read_json(field_meta_path) if field_meta_path.is_file() else {}

        state_fips = str(props.get("state_fips") or "").strip()
        state = _US_STATE_FIPS.get(state_fips.zfill(2), "") if state_fips else ""
        if not state:
            state = str(field_meta.get("state") or farm_meta.get("state") or "").strip()

        geometry = feature.get("geometry")
        if not geometry:
            print(f"warning: {grower_slug}/{farm_slug}/{field_id}: missing geometry", file=sys.stderr)
            continue

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "label": _resolve_field_label(field_meta, farm_display, ordinal),
                    "field_id": field_id,
                    "field_slug": str(field_meta.get("field_slug") or field_id),
                    "farm": farm_display,
                    "farm_slug": farm_slug,
                    "grower": grower_display,
                    "grower_slug": grower_slug,
                    "area_acres": _as_float(props.get("area_acres")),
                    "crop_name": str(props.get("crop_name") or "").strip(),
                    "county": str(props.get("county_name") or "").strip(),
                    "state": state,
                    "source": str(props.get("source") or "").strip(),
                },
            }
        )
    return features


def _grower_features(grower_slug: str) -> tuple[str, list[dict[str, Any]]]:
    grower_path = grower_dir(grower_slug)
    grower_meta_path = grower_path / "grower.json"
    grower_meta = _read_json(grower_meta_path) if grower_meta_path.is_file() else {}
    grower_display = str(grower_meta.get("display_name") or grower_slug).strip() or grower_slug

    farms_path = grower_farms_dir(grower_slug)
    if not farms_path.is_dir():
        return grower_display, []

    features: list[dict[str, Any]] = []
    for farm_path in sorted(path for path in farms_path.iterdir() if path.is_dir()):
        features.extend(_farm_field_features(grower_slug, grower_display, farm_path))
    return grower_display, features


def _render_html(
    *,
    page_title: str,
    headline: str,
    subtitle: str,
    features: list[dict[str, Any]],
) -> str:
    collection = {"type": "FeatureCollection", "features": features}
    data_json = json.dumps(collection, ensure_ascii=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )

    document = _HTML_TEMPLATE.replace("__PAGE_TITLE__", html.escape(page_title))
    document = document.replace("__HEADLINE__", html.escape(headline))
    document = document.replace("__SUBTITLE__", html.escape(subtitle))
    return document.replace("__DATA_JSON__", data_json)


def generate(grower_slug: str, output_path: Path, *, dry_run: bool = False) -> Path | None:
    grower_display, features = _grower_features(grower_slug)
    if not features:
        print(f"warning: no field boundaries found for grower {grower_slug}", file=sys.stderr)
        return None

    farm_count = len({feature["properties"]["farm_slug"] for feature in features})
    farm_word = "farm" if farm_count == 1 else "farms"
    subtitle = f"{len(features)} fields · {farm_count} {farm_word}"
    document = _render_html(
        page_title=f"{grower_display} — Field Map",
        headline=grower_display,
        subtitle=subtitle,
        features=features,
    )

    if dry_run:
        print(f"[dry-run] {grower_slug}: {len(features)} fields -> {output_path}")
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    size_kib = output_path.stat().st_size / 1024
    print(f"{grower_slug}: {len(features)} fields, {size_kib:.1f} KiB -> {output_path}")
    return output_path


def _available_growers() -> list[str]:
    if not GROWERS_ROOT.is_dir():
        return []
    return sorted(
        path.name
        for path in GROWERS_ROOT.iterdir()
        if path.is_dir() and ((path / "grower.json").is_file() or (path / "farms").is_dir())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a lightweight grower-level interactive field map HTML file."
    )
    parser.add_argument(
        "--grower-slug",
        action="append",
        dest="grower_slugs",
        metavar="SLUG",
        help="Grower slug. Repeat for multiple growers. Defaults to every grower under the runtime.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the output directory. Defaults to growers/<grower>/derived/reports/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved inputs and output paths without writing files.",
    )
    args = parser.parse_args(argv)

    grower_slugs = args.grower_slugs or _available_growers()
    if not grower_slugs:
        raise SystemExit(f"no growers found under {GROWERS_ROOT}")

    failures = 0
    for grower_slug in grower_slugs:
        if args.output_dir is not None:
            output_path = args.output_dir / f"{grower_slug}_grower_fields_map.html"
        else:
            output_path = grower_report_path(
                grower_slug, f"{grower_slug}_grower_fields_map.html"
            )
        try:
            result = generate(grower_slug, output_path, dry_run=args.dry_run)
        except (OSError, ValueError) as exc:
            failures += 1
            print(f"error: {grower_slug}: {exc}", file=sys.stderr)
            continue
        if result is None:
            failures += 1

    return 1 if failures else 0


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root { --ink: #1f2937; --muted: #6b7280; --line: #e5e7eb; --accent: #2f6f4e; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    display: flex;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--ink);
    background: #fafaf9;
  }
  #sidebar {
    width: 340px;
    flex: 0 0 340px;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--line);
    background: #fafaf9;
  }
  #sidebar header { padding: 16px 16px 12px; border-bottom: 1px solid var(--line); }
  #sidebar h1 { font-size: 16px; line-height: 1.3; margin: 0 0 4px; }
  #sidebar .subtitle { font-size: 12px; color: var(--muted); }
  #zoom-all {
    margin-top: 12px;
    width: 100%;
    padding: 7px 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    background: #fff;
    border: 1px solid var(--accent);
    border-radius: 6px;
    cursor: pointer;
  }
  #zoom-all:hover { background: var(--accent); color: #fff; }
  #field-list { flex: 1; overflow-y: auto; padding: 6px 8px 16px; }
  .farm-heading {
    margin: 14px 8px 6px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .field-item {
    display: flex;
    gap: 8px;
    align-items: flex-start;
    width: 100%;
    padding: 8px;
    margin: 2px 0;
    text-align: left;
    background: none;
    border: 1px solid transparent;
    border-radius: 6px;
    cursor: pointer;
    font: inherit;
    color: inherit;
  }
  .field-item:hover { background: #fff; border-color: var(--line); }
  .field-item.active { background: #fff; border-color: var(--accent); }
  .swatch { flex: 0 0 auto; width: 10px; height: 10px; margin-top: 4px; border-radius: 2px; }
  .field-text { display: flex; flex-direction: column; }
  .field-name { font-size: 13px; line-height: 1.35; }
  .field-meta { font-size: 11px; color: var(--muted); }
  #map { flex: 1; min-width: 0; }
  .leaflet-popup-content { margin: 12px 14px; }
  .popup-title { display: flex; gap: 6px; align-items: center; font-size: 14px; font-weight: 600; margin-bottom: 8px; }
  .popup .dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
  .popup table { border-collapse: collapse; font-size: 12px; }
  .popup th { padding: 2px 12px 2px 0; text-align: left; font-weight: 500; color: var(--muted); white-space: nowrap; vertical-align: top; }
  .popup td { padding: 2px 0; color: var(--ink); }
  #notes { padding: 10px 14px; border-top: 1px solid var(--line); font-size: 11px; color: var(--muted); }
  @media (max-width: 720px) {
    body { flex-direction: column; }
    #sidebar { width: 100%; flex: 0 0 auto; max-height: 45vh; border-right: 0; border-bottom: 1px solid var(--line); }
    #map { height: 55vh; }
  }
</style>
</head>
<body>
<aside id="sidebar">
  <header>
    <h1>__HEADLINE__</h1>
    <div class="subtitle">__SUBTITLE__</div>
    <button id="zoom-all" type="button">Zoom to all fields</button>
  </header>
  <nav id="field-list" aria-label="Fields"></nav>
  <div id="notes">Basemap imagery &copy; Esri. Field boundaries from the data pipeline.</div>
</aside>
<div id="map" role="application" aria-label="Interactive field map"></div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA_JSON__;
const PALETTE = ['#2f6f4e', '#b45309', '#1d4ed8', '#9d174d', '#0f766e', '#6d28d9', '#b91c1c', '#4d7c0f'];
const DEFAULT_VIEW = [39.5, -98.35];

function esc(value) {
  return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch];
  });
}

function formatAcres(value) {
  return typeof value === 'number' && isFinite(value) ? value.toFixed(1) + ' ac' : '\\u2014';
}

function detailRows(props) {
  const county = [props.county, props.state].filter(Boolean).join(', ');
  return [
    ['Farm', props.farm],
    ['Grower', props.grower],
    ['Area', formatAcres(props.area_acres)],
    ['Crop', props.crop_name || '\\u2014'],
    ['County', county || '\\u2014'],
    ['Field ID', props.field_id],
    ['Source', props.source || '\\u2014']
  ];
}

function popupHtml(props, color) {
  let rows = '';
  detailRows(props).forEach(function (row) {
    rows += '<tr><th>' + esc(row[0]) + '</th><td>' + esc(row[1]) + '</td></tr>';
  });
  return '<div class="popup">' +
    '<div class="popup-title"><span class="dot" style="background:' + color + '"></span>' + esc(props.label) + '</div>' +
    '<table>' + rows + '</table></div>';
}

const map = L.map('map', {zoomControl: true, preferCanvas: true});
const imagery = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom: 19,
  attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community'
});
const street = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
});
const labels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
  maxZoom: 19,
  attribution: 'Labels &copy; Esri'
});
imagery.addTo(map);
labels.addTo(map);
L.control.layers(
  {'Satellite (Esri)': imagery, 'Street (OpenStreetMap)': street},
  {'Place labels': labels},
  {collapsed: false}
).addTo(map);
L.control.scale({imperial: true, metric: true}).addTo(map);

const records = {};
const allBounds = L.latLngBounds([]);
const farms = {};
let selected = null;

DATA.features.forEach(function (feature, index) {
  const props = feature.properties;
  const color = PALETTE[index % PALETTE.length];
  const layer = L.geoJSON(feature, {
    style: {color: color, weight: 2, fillColor: color, fillOpacity: 0.25}
  });
  layer.bindPopup(popupHtml(props, color), {maxWidth: 320});
  layer.bindTooltip(props.label, {sticky: true, direction: 'top', opacity: 0.9});
  layer.on('mouseover', function () {
    layer.setStyle({fillOpacity: 0.45, weight: 3});
  });
  layer.on('mouseout', function () {
    if (!selected || selected.id !== props.field_id) {
      layer.setStyle({fillOpacity: 0.25, weight: 2});
    }
  });
  layer.on('click', function () {
    selectField(props.field_id, false);
  });
  layer.addTo(map);

  allBounds.extend(layer.getBounds());
  records[props.field_id] = {id: props.field_id, layer: layer, color: color, props: props};
  if (!farms[props.farm]) {
    farms[props.farm] = [];
  }
  farms[props.farm].push(props.field_id);
});

function selectField(fieldId, zoom) {
  const record = records[fieldId];
  if (!record) {
    return;
  }
  if (selected && selected.id !== fieldId) {
    selected.layer.setStyle({fillOpacity: 0.25, weight: 2});
  }
  selected = record;
  record.layer.setStyle({fillOpacity: 0.5, weight: 4});
  record.layer.bringToFront();
  if (zoom) {
    map.fitBounds(record.layer.getBounds(), {padding: [40, 40], maxZoom: 17});
  }
  record.layer.openPopup();
  const items = document.querySelectorAll('.field-item');
  for (let i = 0; i < items.length; i += 1) {
    items[i].classList.toggle('active', items[i].getAttribute('data-field-id') === fieldId);
  }
}

const list = document.getElementById('field-list');
Object.keys(farms).forEach(function (farmName) {
  const heading = document.createElement('h2');
  heading.className = 'farm-heading';
  heading.textContent = farmName;
  list.appendChild(heading);

  farms[farmName].forEach(function (fieldId) {
    const record = records[fieldId];
    const props = record.props;
    const metaParts = [formatAcres(props.area_acres)];
    if (props.county) {
      metaParts.push(props.county);
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'field-item';
    button.setAttribute('data-field-id', fieldId);
    button.innerHTML = '<span class="swatch" style="background:' + record.color + '"></span>' +
      '<span class="field-text"><span class="field-name">' + esc(props.label) + '</span>' +
      '<span class="field-meta">' + esc(metaParts.join(' \\u00b7 ')) + '</span></span>';
    button.addEventListener('click', function () {
      selectField(fieldId, true);
    });
    list.appendChild(button);
  });
});

if (allBounds.isValid()) {
  map.fitBounds(allBounds, {padding: [30, 30]});
} else {
  map.setView(DEFAULT_VIEW, 4);
}

document.getElementById('zoom-all').addEventListener('click', function () {
  if (allBounds.isValid()) {
    map.fitBounds(allBounds, {padding: [30, 30]});
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
