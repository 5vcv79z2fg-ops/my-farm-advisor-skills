"""Shared helpers for the Australian farm pipeline stages.

The data-pipeline's reporting legs are US-shaped (SSURGO, CDL, corn/soy
windows). The ``au/`` scripts reuse the same canonical runtime layout and
renderers but source their inputs from SLGA/ASF, the grower's crop records and
Sentinel-2. This module holds the constants and the small amount of geometry /
profile logic those scripts share.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from lib.paths import DATA_ROOT, farm_boundary_path, farm_dir, field_dir

ZONE_BY_FARM = {
    "Kanoona": "Harden",
    "Caroleese": "Trungley-Hall",
    "Tullokan / Dalkeith": "Trungley-Hall",
    "Brentwood (lease)": "Lease-country",
    "Woodlands (lease)": "Lease-country",
    "Rio (lease)": "Lease-country",
}

# The zone's name as a reader meets it. ``ZONE_REGION`` is the same name
# without the farm list, and is what the ``region`` column in
# ``manifests/field-names.csv`` carries - derived here rather than maintained
# beside the zone mapping, so a rename cannot leave a stale region behind.
ZONE_REGION = {
    "Harden": "Harden",
    "Trungley-Hall": "Trungley Hall",
    "Lease-country": "Lease country",
}

ZONE_LABELS = {
    "Harden": "Harden",
    "Trungley-Hall": "Trungley Hall",
    "Lease-country": "Lease country (Brentwood, Woodlands, Rio)",
}

# GADM AUS level-2 calls these "areas"; in New South Wales they are shires.
# Only the printed name is translated - the stored boundary attribute keeps
# what the source delivered, so re-clipping a farm is never needed to fix a
# label.
COUNTY_LABELS = {
    "Harden (Area)": "Hilltops Shire",
    "Bland (Area)": "Bland Shire",
    "Temora (Area)": "Temora Shire",
}

FARM_ORDER = ("kanoona", "caroleese", "tullokan", "brentwood", "woodlands", "rio")

FARM_NAME_BY_SLUG = {
    "kanoona": "Kanoona",
    "caroleese": "Caroleese",
    "tullokan": "Tullokan / Dalkeith",
    "brentwood": "Brentwood (lease)",
    "woodlands": "Woodlands (lease)",
    "rio": "Rio (lease)",
}

# Growing season used by the satellite cards and the crop-window statistics.
# Kanoona runs a longer program and finishes beans/wheat into late November;
# the lease and Temora blocks are cut off mid-November.
SEASON_WINDOW = {
    "kanoona": ("04-01", "11-30"),
}
DEFAULT_SEASON_WINDOW = ("04-01", "11-15")


def season_window(farm_slug: str) -> tuple[str, str]:
    return SEASON_WINDOW.get(farm_slug, DEFAULT_SEASON_WINDOW)


def season_months(farm_slug: str) -> tuple[int, ...]:
    start, end = season_window(farm_slug)
    return tuple(range(int(start.split("-")[0]), int(end.split("-")[0]) + 1))


MONTH_ABBREVIATIONS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def growing_season(farm_slug: str) -> str:
    """The season as a reader meets it: ``01 Apr – 30 Nov``.

    One formatter for the poster, the NDVI card and the zone reports, so the
    same farm cannot advertise two different seasons.
    """
    start, end = season_window(farm_slug)

    def _day_month(md: str) -> str:
        month, day = md.split("-")
        return f"{int(day):02d} {MONTH_ABBREVIATIONS[int(month) - 1]}"

    return f"{_day_month(start)} – {_day_month(end)}"

SOIL_PROPERTIES = (
    "CLAY",
    "SILT",
    "SAND",
    "CEC",
    "PHCALCIUMCHLORIDE",
    "CARBON",
    "BULKDENSITY",
)
SOIL_DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm")
DEPTH_THICKNESS_M = {
    "0-5cm": 0.05,
    "5-15cm": 0.10,
    "15-30cm": 0.15,
    "30-60cm": 0.30,
    "60-100cm": 0.40,
}
DEPTH_TOP_M = {d: sum(DEPTH_THICKNESS_M[x] for x in SOIL_DEPTHS[:i]) for i, d in enumerate(SOIL_DEPTHS)}

# Display label and unit used by maps and cards.
PROPERTY_LABELS = {
    "CLAY": ("Clay", "%"),
    "SILT": ("Silt", "%"),
    "SAND": ("Sand", "%"),
    "CEC": ("CEC", "meq/100g"),
    "PHCALCIUMCHLORIDE": ("pH (CaCl2)", ""),
    "CARBON": ("Organic carbon", "%"),
    "BULKDENSITY": ("Bulk density", "g/cm3"),
}

# Provenance pinned from the australia-soil skill's PROPERTY_URLS (probed
# 2026-11-20). Kept here so the AU pipeline stages do not need to import the
# skill's geopandas-heavy module just to read a DOI.
PROPERTY_META = {
    "CLAY": {"attribute": "Clay", "units": "%", "doi": "https://doi.org/10.25919/hc4s-3130"},
    "SILT": {"attribute": "Silt", "units": "%", "doi": "https://doi.org/10.25919/2ew1-0w57"},
    "SAND": {"attribute": "Sand", "units": "%", "doi": "https://doi.org/10.25919/rjmy-pa10"},
    "CEC": {"attribute": "Cation Exchange Capacity", "units": "meq/100g", "doi": "https://doi.org/10.25919/pkva-gf85"},
    "PHCALCIUMCHLORIDE": {"attribute": "pH - Calcium Chloride", "units": "pH (1:5 CaCl2)", "doi": "https://doi.org/10.25919/7320-hw30"},
    "CARBON": {"attribute": "Organic Carbon", "units": "%", "doi": "https://doi.org/10.25919/ejhm-c070"},
    "BULKDENSITY": {"attribute": "Bulk Density (whole earth)", "units": "g/cm3", "doi": "https://doi.org/10.25919/gxyn-pd07"},
}
SOURCE_NAME = "slga_asf"


def components_root() -> Path:
    """The untracked component tree that sits beside the pipeline runtime."""
    for candidate in (DATA_ROOT.parent / "au-paddocks", DATA_ROOT / "au-paddocks"):
        if candidate.is_dir():
            return candidate
    return DATA_ROOT.parent / "au-paddocks"


def soil_raster_dir(farm_slug: str) -> Path:
    return components_root() / "components" / "soil" / "rasters" / farm_slug


def load_farm_json(grower_slug: str, farm_slug: str) -> dict:
    path = farm_dir(grower_slug, farm_slug) / "farm.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_inventory(grower_slug: str, farm_slug: str) -> pd.DataFrame:
    path = farm_dir(grower_slug, farm_slug) / "manifests" / "field-inventory.csv"
    frame = pd.read_csv(path, dtype={"field_id": str, "field_slug": str})
    return frame


def load_field_names(grower_slug: str, farm_slug: str) -> pd.DataFrame:
    """Readable paddock names, written by the staging step.

    ``ingest_user_boundaries`` normalizes boundary properties and drops the
    display name, so the name travels in its own manifest rather than being
    recovered from the slug.
    """
    path = farm_dir(grower_slug, farm_slug) / "manifests" / "field-names.csv"
    if path.exists():
        return pd.read_csv(path, dtype={"field_id": str, "field_slug": str})
    inventory = load_inventory(grower_slug, farm_slug)
    inventory["paddock_name"] = inventory["field_slug"]
    return inventory


def load_fields(grower_slug: str, farm_slug: str):
    import geopandas as gpd

    return gpd.read_file(farm_boundary_path(grower_slug, farm_slug)).to_crs("EPSG:4326")


def load_field_crops(grower_slug: str, farm_slug: str) -> pd.DataFrame:
    """Per-year crop record, written by the staging step from the field panel.

    This is the substitute for CDL: the grower's own crop and variety records,
    with absent years kept as ``not_managed`` rather than imputed.
    """
    path = farm_dir(grower_slug, farm_slug) / "manifests" / "field-crops.csv"
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "field_id",
                "field_slug",
                "year",
                "status",
                "crop",
                "crop_group",
                "use_type",
            ]
        )
    return pd.read_csv(path, dtype={"field_id": str, "field_slug": str})


def field_meta(grower_slug: str, farm_slug: str, field_slug: str) -> dict:
    path = field_dir(grower_slug, farm_slug, field_slug) / "boundary" / "field_boundary.geojson"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    feature = doc["features"][0]
    props = dict(feature.get("properties") or {})
    props["_geometry"] = feature.get("geometry")
    return props


def display_name(meta: dict, field_slug: str) -> str:
    for key in ("field_name", "paddock_name", "name"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return field_slug


def zone_for_farm(farm_name: str) -> str:
    if farm_name in ZONE_BY_FARM:
        return ZONE_BY_FARM[farm_name]
    for key, zone in ZONE_BY_FARM.items():
        if key.split(" (")[0].lower() in farm_name.lower():
            return zone
    raise KeyError(f"no zone for farm {farm_name!r}")


def region_for_farm(farm_name: str) -> str:
    """The zone's display name, for the ``region`` column and subtitles."""
    return ZONE_REGION[zone_for_farm(farm_name)]


