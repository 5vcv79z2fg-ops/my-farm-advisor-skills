#!/usr/bin/env python3
"""Run the Australian reporting stages, per farm and then per zone.

The US pipeline (``run_farm_pipeline.py``) is built around SSURGO, CDL and a
corn/soy season; the AU stages source SLGA/ASF, the grower's own crop records
and Sentinel-2, and three of the output families are zone-level rather than
farm-level. Rather than bend the US step list, this entrypoint runs the AU
stages in the order their inputs require:

    per farm   soil ingest (optional, needs the farm grids)
               NDVI composites + monthly stats
               NDVI cards, soil property maps, field posters
               texture sheet, soil profile cards

    per zone   the five-year poster (one shared colour scale)
               the Markdown + self-contained HTML report

Every stage is idempotent and reads/writes the same canonical runtime tree, so
re-running one stage is safe: ``--only ndvi`` or ``--only posters``.

Usage:
    DATA_PIPELINE_DATA_ROOT=/path/to/runtime \\
    python au/run_au_pipeline.py --grower-slug au-grain-grower \\
        [--farms kanoona,caroleese,...] [--only ndvi,cards] [--with-soil-ingest]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_AU_DIR = Path(__file__).resolve().parent
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib", _AU_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FARM_ORDER = ("kanoona", "caroleese", "tullokan", "brentwood", "woodlands", "rio")

# stage key -> (script, label, per-farm env required)
FARM_STAGES = {
    "soil-ingest": ("au/ingest_au_soil.py", "SLGA/ASF soil ingest", True),
    "ndvi": ("au/build_au_ndvi.py", "NDVI composites and monthly stats", True),
    "cards": ("au/render_au_ndvi_cards.py", "NDVI cards", True),
    "soil-maps": ("au/render_au_soil_maps.py", "Soil property maps (0–30 cm)", True),
    "field-posters": ("au/render_au_field_posters.py", "Field posters", True),
    "texture": ("au/render_au_texture_triangles.py", "Texture triangle sheets", True),
    "soil-cards": ("au/render_au_soil_cards.py", "Soil profile cards", True),
}

ZONE_STAGES = {
    "zone-posters": ("au/render_au_zone_posters.py", "Zone posters", False),
    "zone-reports": ("au/build_au_zone_reports.py", "Zone reports (MD + HTML)", False),
}

DEFAULT_FARM_ORDER = (
    "ndvi",
    "cards",
    "soil-maps",
    "field-posters",
    "texture",
    "soil-cards",
)
DEFAULT_ZONE_ORDER = ("zone-posters", "zone-reports")


def _run(script: str, env: dict, args: tuple[str, ...] = ()) -> bool:
    path = _SCRIPTS_ROOT / script
    if not path.exists():
        print(f"  MISSING  {script}")
        return False
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(path), *args], env=env, check=False
    )
    elapsed = time.monotonic() - started
    status = "ok" if result.returncode == 0 else "FAILED"
    print(f"  {status}  ({elapsed:.1f}s)  {script}")
    return result.returncode == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG",
                                                                "au-grain-grower"))
    parser.add_argument("--farms", default=",".join(FARM_ORDER),
                        help="Comma-separated farm slugs, in run order")
    parser.add_argument("--only", default=None,
                        help="Comma-separated stage keys to run (default: the "
                             "farm stages then the zone stages)")
    parser.add_argument("--skip-farm-stages", action="store_true")
    parser.add_argument("--skip-zone-stages", action="store_true")
    parser.add_argument("--with-soil-ingest", action="store_true",
                        help="Also re-crop the farm SLGA/ASF grids per field")
    parser.add_argument("--force", action="store_true", help="Force rerun all steps")
    args = parser.parse_args(argv)

    farms = [f.strip() for f in args.farms.split(",") if f.strip()]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = wanted - set(FARM_STAGES) - set(ZONE_STAGES)
        if unknown:
            raise SystemExit(f"unknown stage(s): {sorted(unknown)}")
        farm_order = [s for s in FARM_STAGES if s in wanted]
        zone_order = [s for s in ZONE_STAGES if s in wanted]
    else:
        farm_order = list(DEFAULT_FARM_ORDER)
        zone_order = list(DEFAULT_ZONE_ORDER)
        if args.with_soil_ingest:
            farm_order.insert(0, "soil-ingest")

    base_env = dict(os.environ)
    base_env["AG_GROWER_SLUG"] = args.grower_slug
    if args.force:
        base_env["AG_FORCE"] = "1"
    if not base_env.get("DATA_PIPELINE_DATA_ROOT"):
        print("ERROR: DATA_PIPELINE_DATA_ROOT is not set")
        return 2

    failures: list[str] = []

    if not args.skip_farm_stages:
        for farm in farms:
            print(f"\n=== {farm} ===")
            env = dict(base_env, AG_FARM_SLUG=farm)
            for key in farm_order:
                script, label, _ = FARM_STAGES[key]
                print(f"\n[{label}]")
                if not _run(script, env):
                    failures.append(f"{farm}:{key}")
                    # later stages read this stage's tables, so stop this farm
                    break

    if not args.skip_zone_stages and zone_order:
        print("\n=== zones ===")
        env = dict(base_env)
        for key in zone_order:
            script, label, _ = ZONE_STAGES[key]
            print(f"\n[{label}]")
            if not _run(script, env):
                failures.append(f"zone:{key}")

    print()
    if failures:
        print("FAILED stages: " + ", ".join(failures))
        return 1
    print("AU pipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
