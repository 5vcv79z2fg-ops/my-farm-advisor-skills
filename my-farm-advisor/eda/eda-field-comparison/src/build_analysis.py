"""Build ANALYSIS.md from the committed example tables.

The analysis section is prose plus numbers, and prose drifts. So the numbers do
not live in the markdown: `analysis_template.md` holds sentences with
`{{placeholders}}` and this script fills every one of them from the CSVs in
`examples/`. It fails on a placeholder that nothing computes, and on a computed
finding that the template never prints, so neither side can fall out of date
quietly.

    python src/build_analysis.py            # write ANALYSIS.md
    python src/build_analysis.py --check    # exit 1 if ANALYSIS.md is stale
    python src/build_analysis.py --stdout   # print, do not write

Only the standard library plus pandas and numpy are used, and only the
committed CSVs are read, so this runs offline with no runtime tree.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
EXAMPLES = BASE / "examples"
TEMPLATE = HERE / "analysis_template.md"
OUTPUT = BASE / "ANALYSIS.md"

REGIONS = ("Harden", "Trungley Hall", "Lease country")
CROPS = (
    "Wheat",
    "Canola",
    "Barley",
    "Narrow Leaf Lupin",
    "Field Peas",
    "Faba/Broad/Field Bean",
    "Pasture",
)
MONTH_LABELS = {
    4: "April", 5: "May", 6: "June", 7: "July",
    8: "August", 9: "September", 10: "October", 11: "November",
}
MONTH_ABBR = {
    4: "Apr", 5: "May", 6: "Jun", 7: "Jul",
    8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov",
}

PANEL_COLUMNS = (
    "field_id", "paddock_name", "farm", "region", "year", "status", "crop",
    "crop_group", "crop_mix", "mixed_flag", "area_ha_reported", "area_ha_geom",
    "area_ha_noncropped", "variety", "use_type",
)
NDVI_FIELD_COLUMNS = (
    "field_slug", "farm", "region", "year", "status", "crop",
    "months_expected", "months_observed", "missing_months",
    "season_mean_ndvi", "season_integral_ndvi_months", "peak_month",
    "peak_ndvi",
)
NDVI_MONTH_COLUMNS = (
    "field_slug", "year", "month", "ndvi_median", "used_in_season",
    "valid_fraction",
)
WEATHER_COLUMNS = (
    "farm", "year", "rain_season_mm", "gdd_base0", "frost_days",
    "rain_sowing_mm", "season_days", "season_expected_days",
    "season_complete",
)


@dataclass(frozen=True)
class Table:
    """A markdown table: cells are already formatted strings."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    align: str = ""

    def render(self) -> str:
        align = self.align or "l" * len(self.header)
        if len(align) != len(self.header):
            raise ValueError(f"align {align!r} does not match {self.header!r}")
        widths = [
            max(len(self.header[i]), *(len(r[i]) for r in self.rows))
            if self.rows else len(self.header[i])
            for i in range(len(self.header))
        ]

        def line(cells: tuple[str, ...]) -> str:
            parts = []
            for i, cell in enumerate(cells):
                parts.append(cell.rjust(widths[i]) if align[i] == "r"
                             else cell.ljust(widths[i]))
            return "| " + " | ".join(parts) + " |"

        rule = "| " + " | ".join(
            ("-" * (w - 1) + ":") if align[i] == "r" else ("-" * w)
            for i, w in enumerate(widths)
        ) + " |"
        return "\n".join([line(self.header), rule, *(line(r) for r in self.rows)])


def _pct(value: float, places: int = 1) -> str:
    return f"{value:.{places}f}"


def _num(value: float, places: int = 0) -> str:
    return f"{value:,.{places}f}"


def _ndvi(value: float) -> str:
    return f"{value:.3f}"


def _read(name: str, columns: tuple[str, ...], **kwargs) -> pd.DataFrame:
    path = EXAMPLES / name
    if not path.exists():
        raise SystemExit(
            f"missing {path.relative_to(BASE)} - run the export/build scripts "
            "first (see README.md)"
        )
    frame = pd.read_csv(path, **kwargs)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise SystemExit(f"{name} is missing column(s): {', '.join(missing)}")
    return frame


def load_examples() -> dict[str, pd.DataFrame]:
    return {
        "panel": _read("panel_field_year.csv", PANEL_COLUMNS,
                       dtype={"field_id": str}),
        "field_year": _read("ndvi_field_year.csv", NDVI_FIELD_COLUMNS,
                            dtype={"field_slug": str}),
        "monthly": _read("ndvi_monthly.csv", NDVI_MONTH_COLUMNS,
                         dtype={"field_slug": str}),
        "weather": _read("weather_seasonal.csv", WEATHER_COLUMNS),
    }


