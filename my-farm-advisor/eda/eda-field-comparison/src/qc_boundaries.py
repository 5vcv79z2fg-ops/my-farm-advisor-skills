"""Boundary and area QC for the five annual paddock layers.

Compares each canonical field against its own geometry in the previous
observed year, so the 2024 -> 2025 merge is measured on the same footing as
every unchanged year.

Outputs
-------
examples/qc_boundaries.csv   per field-year boundary and area diagnostics
examples/qc_crosswalk.csv    the three retired sub-paddocks and their parents
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from shapely.ops import unary_union

from common import (
    CONFIRMED_RECORDS,
    REGION_BY_FARM,
    RETIRED_TO_PARENT,
    SCHEMA_BY_YEAR,
    YEARS,
    canonical_ids,
    canonical_meta,
    components_for,
    load_all,
)

OUT_DIR = Path(__file__).resolve().parents[1] / "examples"


def canonical_geom(cid: int, year: int, layers: dict):
    """Union of the records that make up a canonical field in one year."""
    comps = components_for(cid, layers[year])
    if not comps:
        confirmed = CONFIRMED_RECORDS.get((cid, year))
        if confirmed is None:
            return None, None
        source_year = confirmed["geometry_source"]
        comps = components_for(cid, layers[source_year])
        if not comps:
            return None, None
        geom = (
            comps[0].geom
            if len(comps) == 1
            else unary_union([c.geom for c in comps])
        )
        return geom, source_year
    geom = (
        comps[0].geom
        if len(comps) == 1
        else unary_union([c.geom for c in comps])
    )
    return geom, year


def build_boundaries() -> pd.DataFrame:
    layers = load_all()
    meta = canonical_meta(layers)
    rows: list[dict] = []

    for cid in canonical_ids(layers):
        info = meta[cid]
        region = REGION_BY_FARM.get(info["farm"], "Unknown")
        prev_geom = None
        prev_year = None

        for year in YEARS:
            geom, source_year = canonical_geom(cid, year, layers)
            if geom is None:
                rows.append(
                    {
                        "field_id": cid,
                        "paddock_name": info["name"],
                        "farm": info["farm"],
                        "region": region,
                        "year": year,
                        "schema": SCHEMA_BY_YEAR[year],
                        "status": "not_managed",
                        "geometry_source": "",
                        "area_ha_geom": None,
                        "area_ha_reported": None,
                        "area_ha_noncropped": None,
                        "area_delta_pct": None,
                        "symdiff_pct": None,
                        "hausdorff_m": None,
                        "centroid_shift_m": None,
                        "valid": None,
                        "parts": None,
                        "vertices": None,
                    }
                )
                continue

            comps = components_for(cid, layers[year]) or components_for(
                cid, layers[source_year]
            )
            reported = sum(c.area_reported for c in comps)
            area_geom = geom.area / 10_000.0
            delta_pct = (
                (area_geom - reported) / reported * 100.0
                if reported and not pd.isna(reported)
                else None
            )

            if prev_geom is None:
                symdiff = hausdorff = centroid = None
                compares_to = ""
            else:
                inter = geom.intersection(prev_geom).area
                union = geom.union(prev_geom).area
                symdiff = (union - inter) / union * 100.0 if union else 0.0
                hausdorff = geom.hausdorff_distance(prev_geom)
                centroid = geom.centroid.distance(prev_geom.centroid)
                compares_to = prev_year

            rows.append(
                {
                    "field_id": cid,
                    "paddock_name": info["name"],
                    "farm": info["farm"],
                    "region": region,
                    "year": year,
                    "schema": SCHEMA_BY_YEAR[year],
                    "status": "observed",
                    "geometry_source": source_year,
                    "area_ha_geom": round(area_geom, 2),
                    "area_ha_reported": (
                        round(reported, 2) if not pd.isna(reported) else None
                    ),
                    "area_ha_noncropped": (
                        round(area_geom - reported, 2)
                        if not pd.isna(reported)
                        else None
                    ),
                    "area_delta_pct": (
                        round(delta_pct, 2) if delta_pct is not None else None
                    ),
                    "compared_to_year": compares_to,
                    "symdiff_pct": (
                        round(symdiff, 4) if symdiff is not None else None
                    ),
                    "hausdorff_m": (
                        round(hausdorff, 2) if hausdorff is not None else None
                    ),
                    "centroid_shift_m": (
                        round(centroid, 2) if centroid is not None else None
                    ),
                    "valid": bool(geom.is_valid),
                    "parts": comps[0].parts if len(comps) == 1 else len(comps),
                    "vertices": sum(c.vertices for c in comps),
                }
            )

            prev_geom = geom
            prev_year = year

    df = pd.DataFrame(rows)
    return df.sort_values(
        ["region", "farm", "paddock_name", "year"], kind="stable"
    ).reset_index(drop=True)


def build_crosswalk() -> pd.DataFrame:
    layers = load_all()
    meta = canonical_meta(layers)
    rows: list[dict] = []

    for retired, parent in sorted(RETIRED_TO_PARENT.items()):
        child_2024 = layers[2024].get(retired)
        parent_2024 = layers[2024].get(parent)
        parent_2025 = layers[2025].get(parent)
        if not (child_2024 and parent_2024 and parent_2025):
            continue

        inter = child_2024.geom.intersection(parent_2025.geom).area
        overlap_pct = inter / child_2024.geom.area * 100.0

        def crop_of(pid: int, year: int) -> str:
            rec = layers[year].get(pid)
            if rec is None:
                return ""
            return f"{rec.crop} ({rec.variety})" if rec.variety else rec.crop

        rows.append(
            {
                "retired_id": retired,
                "retired_name": child_2024.name,
                "parent_id": parent,
                "parent_name": meta[parent]["name"],
                "retired_ha_2024": round(child_2024.area_geom, 2),
                "parent_ha_2024": round(parent_2024.area_geom, 2),
                "parent_ha_2025": round(parent_2025.area_geom, 2),
                "merged_ha": round(
                    child_2024.area_geom + parent_2024.area_geom, 2
                ),
                "overlap_pct_of_retired": round(overlap_pct, 2),
                "retired_crop_2022": crop_of(retired, 2022),
                "retired_crop_2023": crop_of(retired, 2023),
                "retired_crop_2024": crop_of(retired, 2024),
                "parent_crop_2022": crop_of(parent, 2022),
                "parent_crop_2023": crop_of(parent, 2023),
                "parent_crop_2024": crop_of(parent, 2024),
            }
        )

    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    boundaries = build_boundaries()
    b_path = OUT_DIR / "qc_boundaries.csv"
    boundaries.to_csv(b_path, index=False)

    crosswalk = build_crosswalk()
    c_path = OUT_DIR / "qc_crosswalk.csv"
    crosswalk.to_csv(c_path, index=False)

    observed = boundaries[boundaries["status"] == "observed"]
    changed = observed[observed["symdiff_pct"].fillna(0) > 0.01]

    print(f"qc rows              : {len(boundaries)}")
    print(f"observed             : {len(observed)}")
    print(f"changed boundaries   : {len(changed)}")
    if len(changed):
        cols = [
            "paddock_name",
            "year",
            "area_ha_geom",
            "symdiff_pct",
            "hausdorff_m",
            "centroid_shift_m",
        ]
        print(changed[cols].to_string(index=False))
    print()
    print("unchanged comparisons:", int(observed["symdiff_pct"].notna().sum()) - len(changed))

    flag = observed[observed["area_delta_pct"].abs() > 1.0]
    print("\narea attribute vs geometry, |delta| > 1%:")
    cols = ["paddock_name", "year", "area_ha_geom", "area_ha_reported", "area_delta_pct"]
    print(flag[cols].to_string(index=False))

    print()
    print(crosswalk.to_string(index=False))
    print()
    print(f"wrote {b_path}")
    print(f"wrote {c_path}")


if __name__ == "__main__":
    main()
