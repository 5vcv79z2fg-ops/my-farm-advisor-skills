# Five seasons of a mixed farming operation

This is the data-analysis section for assignment 2. It asks three questions of
the committed example tables: what the paddock panel and rotation say, what the
climate record says, and how the NDVI season behaves. Soil profiles and
boundary diagnostics are deliberately out of scope — they belong to the
pipeline's own outputs, not to a field-year analysis.

Every number below is computed by `src/build_analysis.py` from
`examples/panel_field_year.csv`, `examples/rotation_spine.csv`,
`examples/weather_seasonal.csv`, `examples/ndvi_field_year.csv` and
`examples/ndvi_monthly.csv`. The prose lives in `src/analysis_template.md` and
the builder substitutes each computed value into it; it fails if a template
token has no computation behind it, and fails if a computed finding is never
printed. So the sentences and the tables cannot drift apart, and
`python src/build_analysis.py --check` is the drift test.

## The short version

- **{{n_fields}} paddocks over {{n_seasons}} seasons, but a very uneven
  operation.** {{n_lease_fields}} of the {{n_fields}} paddocks carry
  {{lease_area_pct}} % of the {{area_footprint_ha}} ha footprint;
  {{n_harden_fields}} carry {{harden_area_pct}} %.
- **A two-crop rotation.** Wheat and canola fill {{wheat_canola_share}} % of the
  {{n_observed}} paddock-years with a crop, and canola is the break: canola to
  wheat is {{canola_to_wheat_pct}} % of all consecutive-year transitions.
- **A west–east gradient that outlives any single season.** Harden averages
  {{rain_harden}} mm of growing-season rain and a median season NDVI of
  {{mean_harden}}; lease country averages {{rain_lease}} mm and {{mean_lease}}.
- **{{first_year}} is the season that carries the most information.** It was the
  wettest ({{rain_2022}} mm) and produced one of Harden's best canopies (median
  season integral {{integral_harden_2022}}) alongside the weakest on Trungley
  Hall and lease country ({{integral_trungley_2022}} and
  {{integral_lease_2022}}).
- **Coverage is good and reported honestly.** {{pm_observed}} of
  {{pm_expected}} managed paddock-months have an observation
  ({{pm_gap_pct}} % missing), and the {{pm_gaps}} gaps are named rather than
  interpolated.

![NDVI season behaviour](examples/figures/fig05_ndvi_season.png)

## What this data can and cannot say

Read these first; several limits change how a sentence should be read.

- **Five seasons is five points.** The climate tables hold {{n_seasons}}
  seasons and {{n_farm_seasons}} farm-seasons. Any correlation here is
  descriptive, with an effective sample closer to {{n_farm_seasons}} than to
  {{n_observed}}, because paddocks on one farm share one climate record.
- **There is no yield, price or input data in the repository.** NDVI is canopy
  greenness, not production. Nothing below supports a statement about
  profitability, and "good season" always means "green canopy", never
  "profitable season".
- **A crop record is a paddock-level declaration.** {{n_split_paddocks}}
  paddock-years are split between two crops; the panel assigns the majority
  crop, so rotation shares describe paddocks rather than areas within them.
- **"Not managed" means no crop was recorded, not that the land was bare.**
  {{n_not_managed}} paddock-years carry no crop: {{nm_entry}} are the opening
  seasons of the two older lease blocks, and {{nm_rio_2026}} are Rio paddocks in
  {{last_year}}. NDVI exists for those years and is not uniform — the opening
  lease seasons sit between {{nm_lease_ndvi_low}} and {{nm_lease_ndvi_high}} mean
  NDVI, while Rio's {{last_year}} paddocks sit between {{nm_rio_ndvi_low}} and
  {{nm_rio_ndvi_high}}, a full canopy with no record attached. They are excluded
  from every crop statistic rather than treated as fallow.
- **One scene per month.** The NDVI stage keeps the candidate with the most
  visible paddock pixels per month, so within-month events — a frost, a
  waterlogging spell, a grazing pass — are invisible. The month is the finest
  resolution here.
