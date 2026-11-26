"""Shared helpers for the Australian paddock field-year panel.

The panel is built from five annual ESRI shapefiles exported from the
grower's management system. Geometry is never modified apart from
reprojection; retired sub-paddocks are mapped onto their surviving parent
field so that field identity is stable across the 2024 -> 2025 boundary
revision.
"""

from __future__ import annotations

import math
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import shapefile
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union

warnings.filterwarnings("ignore", category=DeprecationWarning)

YEARS = (2022, 2023, 2024, 2025, 2026)

SOURCE_CRS = "EPSG:4326"
TARGET_CRS = "EPSG:3577"

_TO_TARGET = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True).transform

def _runtime_root() -> Path:
    """Locate the untracked runtime tree that holds the raw paddock layers.

    Precedence follows the repository convention: an explicit override first,
    then the shared ``DATA_PIPELINE_DATA_ROOT``, then a home-relative default.
    Raw layers never live inside the repository.
    """
    explicit = os.environ.get("AU_PADDOCKS_RUNTIME", "").strip()
    if explicit:
        return Path(explicit)
    shared = os.environ.get("DATA_PIPELINE_DATA_ROOT", "").strip()
    if shared:
        return Path(shared) / "au-paddocks"
    return Path.home() / "my-farm-advisor-runtime" / "au-paddocks"


RUNTIME_ROOT = _runtime_root()

# Two boundary schemas: A covers 2022-2024 and B covers 2025-2026.
SCHEMA_BY_YEAR = {2022: "A", 2023: "A", 2024: "A", 2025: "B", 2026: "B"}

# Retired pre-2025 sub-paddocks and the surviving field that absorbed them.
RETIRED_TO_PARENT: dict[int, int] = {
    1169914: 1153771,  # Copperbella Creek -> Copperbella
    1153773: 1153777,  # Hayshed          -> Lane Lucerne
    1169687: 1153779,  # House            -> Oat Paddock
}

# Field was absent from the year's shapefile but the grower confirmed its use.
# geometry_source names the year whose polygon is reused (verified identical).
CONFIRMED_RECORDS: dict[tuple[int, int], dict] = {
    (1153780, 2025): {
        "crop": "Pasture",
        "variety": "Annual Pasture",
        "geometry_source": 2024,
        "crop_source": "grower_confirmed",
    },
    (1153780, 2026): {
        "crop": "Pasture",
        "variety": "Annual Pasture",
        "geometry_source": 2026,
        "crop_source": "grower_confirmed",
    },
}

REGION_BY_FARM: dict[str, str] = {
    "Kanoona": "Harden",
    "Caroleese": "Trungley Hall",
    "Tullokan / Dalkeith": "Trungley Hall",
    "Brentwood (lease)": "Lease country",
    "Woodlands (lease)": "Lease country",
    "Rio (lease)": "Lease country",
}

REGIONS = ("Harden", "Trungley Hall", "Lease country")

CROP_GROUP: dict[str, str] = {
    "Wheat": "Cereal",
    "Barley": "Cereal",
    "Canola": "Oilseed",
    "Narrow Leaf Lupin": "Legume",
    "Faba/Broad/Field Bean": "Legume",
    "Pasture": "Pasture",
}

# Grower-record corrections, confirmed 2026-11-26. The annual shapefiles carry
# the crop and variety as first entered; where the grower has since corrected
# the record, the panel applies it here rather than in the exported CSV, so a
# rebuild reproduces it and no downstream table can disagree. Keys are narrow:
# the variety correction names farm, year, crop and the recorded variety, so
# the other 32 Vixen rows across the panel are untouched.
CROP_LABEL_CORRECTIONS: dict[str, str] = {
    # Field peas were a pasture phase in the grower's records, not a crop.
    "Field Peas": "Pasture",
}

VARIETY_CORRECTIONS: dict[tuple[str, int, str, str], str] = {
    # Kanoona's 2023 wheat was Illabo, not the Vixen on the shapefile.
    ("Kanoona", 2023, "Wheat", "Vixen"): "Illabo",
}

# Paddock is permanent pasture for all five years, so it cannot rotate.
PERMANENT_PASTURE = {1153780}

# Dual-purpose wheat: grazed through winter, locked up mid-to-late August and
# then harvested for grain. Grower-confirmed for Kanoona wheat in 2022 and
# 2023 only. From 2024 every wheat crop is grain-only. '' is the default.
DUAL_PURPOSE_WHEAT: set[tuple[str, int]] = {("Kanoona", 2022), ("Kanoona", 2023)}

USE_TYPE_GRAIN = "grain"
USE_TYPE_DUAL = "dual_purpose"

# Management departures from the default farm programme. One row per event;
# dates are windows because lock-up timing was recalled to the month only.
# Consumers: fetch_weather.py (early sowing moves the season start) and the
# NDVI steps (grazing changes what a low winter NDVI means).
MANAGEMENT_EVENTS: list[dict[str, str]] = [
    {
        "farm": "Kanoona",
        "year": "2023",
        "crop": "Wheat",
        "event": "early_sow",
        "start_date": "2023-03-15",
        "end_date": "2023-03-15",
        "confidence": "day",
        "source": "grower, 2026-11-20",
    },
    {
        "farm": "Kanoona",
        "year": "2022",
        "crop": "Wheat",
        "event": "grazing_lockup",
        "start_date": "2022-08-15",
        "end_date": "2022-08-31",
        "confidence": "month",
        "source": "grower recall, 2026-11-20",
    },
    {
        "farm": "Kanoona",
        "year": "2023",
        "crop": "Wheat",
        "event": "grazing_lockup",
        "start_date": "2023-08-15",
        "end_date": "2023-08-31",
        "confidence": "month",
        "source": "grower recall, 2026-11-20",
    },
]


