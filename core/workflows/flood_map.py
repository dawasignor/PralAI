"""get_flood_map: the best-ranked NRT product's flood map for any AOI (BUG-021).

`get_flood_extent` reads its product list, window and permanent-water raster
from an experiment's VALIDATION config, so only an experiment with an accuracy
set-up (Kelani) could ever map a flood: every other AOI skipped the extent and,
with it, depth, roads, Modules 9-10 and the report maps. This tool needs no
validation config. It builds the extent config from the call itself:

    aoi.bbox            the user's AOI
    temporal.window     the event window, read as UTC days (cumulative union)
    products            the shared catalogue below (PRODUCTS)
    domain              sea excluded (Copernicus WBM); permanent water from
                        fetch_permanent_water's water_class.tif (class 3), passed
                        in or fetched here

and runs the same extent computation (`impact.extent_from_config`), so a flood
km2 means the same thing on both paths. Accuracy validation stays with
`accuracy_metrics` and its config; the two are not coupled.

It then draws the **flood overlay**: the extent over the OpenStreetMap tiles,
with the AOI box, major places and the mapped buildings of interest -- the map
the public report and the executive summary show (user decision 2026-10-26).
Flooded pixels are blue; land the product did not observe in the window is
grey, because "not observed" is never "dry".

**Where each product comes from.** GFM from the EODC STAC API; MODIS / VIIRS
(MCDWD, VCDWD) from the NASA GIBS imagery archive (the LANCE NRT files
themselves are kept about a week); OPERA DSWx-HLS and DSWx-S1 from NASA
PO.DAAC, searched in CMR and downloaded with the Earthdata login
(`EARTHDATA_TOKEN` as a Bearer header, core/workflows/opera.py; BUG-029). A
login that is missing or refused makes OPERA NOT KNOWN with that reason, never
a substitute product.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from . import tool

# The shared product catalogue: how each NRT product is read, for any AOI. The
# encodings are the ones the validation config uses (user decisions 2026-10-19
# and 2026-10-21); keep the two in step when either changes.
PRODUCTS: dict[str, dict[str, Any]] = {
    "gfm": {
        "label": "GFM (Sentinel-1)", "sensor": "SAR", "platforms": ["sentinel-1"],
        "source": "stac", "stac": "https://stac.eodc.eu/api/v1", "collection": "GFM",
        "provider": "Copernicus GFM via the EODC STAC API",
        "flood_asset": "ensemble_flood_extent",
        "encoding": {"flood": [1], "nonflood": [0], "nodata": [255]},
        "nodata_masks": [{"asset": "exclusion_mask", "values": [1]}],
        # One item without its exclusion mask is left out and reported, not
        # the whole product (user decision 2026-10-26, BUG-022). Two or more
        # still make GFM NOT KNOWN. The validation config keeps the strict 0.
        "max_items_missing_masks": 1,
    },
    "mcdwd": {
        "label": "MODIS MCDWD (2-Day)", "sensor": "Optical", "platforms": ["modis"],
        "composite": "2-Day", "source": "gibs", "layer": "MODIS_Combined_Flood_2-Day",
        "provider": "NASA LANCE via the NASA GIBS imagery archive",
        "encoding": {"flood": [3], "nonflood": [0, 1], "nodata": [2, 252, 253, 254, 255]},
    },
    "vcdwd": {
        "label": "VIIRS VCDWD (2-Day)", "sensor": "Optical", "platforms": ["viirs"],
        "composite": "2-Day", "source": "gibs", "layer": "VIIRS_Combined_Flood_2-Day",
        "provider": "NASA LANCE via the NASA GIBS imagery archive",
        "encoding": {"flood": [3], "nonflood": [0, 1], "nodata": [2, 252, 253, 254, 255]},
    },
    "dswx_hls": {
        "label": "OPERA DSWx-HLS", "sensor": "Optical", "source": "opera",
        "provider": "NASA PO.DAAC via NASA CMR (Earthdata login)",
        "encoding": {"flood": [1, 2], "nonflood": [0], "nodata": [252, 253, 254, 255]},
    },
    "dswx_s1": {
        "label": "OPERA DSWx-S1", "sensor": "SAR", "source": "opera",
        "provider": "NASA PO.DAAC via NASA CMR (Earthdata login)",
        "encoding": {"flood": [1, 3], "nonflood": [0], "nodata": [250, 251, 255]},
    },
}
# Ranking labels -> catalogue ids (rank_flood_products names products by label).
BY_LABEL = {"GFM (Sentinel-1)": "gfm", "MODIS MCDWD": "mcdwd", "VIIRS VCDWD": "vcdwd",
            "OPERA DSWx-HLS": "dswx_hls", "OPERA DSWx-S1": "dswx_s1"}

# Overlay colours, RGBA 0-255 (user decision 2026-10-26): flood bright red at
# 50% opacity; land not observed a dark grey strong enough to read as "no
# data" over the tiles, not as a tint.
#
# Changed 2026-11-19 (user, "the contrast"): flooded land DEEP RED and nearly
# opaque; land not observed or excluded (cloud, no pass) a LIGHT grey, so it
# reads as "unknown" and the flood stands out against it.
FLOOD_RGBA = (165, 15, 21, 215)
UNOBSERVED_RGBA = (200, 200, 200, 150)

# The call-out every flood map carries (user, 2026-11-19): unobserved land is
# unknown, never safe.
NOT_OBSERVED_WARNING = "! Not observed does not mean not affected"

# A flood patch smaller than this on the figure (display pixels) cannot be
# seen at the AOI's scale, so it is marked with a red pin and called a minor
# flood in the legend (user decision 2026-10-27, BUG-063). Pins closer than
# PIN_CELL_PX share one pin, so a scatter of specks is not a forest of pins.
MIN_VISIBLE_PX = 36
PIN_CELL_PX = 60
MINOR_FLOOD_LABEL = "minor flood (too small to see at this scale)"


def _gibs_files(spec: dict[str, Any], start: date, end: date, bbox: list[float],
                folder: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The 2-Day GIBS files whose BOTH days fall inside the window (the
    validation rule, user decision 2026-10-21), fetched into `folder`."""
    from . import gibs

    caps = gibs._get(gibs.CAPS).decode("utf-8", errors="ignore")
    tm, rgb2val = gibs.finest_matrix(caps), gibs.colormap()
    files, problems = [], []
    day = start + timedelta(days=1)          # a 2-Day file dated D covers D-1 and D
    while day <= end:
        target = folder / spec["layer"] / f"{day.isoformat()}.tif"
        _, tile_problems = gibs.write_day(spec["layer"], day, bbox, tm, rgb2val, target,
                                          "get_flood_map")
        problems += [f"{day}: {p}" for p in tile_problems]
        files.append({"path": str(target), "datetime": f"{day.isoformat()}T12:00:00Z",
                      "period": [(day - timedelta(days=1)).isoformat(), day.isoformat()]})
        day += timedelta(days=1)
    return files, problems