- **Gaps cluster at the end of the season.** Of the {{pm_gaps}} missing
  paddock-months, {{nov_gaps}} are November and {{jun_gaps}} are June;
  {{incomplete_seasons}} of {{n_observed}} seasons are incomplete rather than
  absent.

## 1. The panel: {{n_fields}} paddocks, three very different scales

The panel is {{n_fields}} paddocks on {{n_farms}} farms in {{n_zones}} zones,
{{n_field_years}} paddock-years from {{first_year}} to {{last_year}}.
{{n_observed}} paddock-years have a crop and {{n_not_managed}} do not. The
footprint totals {{area_footprint_ha}} ha: {{area_cropped_ha}} ha cropped in
{{last_year_minus_one}} and {{area_noncropped_ha}} ha of non-cropped area, and
the two add to the footprint. "Area" below always means cropped area unless it
says footprint.

{{zone_profile}}

The zone table is the first warning against averaging paddocks. Lease country
holds {{lease_field_pct}} % of the paddocks but {{lease_area_pct}} % of the
area; Harden holds {{harden_field_pct}} % of the paddocks and
{{harden_area_pct}} % of the area. Median paddock size runs
{{median_ha_harden}} ha at Harden, {{median_ha_trungley}} ha at Trungley Hall
and {{median_ha_lease}} ha on the lease blocks — a {{size_ratio}}-fold range.
An unweighted paddock mean therefore over-weights Harden, and an area-weighted
statement over-weights the {{n_lease_fields}} lease paddocks. Both are reported
where they differ.

{{year_profile}}

Managed area is not stable across the five seasons: {{area_first}} ha in
{{first_year}} and {{year_two}}, then growth as the lease blocks enter, peaking
at {{area_peak}} ha in {{last_year_minus_one}}, and {{area_last}} ha in
{{last_year}} when the Rio paddocks have no crop record. A year-on-year change
in crop shares therefore partly reflects *which paddocks were farmed*, not a
decision on the same land. That is why the rotation section works from
transitions within a paddock rather than from year totals.

## 2. Rotation: canola is the break, and it sets the wheat area

Across the {{n_transitions}} consecutive-year transitions where both years have
a crop, two moves dominate:

{{rotation_transitions}}

Canola to wheat ({{canola_to_wheat_pct}} %) and wheat to canola
({{wheat_to_canola_pct}} %) account for most of the sequence. Same-crop repeats
are {{same_crop_pct}} % of transitions and wheat-after-wheat specifically
{{wheat_to_wheat_pct}} %, so continuous wheat is the exception rather than the
system. Over the whole panel wheat is {{wheat_share}} % of cropped paddock-years
and canola {{canola_share}} %; by crop group that is {{cereal_share}} % cereal,
{{oilseed_share}} % oilseed, {{legume_share}} % legume and {{pasture_share}} %
pasture. This is a two-crop rotation with a legume and pasture fringe, not a
diverse break-crop system.

{{crop_mix_zone}}

The zones differ in more than scale. Harden carries every pasture
paddock-year and the only field peas, and no barley or lupin at all. Trungley
Hall is the most mixed: barley, lupin and faba beans all appear.
Lease country has no pasture and no faba beans. Shannon diversity over crops
reflects that mix — Trungley Hall {{shannon_trungley}}, Harden
{{shannon_harden}}, lease country {{shannon_lease}} — but the lease figure
rests on {{lease_observed_field_years}} cropped paddock-years against
{{harden_observed_field_years}} at Harden, so it is the least reliable of the
three.

{{crop_mix_year}}

The year table shows the rotation pulsing rather than averaging out.
{{year_two}} was a canola year ({{canola_2023_pct}} % of cropped paddock-years),
which pushed {{year_three}} to {{wheat_2024_pct}} % wheat and only
{{canola_2024_pct}} % canola: {{canola_wheat_2324}} transitions that year ran
canola to wheat. The correction came immediately — {{wheat_canola_2425}}
transitions from {{year_three}} to {{year_four}} ran wheat to canola. The canola
share in one season largely sets the wheat share in the next, so a single year's
crop mix is a poor description of the rotation.

## 3. Climate: a west–east gradient and five very different seasons

