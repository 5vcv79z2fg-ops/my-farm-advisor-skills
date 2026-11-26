"""SILO daily weather for the Australian reports.

The US weather leg samples NASA POWER at each field centroid, so every field
poster gets its own climate series. In the AU branch climate is a property of
the farm: one SILO point (0.05 degree grid) per farm, shared by all of its
paddocks. That is why the charts live in the zone report, one row per farm,
and never on the 33 paddock posters - 16 identical charts would be noise.

SILO is the only climate source here. The raw response is cached per farm by
``eda/eda-field-comparison/src/fetch_weather.py``; this module reads that cache
and normalises it to the columns the shared ``weather_reporting`` plotters
expect, so the AU charts reuse the US drawing code instead of a fork.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from lib.au_common import FARM_NAME_BY_SLUG, components_root
from lib.paths import farm_boundary_path, farm_table_path

DAILY_BASENAME = "weather_daily.csv"
SILO_GRID_DEG = 0.05

# Columns the shared plotters read. Kept in one place so the adapter and the
# stage cannot disagree about a name.
REPORT_COLUMNS = {
    "date": "date",
    "tmax_c": "T2M_MAX",
    "tmin_c": "T2M_MIN",
    "tavg_c": "T2M",
    "rain_mm": "PRECTOTCORR",
}


def weather_cache_dir() -> Path:
    """Where the raw SILO CSVs live.

    Runtime-first default: the untracked component tree beside the pipeline
    runtime. ``AU_WEATHER_DIR`` overrides it for a different cache location.
    """
    raw = os.environ.get("AU_WEATHER_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return components_root() / "weather" / "raw"


def _snap(value: float) -> float:
    return round(round(value / SILO_GRID_DEG) * SILO_GRID_DEG, 2)


def silo_point(grower_slug: str, farm_slug: str) -> tuple[float, float] | None:
    """Snapped (lat, lon) of the farm's SILO grid point, from its boundary."""
    path = farm_boundary_path(grower_slug, farm_slug)
    if not path.exists():
        return None
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.ops import unary_union

    fields = gpd.read_file(path).to_crs(3577)
    centroid = unary_union(list(fields.geometry)).centroid
    back = Transformer.from_crs("EPSG:3577", "EPSG:4326", always_xy=True)
    lon, lat = back.transform(centroid.x, centroid.y)
    return _snap(lat), _snap(lon)


def cache_path(grower_slug: str, farm_slug: str, farm_name: str) -> Path | None:
    """The SILO CSV for this farm, matched by grid point then by name."""
    root = weather_cache_dir()
    if not root.is_dir():
        return None
    stem = str(farm_name).replace(" ", "_").replace("/", "-")
    point = silo_point(grower_slug, farm_slug)
    if point is not None:
        exact = root / f"{stem}_{point[0]}_{point[1]}.csv"
        if exact.exists():
            return exact
    matches = [
        path for path in sorted(root.glob(f"{stem}_*.csv"))
        if not path.stem.startswith("test")
    ]
    return matches[0] if matches else None


def read_silo(path: Path) -> pd.DataFrame:
    """Normalise one raw SILO response to date / tmax / tmin / rain."""
    raw = pd.read_csv(path)
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["YYYY-MM-DD"], errors="coerce"),
            "tmax_c": pd.to_numeric(raw.get("max_temp"), errors="coerce"),
            "tmin_c": pd.to_numeric(raw.get("min_temp"), errors="coerce"),
            "rain_mm": pd.to_numeric(raw.get("daily_rain"), errors="coerce"),
        }
    )
    frame = frame.dropna(subset=["date", "tmax_c", "tmin_c"]).sort_values("date")
    frame["rain_mm"] = frame["rain_mm"].fillna(0.0)
    frame["tavg_c"] = (frame["tmax_c"] + frame["tmin_c"]) / 2.0
    # Base 0 degC: the rotation mixes wheat, barley, canola and legumes, and a
    # single base that suits all of them is 0. A crop-specific base would
    # require one chart per crop on a farm that sows four.
    frame["gdd_base_0"] = np.maximum(0.0, frame["tavg_c"])
    return frame.reset_index(drop=True)


def stage_daily_weather(
    grower_slug: str, farm_slug: str, farm_name: str | None = None
) -> Path | None:
    """Write ``derived/tables/weather_daily.csv`` for one farm."""
    name = farm_name or FARM_NAME_BY_SLUG.get(farm_slug, farm_slug)
    source = cache_path(grower_slug, farm_slug, name)
    if source is None:
        return None
    frame = read_silo(source)
    out = farm_table_path(grower_slug, farm_slug, DAILY_BASENAME)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False, date_format="%Y-%m-%d")
    return out


def load_daily_weather(
    grower_slug: str, farm_slug: str, farm_name: str | None = None
) -> pd.DataFrame:
    """Per-farm daily weather: the staged table, else the SILO cache."""
    staged = farm_table_path(grower_slug, farm_slug, DAILY_BASENAME)
    if staged.exists():
        frame = pd.read_csv(staged)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        return frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    name = farm_name or FARM_NAME_BY_SLUG.get(farm_slug, farm_slug)
    source = cache_path(grower_slug, farm_slug, name)
    return read_silo(source) if source is not None else pd.DataFrame()


def reporting_frame(daily: pd.DataFrame, field_id: str) -> pd.DataFrame:
    """The frame ``weather_reporting`` plots: one station, US column names."""
    frame = daily.rename(columns=REPORT_COLUMNS).copy()
    frame["field_id"] = field_id
    frame["date"] = pd.to_datetime(frame["date"])
    return frame
