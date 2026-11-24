"""Image chips for the labelled sample: small pictures around one point.

Design: docs/sample_reference.md. A chip is a window of a public COG read
around the point, rendered on the server and cached as PNG, so the browser
only shows pictures (Option 1).

Two sources, both readable without a login:

    sentinel2_l2a   Earth Search, `sentinel-2-c1-l2a` -- the collection
                    fetch_lulc_data uses, so the pre-event chip is that scene
    landsat_c2_l2   Microsoft Planetary Computer, `landsat-c2-l2`, signed with
                    an anonymous token (Earth Search's copy is requester-pays)

Two views per image, each in true and false colour:

    wide    2,560 m across -- context
    close     640 m across -- the 10 m cell is 4 px wide

**Fixed stretches, not per-chip ones.** A percentile stretch makes a cloudy
chip and a clear one look alike in contrast, and makes the same water look
different from chip to chip. Reflectance is mapped linearly from a fixed range
for every chip, so a labeller compares like with like. Nearest-neighbour only:
no value is invented between pixels.

**Images with tile cloud 100% are not offered.** Nothing can be seen through
them; they are listed in `images.json` as excluded, with the reason, so the
omission is visible.
"""

from __future__ import annotations

import json
import struct
import threading
import time
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
PLANETARY = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2"

SOURCES: dict[str, dict[str, Any]] = {
    # True colour from the 8-bit `visual` (TCI) file: one small download instead
    # of three 16-bit bands. Its stretch is ESA's own fixed one, so it is still
    # the same mapping for every chip.
    "sentinel2_l2a": {"stac": EARTH_SEARCH, "collection": "sentinel-2-c1-l2a",
                      "platform": "sentinel-2", "sign": False,
                      "true": ("visual",),
                      "false": ("swir22", "nir08", "red")},
    "landsat_c2_l2": {"stac": PLANETARY, "collection": "landsat-c2-l2",
                      "platform": "landsat", "sign": True,
                      "true": ("red", "green", "blue"),
                      "false": ("swir16", "nir08", "red")},
}
VIEWS = {"wide": 2560.0, "close": 640.0}      # metres across
PX = 256
# Surface reflectance mapped to 0-255. Same for every chip, on purpose.
STRETCH = {"true": (0.0, 0.25), "false": (0.0, 0.40)}


# -- STAC --------------------------------------------------------------------
def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def _band_info(asset: dict) -> dict[str, float]:
    bands = asset.get("raster:bands") or [{}]
    b = bands[0] or {}
    return {"scale": float(b.get("scale", 1.0)), "offset": float(b.get("offset", 0.0)),
            "nodata": b.get("nodata", 0)}


def _image(source: str, item: dict, role: str) -> dict[str, Any]:
    spec = SOURCES[source]
    props = item.get("properties") or {}
    wanted = set(spec["true"]) | set(spec["false"])
    assets = {k: {"href": v["href"], **_band_info(v)}
              for k, v in (item.get("assets") or {}).items() if k in wanted}
    missing = wanted - set(assets)
    if missing:
        raise ValueError(f"{item.get('id')}: assets {sorted(missing)} missing")
    tier = props.get("landsat:collection_category")
    return {"key": f"{source}:{item['id']}", "source": source, "image_id": item["id"],
            "role": role, "datetime": props.get("datetime"),
            "platform": props.get("platform"), "cloud": props.get("eo:cloud_cover"),
            "tier": tier, "geometry": item.get("geometry"), "assets": assets}


def find_images(bbox: list[float], start_utc: str, end_utc: str,
                pre_event_ids: list[str]) -> dict[str, Any]:
    """Every in-window image over the AOI, plus the pre-event scene by id."""
    images, excluded = [], []
    for source, spec in SOURCES.items():
        body = {"collections": [spec["collection"]], "bbox": bbox, "limit": 200,
                "datetime": f"{start_utc}/{end_utc}"}
        for item in _post(f"{spec['stac']}/search", body).get("features", []):
            cloud = (item.get("properties") or {}).get("eo:cloud_cover")
            if cloud is not None and float(cloud) >= 100.0:
                excluded.append({"source": source, "image_id": item["id"],
                                 "reason": "tile cloud 100%"})
                continue
            images.append(_image(source, item, "event"))
    spec = SOURCES["sentinel2_l2a"]
    for item_id in pre_event_ids:
        item = _get(f"{spec['stac']}/collections/{spec['collection']}/items/{item_id}")
        images.append(_image("sentinel2_l2a", item, "pre_event"))
    images.sort(key=lambda i: (i["role"] != "pre_event", i["datetime"] or "", i["image_id"]))
    return {"images": images, "excluded": excluded,
            "window_utc": [start_utc, end_utc], "pre_event_ids": pre_event_ids}


# -- which images see a point -----------------------------------------------
def _in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def covers(geometry: dict | None, lon: float, lat: float) -> bool:
    if not geometry:
        return False
    polys = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
             else geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [])
    for poly in polys:
        if poly and _in_ring(lon, lat, poly[0]) and not any(
                _in_ring(lon, lat, hole) for hole in poly[1:]):
            return True
    return False