Climate comes from SILO at 0.05°, one grid point per farm, and every paddock on
a farm shares that point. The table is therefore per zone, averaged over its
farms, not per paddock. Growing seasons differ by farm — Kanoona runs to 30
November, the other farms to 15 November — and every figure below uses each
farm's own window.

{{climate_zone}}

Harden is the wetter, frostier end: {{rain_harden}} mm of season rain
({{rain_harden_range}} mm across the five seasons), {{gdd_harden}} degree-days
base 0 °C and {{frost_harden}} frost days. Lease country is the drier, warmer
end at {{rain_lease}} mm ({{rain_lease_range}}), {{gdd_lease}} degree-days and
{{frost_lease}} frost days. Trungley Hall sits between on rain
({{rain_trungley}} mm, {{rain_trungley_range}}) and frost ({{frost_trungley}}
days) but has essentially the same heat sum as Harden ({{gdd_trungley}} against
{{gdd_harden}} degree-days) — a reminder that GDD is set by temperature and
season length, not by rainfall. Sowing rain
follows the rain gradient: {{sowing_harden}} mm at Harden, {{sowing_trungley}} mm
at Trungley Hall, {{sowing_lease}} mm on the lease blocks.

{{climate_year}}

The seasons differ far more than the zones do. {{first_year}} took
{{rain_2022}} mm against {{rain_2023}} mm in {{year_two}} — a
{{rain_2022_vs_2023}}-fold difference — and {{first_year}} holds the wettest
single farm-season in the record ({{wettest_farm}}, {{wettest_year}},
{{wettest_mm}} mm), while the driest is {{driest_farm}} in {{driest_year}} at
{{driest_mm}} mm. The heat sum moves the other way: {{year_five}} is the warmest
season at {{gdd_2026}} degree-days, {{gdd_2026_gap_pct}} % above {{first_year}}
({{gdd_2022}}). Sowing rain separates the seasons even more sharply —
{{sowing_2022}} mm in {{first_year}} against {{sowing_2025}} mm in {{year_four}},
a factor of {{sowing_ratio}}. Mean frost days run from {{frost_2022}} in the wet
{{first_year}} to {{frost_2023}} in {{year_two}}, and every season after
{{first_year}} stayed below 320 mm — {{rain_2024}} mm in {{year_three}},
{{rain_2025}} mm in {{year_four}} and {{rain_2026}} mm in {{year_five}}.

Rain and heat are not independent: the correlation between season rain and GDD
base 0 is {{corr_rain_gdd}} across the {{n_farm_seasons}} farm-seasons, so drier
seasons tend to run hotter. A claim of the form "heat drove this, not rain" is
therefore not testable with five seasons, and this analysis does not make one.

One record is still open. {{partial_year}} at {{partial_farm}} covers
{{partial_days}} of {{partial_expected}} season days, because the daily record
stops short of the season end, so its totals are lower than a completed year's
and its lines stop rather than being projected. Every other farm-season is
complete.

## 4. NDVI: one broad peak, a zonal gradient, and a {{first_year}} exception

### Coverage

The {{n_observed}} managed seasons expect {{pm_expected}} paddock-months and
have {{pm_observed}}, leaving {{pm_gaps}} gaps ({{pm_gap_pct}} %). The
not-managed seasons add {{nm_months_observed}} observed months of
{{nm_months_expected}}, but they carry no crop and enter no crop comparison.
Gaps cluster at the end of the season — {{nov_gaps}} of the {{pm_gaps}} are
November, when the canopy is already senescing, and {{jun_gaps}} are June — and
the median visible fraction of a used month is {{valid_fraction_median}} in
every zone-year, so the months that do appear are not marginal observations.

### Season shape

{{ndvi_month}}

The season is a single broad peak: {{clim_apr}} in April and {{clim_may}} in
May, a rapid rise through {{clim_jun}} in June and {{clim_jul}} in July, a
maximum of {{clim_aug}} in August, a plateau through {{clim_sep}} in September
and {{clim_oct}} in October, then a fall to {{clim_nov}} in November. The August
spread is wide — {{aug_p10}} to {{aug_p90}} between the 10th and 90th
percentiles — because crops and zones peak at different times. That single rise,
plateau and senescence, with no second peak, is what one winter crop filling the
whole window looks like. The season integral over the same seasons runs from
{{integral_min}} to {{integral_max}} NDVI-months, with a median of
{{integral_median}}.

