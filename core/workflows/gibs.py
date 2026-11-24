"""NASA GIBS flood layers (MODIS / VIIRS MCDWD, VCDWD), decoded to classes.

Shared by `scripts/fetch_gibs_flood.py` (the validation download) and the
`get_flood_map` tool (BUG-021), so the two read GIBS the same way. The method
and its caveats are in the script's docstring: palette PNG tiles from the
GIBS `250m` tile matrix, each colour mapped back to its source value through
GIBS's published colormap; a colour not in the colormap becomes 252, never a
guess. GIBS re-grids to its own EPSG:4326 lattice, so the values are the
product's and the pixel footprints are GIBS's.
"""

from __future__ import annotations

import math
import re
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

CAPS = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml"
COLORMAP = "https://gibs.earthdata.nasa.gov/colormaps/v1.3/MODIS_Flood.xml"
TILE = ("https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/{layer}/default/"
        "{date}/250m/{matrix}/{row}/{col}.png")
LAYERS = [f"{s}_Combined_Flood_{n}-Day" for s in ("MODIS", "VIIRS") for n in (1, 2, 3)]
NOT_IN_COLORMAP, NO_TILE, GIBS_NODATA = 252, 253, 254
NAMES = {0: "no_water", 1: "surface_water", 2: "recurring_flood", 3: "flood",
         255: "insufficient", GIBS_NODATA: "gibs_nodata", NO_TILE: "no_tile",
         NOT_IN_COLORMAP: "UNMAPPED"}


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def finest_matrix(caps: str) -> dict:
    """The finest TileMatrix of the `250m` set, read from the capabilities."""
    tms = caps[caps.find("<ows:Identifier>250m</ows:Identifier>"):]
    tms = tms[:tms.find("</TileMatrixSet>")]
    out = []
    for m in re.finditer(r"<TileMatrix>(.*?)</TileMatrix>", tms, flags=re.S):
        b = m.group(1)

        def g(tag: str) -> str:
            return re.search(fr"<{tag}>([^<]+)</{tag}>", b).group(1)

        a, c = (float(v) for v in g("TopLeftCorner").split())
        left, top = (c, a) if abs(a) == 90.0 and abs(c) == 180.0 else (a, c)
        out.append({"id": g("ows:Identifier"), "left": left, "top": top,
                    "tw": int(g("TileWidth")), "th": int(g("TileHeight")),
                    "mw": int(g("MatrixWidth"))})
    tm = out[-1]
    tm["deg"] = 360.0 / (tm["mw"] * tm["tw"])
    return tm


def colormap() -> dict[tuple[int, int, int], int]:
    xml = _get(COLORMAP).decode()
    rgb2val = {}
    for m in re.finditer(r"<ColorMapEntry ([^>]*)/>", xml):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        rgb = tuple(int(v) for v in a["rgb"].split(","))
        rgb2val[rgb] = GIBS_NODATA if a.get("nodata") == "true" else int(a["sourceValue"])
    return rgb2val


def layer_range(caps: str, layer: str) -> list[str]:
    i = caps.find(f"<ows:Identifier>{layer}</ows:Identifier>")
    blk = caps[caps.rfind("<Layer>", 0, i):caps.find("</Layer>", i)]
    return re.findall(r"<Value>([^<]+)</Value>", blk)


def holds(ranges: list[str], day: date) -> bool:
    """Is `day` inside one of a layer's capability time values ("a/b/P1D" or
    one date)? The ranges have gaps, so each is tested."""
    for value in ranges:
        parts = value.split("/")
        try:
            lo = date.fromisoformat(parts[0][:10])
            hi = date.fromisoformat(parts[1][:10]) if len(parts) > 1 else lo
        except ValueError:
            continue
        if lo <= day <= hi:
            return True
    return False


def fetch(layer: str, day: date, bbox: list[float], tm: dict,
          rgb2val: dict) -> tuple[np.ndarray, rasterio.Affine, list[str]]:
    w, s, e, n = bbox
    span = tm["tw"] * tm["deg"]
    c0, c1 = math.floor((w - tm["left"]) / span), math.floor((e - tm["left"]) / span)
    r0, r1 = math.floor((tm["top"] - n) / span), math.floor((tm["top"] - s) / span)
    mosaic = np.full(((r1 - r0 + 1) * tm["th"], (c1 - c0 + 1) * tm["tw"]), NO_TILE, "uint8")
    problems = []
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            url = TILE.format(layer=layer, date=day.isoformat(), matrix=tm["id"], row=r, col=c)
            try:
                raw = _get(url)
            except Exception as exc:                          # noqa: BLE001
                problems.append(f"tile {r}/{c}: {exc}")
                continue
            with rasterio.MemoryFile(raw) as mf, mf.open() as src:
                idx = src.read(1)
                try:
                    ct = src.colormap(1)
                except ValueError:
                    problems.append(f"tile {r}/{c}: not a palette PNG")
                    continue
            lut = np.full(256, NOT_IN_COLORMAP, "uint8")
            for k, (rr, gg, bb, _a) in ct.items():
                lut[k] = rgb2val.get((rr, gg, bb), NOT_IN_COLORMAP)
            mosaic[(r - r0) * tm["th"]:(r - r0 + 1) * tm["th"],
                   (c - c0) * tm["tw"]:(c - c0 + 1) * tm["tw"]] = lut[idx]
    left, top = tm["left"] + c0 * span, tm["top"] - r0 * span
    x0, x1 = int((w - left) / tm["deg"]), int(math.ceil((e - left) / tm["deg"]))
    y0, y1 = int((top - n) / tm["deg"]), int(math.ceil((top - s) / tm["deg"]))
    transform = from_origin(left + x0 * tm["deg"], top - y0 * tm["deg"], tm["deg"], tm["deg"])
    return mosaic[y0:y1, x0:x1], transform, problems


def write_day(layer: str, day: date, bbox: list[float], tm: dict, rgb2val: dict,
              target: Path, written_by: str) -> tuple[dict, list[str]]:
    """Fetch one layer for one day, write it as a tagged GeoTIFF (BUG-020), and
    return (class shares, problems)."""
    arr, transform, problems = fetch(layer, day, bbox, tm, rgb2val)
    target.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target, "w", driver="GTiff", width=arr.shape[1],
                       height=arr.shape[0], count=1, dtype="uint8",
                       crs="EPSG:4326", transform=transform, compress="deflate") as dst:
        dst.write(arr, 1)
        # A GIBS flood layer is a composite of the day's passes (UTC day), not
        # one acquisition; the tag names are core/dating.py's.
        dst.update_tags(
            DATA_SOURCE=f"NASA LANCE {layer} via NASA GIBS WMTS "
                        "(epsg4326/best), decoded with MODIS_Flood.xml",
            DATED=f"{layer}, composite of the passes on {day.isoformat()} "
                  "(UTC day; 1-3 day windows end on this day)",
            ACQUISITION_UTC=f"{day.isoformat()}T00:00:00Z/{day.isoformat()}T23:59:59Z",
            AOI_TIMEZONE="not known",
            WRITTEN_BY_TOOL=f"{written_by}, retrieved "
                            + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))
    vals, counts = np.unique(arr, return_counts=True)
    share = {NAMES.get(int(v), str(v)): round(int(k) / arr.size, 4)
             for v, k in zip(vals, counts)}
    return share, problems
