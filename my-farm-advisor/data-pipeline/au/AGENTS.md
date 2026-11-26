# Local operating rules — Australian pipeline branch

Scope: `my-farm-advisor/data-pipeline/au/`. The runtime contract lives in
`../AGENTS.md`; this file adds only what is specific to the AU stages.

## Read first

`../README.md` for the runtime tree and install steps, then
[GUIDE.md](GUIDE.md) for the stage order and configuration. The executable
source is `../src/scripts/au/`; this folder documents it and does not replace
it.

## Rules

- Run from the runtime copy, never from the checkout. Refresh the runtime
  source after editing a stage, or the old code runs.
- Never treat the US step list as a substitute: the AU branch uses SLGA/ASF,
  Copernicus, Sentinel-2, SILO and grower crop records, never SSURGO, CDL,
  WorldCereal or NASA POWER.
- Keep the SILO address in `SILO_EMAIL` only. It is never written to a stage,
  a report, a manifest or a commit.
- Zone membership is data, not prose: add a farm to `ZONE_BY_FARM` in
  `../src/scripts/lib/au_common.py` and let `ZONE_REGION` derive the printed
  name. Do not hardcode a zone label in a renderer.
- Season windows belong in `SEASON_WINDOW` / `DEFAULT_SEASON_WINDOW`. Do not
  write a farm's dates into a report or a plotting function.
- Counting is managed-only. A `not_managed` field-year is printed as
  `not managed`, excluded from every statistic, and never imputed.
- Gaps stay gaps. A month below the 50 % paddock-valid gate is reported as a
  gap with a reason; never interpolate NDVI.
- Generated outputs, downloaded payloads and reports stay under
  `DATA_PIPELINE_DATA_ROOT`, never in this folder or the checkout.
- Persisted provenance records root-relative `*_rel` paths, not absolute paths.
- Rendered figures follow the repo figure gate: frame contract, colour scale
  stated, managed-only counts labelled, no empty panel left unexplained.

## Before rerunning a stage

1. Confirm `DATA_PIPELINE_DATA_ROOT` is absolute and outside the checkout.
2. Confirm the runtime source is current.
3. Confirm the stage's inputs exist (farm boundaries, `field-names.csv`,
   weather cache, downloaded scenes).
4. After the run, check the managed field-year count (155) and paddock count
   (33) against the panel; a mismatch means a farm or boundary was skipped.

## Validation

From the repository root run `./scripts/validate.sh`. Keep this folder to
documentation only; stage code belongs in `../src/scripts/au/`.