Peak timing confirms it. Across all {{n_observed}} seasons the peak falls in
September {{peak_sep}} times, August {{peak_aug}}, July {{peak_jul}} and October
{{peak_oct}}. Wheat peaks in September in {{wheat_peak_sep}} of {{wheat_n}}
seasons; canola peaks in July or August in {{canola_peak_jul_aug}} of
{{canola_n}}, earlier than wheat, as expected for an earlier oilseed.

### Crop and zone

{{ndvi_crop}}

Read the ordering carefully. Pasture leads on the mean because its canopy is
present across the whole window rather than senescing, and lupin trails. Field
peas and faba beans rest on {{small_crop_n}} seasons each and should not be
ranked. The integral ranks the crops in the same order as the mean, so the
ordering is not an artefact of how many months each season happened to observe.

{{ndvi_zone}}

The zone gradient is the same one the climate shows, and it narrows when the
crop mix is held constant. Across all crops the median season mean is
{{mean_harden}} at Harden, {{mean_trungley}} at Trungley Hall and {{mean_lease}}
on lease country, and the median integral is {{integral_harden}},
{{integral_trungley}} and {{integral_lease}}; wheat alone gives
{{wheat_mean_harden}}, {{wheat_mean_trungley}} and {{wheat_mean_lease}}. Holding the crop constant
closes part of the Trungley gap and none of the lease gap: the lease blocks also
carry lupin, the weakest crop in the panel, but that is not the whole story.

The integral tells the same thing, and the gap between Harden and lease country
({{zone_integral_gap}} NDVI-months) is larger than the entire spread of Harden's
own best and worst seasons ({{integral_harden_worst}} to
{{integral_harden_best}}, a range of {{integral_harden_span}}). A zone effect
here is not a proxy for a bad year.

### The {{first_year}} exception

{{ndvi_zone_year}}

This table is the central result of the section. Harden's median season integral
never drops below {{integral_harden_worst}}, but Trungley Hall and lease country
collapse in {{first_year}} to {{integral_trungley_2022}} and
{{integral_lease_2022}} while Harden that same season records
{{integral_harden_2022}}. The western blocks lost most of their season and the
eastern block did not, in the wettest year on record.

That is not a coverage artefact. The Trungley Hall wheat seasons in
{{first_year}} hold {{trungley_2022_months_low}} to
{{trungley_2022_months_high}} of {{season_months}} months because November is
missing, and a missing November raises a season mean by about
{{nov_bias_mean}} NDVI — several times smaller than the {{first_year}} western
deficit. Visibility is not the explanation either: the
median visible fraction of a used month is {{valid_fraction_median}} in every
zone-year. And the collapse cuts
across crops — lease-country canola and lupin are as depressed as Trungley Hall
wheat — so it is not a crop story.

What it is cannot be settled with these tables. The obvious candidate is the
sowing window, which was the wettest of the five seasons: {{sowing_kanoona_2022}}
mm at Kanoona against {{sowing_others_2022_low}}–{{sowing_others_2022_high}} mm
on the other five farms, with the least sowing rain where the canopy was best.
But sowing date is not recorded, so the association is where the evidence stops.

One tempting explanation has to be refused. In {{first_year}} the
{{grain_2022_n}} grain-wheat seasons average {{grain_2022_mean}} mean NDVI and
{{grain_2022_integral}} integral, against {{dual_2022_mean}} and
{{dual_2022_integral}} for the {{dual_2022_n}} dual-purpose seasons — which
reads like a use-type effect. But the grain seasons are exactly the paddocks in
{{grain_2022_regions}} and the dual-purpose seasons exactly those in
{{dual_2022_regions}}; zone and use type are perfectly confounded that year, so
the comparison attributes nothing to either.

{{ndvi_wheat_year}}