def _opera_files(key: str, start: date, end: date, bbox: list[float],
                 folder: Path) -> tuple[list[dict[str, Any]], list[str], str]:
    """The OPERA B01_WTR files acquired in the window's UTC days, downloaded
    into `folder` with the Earthdata login. Returns (files, notes, reason):
    `reason` is set, and `files` empty, when the product cannot be read."""
    from . import opera

    try:
        got = opera.fetch(key, bbox, f"{start.isoformat()}T00:00:00Z",
                          f"{end.isoformat()}T23:59:59Z", folder)
    except opera.LoginRefused as exc:
        return [], [], str(exc)
    except Exception as exc:                                   # noqa: BLE001
        return [], [], f"the OPERA search or download failed ({type(exc).__name__}: {exc})"[:300]
    notes = []
    if got["superseded"]:
        notes.append(f"{len(got['superseded'])} reprocessed OPERA granule(s) superseded by a "
                     "newer production and not used")
    if got["no_wtr_link"]:
        notes.append(f"{len(got['no_wtr_link'])} OPERA granule(s) with no water layer link, "
                     "not used")
    files = [{"path": f["path"], "datetime": f["datetime"]} for f in got["files"]]
    return files, notes, ("" if files else "no OPERA granule over the AOI in the window")


def _overlay(extent_tif: str, bbox: list[float], frame: list[float],
             size: tuple[int, int]) -> np.ndarray:
    """The extent as an RGBA layer on the OSM image's Web Mercator pixels."""
    out = shown_codes(extent_tif, bbox, frame, size)
    rgba = np.zeros(out.shape + (4,), dtype="uint8")
    rgba[out == 1] = FLOOD_RGBA
    rgba[out == 2] = UNOBSERVED_RGBA
    return rgba