def county_label(raw: str) -> str:
    """``Harden (Area)`` -> ``Hilltops Shire``; unknown names pass through."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in COUNTY_LABELS:
        return COUNTY_LABELS[text]
    if text.endswith(" Shire"):
        return text
    stem = text.split(" (")[0].strip()
    return f"{stem} Shire" if stem else ""


def usda_texture(sand: float, clay: float) -> str:
    if not np.isfinite(sand) or not np.isfinite(clay):
        return ""
    s, c = float(sand), float(clay)
    si = 100.0 - s - c
    if si < 0:
        return ""
    if si + 1.5 * c < 15:
        return "sand"
    if si + 2.0 * c < 30:
        return "loamy sand"
    if c >= 40:
        if si >= 40:
            return "silty clay"
        if s > 45:
            return "sandy clay"
        return "clay"
    if c >= 35:
        if s >= 45:
            return "sandy clay"
        if si >= 40:
            return "silty clay"
        return "clay loam"
    if c >= 27:
        if s >= 45:
            return "sandy clay loam"
        if si >= 40:
            return "silty clay loam"
        return "clay loam"
    if c >= 20:
        if s >= 45:
            return "sandy clay loam"
        if si >= 50:
            return "silt loam"
        return "loam"
    if c < 7:
        if si >= 80:
            return "silt"
        if si >= 50:
            return "silt loam"
        return "sandy loam"
    if si >= 80:
        return "silt"
    if si >= 50:
        return "silt loam"
    if s >= 52:
        return "sandy loam"
    return "loam"


def profile_from_long(long: pd.DataFrame) -> pd.DataFrame:
    """One row per field from the long SLGA property table.

    Adds depth-weighted means for display, plus carbon stock as a slab sum
    (never the product of two depth-weighted means).
    """
    if long.empty:
        return pd.DataFrame()
    wide = (
        long.pivot_table(
            index=["field_id", "field_slug", "paddock_name", "farm", "region"],
            columns=["property", "depth"],
            values="mean",
        )
        .sort_index(axis=1)
    )
    wide.columns = [f"{prop}_{depth}" for prop, depth in wide.columns]
    wide = wide.reset_index()

    for prop in SOIL_PROPERTIES:
        slabs = [f"{prop}_{d}" for d in SOIL_DEPTHS]
        if all(c in wide.columns for c in slabs):
            w = np.array([DEPTH_THICKNESS_M[d] for d in SOIL_DEPTHS])
            wide[f"{prop}_0_30"] = (
                wide[[f"{prop}_{d}" for d in SOIL_DEPTHS[:3]]].mul(w[:3], axis=1).sum(axis=1)
                / w[:3].sum()
            )
            wide[f"{prop}_0_100"] = wide[slabs].mul(w, axis=1).sum(axis=1) / w.sum()

    wide["texture_usda_0_30"] = [
        usda_texture(s, c)
        for s, c in zip(wide.get("SAND_0_30", np.nan), wide.get("CLAY_0_30", np.nan), strict=False)
    ]
    for label in ("0_30", "0_100"):
        needed = {f"{p}_{label}" for p in ("CARBON", "BULKDENSITY")}
        if needed.issubset(wide.columns):
            total = 0.0
            for depth in SOIL_DEPTHS if label == "0_100" else SOIL_DEPTHS[:3]:
                total = total + (
                    wide[f"CARBON_{depth}"] / 100.0
                    * wide[f"BULKDENSITY_{depth}"]
                    * (DEPTH_THICKNESS_M[depth] * 100.0)
                    * 100.0
                )
            wide[f"carbon_stock_t_ha_{label}"] = total
    return wide


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def env_slugs() -> tuple[str, str]:
    grower = os.environ.get("AG_GROWER_SLUG", "").strip()
    farm = os.environ.get("AG_FARM_SLUG", "").strip()
    if not grower or not farm:
        raise SystemExit("AG_GROWER_SLUG and AG_FARM_SLUG are required")
    return grower, farm
