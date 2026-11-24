"""Recover MODIS / VIIRS NRT flood classes from the NASA GIBS imagery archive.

    python scripts/fetch_gibs_flood.py --bbox 79.78 7.88 80.08 8.33 \
        --first 2025-11-21 --last 2025-12-03 \
        --out experiments/puttalam-2025/validation/gibs

**Why GIBS.** The LANCE NRT flood products (MCDWD, VCDWD) are kept for about a
week, and their user guide (Rev E, 2025-04-22) states that "at present there
is no publicly accessible archive of older products". GIBS keeps the rendered
flood layers for years (MODIS from 2023-07-26, VIIRS from 2025-06-24, read
from the capabilities at run time).

**How classes are recovered.** A GIBS tile is a palette PNG; GIBS publishes the
palette as a colormap (`colormaps/v1.3/MODIS_Flood.xml`), colour -> source
value. Each pixel's palette entry is mapped back to its value, which is exact
for every colour in the colormap. A colour that is not in it becomes 252 and is
reported -- never guessed.

    0 no water | 1 surface water (matching reference water) | 2 recurring flood
    (user guide: "not yet populated") | 3 flood | 255 insufficient data
    (including the HAND mask) | 254 GIBS no-data | 253 tile not fetched |
    252 colour not in the colormap

**Caveat carried with every output.** GIBS re-grids to its own EPSG:4326 tile
matrix (`250m` set, finest level, about 0.0021973 deg), so the geometry is
GIBS's, not the product's native 0.0020833 deg grid. The values are the
product's; the pixel footprints are not.

Composites (user guide): 1-Day = the product date; 2-Day = that day and the
previous one; 3-Day = that day and the two before. Writes one GeoTIFF per layer
and date, and `summary.json` with the class counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The GIBS reading lives in core/workflows/gibs.py, shared with get_flood_map.
from core.workflows.gibs import (CAPS, LAYERS, NAMES, _get, colormap,  # noqa: E402
                                 finest_matrix, layer_range, write_day)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layers", nargs="*", default=LAYERS)
    args = ap.parse_args()

    caps = _get(CAPS).decode("utf-8", errors="ignore")
    tm, rgb2val = finest_matrix(caps), colormap()
    out = Path(args.out)
    summary = {"bbox": args.bbox, "deg_per_px": tm["deg"], "tile_matrix": f"250m/{tm['id']}",
               "source": "NASA GIBS WMTS epsg4326/best; classes decoded with "
                         "colormaps/v1.3/MODIS_Flood.xml",
               "class_names": NAMES, "layer_ranges": {}, "layers": {}}
    day, last = date.fromisoformat(args.first), date.fromisoformat(args.last)
    for layer in args.layers:
        summary["layer_ranges"][layer] = layer_range(caps, layer)
    while day <= last:
        for layer in args.layers:
            target = out / layer / f"{day.isoformat()}.tif"
            share, problems = write_day(layer, day, args.bbox, tm, rgb2val, target,
                                        "scripts/fetch_gibs_flood.py")
            summary["layers"].setdefault(layer, {})[day.isoformat()] = {
                "share": share, "problems": problems}
            print(f"{layer:28} {day}  " + "  ".join(f"{k} {v:.1%}" for k, v in share.items())
                  + (f"  [{'; '.join(problems)}]" if problems else ""))
        day += timedelta(days=1)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"summary: {out / 'summary.json'}")


if __name__ == "__main__":
    main()