def use_type_for(farm: str, year: int, crop: str) -> str:
    """Grain-only unless the grower grazed this farm-year's wheat."""
    if crop == "Wheat" and (farm, int(year)) in DUAL_PURPOSE_WHEAT:
        return USE_TYPE_DUAL
    return USE_TYPE_GRAIN


def early_sow_start(farm: str, year: int, crop: str) -> str:
    """First day of the crop's growing window, honouring a recorded early sow."""
    for event in MANAGEMENT_EVENTS:
        if (
            event["event"] == "early_sow"
            and event["farm"] == farm
            and int(event["year"]) == int(year)
            and event["crop"] == crop
        ):
            return event["start_date"]
    return ""

_SHORT_CROP = {
    "Narrow Leaf Lupin": "Lupin",
    "Faba/Broad/Field Bean": "Faba",
}


def short_crop(crop: str) -> str:
    return _SHORT_CROP.get(crop, crop)


def parse_area(raw: object) -> float:
    """Turn an area attribute such as '65.38 ha' into a float."""
    if raw is None:
        return float("nan")
    text = str(raw).strip()
    if not text:
        return float("nan")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in ("", ".", "-"):
        return float("nan")
    return float(cleaned)


def _clean(text: object) -> str:
    if text is None:
        return ""
    return str(text).strip()


@dataclass
class FieldRecord:
    """One paddock polygon as it appears in a single annual shapefile."""

    paddock_id: int
    name: str
    farm: str
    crop: str
    variety: str
    area_reported: float
    year: int
    geom: Polygon | MultiPolygon
    parts: int
    vertices: int
    valid: bool = field(default=True)
    # "shapefile" unless the grower corrected the variety, in which case
    # "grower_correction" marks the row so the audit trail survives export.
    variety_source: str = "shapefile"

    @property
    def area_geom(self) -> float:
        return self.geom.area / 10_000.0


def layer_path(year: int) -> Path:
    return RUNTIME_ROOT / str(year) / "au-paddocks.shp"


def load_year(year: int) -> dict[int, FieldRecord]:
    """Read one annual shapefile, keyed by paddock id, in EPSG:3577."""
    reader = shapefile.Reader(str(layer_path(year)))
    out: dict[int, FieldRecord] = {}
    for shp, rec in zip(reader.shapes(), reader.records()):
        row = rec.as_dict()
        raw = make_valid(shape(shp.__geo_interface__))
        geom = transform(_TO_TARGET, raw)
        pid = int(row["paddock_id"])
        farm = _clean(row["property_n"])
        recorded_crop = _clean(row["crop_name"])
        crop = CROP_LABEL_CORRECTIONS.get(recorded_crop, recorded_crop)
        variety = _clean(row["variety_na"])
        variety_source = "shapefile"
        corrected_variety = VARIETY_CORRECTIONS.get((farm, year, crop, variety))
        if corrected_variety is not None:
            variety = corrected_variety
            variety_source = "grower_correction"
        out[pid] = FieldRecord(
            paddock_id=pid,
            name=_clean(row["paddock_na"]),
            farm=farm,
            crop=crop,
            variety=variety,
            area_reported=parse_area(row["area"]),
            year=year,
            geom=geom,
            parts=len(shp.parts),
            vertices=len(shp.points),
            valid=bool(geom.is_valid),
            variety_source=variety_source,
        )
    return out


def load_all() -> dict[int, dict[int, FieldRecord]]:
    return {year: load_year(year) for year in YEARS}


def canonical_ids(layers: dict[int, dict[int, FieldRecord]]) -> list[int]:
    """Surviving field identities: the 2025 set plus the 2026-only paddock."""
    ids = set(layers[2025]) | set(layers[2026])
    for retired in RETIRED_TO_PARENT:
        ids.discard(retired)
    return sorted(ids)


def canonical_meta(
    layers: dict[int, dict[int, FieldRecord]],
) -> dict[int, dict[str, str]]:
    """Name and farm for each canonical field, preferring the newest record."""
    meta: dict[int, dict[str, str]] = {}
    for cid in canonical_ids(layers):
        for year in (2025, 2026, 2024, 2023, 2022):
            if cid in layers[year]:
                rec = layers[year][cid]
                meta[cid] = {"name": rec.name, "farm": rec.farm}
                break
    return meta


def components_for(
    cid: int, layer: dict[int, FieldRecord]
) -> list[FieldRecord]:
    """Records in one year that belong to a canonical field."""
    out = []
    if cid in layer:
        out.append(layer[cid])
    for retired, parent in RETIRED_TO_PARENT.items():
        if parent == cid and retired in layer:
            out.append(layer[retired])
    return out


def geometry_for(
    cid: int, layers: dict[int, dict[int, FieldRecord]]
) -> tuple[Polygon | MultiPolygon, int] | tuple[None, None]:
    """Canonical polygon and the year it was taken from."""
    for year in (2025, 2026, 2024, 2023, 2022):
        comps = components_for(cid, layers[year])
        if comps:
            geom = comps[0].geom if len(comps) == 1 else unary_union(
                [c.geom for c in comps]
            )
            return geom, year
    return None, None


def shannon(proportions: list[float]) -> float:
    """Shannon diversity index over positive proportions (natural log)."""
    total = sum(proportions)
    if total <= 0:
        return 0.0
    h = 0.0
    for value in proportions:
        if value <= 0:
            continue
        p = value / total
        h -= p * math.log(p)
    return h