def _farm_regions(panel: pd.DataFrame) -> dict[str, str]:
    """farm -> zone, read from the panel so no mapping is copied by hand."""
    pairs = panel[["farm", "region"]].drop_duplicates()
    doubled = pairs.groupby("farm")["region"].nunique()
    if (doubled > 1).any():
        raise SystemExit(
            "farm(s) mapped to more than one region: "
            + ", ".join(doubled[doubled > 1].index)
        )
    return dict(zip(pairs["farm"], pairs["region"]))


def _shannon(values: pd.Series) -> float:
    share = values.value_counts(normalize=True)
    return float(-(share * np.log(share)).sum())


def build_findings(data: dict[str, pd.DataFrame]) -> dict[str, str | Table]:
    panel = data["panel"]
    field_year = data["field_year"]
    monthly = data["monthly"]
    weather = data["weather"].copy()

    region_by_farm = _farm_regions(panel)
    weather["region"] = weather["farm"].map(region_by_farm)
    if weather["region"].isna().any():
        unknown = sorted(weather.loc[weather["region"].isna(), "farm"].unique())
        raise SystemExit(f"weather farm(s) not in the panel: {unknown}")

    observed = panel[panel["status"] == "observed"].copy()
    not_managed = panel[panel["status"] == "not_managed"].copy()
    fobs = field_year[field_year["status"] == "observed"].copy()
    fnm = field_year[field_year["status"] == "not_managed"].copy()

    years = sorted(int(y) for y in panel["year"].unique())
    # Footprint from the current boundary schema: 2025 observes all 33 paddocks
    # and is the only season where geometry, reported and non-cropped area are
    # all present for every field.
    latest_all = panel[panel["year"] == years[-2]]
    if latest_all["field_id"].nunique() != panel["field_id"].nunique():
        raise SystemExit("expected the second-last season to observe every field")
    if latest_all["area_ha_geom"].isna().any():
        raise SystemExit("second-last season has missing geometry")
    fields = latest_all.drop_duplicates("field_id")
    footprint = fields.groupby("region")["area_ha_geom"].sum()

    total_footprint = float(fields["area_ha_geom"].sum())
    total_cropped = float(latest_all["area_ha_reported"].sum())
    total_noncropped = float(latest_all["area_ha_noncropped"].sum())

    f: dict[str, str | Table] = {}

    # ---------------- dataset shape -------------------------------------
    f["n_fields"] = _num(panel["field_id"].nunique())
    f["n_farms"] = _num(panel["farm"].nunique())
    f["n_zones"] = _num(panel["region"].nunique())
    f["n_seasons"] = _num(len(years))
    f["first_year"] = str(years[0])
    f["last_year"] = str(years[-1])
    f["year_two"] = str(years[1])
    f["year_three"] = str(years[2])
    f["year_four"] = str(years[3])
    f["year_five"] = str(years[4])
    f["last_year_minus_one"] = str(years[-2])
    f["n_field_years"] = _num(len(panel))
    f["n_observed"] = _num(len(observed))
    f["n_not_managed"] = _num(len(not_managed))
    f["n_farm_seasons"] = _num(len(weather))
    f["area_footprint_ha"] = _num(total_footprint, 1)
    f["area_cropped_ha"] = _num(total_cropped, 1)
    f["area_noncropped_ha"] = _num(total_noncropped, 0)
    # Footprint = cropped + non-cropped, by construction; assert it so a broken
    # export fails here rather than in the prose.
    if abs(total_footprint - total_cropped - total_noncropped) > 0.5:
        raise SystemExit("footprint does not reconcile with cropped + non-cropped")
    f["lease_area_pct"] = _pct(100.0 * footprint["Lease country"]
                               / total_footprint)
    f["harden_area_pct"] = _pct(100.0 * footprint["Harden"] / total_footprint)
    f["n_lease_fields"] = _num(int((fields["region"] == "Lease country").sum()))
    f["n_harden_fields"] = _num(int((fields["region"] == "Harden").sum()))
    f["lease_field_pct"] = _pct(
        100.0 * (fields["region"] == "Lease country").sum() / len(fields)
    )
    f["harden_field_pct"] = _pct(
        100.0 * (fields["region"] == "Harden").sum() / len(fields)
    )

    size_by_region = fields.groupby("region")["area_ha_geom"]
    f["median_ha_harden"] = _num(size_by_region.median()["Harden"], 1)
    f["median_ha_trungley"] = _num(size_by_region.median()["Trungley Hall"], 1)
    f["median_ha_lease"] = _num(size_by_region.median()["Lease country"], 1)
    f["size_ratio"] = _num(size_by_region.median()["Lease country"]
                           / size_by_region.median()["Harden"], 0)

    # Not-managed rows: entry seasons of the two lease blocks, plus Rio 2026.
    f["nm_entry"] = _num(int((not_managed["farm"].isin(
        ["Brentwood (lease)", "Woodlands (lease)"])).sum()))
    f["nm_rio_2026"] = _num(int(((not_managed["farm"] == "Rio (lease)")
                                 & (not_managed["year"] == years[-1])).sum()))
    leaseless = fnm[fnm["farm"].isin(["Brentwood (lease)",
                                      "Woodlands (lease)"])]
    rio = fnm[fnm["farm"] == "Rio (lease)"]
    f["nm_lease_ndvi_low"] = _ndvi(float(leaseless["season_mean_ndvi"].min()))
    f["nm_lease_ndvi_high"] = _ndvi(float(leaseless["season_mean_ndvi"].max()))
    f["nm_rio_ndvi_low"] = _ndvi(float(rio["season_mean_ndvi"].min()))
    f["nm_rio_ndvi_high"] = _ndvi(float(rio["season_mean_ndvi"].max()))
    f["n_split_paddocks"] = _num(int(
        panel["mixed_flag"].fillna(False).astype(bool).sum()
    ))

    zone_rows = []
    for region in REGIONS:
        block = panel[panel["region"] == region]
        zone_rows.append((
            region,
            _num(block["farm"].nunique()),
            _num(block["field_id"].nunique()),
            _num(footprint[region], 1),
            _pct(100.0 * footprint[region] / total_footprint),
            _num(int((block["status"] == "observed").sum())),
            _num(int((block["status"] == "not_managed").sum())),
        ))
    zone_rows.append((
        "All", _num(panel["farm"].nunique()), _num(panel["field_id"].nunique()),
        _num(total_footprint, 1), _pct(100.0), _num(len(observed)),
        _num(len(not_managed)),
    ))
    f["zone_profile"] = Table(
        ("Zone", "Farms", "Paddocks", "Footprint ha", "Area share %",
         "Observed field-years", "Not managed"),
        tuple(zone_rows), "lrrrrrr",
    )

    months_by_year = fobs.groupby("year")["months_observed"].sum()
    year_rows = []
    for year in years:
        block = panel[panel["year"] == year]
        year_rows.append((
            str(year),
            _num(int((block["status"] == "observed").sum())),
            _num(int((block["status"] == "not_managed").sum())),
            _num(float(block.loc[block["status"] == "observed",
                                 "area_ha_reported"].sum()), 0),
            _num(int(months_by_year.get(year, 0))),
        ))
    f["year_profile"] = Table(
        ("Season", "Paddocks with a crop", "Not managed", "Cropped ha",
         "Paddock-months observed"),
        tuple(year_rows), "lrrrr",
    )
    f["area_first"] = _num(float(
        observed.loc[observed["year"] == years[0], "area_ha_reported"].sum()), 0)
    f["area_peak"] = _num(float(
        observed.loc[observed["year"] == years[-2], "area_ha_reported"].sum()), 0)
    f["area_last"] = _num(float(
        observed.loc[observed["year"] == years[-1], "area_ha_reported"].sum()), 0)

    # ---------------- rotation -------------------------------------------
    ordered = observed.sort_values(["field_id", "year"])
    ordered["next_crop"] = ordered.groupby("field_id")["crop"].shift(-1)
    ordered["next_year"] = ordered.groupby("field_id")["year"].shift(-1)
    pairs = ordered[ordered["next_year"] == ordered["year"] + 1]
    n_pairs = len(pairs)
    transitions = pairs.groupby(["crop", "next_crop"]).size().sort_values(
        ascending=False)
    f["n_transitions"] = _num(n_pairs)

    def transition_pct(crop: str, nxt: str) -> str:
        count = int(transitions.get((crop, nxt), 0))
        return _pct(100.0 * count / n_pairs)

    f["canola_to_wheat_pct"] = transition_pct("Canola", "Wheat")
    f["wheat_to_canola_pct"] = transition_pct("Wheat", "Canola")
    f["wheat_to_wheat_pct"] = transition_pct("Wheat", "Wheat")
    f["same_crop_pct"] = _pct(
        100.0 * float((pairs["crop"] == pairs["next_crop"]).mean()))

    top = transitions.head(6)
    transition_rows = [
        (f"{crop} to {nxt}", _num(int(count)),
         _pct(100.0 * float(count) / n_pairs))
        for (crop, nxt), count in top.items()
    ]
    other = n_pairs - int(top.sum())
    transition_rows.append(("All other transitions", _num(other),
                            _pct(100.0 * other / n_pairs)))
    f["rotation_transitions"] = Table(
        ("Transition", "Field-years", "Share %"),
        tuple(transition_rows), "lrr",
    )

    share = observed["crop"].value_counts(normalize=True) * 100.0
    f["wheat_share"] = _pct(float(share["Wheat"]))
    f["canola_share"] = _pct(float(share["Canola"]))
    f["wheat_canola_share"] = _pct(float(share["Wheat"] + share["Canola"]))
    group_share = observed["crop_group"].value_counts(normalize=True) * 100.0
    f["cereal_share"] = _pct(float(group_share.get("Cereal", 0.0)))
    f["oilseed_share"] = _pct(float(group_share.get("Oilseed", 0.0)))
    f["legume_share"] = _pct(float(group_share.get("Legume", 0.0)))
    f["pasture_share"] = _pct(float(group_share.get("Pasture", 0.0)))

    mix = pd.crosstab(observed["region"], observed["crop"], normalize="index")
    mix = (mix * 100.0).reindex(REGIONS).reindex(columns=CROPS, fill_value=0.0)
    zone_mix_rows = []
    for crop in CROPS:
        values = [float(mix.loc[r, crop]) for r in REGIONS]
        zone_mix_rows.append((
            crop, *(_pct(v) for v in values),
            _pct(float((observed["crop"] == crop).mean() * 100.0)),
        ))
    f["crop_mix_zone"] = Table(
        ("Crop", *REGIONS, "All"), tuple(zone_mix_rows), "lrrrr",
    )

    by_year = pd.crosstab(observed["year"], observed["crop"], normalize="index")
    by_year = by_year * 100.0
    legume = ["Narrow Leaf Lupin", "Field Peas", "Faba/Broad/Field Bean"]
    year_mix_rows = []
    for label, crops in (("Wheat", ["Wheat"]), ("Canola", ["Canola"]),
                         ("Barley", ["Barley"]), ("Other legumes", legume),
                         ("Pasture", ["Pasture"])):
        values = []
        for year in years:
            present = [c for c in crops if c in by_year.columns]
            values.append(float(by_year.loc[year, present].sum()))
        year_mix_rows.append((label, *(_pct(v) for v in values)))
    f["crop_mix_year"] = Table(
        ("Crop", *(str(y) for y in years)), tuple(year_mix_rows), "lrrrrr",
    )

    f["shannon_harden"] = f"{_shannon(observed.loc[observed['region'] == 'Harden', 'crop']):.2f}"
    f["shannon_trungley"] = f"{_shannon(observed.loc[observed['region'] == 'Trungley Hall', 'crop']):.2f}"
    f["shannon_lease"] = f"{_shannon(observed.loc[observed['region'] == 'Lease country', 'crop']):.2f}"
    f["lease_observed_field_years"] = _num(int(
        (observed["region"] == "Lease country").sum()))
    f["harden_observed_field_years"] = _num(int(
        (observed["region"] == "Harden").sum()))

    wheat_2024 = float(by_year.loc[years[2], "Wheat"])
    canola_2024 = float(by_year.loc[years[2], "Canola"])
    canola_2023 = float(by_year.loc[years[1], "Canola"])
    f["wheat_2024_pct"] = _pct(wheat_2024)
    f["canola_2024_pct"] = _pct(canola_2024)
    f["canola_2023_pct"] = _pct(canola_2023)

    pair_2324 = pairs[pairs["year"] == years[1]]
    pair_2425 = pairs[pairs["year"] == years[2]]
    f["canola_wheat_2324"] = (
        f"{int(((pair_2324['crop'] == 'Canola') & (pair_2324['next_crop'] == 'Wheat')).sum())}"
        f" of {len(pair_2324)}")
    f["wheat_canola_2425"] = (
        f"{int(((pair_2425['crop'] == 'Wheat') & (pair_2425['next_crop'] == 'Canola')).sum())}"
        f" of {len(pair_2425)}")

    # ---------------- climate --------------------------------------------
    def zone_mean(column: str, region: str) -> float:
        return float(weather.loc[weather["region"] == region, column].mean())

    f["rain_harden"] = _num(zone_mean("rain_season_mm", "Harden"))
    f["rain_trungley"] = _num(zone_mean("rain_season_mm", "Trungley Hall"))
    f["rain_lease"] = _num(zone_mean("rain_season_mm", "Lease country"))
    f["rain_harden_range"] = "{}–{}".format(
        _num(float(weather.loc[weather["region"] == "Harden",
                               "rain_season_mm"].min())),
        _num(float(weather.loc[weather["region"] == "Harden",
                               "rain_season_mm"].max())))
    f["rain_trungley_range"] = "{}–{}".format(
        _num(float(weather.loc[weather["region"] == "Trungley Hall",
                               "rain_season_mm"].min())),
        _num(float(weather.loc[weather["region"] == "Trungley Hall",
                               "rain_season_mm"].max())))
    f["rain_lease_range"] = "{}–{}".format(
        _num(float(weather.loc[weather["region"] == "Lease country",
                               "rain_season_mm"].min())),
        _num(float(weather.loc[weather["region"] == "Lease country",
                               "rain_season_mm"].max())))
    f["gdd_harden"] = _num(zone_mean("gdd_base0", "Harden"))
    f["gdd_trungley"] = _num(zone_mean("gdd_base0", "Trungley Hall"))
    f["gdd_lease"] = _num(zone_mean("gdd_base0", "Lease country"))
    f["frost_harden"] = _num(zone_mean("frost_days", "Harden"))
    f["frost_trungley"] = _num(zone_mean("frost_days", "Trungley Hall"))
    f["frost_lease"] = _num(zone_mean("frost_days", "Lease country"))
    f["sowing_harden"] = _num(zone_mean("rain_sowing_mm", "Harden"))
    f["sowing_trungley"] = _num(zone_mean("rain_sowing_mm", "Trungley Hall"))
    f["sowing_lease"] = _num(zone_mean("rain_sowing_mm", "Lease country"))

    climate_zone_rows = []
    for region in REGIONS:
        block = weather[weather["region"] == region]
        climate_zone_rows.append((
            region,
            _num(float(block["rain_season_mm"].mean())),
            "{}–{}".format(_num(float(block["rain_season_mm"].min())),
                           _num(float(block["rain_season_mm"].max()))),
            _num(float(block["gdd_base0"].mean())),
            _num(float(block["frost_days"].mean())),
            _num(float(block["rain_sowing_mm"].mean())),
        ))
    f["climate_zone"] = Table(
        ("Zone", "Season rain mm", "Season range mm", "GDD base 0",
         "Frost days", "Sowing rain mm"),
        tuple(climate_zone_rows), "lrrrrr",
    )

    year_weather = weather.groupby("year")[
        ["rain_season_mm", "gdd_base0", "frost_days", "rain_sowing_mm"]
    ].mean()
    climate_year_rows = []
    for year in years:
        row = year_weather.loc[year]
        climate_year_rows.append((
            str(year), _num(float(row["rain_season_mm"])),
            _num(float(row["gdd_base0"])), _num(float(row["frost_days"])),
            _num(float(row["rain_sowing_mm"])),
        ))
    f["climate_year"] = Table(
        ("Season", "Rain mm", "GDD base 0", "Frost days", "Sowing rain mm"),
        tuple(climate_year_rows), "lrrrr",
    )

    def year_mean(column: str, year: int) -> float:
        return float(year_weather.loc[year, column])

    f["rain_2022"] = _num(year_mean("rain_season_mm", years[0]))
    f["rain_2023"] = _num(year_mean("rain_season_mm", years[1]))
    f["rain_2024"] = _num(year_mean("rain_season_mm", years[2]))
    f["rain_2025"] = _num(year_mean("rain_season_mm", years[3]))
    f["rain_2026"] = _num(year_mean("rain_season_mm", years[4]))
    f["gdd_2022"] = _num(year_mean("gdd_base0", years[0]))
    f["gdd_2026"] = _num(year_mean("gdd_base0", years[-1]))
    f["gdd_2026_gap_pct"] = _pct(
        100.0 * (year_mean("gdd_base0", years[-1])
                 / year_mean("gdd_base0", years[0]) - 1.0))
    f["frost_2022"] = _num(year_mean("frost_days", years[0]))
    f["frost_2023"] = _num(year_mean("frost_days", years[1]))
    f["sowing_2022"] = _num(year_mean("rain_sowing_mm", years[0]))
    f["sowing_2025"] = _num(year_mean("rain_sowing_mm", years[3]))
    f["sowing_ratio"] = _num(year_mean("rain_sowing_mm", years[0])
                             / year_mean("rain_sowing_mm", years[3]), 1)
    f["rain_2022_vs_2023"] = _num(year_mean("rain_season_mm", years[0])
                                  / year_mean("rain_season_mm", years[1]), 1)
    f["corr_rain_gdd"] = f"{weather['rain_season_mm'].corr(weather['gdd_base0']):.2f}"

    wettest = weather.loc[weather["rain_season_mm"].idxmax()]
    driest = weather.loc[weather["rain_season_mm"].idxmin()]
    f["wettest_farm"] = str(wettest["farm"])
    f["wettest_year"] = str(int(wettest["year"]))
    f["wettest_mm"] = _num(float(wettest["rain_season_mm"]))
    f["driest_farm"] = str(driest["farm"])
    f["driest_year"] = str(int(driest["year"]))
    f["driest_mm"] = _num(float(driest["rain_season_mm"]))

    partial = weather[~weather["season_complete"].astype(bool)]
    if len(partial) != 1:
        raise SystemExit(f"expected one partial farm-season, got {len(partial)}")
    last = partial.iloc[0]
    f["partial_farm"] = str(last["farm"])
    f["partial_year"] = str(int(last["year"]))
    f["partial_days"] = _num(float(last["season_days"]))
    f["partial_expected"] = _num(float(last["season_expected_days"]))

    # ---------------- NDVI coverage and shape ----------------------------
    f["pm_expected"] = _num(int(fobs["months_expected"].sum()))
    f["pm_observed"] = _num(int(fobs["months_observed"].sum()))
    gaps = int(fobs["months_expected"].sum() - fobs["months_observed"].sum())
    f["pm_gaps"] = _num(gaps)
    f["pm_gap_pct"] = _pct(
        100.0 * gaps / float(fobs["months_expected"].sum()))
    f["incomplete_seasons"] = _num(int(
        (fobs["months_observed"] < fobs["months_expected"]).sum()))
    f["nm_months_observed"] = _num(int(fnm["months_observed"].sum()))
    f["nm_months_expected"] = _num(int(fnm["months_expected"].sum()))
    if fobs["months_expected"].nunique() != 1:
        raise SystemExit("seasons do not all expect the same number of months")
    f["season_months"] = _num(int(fobs["months_expected"].iloc[0]))
    trungley_2022_wheat = fobs[
        (fobs["region"] == "Trungley Hall")
        & (fobs["crop"] == "Wheat")
        & (fobs["year"] == years[0])
    ]
    f["trungley_2022_months_low"] = _num(
        int(trungley_2022_wheat["months_observed"].min()))
    f["trungley_2022_months_high"] = _num(
        int(trungley_2022_wheat["months_observed"].max()))
    small_crops = fobs[fobs["crop"].isin(
        ["Field Peas", "Faba/Broad/Field Bean"])].groupby("crop").size()
    f["small_crop_n"] = _num(int(small_crops.min()))

    used = monthly[monthly["used_in_season"].astype(bool)].copy()
    unused = monthly[~monthly["used_in_season"].astype(bool)]
    gap_by_month = unused.groupby("month").size().to_dict()
    f["nov_gaps"] = _num(int(gap_by_month.get(11, 0)))
    f["jun_gaps"] = _num(int(gap_by_month.get(6, 0)))

    climatology = used.groupby("month")["ndvi_median"].median()
    clim_rows = []
    for month in sorted(climatology.index):
        values = used.loc[used["month"] == month, "ndvi_median"]
        clim_rows.append((
            str(month), MONTH_LABELS[int(month)], _ndvi(float(climatology[month])),
            _ndvi(float(values.quantile(0.1))),
            _ndvi(float(values.quantile(0.9))),
        ))
    f["ndvi_month"] = Table(
        ("Month", "Name", "Median NDVI", "P10", "P90"),
        tuple(clim_rows), "rlrrr",
    )
    f["clim_apr"] = _ndvi(float(climatology[4]))
    f["clim_may"] = _ndvi(float(climatology[5]))
    f["clim_jun"] = _ndvi(float(climatology[6]))
    f["clim_jul"] = _ndvi(float(climatology[7]))
    f["clim_aug"] = _ndvi(float(climatology[8]))
    f["clim_sep"] = _ndvi(float(climatology[9]))
    f["clim_oct"] = _ndvi(float(climatology[10]))
    f["clim_nov"] = _ndvi(float(climatology[11]))
    f["aug_p10"] = _ndvi(float(used.loc[used["month"] == 8,
                                       "ndvi_median"].quantile(0.1)))
    f["aug_p90"] = _ndvi(float(used.loc[used["month"] == 8,
                                       "ndvi_median"].quantile(0.9)))

    peaks = fobs["peak_month"].value_counts()
    f["peak_sep"] = _num(int(peaks.get(9, 0)))
    f["peak_aug"] = _num(int(peaks.get(8, 0)))
    f["peak_jul"] = _num(int(peaks.get(7, 0)))
    f["peak_oct"] = _num(int(peaks.get(10, 0)))
    wheat_peaks = fobs.loc[fobs["crop"] == "Wheat", "peak_month"].value_counts()
    f["wheat_peak_sep"] = _num(int(wheat_peaks.get(9, 0)))
    f["wheat_n"] = _num(int((fobs["crop"] == "Wheat").sum()))
    canola_peaks = fobs.loc[fobs["crop"] == "Canola",
                            "peak_month"].value_counts()
    f["canola_peak_jul_aug"] = _num(
        int(canola_peaks.get(7, 0) + canola_peaks.get(8, 0)))
    f["canola_n"] = _num(int((fobs["crop"] == "Canola").sum()))

    integral = fobs["season_integral_ndvi_months"]
    f["integral_median"] = _ndvi(float(integral.median()))
    f["integral_min"] = _ndvi(float(integral.min()))
    f["integral_max"] = _ndvi(float(integral.max()))

    crop_rows = []
    for crop in CROPS:
        block = fobs[fobs["crop"] == crop]
        if block.empty:
            continue
        crop_rows.append((
            crop, _num(len(block)),
            _ndvi(float(block["season_mean_ndvi"].median())),
            _ndvi(float(block["season_integral_ndvi_months"].median())),
            _ndvi(float(block["peak_ndvi"].median())),
        ))
    f["ndvi_crop"] = Table(
        ("Crop", "Field-years", "Median mean NDVI", "Median integral",
         "Median peak"),
        tuple(crop_rows), "lrrrr",
    )

    zone_rows = []
    for region in REGIONS:
        block = fobs[fobs["region"] == region]
        wheat = block[block["crop"] == "Wheat"]
        zone_rows.append((
            region, _num(len(block)),
            _ndvi(float(block["season_mean_ndvi"].median())),
            _ndvi(float(wheat["season_mean_ndvi"].median())),
            _ndvi(float(block["season_integral_ndvi_months"].median())),
        ))
    f["ndvi_zone"] = Table(
        ("Zone", "Field-years", "Median mean NDVI", "Wheat only",
         "Median integral"),
        tuple(zone_rows), "lrrrr",
    )
    for region, key in (("Harden", "harden"), ("Trungley Hall", "trungley"),
                        ("Lease country", "lease")):
        block = fobs[fobs["region"] == region]
        wheat = block[block["crop"] == "Wheat"]
        f[f"mean_{key}"] = _ndvi(float(block["season_mean_ndvi"].median()))
        f[f"wheat_mean_{key}"] = _ndvi(
            float(wheat["season_mean_ndvi"].median()))
        f[f"integral_{key}"] = _ndvi(
            float(block["season_integral_ndvi_months"].median()))
    f["zone_integral_gap"] = _ndvi(
        float(fobs.loc[fobs["region"] == "Harden",
                       "season_integral_ndvi_months"].median())
        - float(fobs.loc[fobs["region"] == "Lease country",
                         "season_integral_ndvi_months"].median()))

    wheat_by_year = fobs[fobs["crop"] == "Wheat"].groupby("year")
    wheat_rows = []
    for year in years:
        block = wheat_by_year.get_group(year) if year in wheat_by_year.groups \
            else fobs.iloc[0:0]
        wheat_rows.append((
            str(year), _num(len(block)),
            _ndvi(float(block["season_mean_ndvi"].median())),
            _ndvi(float(block["season_integral_ndvi_months"].median())),
        ))
    f["ndvi_wheat_year"] = Table(
        ("Season", "Wheat field-years", "Median mean NDVI", "Median integral"),
        tuple(wheat_rows), "lrrr",
    )
    for year in years:
        block = fobs[(fobs["crop"] == "Wheat") & (fobs["year"] == year)]
        f[f"wheat_{year}"] = _ndvi(float(block["season_mean_ndvi"].median()))

    zone_year = fobs.pivot_table(
        index="region", columns="year",
        values="season_integral_ndvi_months", aggfunc="median",
    ).reindex(REGIONS)
    zone_year_rows = []
    for region in REGIONS:
        zone_year_rows.append((
            region, *(_ndvi(float(zone_year.loc[region, y])) for y in years),
        ))
    f["ndvi_zone_year"] = Table(
        ("Zone", *(str(y) for y in years)), tuple(zone_year_rows), "lrrrrr",
    )
    for region, key_region in (("Harden", "harden"), ("Trungley Hall", "trungley"),
                               ("Lease country", "lease")):
        f[f"integral_{key_region}_2022"] = _ndvi(
            float(zone_year.loc[region, years[0]]))
    f["integral_harden_best"] = _ndvi(float(zone_year.loc["Harden"].max()))
    f["integral_harden_worst"] = _ndvi(float(zone_year.loc["Harden"].min()))
    f["integral_harden_span"] = _ndvi(
        float(zone_year.loc["Harden"].max() - zone_year.loc["Harden"].min()))

    # Scene counts are not part of the committed export; visibility is.
    f["valid_fraction_median"] = _ndvi(
        float(used["valid_fraction"].median()))
    zone_year_visibility = used.groupby(["year", "region"])[
        "valid_fraction"].median()
    if zone_year_visibility.min() < 0.999:
        raise SystemExit("a zone-year's median visibility is below full")

    sowing_2022 = weather[weather["year"] == years[0]]
    f["sowing_kanoona_2022"] = _num(float(
        sowing_2022.loc[sowing_2022["farm"] == "Kanoona",
                        "rain_sowing_mm"].iloc[0]), 1)
    others = sowing_2022[sowing_2022["farm"] != "Kanoona"]["rain_sowing_mm"]
    f["sowing_others_2022_low"] = _num(float(others.min()), 1)
    f["sowing_others_2022_high"] = _num(float(others.max()), 1)

    grain_2022 = fobs[(fobs["year"] == years[0]) & (fobs["crop"] == "Wheat")
                      & (fobs["use_type"] == "grain")]
    dual_2022 = fobs[(fobs["year"] == years[0]) & (fobs["crop"] == "Wheat")
                     & (fobs["use_type"] == "dual_purpose")]
    f["grain_2022_n"] = _num(len(grain_2022))
    f["dual_2022_n"] = _num(len(dual_2022))
    f["grain_2022_mean"] = _ndvi(float(grain_2022["season_mean_ndvi"].median()))
    f["dual_2022_mean"] = _ndvi(float(dual_2022["season_mean_ndvi"].median()))
    f["grain_2022_integral"] = _ndvi(
        float(grain_2022["season_integral_ndvi_months"].median()))
    f["dual_2022_integral"] = _ndvi(
        float(dual_2022["season_integral_ndvi_months"].median()))
    f["grain_2022_regions"] = ", ".join(sorted(grain_2022["region"].unique()))
    f["dual_2022_regions"] = ", ".join(sorted(dual_2022["region"].unique()))

    joined = fobs.merge(
        weather[["farm", "year", "rain_season_mm", "gdd_base0"]],
        on=["farm", "year"], how="left",
    )
    if joined["rain_season_mm"].isna().any():
        raise SystemExit("field-years without a matching farm-season weather row")
    f["pearson_integral_rain"] = f"{joined['season_integral_ndvi_months'].corr(joined['rain_season_mm']):.2f}"
    f["spearman_integral_rain"] = f"{joined['season_integral_ndvi_months'].corr(joined['rain_season_mm'], method='spearman'):.2f}"
    wheat_join = joined[joined["crop"] == "Wheat"]
    canola_join = joined[joined["crop"] == "Canola"]
    f["spearman_wheat"] = f"{wheat_join['season_integral_ndvi_months'].corr(wheat_join['rain_season_mm'], method='spearman'):.2f}"
    f["spearman_canola"] = f"{canola_join['season_integral_ndvi_months'].corr(canola_join['rain_season_mm'], method='spearman'):.2f}"

    joined["tercile"] = pd.qcut(joined["rain_season_mm"], 3,
                                labels=["dry", "mid", "wet"])
    tercile_rows = []
    for label in ("dry", "mid", "wet"):
        block = joined[joined["tercile"] == label]
        tercile_rows.append((
            label, _num(len(block)),
            _num(float(block["rain_season_mm"].mean())),
            _ndvi(float(block["season_integral_ndvi_months"].median())),
        ))
    f["rain_tercile"] = Table(
        ("Season-rain tercile", "Field-years", "Mean rain mm",
         "Median integral"),
        tuple(tercile_rows), "lrrr",
    )
    f["tercile_dry_integral"] = _ndvi(float(
        joined.loc[joined["tercile"] == "dry",
                   "season_integral_ndvi_months"].median()))
    f["tercile_mid_integral"] = _ndvi(float(
        joined.loc[joined["tercile"] == "mid",
                   "season_integral_ndvi_months"].median()))
    f["tercile_wet_integral"] = _ndvi(float(
        joined.loc[joined["tercile"] == "wet",
                   "season_integral_ndvi_months"].median()))

    complete = fobs[fobs["months_observed"] == fobs["months_expected"]]
    matrix = used.pivot_table(index=["field_slug", "year"], columns="month",
                              values="ndvi_median")
    keys = list(zip(complete["field_slug"], complete["year"]))
    complete_matrix = matrix.loc[keys]
    shift = complete_matrix.mean(axis=1) - complete_matrix.drop(
        columns=[11]).mean(axis=1)
    # November is the dimmest month, so dropping it lifts the mean. Store the
    # size of that lift; the direction is stated in the prose.
    f["nov_bias_mean"] = _ndvi(abs(float(shift.median())))
    f["nov_bias_integral"] = _ndvi(
        float(complete_matrix[11].median()))
    f["nov_complete_mean"] = _ndvi(float(complete_matrix[11].mean()))
    f["complete_seasons"] = _num(len(complete))
    f["nov_median"] = _ndvi(float(climatology[11]))

    return f


