"""Build the field-year panel from the five annual paddock shapefiles.

Output
------
examples/panel_field_year.csv   long format, one row per field-year
examples/rotation_spine.csv     wide format, one row per field

Absent years are labelled ``not_managed``. They are never labelled missing
and are never imputed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
from shapely.ops import unary_union

from common import (
    CONFIRMED_RECORDS,
    CROP_GROUP,
    MANAGEMENT_EVENTS,
    PERMANENT_PASTURE,
    REGION_BY_FARM,
    YEARS,
    canonical_ids,
    canonical_meta,
    components_for,
    load_all,
    short_crop,
    use_type_for,
)

OUT_DIR = Path(__file__).resolve().parents[1] / "examples"

COLUMNS = [
    "field_id",
    "paddock_name",
    "farm",
    "region",
    "year",
    "status",
    "land_use",
    "use_type",
    "area_ha_reported",
    "area_ha_geom",
    "area_ha_noncropped",
    "crop",
    "variety",
    "variety_source",
    "crop_group",
    "crop_mix",
    "mixed_flag",
    "crop_source",
    "geometry_source",
    "prev_crop",
    "rotation_pos",
    "years_observed",
]


def _round_nonzero(value: float) -> float:
    """Round to 2 dp and avoid writing -0.0 into the CSV."""
    rounded = round(value, 2)
    return 0.0 if abs(rounded) < 0.005 else rounded


def resolve_year(
    cid: int, year: int, layers: dict
) -> dict | None:
    """Crop, variety, areas and provenance for one canonical field-year."""
    comps = components_for(cid, layers[year])
    crop_ha: dict[str, float] = defaultdict(float)
    variety_by_crop: dict[str, list[str]] = defaultdict(list)
    variety_source_by_crop: dict[str, list[str]] = defaultdict(list)
    for comp in comps:
        if not comp.crop:
            continue
        crop_ha[comp.crop] += comp.area_geom
        variety_source_by_crop[comp.crop].append(comp.variety_source)
        if comp.variety and comp.variety not in variety_by_crop[comp.crop]:
            variety_by_crop[comp.crop].append(comp.variety)

    # A record exists but carries no crop, or the paddock is absent from the
    # year's file entirely. Fall back to a grower-confirmed crop.
    if not crop_ha:
        confirmed = CONFIRMED_RECORDS.get((cid, year))
        if confirmed is None:
            return None
        geom_year = confirmed["geometry_source"]
        geom_comps = components_for(cid, layers[geom_year])
        if not geom_comps:
            return None
        geom = (
            geom_comps[0].geom
            if len(geom_comps) == 1
            else unary_union([c.geom for c in geom_comps])
        )
        return {
            "status": "observed",
            "crop": confirmed["crop"],
            "variety": confirmed["variety"],
            "variety_source": "grower_confirmed",
            "crop_mix": "",
            "mixed_flag": False,
            "crop_source": confirmed["crop_source"],
            "geometry_source": geom_year,
            "area_ha_reported": sum(c.area_reported for c in geom_comps),
            "area_ha_geom": geom.area / 10_000.0,
        }

    ranked = sorted(crop_ha.items(), key=lambda kv: -kv[1])
    dominant = ranked[0][0]
    mixed = len(ranked) > 1
    geom = comps[0].geom if len(comps) == 1 else unary_union(
        [c.geom for c in comps]
    )

    return {
        "status": "observed",
        "crop": dominant,
        "variety": ", ".join(variety_by_crop[dominant]),
        "variety_source": (
            "grower_correction"
            if "grower_correction" in variety_source_by_crop[dominant]
            else "shapefile"
        ),
        "crop_mix": (
            "; ".join(f"{short_crop(c)} {ha:.2f}" for c, ha in ranked)
            if mixed
            else ""
        ),
        "mixed_flag": mixed,
        "crop_source": "shapefile",
        "geometry_source": year,
        "area_ha_reported": sum(c.area_reported for c in comps),
        "area_ha_geom": geom.area / 10_000.0,
    }


def build() -> pd.DataFrame:
    layers = load_all()
    meta = canonical_meta(layers)
    ids = canonical_ids(layers)

    rows: list[dict] = []
    for cid in ids:
        info = meta[cid]
        farm = info["farm"]
        region = REGION_BY_FARM.get(farm, "Unknown")
        land_use = "permanent_pasture" if cid in PERMANENT_PASTURE else "cropping"

        observed_crops: dict[int, str] = {}
        per_field: list[dict] = []
        prev_crop = ""
        run = 0

        for year in YEARS:
            resolved = resolve_year(cid, year, layers)
            if resolved is None:
                per_field.append(
                    {
                        "field_id": cid,
                        "paddock_name": info["name"],
                        "farm": farm,
                        "region": region,
                        "year": year,
                        "status": "not_managed",
                        "land_use": land_use,
                        "use_type": "",
                        "area_ha_reported": None,
                        "area_ha_geom": None,
                        "area_ha_noncropped": None,
                        "crop": "",
                        "variety": "",
                        "variety_source": "",
                        "crop_group": "",
                        "crop_mix": "",
                        "mixed_flag": False,
                        "crop_source": "",
                        "geometry_source": "",
                        "prev_crop": "",
                        "rotation_pos": None,
                    }
                )
                prev_crop = ""
                run = 0
                continue

            crop = resolved["crop"]
            observed_crops[year] = crop
            run = run + 1 if crop == prev_crop else 1
            per_field.append(
                {
                    "field_id": cid,
                    "paddock_name": info["name"],
                    "farm": farm,
                    "region": region,
                    "year": year,
                    "status": "observed",
                    "land_use": land_use,
                    "use_type": use_type_for(farm, year, crop),
                    "area_ha_reported": round(resolved["area_ha_reported"], 2),
                    "area_ha_geom": round(resolved["area_ha_geom"], 2),
                    "area_ha_noncropped": _round_nonzero(
                        resolved["area_ha_geom"] - resolved["area_ha_reported"]
                    ),
                    "crop": crop,
                    "variety": resolved["variety"],
                    "variety_source": resolved["variety_source"],
                    "crop_group": CROP_GROUP.get(crop, "Other"),
                    "crop_mix": resolved["crop_mix"],
                    "mixed_flag": resolved["mixed_flag"],
                    "crop_source": resolved["crop_source"],
                    "geometry_source": str(resolved["geometry_source"]),
                    "prev_crop": prev_crop,
                    "rotation_pos": run,
                }
            )
            prev_crop = crop

        years_observed = len(observed_crops)
        for row in per_field:
            row["years_observed"] = years_observed
        rows.extend(per_field)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["rotation_pos"] = df["rotation_pos"].astype("Int64")
    df["years_observed"] = df["years_observed"].astype("Int64")
    df = df.sort_values(
        ["region", "farm", "paddock_name", "year"], kind="stable"
    ).reset_index(drop=True)
    return df


def rotation_spine(df: pd.DataFrame) -> pd.DataFrame:
    wide = df.pivot_table(
        index=["field_id", "paddock_name", "farm", "region"],
        columns="year",
        values="crop",
        aggfunc="first",
    ).reindex(columns=list(YEARS))
    wide = wide.reset_index()
    wide.columns = [
        "_".join(str(p) for p in col if p != "")
        if isinstance(col, tuple)
        else col
        for col in wide.columns
    ]
    return wide


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = build()

    panel_path = OUT_DIR / "panel_field_year.csv"
    df.to_csv(panel_path, index=False)

    spine = rotation_spine(df)
    spine_path = OUT_DIR / "rotation_spine.csv"
    spine.to_csv(spine_path, index=False)

    events = pd.DataFrame(MANAGEMENT_EVENTS)
    events = events.sort_values(["farm", "year", "start_date"]).reset_index(drop=True)
    events_path = OUT_DIR / "management_events.csv"
    events.to_csv(events_path, index=False)

    observed = df[df["status"] == "observed"]
    print(f"panel rows          : {len(df)}")
    print(f"observed field-years: {len(observed)}")
    print(f"not_managed         : {len(df) - len(observed)}")
    print(f"distinct fields     : {df['field_id'].nunique()}")
    print(f"blank crops         : {(observed['crop'] == '').sum()}")
    print()
    print("observed rows per year:")
    for year, n in observed.groupby("year").size().items():
        print(f"  {year}: {n}")
    print()
    print("fields per region:")
    for region, n in df.groupby("region")["field_id"].nunique().items():
        print(f"  {region}: {n}")
    print()
    print("crop counts by year (observed, dominant crop):")
    for year in YEARS:
        counts = Counter(observed.loc[observed["year"] == year, "crop"])
        print(f"  {year}: {dict(sorted(counts.items()))}")
    print()
    print(f"wrote {panel_path}")
    print(f"wrote {spine_path}")
    print(f"wrote {events_path}")


if __name__ == "__main__":
    main()
