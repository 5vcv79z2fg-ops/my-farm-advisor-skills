"""Fetch SILO daily climate data for each farm and derive seasonal metrics.

SILO identifies callers by email address rather than an API key. The address
is read from the SILO_EMAIL environment variable, is sent to SILO as the
``username`` query parameter, and is never written to a file in this
repository.

Usage
-----
    SILO_EMAIL=you@example.com python fetch_weather.py --test
    SILO_EMAIL=you@example.com python fetch_weather.py
    python fetch_weather.py --offline

``--offline`` rebuilds the derived tables from the raw cache without any
network access. It needs no email address and is the normal path once the
cache exists.

Season definitions
------------------
Growing seasons differ by location. Kanoona, further east, runs
**1 April - 30 November** (244 days). Every other farm runs **1 April -
15 November** (229 days), matching an earlier harvest start.

Because the windows differ in length, a cross-region comparison of raw
season totals would credit Kanoona with two extra weeks. Every farm therefore
also carries a **common window of 1 April - 15 November** (229 days), which is
complete for all 30 farm-years including 2026. Use `common_*` columns to
compare regions; use `season_*` columns to describe each farm's own season.

Sowing (1 April - 31 May) and flowering (1 September - 31 October) sub-windows
carry the rainfall and frost exposure that matter at those stages.

GDD bases are 0 degC for wheat and pasture, 5 degC for canola and legumes.
Frost is Tmin <= 2 degC, heat is Tmax >= 30 degC.

Outputs
-------
examples/weather_seasonal.csv   one row per farm-year
examples/weather_monthly.csv    one row per farm-month
${MY_FARM_RUNTIME}/au-paddocks/weather/weather_daily.csv   daily cache
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

from common import RUNTIME_ROOT, canonical_ids, geometry_for, load_all

OUT_DIR = Path(__file__).resolve().parents[1] / "examples"
CACHE_DIR = RUNTIME_ROOT / "weather" / "raw"

API = "https://www.longpaddock.qld.gov.au/cgi-bin/silo/DataDrillDataset.php"

# R rainfall, X max temp, N min temp, J radiation, E pan evaporation
VARIABLES = "RXNJE"

START = "20220101"
FINISH = "20261119"

# Sowing window start. Season end differs by farm: Kanoona runs later.
SEASON_START = (4, 1)
SEASON_END_DEFAULT = (11, 15)
SEASON_END_BY_FARM = {"Kanoona": (11, 30)}

# Common window, used for every cross-region comparison.
COMMON_END = (11, 15)

# Stage windows.
SOWING_START = (4, 1)
SOWING_END = (5, 31)
FLOWERING_START = (9, 1)
FLOWERING_END = (10, 31)

# GDD bases. Wheat and pasture use 0 degC; canola and legumes 5 degC.
GDD_BASE_LOW = 0.0
GDD_BASE_HIGH = 5.0

FROST_THRESHOLD = 2.0
HEAT_THRESHOLD = 30.0


def snap(value: float) -> float:
    """SILO grid points fall on 0.05 degree increments."""
    return round(round(value / 0.05) * 0.05, 2)


def farm_centroids() -> dict[str, tuple[float, float]]:
    """WGS84 centroid of each farm, snapped to the SILO grid."""
    layers = load_all()
    totals: dict[str, list] = {}
    for cid in canonical_ids(layers):
        geom, _ = geometry_for(cid, layers)
        if geom is None:
            continue
        # Recover the farm from the newest record for this field.
        farm = None
        for year in (2025, 2026, 2024, 2023, 2022):
            if cid in layers[year]:
                farm = layers[year][cid].farm
                break
        if farm is None:
            continue
        totals.setdefault(farm, []).append(geom)

    from pyproj import Transformer

    back = Transformer.from_crs("EPSG:3577", "EPSG:4326", always_xy=True)
    points = {}
    for farm, geoms in totals.items():
        merged = geoms[0]
        for g in geoms[1:]:
            merged = merged.union(g)
        lon, lat = back.transform(merged.centroid.x, merged.centroid.y)
        points[farm] = (snap(lat), snap(lon))
    return points


def cache_path(farm: str, lat: float, lon: float) -> Path:
    stem = farm.replace(" ", "_").replace("/", "-")
    return CACHE_DIR / f"{stem}_{lat}_{lon}.csv"


def build_url(lat: float, lon: float, email: str, start: str, finish: str) -> str:
    params = {
        "lat": f"{lat:.2f}",
        "lon": f"{lon:.2f}",
        "start": start,
        "finish": finish,
        "format": "csv",
        "comment": VARIABLES,
        "username": email,
        "password": "apirequest",
    }
    return API + "?" + urllib.parse.urlencode(params)


def describe_missing_email() -> None:
    print("SILO_EMAIL is not set and the raw cache is incomplete.")
    print()
    print("SILO does not issue API keys. Requests are identified by email")
    print("address, supplied as the 'username' query parameter.")
    print()
    print("  1. Decide which email address to use for the request.")
    print("  2. Export it for this shell only:")
    print("       export SILO_EMAIL=you@example.com")
    print("  3. Verify with one request:")
    print("       python fetch_weather.py --test")
    print()
    print("The address is read at runtime, sent to SILO, and written to no file.")
    print("If the cache is already complete, use '--offline' instead.")


def fetch(url: str, cache: Path, email: str, force: bool = False) -> str:
    if cache.exists() and not force:
        return cache.read_text(encoding="utf-8")
    if not email or "@" not in email:
        raise RuntimeError(f"cache miss for {cache.name} and SILO_EMAIL is not set")
    request = urllib.request.Request(url, headers={"User-Agent": "farm-eda/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        text = response.read().decode("utf-8", errors="replace")
    if not text.strip():
        raise RuntimeError("empty response from SILO")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def parse_csv(text: str) -> pd.DataFrame:
    """Parse a SILO CSV point response into a tidy frame.

    The header is latitude,longitude,YYYY-MM-DD,daily_rain,...,metadata.
    Each variable is followed by a ``_source`` column that must be dropped
    before renaming, or two columns end up sharing a name.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header_idx = next(
        (
            i
            for i, ln in enumerate(lines)
            if "yyyy-mm-dd" in ln.lower() or ln.lower().startswith("date")
        ),
        None,
    )
    if header_idx is None:
        raise RuntimeError(f"no header row in SILO response: {lines[:3]}")

    body = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(body), skipinitialspace=True)
    df.columns = [str(c).strip() for c in df.columns]

    keep: dict[str, str] = {}
    for col in df.columns:
        low = col.lower()
        if low.endswith("_source") or low in ("metadata", "latitude", "longitude"):
            continue
        if low in ("date", "yyyy-mm-dd"):
            keep[col] = "date"
        elif low.startswith("daily_rain"):
            keep[col] = "rain"
        elif low.startswith("max_temp"):
            keep[col] = "tmax"
        elif low.startswith("min_temp"):
            keep[col] = "tmin"
        elif low.startswith("radiation"):
            keep[col] = "radiation"
        elif low.startswith("evap"):
            keep[col] = "evap_pan"

    df = df[list(keep)].rename(columns=keep)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if df["date"].isna().all():
        raise RuntimeError("could not parse any dates from SILO response")
    for col in ("rain", "tmax", "tmin", "radiation", "evap_pan"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _window_mask(df: pd.DataFrame, start: tuple[int, int], end: tuple[int, int]):
    """Inclusive month-day window, evaluated inside a single calendar year."""
    sm, sd = start
    em, ed = end
    after_start = (df["month"] > sm) | ((df["month"] == sm) & (df["day"] >= sd))
    before_end = (df["month"] < em) | ((df["month"] == em) & (df["day"] <= ed))
    return after_start & before_end


def _stage(df: pd.DataFrame, start: tuple[int, int], end: tuple[int, int]) -> dict:
    """Aggregate the climate metrics that matter for one stage window."""
    sub = df[_window_mask(df, start, end)]
    return {
        "days": len(sub),
        "rain_mm": float(sub["rain"].sum()),
        "gdd_base0": float(sub["gdd_base0"].sum()),
        "gdd_base5": float(sub["gdd_base5"].sum()),
        "frost_days": int(sub["frost_day"].sum()),
        "heat_days": int(sub["heat_day"].sum()),
        "radiation_mj": float(sub["radiation"].sum()),
        "evap_pan_mm": float(sub["evap_pan"].sum()),
    }


def _expected_days(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Calendar length of a month-day window, on a non-leap reference year."""
    return (date(2025, end[0], end[1]) - date(2025, start[0], start[1])).days + 1


def _window_label(start: tuple[int, int], end: tuple[int, int]) -> str:
    return f"{start[0]:02d}-{start[1]:02d}..{end[0]:02d}-{end[1]:02d}"


def seasonal(frame: pd.DataFrame, farm: str) -> pd.DataFrame:
    df = frame.copy()
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["day"] = df["date"].dt.day

    df["mean_temp"] = (df["tmax"] + df["tmin"]) / 2.0
    df["gdd_base0"] = (df["mean_temp"] - GDD_BASE_LOW).clip(lower=0)
    df["gdd_base5"] = (df["mean_temp"] - GDD_BASE_HIGH).clip(lower=0)
    df["frost_day"] = (df["tmin"] <= FROST_THRESHOLD).astype(int)
    df["heat_day"] = (df["tmax"] >= HEAT_THRESHOLD).astype(int)

    season_end = SEASON_END_BY_FARM.get(farm, SEASON_END_DEFAULT)
    season_expected = _expected_days(SEASON_START, season_end)
    common_expected = _expected_days(SEASON_START, COMMON_END)

    rows = []
    for year, grp in df.groupby("year"):
        season = _stage(grp, SEASON_START, season_end)
        common = _stage(grp, SEASON_START, COMMON_END)
        sowing = _stage(grp, SOWING_START, SOWING_END)
        flowering = _stage(grp, FLOWERING_START, FLOWERING_END)

        rows.append(
            {
                "farm": farm,
                "year": int(year),
                "season_window": _window_label(SEASON_START, season_end),
                "days_in_record": len(grp),
                "season_days": season["days"],
                "season_expected_days": season_expected,
                "season_complete": season["days"] >= season_expected - 4,
                "rain_season_mm": round(season["rain_mm"], 1),
                "rain_annual_mm": round(float(grp["rain"].sum()), 1),
                "gdd_base0": round(season["gdd_base0"], 1),
                "gdd_base5": round(season["gdd_base5"], 1),
                "frost_days": season["frost_days"],
                "heat_days": season["heat_days"],
                "radiation_mj": round(season["radiation_mj"], 1),
                "evap_pan_mm": round(season["evap_pan_mm"], 1),
                # Common window: same dates on every farm, so regions compare.
                "common_window": _window_label(SEASON_START, COMMON_END),
                "common_days": common["days"],
                "common_expected_days": common_expected,
                "common_complete": common["days"] >= common_expected - 4,
                "rain_common_mm": round(common["rain_mm"], 1),
                "gdd_base0_common": round(common["gdd_base0"], 1),
                "gdd_base5_common": round(common["gdd_base5"], 1),
                "heat_days_common": common["heat_days"],
                "frost_days_common": common["frost_days"],
                # What a longer season adds beyond the common window.
                "extension_days": season["days"] - common["days"],
                "rain_extension_mm": round(
                    season["rain_mm"] - common["rain_mm"], 1
                ),
                "gdd_base0_extension": round(
                    season["gdd_base0"] - common["gdd_base0"], 1
                ),
                # Stage sub-windows, inside every farm's season.
                "rain_sowing_mm": round(sowing["rain_mm"], 1),
                "frost_days_sowing": sowing["frost_days"],
                "frost_days_flowering": flowering["frost_days"],
            }
        )
    return pd.DataFrame(rows)


def monthly(frame: pd.DataFrame, farm: str) -> pd.DataFrame:
    df = frame.copy()
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    agg = (
        df.groupby(["year", "month"])
        .agg(
            rain_mm=("rain", "sum"),
            tmax_mean=("tmax", "mean"),
            tmin_mean=("tmin", "mean"),
            days=("date", "count"),
        )
        .reset_index()
    )
    agg.insert(0, "farm", farm)
    for col in ("rain_mm", "tmax_mean", "tmin_mean"):
        agg[col] = agg[col].round(1)
    return agg


def build_daily(
    points: dict[str, tuple[float, float]], email: str, force: bool
) -> pd.DataFrame:
    daily_frames = []
    for farm, (lat, lon) in sorted(points.items()):
        cache = cache_path(farm, lat, lon)
        had_cache = cache.exists() and not force
        url = build_url(lat, lon, email, START, FINISH)
        text = fetch(url, cache, email, force=force)
        frame = parse_csv(text)
        frame["farm"] = farm
        frame["grid_lat"] = lat
        frame["grid_lon"] = lon
        daily_frames.append(frame)
        print(
            f"{farm:22} {lat:7.2f} {lon:8.2f}  {len(frame):5d} days"
            f"  [{'cache' if had_cache else 'SILO'}]"
        )
        if not had_cache:
            time.sleep(1.0)
    return pd.concat(daily_frames, ignore_index=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="rebuild from the raw cache; never touch the network",
    )
    args = parser.parse_args()

    email = os.environ.get("SILO_EMAIL", "").strip()
    points = farm_centroids()

    if args.test:
        farm = "Kanoona"
        lat, lon = points[farm]
        if not email or "@" not in email:
            describe_missing_email()
            return 2
        url = build_url(lat, lon, email, "20250101", "20250110")
        print(f"GET {url.replace(email, '<email>')}")
        text = fetch(url, CACHE_DIR / "test_kanoona_2025.csv", email, force=True)
        print()
        print(text[:1200])
        return 0

    missing = [
        farm
        for farm, (lat, lon) in points.items()
        if not cache_path(farm, lat, lon).exists()
    ]
    needs_network = bool(args.force or missing)
    if needs_network and (args.offline or not email or "@" not in email):
        describe_missing_email()
        if missing:
            print()
            print(f"Missing cache for {len(missing)} farm(s): {', '.join(missing)}")
        return 2

    daily = build_daily(points, email, args.force)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = RUNTIME_ROOT / "weather" / "weather_daily.csv"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(daily_path, index=False)
    print(f"\nwrote {daily_path}")

    seasonal_df = pd.concat(
        [seasonal(f, farm) for farm, f in daily.groupby("farm")],
        ignore_index=True,
    )
    seasonal_df = seasonal_df.sort_values(["farm", "year"]).reset_index(drop=True)
    seasonal_path = OUT_DIR / "weather_seasonal.csv"
    seasonal_df.to_csv(seasonal_path, index=False)

    monthly_df = pd.concat(
        [monthly(f, farm) for farm, f in daily.groupby("farm")],
        ignore_index=True,
    )
    monthly_path = OUT_DIR / "weather_monthly.csv"
    monthly_df.to_csv(monthly_path, index=False)

    summary = seasonal_df.groupby("year").agg(
        season_days=("season_days", "max"),
        season_complete=("season_complete", "all"),
        common_complete=("common_complete", "all"),
        rain_season_mm=("rain_season_mm", "mean"),
        rain_common_mm=("rain_common_mm", "mean"),
        gdd_base0=("gdd_base0", "mean"),
        gdd_base0_common=("gdd_base0_common", "mean"),
        heat_days=("heat_days", "mean"),
        heat_days_common=("heat_days_common", "mean"),
        rain_sowing_mm=("rain_sowing_mm", "mean"),
        frost_days_flowering=("frost_days_flowering", "mean"),
    ).round(1)
    print()
    print(summary.to_string())
    print()
    print(f"wrote {seasonal_path}")
    print(f"wrote {monthly_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
