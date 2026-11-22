#!/usr/bin/env python3
"""Stage one farm's daily SILO weather as a pipeline table.

The AU branch has one climate point per farm, so weather is a farm input, not a
field input. This stage normalises the cached SILO response
(``<runtime>/au-paddocks/weather/raw/<Farm>_<lat>_<lon>.csv``) to
``farms/<farm>/derived/tables/weather_daily.csv`` with the columns every later
stage reads: date, tmax_c, tmin_c, tavg_c, rain_mm, gdd_base_0.

Nothing is downloaded here. Refreshing the cache belongs to
``eda/eda-field-comparison/src/fetch_weather.py``, which identifies itself to
SILO by email and is the single place that talks to the API.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_SCRIPTS_ROOT, _SCRIPTS_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.au_common import FARM_NAME_BY_SLUG  # noqa: E402
from lib.au_weather import cache_path, stage_daily_weather, weather_cache_dir  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG", ""))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG", ""))
    args = parser.parse_args(argv)
    if not args.grower_slug or not args.farm_slug:
        raise SystemExit("AG_GROWER_SLUG and AG_FARM_SLUG are required")

    name = FARM_NAME_BY_SLUG.get(args.farm_slug, args.farm_slug)
    source = cache_path(args.grower_slug, args.farm_slug, name)
    if source is None:
        raise SystemExit(
            f"no SILO cache for {name} under {weather_cache_dir()} "
            "(refresh it with eda/eda-field-comparison/src/fetch_weather.py)"
        )
    written = stage_daily_weather(args.grower_slug, args.farm_slug, name)
    print(f"{written}  (from {source.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
