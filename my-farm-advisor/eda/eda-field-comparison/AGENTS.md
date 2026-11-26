# Local operating rules — field comparison EDA

Scope: `my-farm-advisor/eda/eda-field-comparison/`. Repo-wide policy lives in
`../../../AGENTS.md`; this file adds only what is specific to this workflow.

## Data contract

Read shapefiles from `${AU_PADDOCKS_RUNTIME}/<year>/au-paddocks.shp`, or from
`$DATA_PIPELINE_DATA_ROOT/au-paddocks/<year>/` when the shared runtime root is
set. `src/common.py` resolves both, falling back to a home-relative default.
Raw layers are never committed. Derived CSVs and figures under `examples/` are
committed; daily weather stays in the runtime directory.

Required attributes per record:

| Attribute | Used for |
|---|---|
| `paddock_id` | stable field identity across years |
| `paddock_na` | display name |
| `property_n` | farm, mapped to region |
| `area` | reported cropped area |
| `crop_name` | crop class |
| `variety_na` | variety |

If an attribute is renamed upstream, update `src/common.py` in one place. Do
not fork field lists per script.

## Rules

- Never edit a polygon. Only reproject it. Boundary correction belongs to the
  boundary repair tooling, not to this workflow.
- Never join years by paddock name. Join by `paddock_id`, then apply
  `RETIRED_TO_PARENT`.
- Never impute a `not_managed` field-year. It is a fact about management, not
  a data gap.
- Never overwrite a mixed-year crop with a single label. Keep `crop_mix`.
- Never commit a raw shapefile, a weather cache, or a credential.
- Keep the SILO email address in the environment only.

## Before changing anything

1. Re-run `python src/build_panel.py`; confirm 33 fields and 155 observed
   field-years.
2. Re-run `python src/qc_boundaries.py`; confirm exactly three changed
   boundaries, all in 2025.
3. If either count changes, stop and explain why before regenerating figures.

## Figure conventions

Region colours and crop colours are defined once at the top of
`src/make_figures.py`. Use those constants; do not introduce per-figure
palettes. Every figure carries an `n` or a scale bar so the reader can judge
what is being compared.

## Validation

From the repository root run `./scripts/validate.sh`. Keep this directory free
of `data/`, tracked binaries, and generated runtime payloads.