Holding the crop fixed removes the mix effect but not the year effect.
{{first_year}} is the weak wheat season ({{wheat_2022}}) and {{year_five}} the
strongest ({{wheat_2026}}); {{year_two}} ({{wheat_2023}}) and {{year_four}}
({{wheat_2025}}) lie in between, and the {{year_three}} dip ({{wheat_2024}}) is
system-wide rather than western — it is the season with the most wheat
({{wheat_2024_pct}} % of paddock-years) and only {{rain_2024}} mm of rain.

### Rain and canopy

Pooling all {{n_observed}} seasons, the integral and season rain correlate at
{{pearson_integral_rain}} Pearson and {{spearman_integral_rain}} Spearman — flat
in levels, weakly monotone in ranks. Wheat alone (rho {{spearman_wheat}}) and
canola alone (rho {{spearman_canola}}) do not sharpen it.

{{rain_tercile}}

The dry third sits clearly below the others
({{tercile_dry_integral}} against {{tercile_mid_integral}} and
{{tercile_wet_integral}}), but the middle and wet thirds are effectively equal.
In this sample, once a season is not dry, more rain does not buy more canopy,
and {{first_year}} — the wettest season — is the counter-example that flattens
the correlation.

### What a missing month costs

Across the {{complete_seasons}} complete managed seasons, dropping November
raises the season mean by {{nov_bias_mean}} NDVI — November's own median is
{{nov_median}} and its complete-season mean {{nov_complete_mean}} — and removes
{{nov_bias_integral}} NDVI-months from the integral. A season missing November
therefore reads slightly greener on the mean and slightly shorter on the
integral. With {{nov_gaps}} of {{pm_gaps}} gaps in November, that is the dominant
measurement bias in this section; the integral is the more robust season metric
and it is the one used for the zone comparisons.

## 5. What the five seasons say

1. **The rotation is a two-crop pulse, not a fixed plan.** Wheat and canola are
   {{wheat_canola_share}} % of cropped paddock-years, and the canola share in
   one season sets the wheat share in the next: {{canola_wheat_2324}} canola to
   wheat transitions into {{year_three}}, then {{wheat_canola_2425}} wheat to
   canola into {{year_four}}.
2. **Paddock averages hide the operation.** Six paddocks hold
   {{lease_area_pct}} % of the area and sixteen hold {{harden_area_pct}} %, so
   this document states which basis each statistic uses.
3. **The zone gradient is the largest stable signal.** Harden
   ({{mean_harden}}, {{rain_harden}} mm) leads Trungley Hall
   ({{mean_trungley}}, {{rain_trungley}} mm) and lease country
   ({{mean_lease}}, {{rain_lease}} mm) on the season integral in all five
   seasons, and the Harden-to-lease gap of {{zone_integral_gap}} NDVI-months
   exceeds Harden's own seasonal range of {{integral_harden_span}}.
4. **{{first_year}} is the informative exception.** The wettest year produced a
   near-best Harden canopy and the worst western canopy; the association with the
   sowing window is real in the data, and the mechanism is untestable without
   sowing dates.
5. **Rain is a weak predictor once the season is not dry.** Rank correlation is
   {{spearman_integral_rain}} overall, and the mid and wet terciles are
   indistinguishable ({{tercile_mid_integral}} against
   {{tercile_wet_integral}}).
6. **The measurement limits are known and bounded.** {{pm_gaps}} of
   {{pm_expected}} paddock-months are missing ({{pm_gap_pct}} %), mostly in
   November, which biases the mean up by {{nov_bias_mean}} and the integral down
   by {{nov_bias_integral}}; nothing is imputed, and {{year_five}} remains a
   partial season.

## Reproducing this section

From this directory, with the Python environment the repository already uses
for the EDA:

```bash
python src/export_ndvi_examples.py   # runtime -> examples/ (the only runtime step)
python src/make_figures.py --only 5  # examples/figures/fig05_ndvi_season.png
python src/build_analysis.py         # writes ANALYSIS.md
python src/build_analysis.py --check # fails if ANALYSIS.md is stale
```

`export_ndvi_examples.py` is the bridge from the untracked runtime tree to the
committed tables; every step after it reads only `examples/`. `build_analysis.py`
needs pandas and numpy, not the geospatial stack, and runs offline.