PLACEHOLDER = re.compile(r"\{\{([a-z0-9_]+)\}\}")


def render(template: str, findings: dict[str, str | Table]) -> str:
    found = set(PLACEHOLDER.findall(template))
    unknown = sorted(found - set(findings))
    unused = sorted(set(findings) - found)
    if unknown:
        raise SystemExit(
            "template uses placeholder(s) nothing computes: " + ", ".join(unknown)
        )
    if unused:
        raise SystemExit(
            "computed finding(s) the template never prints: " + ", ".join(unused)
        )

    def substitute(match: re.Match[str]) -> str:
        value = findings[match.group(1)]
        if isinstance(value, Table):
            return "\n" + value.render() + "\n"
        return value

    text = PLACEHOLDER.sub(substitute, template)
    return text if text.endswith("\n") else text + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if ANALYSIS.md differs from a fresh render")
    parser.add_argument("--stdout", action="store_true",
                        help="print the render instead of writing it")
    args = parser.parse_args(argv)

    if not TEMPLATE.exists():
        raise SystemExit(f"missing template {TEMPLATE.relative_to(BASE)}")
    findings = build_findings(load_examples())
    rendered = render(TEMPLATE.read_text(encoding="utf-8"), findings)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if args.check:
        if current == rendered:
            print(f"{OUTPUT.name} is up to date "
                  f"({len(findings)} findings, {len(rendered.splitlines())} lines)")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(), rendered.splitlines(),
            fromfile=f"{OUTPUT.name} (committed)", tofile=f"{OUTPUT.name} (fresh)",
            lineterm="",
        )
        print("\n".join(list(diff)[:120]))
        print("run `python src/build_analysis.py` to regenerate", file=sys.stderr)
        return 1

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(BASE)} "
          f"({len(findings)} findings, {len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