def shown_codes(extent_tif: str, bbox: list[float], frame: list[float],
                size: tuple[int, int]) -> np.ndarray:
    """The extent on the OSM image's Web Mercator pixels: 1 flooded, 2 land
    not observed, 0 nothing to draw. Shared by the flood overlay and the
    hazard-and-exposure map, so both draw the same pixels."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    from .osm_context import _merc
    from .validation.domain import _aoi_mask, _sea

    with rasterio.open(extent_tif) as src:
        codes = src.read(1)
        grid = {"crs": src.crs, "transform": src.transform, "height": src.height,
                "width": src.width, "resolution": abs(src.transform.a)}
    # 0 dry, 1 flood, 2 permanent water, 255 not observed OR outside the land.
    # The sea and the outside of the AOI are taken out of "not observed": the
    # tiles already show the sea, and grey there would read as missing data.
    land = _aoi_mask(bbox, grid) & ~_sea(bbox, grid, ["ocean"])
    shown = np.full(codes.shape, 0, dtype="uint8")          # 0 = draw nothing
    shown[land & (codes == 1)] = 1
    shown[land & (codes == 255)] = 2
    west, north = _merc(frame[0], frame[3])
    east, south = _merc(frame[2], frame[1])
    w, h = size
    out = np.zeros((h, w), dtype="uint8")
    reproject(shown, out, src_transform=grid["transform"], src_crs=grid["crs"],
              dst_transform=from_bounds(west, south, east, north, w, h),
              dst_crs="EPSG:3857", resampling=Resampling.nearest,
              src_nodata=None, dst_nodata=0)
    return out


def _minor_floods(extent_tif: str, bbox: list[float], frame: list[float],
                  size: tuple[int, int]) -> list[dict[str, Any]]:
    """Flood patches too small to see on the figure: one pin per PIN_CELL_PX
    cell, at the area-weighted centre of the patches in it."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject, transform
    from scipy import ndimage

    from .osm_context import _merc, _px, choose_zoom
    from .validation.domain import _aoi_mask, _sea

    with rasterio.open(extent_tif) as src:
        codes = src.read(1)
        grid = {"crs": src.crs, "transform": src.transform, "height": src.height,
                "width": src.width, "resolution": abs(src.transform.a)}
    land = _aoi_mask(bbox, grid) & ~_sea(bbox, grid, ["ocean"])
    labels, n = ndimage.label(land & (codes == 1), structure=np.ones((3, 3), int))
    if not n:
        return []
    west, north = _merc(frame[0], frame[3])
    east, south = _merc(frame[2], frame[1])
    w, h = size
    shown = np.zeros((h, w), dtype="int32")
    reproject(labels.astype("int32"), shown, src_transform=grid["transform"],
              src_crs=grid["crs"], dst_transform=from_bounds(west, south, east, north, w, h),
              dst_crs="EPSG:3857", resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    on_figure = np.bincount(shown.ravel(), minlength=n + 1)
    minor = [i for i in range(1, n + 1) if on_figure[i] < MIN_VISIBLE_PX]
    if not minor:
        return []
    sizes = ndimage.sum(np.ones_like(labels), labels, minor)
    rows_cols = ndimage.center_of_mass(np.ones_like(labels), labels, minor)
    xs, ys = zip(*[grid["transform"] * (c + 0.5, r + 0.5) for r, c in rows_cols])
    lons, lats = transform(grid["crs"], "EPSG:4326", list(xs), list(ys))
    cells: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    z = choose_zoom(frame)                    # the figure's zoom, as _draw_overlay picks it
    ox, oy = _px(frame[0], frame[3], z)
    for lon, lat, n_px in zip(lons, lats, sizes):
        x, y = _px(lon, lat, z)
        cells.setdefault((int((x - ox) // PIN_CELL_PX), int((y - oy) // PIN_CELL_PX)),
                         []).append((lon, lat, float(n_px)))
    pins = []
    for members in cells.values():
        total = sum(m[2] for m in members)
        pins.append({"lon": sum(m[0] * m[2] for m in members) / total,
                     "lat": sum(m[1] * m[2] for m in members) / total,
                     "patches": len(members)})
    return pins


def _stored_context(path: str, bbox: list[float]) -> dict[str, Any] | None:
    """The buildings of interest and major places from a stored where_am_i
    result for the SAME box, so the overlay does not ask OpenStreetMap again
    (user decision 2026-11-12: no redundant fetches). None when there is no
    such record, or it is for another box."""
    import json

    if not path or not Path(path).is_file():
        return None
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    p = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
    held = ((p.get("aoi") or {}).get("bbox")) or (rec.get("params") or {}).get("bbox") or []
    if [round(float(v), 5) for v in held] != [round(float(v), 5) for v in bbox]:
        return None
    cats = (p.get("facilities") or {}).get("categories")
    if not isinstance(cats, dict):
        return None
    return {"facilities": cats, "missing": (p.get("counts") or {}).get("missing") or [],
            "major_places": (p.get("admin") or {}).get("major_places") or []}


def _draw_overlay(payload: dict[str, Any], bbox: list[float], folder: Path,
                  aoi_fraction: float, osm_context: str = "") -> dict[str, Any]:
    """The flood overlay figure. Returns the payload's `map` block."""
    from .. import dating, places
    from . import osm_context as O

    stored = _stored_context(osm_context, bbox)
    frame = O.map_extent(bbox, aoi_fraction)
    z = O.choose_zoom(frame)
    img, origin = O._stitch(frame, z, folder / "tiles", places.USER_AGENT)
    rgba = _overlay(payload["raster"]["path"], bbox, frame, img.size)
    try:
        pins = _minor_floods(payload["raster"]["path"], bbox, frame, img.size)
    except Exception as exc:                                   # noqa: BLE001
        pins = []
        notes_pins = f"minor floods not marked: {type(exc).__name__}"
    else:
        notes_pins = ""

    notes = []
    if stored is not None:
        notes.append("buildings and places from the stored area overview (not fetched again)")
    try:
        located = ({"facilities": stored["facilities"], "missing": stored["missing"]}
                   if stored is not None else O.counts(bbox))
        markers = [{"category": key, "lon": item["lon"], "lat": item["lat"]}
                   for key, cat in (located.get("facilities") or {}).items()
                   for item in cat["items"]]
        if located.get("missing"):
            notes.append(f"buildings not located: {', '.join(located['missing'])}")
    except Exception as exc:                                   # noqa: BLE001
        markers, notes = [], [f"buildings of interest not drawn: {type(exc).__name__}"]
    try:
        major = (stored["major_places"] if stored is not None
                 else O.admin_and_places(bbox).get("major_places", []))
        labels = [{"name": p["name"], "kind": p["type"], "lon": p["lon"], "lat": p["lat"]}
                  for p in major]
    except Exception as exc:                                   # noqa: BLE001
        labels = []
        notes.append(f"place names not drawn: {type(exc).__name__}")

    label = payload["label"]
    window = " to ".join(payload.get("window") or [])
    if payload["status"] == "flood_mapped":
        title = (f"{label}: {payload['flood_km2']:g} km2 flooded, "
                 f"{payload['coverage']:.0%} of the land observed  ({window})")
    else:
        title = (f"{label}: no flood mapped, {payload['coverage']:.0%} of the land "
                 f"observed  ({window})")
    zone = dating.zone_at(bbox)
    dated = "; ".join(dating.lines("get_flood_map", payload, zone)) + \
        f"; tiles retrieved {dating.stamp(payload['retrieved_at'], zone)}"
    # One image per product, so a second product never overwrites the first.
    png = folder / f"flood_overlay_{payload.get('product') or 'flood'}.png"
    if notes_pins:
        notes.append(notes_pins)
    O.draw(img, origin, z, bbox, labels, title, png, markers=markers,
           dated=dated, overlay=rgba,
           overlay_legend=[(FLOOD_RGBA, f"flooded ({label})"),
                           (UNOBSERVED_RGBA, "land not observed in the window")],
           overlay_title="Flood extent", pins=pins, pin_label=MINOR_FLOOD_LABEL)
    return {"ok": True, "png": str(png), "zoom": z, "frame": [round(v, 5) for v in frame],
            "markers": len(markers), "labels": len(labels), "notes": notes,
            "minor_flood_pins": len(pins),
            "minor_flood_patches": sum(p["patches"] for p in pins),
            "attribution": O.ATTRIBUTION}


@tool(
    "get_flood_map",
    "The flood map of one NRT flood product for ANY AOI, with no validation "
    "config: the product (the best-ranked one from rank_flood_products by "
    "default; ANY other when the user asks for it -- gfm, mcdwd, vcdwd, dswx_hls, "
    "dswx_s1; OPERA is downloaded with the Earthdata "
    "login and is NOT KNOWN when that login is missing or refused) is "
    "read for the user's bbox and window, every pass unioned; flood area over land "
    "with the sea and JRC permanent water excluded, observed area and coverage, and "
    "an extent raster for depth and roads. Draws the 'flood overlay' figure: the "
    "extent on OpenStreetMap with the AOI box, places and buildings of interest. "
    "Runs once per product: asking again for a product already mapped for this "
    "AOI and window returns the kept map (refresh=true to fetch again).",
    label="Flood map and overlay",
    source="GFM (EODC STAC) / MODIS, VIIRS (NASA GIBS) / OPERA (NASA PO.DAAC) + OpenStreetMap",
    order=69,
    data_source_uncertainty=(
        "What: each product has blind spots -- GFM's exclusion mask (dense "
        "vegetation, urban), wind roughening the water for radar (GFM, OPERA "
        "DSWx-S1), cloud for optical (OPERA DSWx-HLS, MODIS, VIIRS), 250-375 m "
        "pixels for MODIS and VIIRS read from GIBS image colours on GIBS's own "
        "grid, and OPERA NOT KNOWN without the Earthdata login. Affects: flood km2, "
        "and with it depth, roads and the report maps; not observed can be a large "
        "share of the AOI. Reduce: compare several products, check against a "
        "reference map, and report observed coverage beside the flood area."),
    method_uncertainty=(
        "What: every pass in the window is combined, so the result is the largest "
        "extent seen, not a snapshot; unobserved land stays unknown, never dry, and "
        "the sea and permanent water are excluded. Affects: the flood area is a "
        "maximum across the window, not the situation on any one day. Reduce: "
        "report per-pass extents and their dates beside the combined one."),
)
def get_flood_map(product: str, bbox: list[float], start: str, end: str,
                  output_dir: str = "outputs", event_id: str = "",
                  permanent_water_raster: str = "", figure_dir: str = "",
                  aoi_fraction: float = 0.75, write_figure: bool = True,
                  osm_context: str = "", refresh: bool = False) -> dict[str, Any]:
    """**Run once per product** (user decision 2026-11-12). The best-ranked
    product is mapped by default; any other is mapped on demand, by asking for
    it. Each product's result is kept beside its raster
    (`<output_dir>/<event_id>/flood_map_<id>.json`) and a repeat call for the
    same product, AOI and window returns it -- no second download, no second
    overlay -- unless `refresh`. Later steps (population, the hazard map,
    Modules 9-10) read the same raster.

    Args:
        product: catalogue id (gfm, mcdwd, vcdwd, dswx_hls, dswx_s1) or the
            ranking's label ("GFM (Sentinel-1)").
        bbox: [west, south, east, north], WGS84.
        start, end: the event window, ISO dates, read as UTC days.
        output_dir, event_id: the extent raster goes in <output_dir>/<event_id>/.
        permanent_water_raster: fetch_permanent_water's water_class.tif; fetched
            here when empty, so every caller excludes the same water.
        figure_dir: where flood_overlay.png goes (default: beside the raster).
        aoi_fraction: share of the overlay's frame the AOI box fills.
        write_figure: false to skip the overlay.
        osm_context: a stored where_am_i result (its JSON file) for this AOI;
            the overlay then draws its buildings and places from it instead of
            asking OpenStreetMap again.
        refresh: true to fetch and draw again although this product's map for
            this AOI and window is already kept.
    """
    from datetime import datetime, timezone

    from .. import dating
    from .impact import extent_from_config

    key = BY_LABEL.get(product, product)
    if key not in PRODUCTS:
        raise ValueError(f"product {product!r} is not in the catalogue; known: "
                         f"{sorted(PRODUCTS)}")
    if len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    bbox = [float(v) for v in bbox]
    first, last = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    if last < first:
        raise ValueError(f"window ends before it starts: {start} to {end}")
    folder = Path(output_dir) / (event_id or "flood_map")
    folder.mkdir(parents=True, exist_ok=True)

    # Run once: this product's map for this AOI and window, if already kept.
    kept = folder / f"flood_map_{key}.json"
    from .. import cache_policy

    # With the cache off (an RQ5 run), only this run's own map is reused: an
    # uncached run never takes another run's download.
    request = {"product": key, "bbox": [round(v, 6) for v in bbox],
               "window": [first.isoformat(), last.isoformat()],
               "run": cache_policy.run_id() if cache_policy.is_off() else ""}
    if kept.is_file() and not refresh:
        import json

        try:
            held = json.loads(kept.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            held = {}
        raster_ok = Path((held.get("raster") or {}).get("path", "")).is_file()
        figure_ok = (not write_figure or not (held.get("raster") or {}).get("path")
                     or Path((held.get("figure") or {}).get("path", "")).is_file())
        if held.get("request") == request and raster_ok and figure_ok:
            held["reused"] = {"from": str(kept), "note": "this product's map for this AOI and "
                              "window was already made; reused, nothing downloaded or drawn"}
            return held

    spec = dict(PRODUCTS[key])
    notes: list[str] = []

    if spec.get("source") == "gibs":
        files, problems = _gibs_files(spec, first, last, bbox, folder / "gibs")
        spec = {**spec, "source": "files", "files": files}
        if problems:
            notes.append(f"{len(problems)} GIBS tile problems; first: {problems[0]}")
        if not files:
            spec = {**spec, "source": None, "not_evaluated_reason":
                    "the window is shorter than one 2-Day composite inside it"}

    if spec.get("source") == "opera":
        files, opera_notes, reason = _opera_files(key, first, last, bbox, folder / "opera")
        notes += opera_notes
        spec = ({**spec, "source": "files", "files": files} if files else
                {**spec, "source": None, "not_evaluated_reason": reason})

    water = permanent_water_raster
    if not water:
        from .water import fetch_permanent_water

        jrc = fetch_permanent_water(bbox, output_dir=output_dir, event_id=event_id or "flood_map")
        dating.tag_rasters("permanent_water", jrc, dating.zone_at(bbox))
        water = ((jrc.get("water") or {}).get("raster") or {}).get("path", "")
        notes.append("permanent water fetched by this tool (fetch_permanent_water)")

    cfg = {"aoi": {"bbox": bbox},
           "temporal": {"mode": "cumulative", "window": [first.isoformat(), last.isoformat()],
                        "tolerance_days": 1},
           "domain": {"exclude_wbm_classes": ["ocean"],
                      "permanent_water": {"raster": water, "classes": [3]}},
           "products": {key: spec}, "_dir": str(folder)}
    payload = extent_from_config(cfg, key, config_ref="built from the run (no validation "
                                 "config): user AOI, event window, shared catalogue",
                                 output_dir=output_dir, event_id=event_id, bbox=bbox)
    payload["retrieved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["config"] = {"built_from": "the call (BUG-021)", "window_utc_days":
                         [first.isoformat(), last.isoformat()], "permanent_water": water,
                         "catalogue_id": key}
    if notes:
        payload["notes"] = notes

    if write_figure and (payload.get("raster") or {}).get("path"):
        fig_folder = Path(figure_dir) / (event_id or "flood_map") if figure_dir else folder
        try:
            payload["map"] = _draw_overlay(payload, bbox, fig_folder, float(aoi_fraction),
                                           osm_context=osm_context)
            # `figure` is what core/figure_refs.register_run looks for; named
            # per product so the console can show any of them.
            payload["figure"] = {
                "name": f"flood_overlay_{key}", "path": payload["map"]["png"],
                "caption": f"{payload['label']} flood extent (red) over OpenStreetMap, "
                           "with the AOI box, major places and buildings of interest; "
                           "dark grey is land the product did not observe."}
        except Exception as exc:                               # noqa: BLE001
            payload["map"] = {"ok": False, "problem": f"{type(exc).__name__}: {exc}"}
    # Kept beside the raster for the next call (see the docstring).
    import json

    payload["request"] = request
    try:
        kept.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except OSError:
        pass                         # a result that cannot be kept is still returned
    return payload