def images_for_point(images: list[dict], lon: float, lat: float) -> list[dict]:
    """Images whose footprint holds the point, one per acquisition.

    Neighbouring MGRS tiles overlap, so one pass can cover a point twice; the
    same pass is one observation, so only the first tile (by id) is kept."""
    seen, out = set(), []
    for img in images:
        if not covers(img["geometry"], lon, lat):
            continue
        when = (img["datetime"] or "")[:16]
        key = (img["role"], img["source"], img["platform"], when)
        if key in seen:
            continue
        seen.add(key)
        out.append(img)
    return out


# -- rendering ---------------------------------------------------------------
_token: dict[str, Any] = {"value": None, "until": 0.0}
_token_lock = threading.Lock()


def _signed(href: str, source: str) -> str:
    if not SOURCES[source]["sign"]:
        return href
    with _token_lock:
        if not _token["value"] or time.time() > _token["until"] - 300:
            data = _get(PC_TOKEN)
            _token["value"] = data["token"]
            expiry = data.get("msft:expiry")
            _token["until"] = (datetime.fromisoformat(expiry.replace("Z", "+00:00")).timestamp()
                               if expiry else time.time() + 1800)
        return f"{href}?{_token['value']}"


GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
            "GDAL_HTTP_MULTIRANGE": "YES",
            "GDAL_HTTP_VERSION": "2"}


def _read(href: str, lon: float, lat: float, metres: float) -> np.ndarray:
    """A (bands, PX, PX) window centred on the point; 0 where the image ends.

    The window is clipped to the image and pasted into a zero array, rather
    than read with rasterio's `boundless=True`, which builds a VRT and was
    several times slower over HTTP."""
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window, from_bounds

    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(href) as src:
            xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
            half = metres / 2.0
            win = from_bounds(xs[0] - half, ys[0] - half, xs[0] + half, ys[0] + half,
                              src.transform)
            out = np.zeros((src.count, PX, PX), dtype=src.dtypes[0])
            c0, r0 = max(0.0, win.col_off), max(0.0, win.row_off)
            c1 = min(float(src.width), win.col_off + win.width)
            r1 = min(float(src.height), win.row_off + win.height)
            if c1 <= c0 or r1 <= r0:
                return out
            sx, sy = PX / win.width, PX / win.height          # output px per source px
            oc0, or0 = int(round((c0 - win.col_off) * sx)), int(round((r0 - win.row_off) * sy))
            oc1, or1 = int(round((c1 - win.col_off) * sx)), int(round((r1 - win.row_off) * sy))
            if oc1 <= oc0 or or1 <= or0:
                return out
            out[:, or0:or1, oc0:oc1] = src.read(
                window=Window(c0, r0, c1 - c0, r1 - r0),
                out_shape=(src.count, or1 - or0, oc1 - oc0))
            return out


def _close(arr: np.ndarray) -> np.ndarray:
    """The central quarter of a wide chip, enlarged 4x by repetition."""
    k = int(VIEWS["wide"] / VIEWS["close"])
    lo = PX // 2 - PX // (2 * k)
    crop = arr[lo:lo + PX // k, lo:lo + PX // k]
    return np.repeat(np.repeat(crop, k, axis=0), k, axis=1)


def render_all(image: dict, lon: float, lat: float) -> dict[str, bytes]:
    """All four chips of one image at one point, from one read per file.

    Returns {"true_wide", "true_close", "false_wide", "false_close"}: PNG bytes.
    """
    from concurrent.futures import ThreadPoolExecutor

    source = image["source"]
    names = list(dict.fromkeys(SOURCES[source]["true"] + SOURCES[source]["false"]))
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        arrays = dict(zip(names, pool.map(
            lambda n: _read(_signed(image["assets"][n]["href"], source), lon, lat,
                            VIEWS["wide"]), names)))
    out = {}
    for colour in ("true", "false"):
        files = SOURCES[source][colour]
        if files == ("visual",):
            rgb = np.moveaxis(arrays["visual"][:3], 0, -1).astype("uint8")
        else:
            lo, hi = STRETCH[colour]
            bands = []
            for name in files:
                a = image["assets"][name]
                dn = arrays[name][0]
                refl = dn.astype("float32") * a["scale"] + a["offset"]
                scaled = np.clip((refl - lo) / (hi - lo), 0.0, 1.0) * 255.0
                scaled[dn == (a["nodata"] if a["nodata"] is not None else 0)] = 0
                bands.append(scaled.astype("uint8"))
            rgb = np.stack(bands, axis=-1)
        out[f"{colour}_wide"] = png(rgb)
        out[f"{colour}_close"] = png(_close(rgb))
    return out


def png(rgb: np.ndarray) -> bytes:
    """A minimal RGB PNG encoder (zlib only), so chips need no imaging library."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


CHIP_NAMES = ("true_wide", "true_close", "false_wide", "false_close")
_render_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def chip_path(cache: Path, image: dict, point_id: str, name: str) -> Path:
    return cache / image["key"].replace(":", "__") / f"{point_id}_{name}.png"


def ensure_chips(cache: Path, image: dict, point: dict) -> None:
    """Render the four chips of (image, point) once; concurrent callers wait."""
    key = f"{image['key']}|{point['point_id']}"
    with _locks_guard:
        lock = _render_locks.setdefault(key, threading.Lock())
    with lock:
        if all(chip_path(cache, image, point["point_id"], n).exists() for n in CHIP_NAMES):
            return
        chips = render_all(image, float(point["lon"]), float(point["lat"]))
        for name, data in chips.items():
            target = chip_path(cache, image, point["point_id"], name)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
