"""Step 3: each product on its own native grid, composited over the window.

    single_date  observations within `temporal.tolerance_days` of `temporal.date`
    cumulative   every observation in `temporal.window`, unioned

**The union rule.** A pixel is flood if any observation says flood; non-flood if
at least one observation saw it and none said flood; no-data if none saw it.
That is how a cumulative reference (the union of several passes) is built, so a
cumulative product is built the same way or the two are not comparable.

**Encoding is harmonised to 1/0/255 from the config, never guessed.** Each
product names which of its values mean flood, non-flood and no-data, and which
companion layers turn a pixel into no-data. GFM's Exclusion Mask is one: the
product's own statement of where SAR water mapping does not work, so a pixel
under it is an *absent* measurement -- no-data, lowering coverage -- and never
a non-flood claim the product did not make.

Two sources:

    stac   search a STAC API and read the AOI window of each item's assets
    files  local rasters listed in the config (for data behind a login)

A product with no source, or none in the window, raises `NotEvaluated` with the
reason, and its row in the table says so.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from .metrics import FLOOD, NODATA, NONFLOOD


class NotEvaluated(Exception):
    """A product that cannot be compared, with the reason as its message."""


def _window(cfg: dict[str, Any]) -> tuple[datetime, datetime]:
    temporal = cfg["temporal"]
    if temporal["mode"] == "cumulative":
        first, last = (date.fromisoformat(str(d)) for d in temporal["window"])
    else:
        centre = date.fromisoformat(str(temporal["date"]))
        tolerance = int(temporal.get("tolerance_days", 1))
        first, last = centre - timedelta(days=tolerance), centre + timedelta(days=tolerance)
    return (datetime(first.year, first.month, first.day, tzinfo=timezone.utc),
            datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=timezone.utc))


def _harmonise(values: np.ndarray, encoding: dict[str, list[int]]) -> np.ndarray:
    out = np.full(values.shape, NODATA, dtype="uint8")
    out[np.isin(values, encoding["nonflood"])] = NONFLOOD
    out[np.isin(values, encoding["flood"])] = FLOOD
    return out


class _Grid:
    """The product's native lattice, cut to the AOI. Fixed by the first file;
    every later file must sit on the same lattice, or it is refused -- a
    resampled product is no longer on its native grid."""

    def __init__(self, src, bbox: list[float]):
        import math

        from rasterio.transform import Affine
        from rasterio.warp import transform_bounds

        self.crs = src.crs
        a = src.transform
        if a.b or a.d:
            raise NotEvaluated("product raster is rotated; not supported")
        self.res = (a.a, -a.e)
        west, south, east, north = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
        col0 = math.floor((west - a.c) / a.a)
        row0 = math.floor((a.f - north) / -a.e)
        col1 = math.ceil((east - a.c) / a.a)
        row1 = math.ceil((a.f - south) / -a.e)
        self.transform = Affine(a.a, 0, a.c + col0 * a.a, 0, a.e, a.f + row0 * a.e)
        self.shape = (row1 - row0, col1 - col0)

    def window_in(self, src):
        from rasterio.windows import Window

        a = src.transform
        if src.crs != self.crs or (a.a, -a.e) != self.res:
            raise NotEvaluated(f"{src.name}: CRS or resolution differs from the first "
                               "file; mixing grids is not a native-grid comparison")
        col = (self.transform.c - a.c) / a.a
        row = (self.transform.f - a.f) / a.e
        if abs(col - round(col)) > 1e-6 or abs(row - round(row)) > 1e-6:
            raise NotEvaluated(f"{src.name}: not on the same pixel lattice as the first file")
        return Window(round(col), round(row), self.shape[1], self.shape[0])


def _stac_items(spec: dict[str, Any], bbox: list[float],
                window: tuple[datetime, datetime]) -> list[dict[str, Any]]:
    from ..availability import stac_search

    body = {"collections": [spec["collection"]], "bbox": bbox, "limit": 250,
            "datetime": f"{window[0]:%Y-%m-%dT%H:%M:%SZ}/{window[1]:%Y-%m-%dT%H:%M:%SZ}"}
    items = stac_search(spec["stac"], body)
    return sorted(items, key=lambda f: (f.get("properties") or {}).get("datetime", ""))


def composite(cfg: dict[str, Any], key: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The product's composite on its native grid, 1/0/255, and what went in."""
    import rasterio

    from .config import resolve

    source = spec.get("source")
    if not source:
        raise NotEvaluated(spec.get("not_evaluated_reason")
                           or "no data source configured for this product")
    encoding = spec.get("encoding") or {}
    if not all(k in encoding for k in ("flood", "nonflood", "nodata")):
        raise NotEvaluated("products.%s.encoding must give flood, nonflood and nodata "
                           "values; they are not guessed" % key)
    bbox = cfg["aoi"]["bbox"]
    window = _window(cfg)

    # Each observation: (label, flood-layer href, [(mask href, values)])
    observations: list[tuple[str, str, list[tuple[str, list[int]]]]] = []
    # An item without its configured no-data mask cannot be read honestly. By
    # default (and always for validation) it makes the product NOT KNOWN.
    # `max_items_missing_masks` lets a caller leave out at most that many such
    # items instead -- get_flood_map allows one (user decision 2026-10-26,
    # BUG-022) -- and every one left out is returned in `skipped_items` for the
    # uncertainty block. Its pixels count only if another pass observed them.
    allowed = int(spec.get("max_items_missing_masks", 0))
    skipped: list[dict[str, Any]] = []
    if source == "stac":
        for item in _stac_items(spec, bbox, window):
            assets = item.get("assets") or {}
            if spec["flood_asset"] not in assets:
                continue
            masks = [(assets[m["asset"]]["href"], m["values"])
                     for m in spec.get("nodata_masks") or [] if m["asset"] in assets]
            if len(masks) != len(spec.get("nodata_masks") or []):
                missing = [m["asset"] for m in spec.get("nodata_masks") or []
                           if m["asset"] not in assets]
                skipped.append({"item": item.get("id"), "missing": missing,
                                "datetime": (item.get("properties") or {}).get("datetime")})
                if len(skipped) > allowed:
                    raise NotEvaluated(
                        f"{item.get('id')}: a configured no-data mask asset is missing; the "
                        "item cannot be read honestly" + (
                            f" ({len(skipped)} items lack a mask, {allowed} may be left out)"
                            if allowed else ""))
                continue
            observations.append(((item.get("properties") or {}).get("datetime", item.get("id", "")),
                                 assets[spec["flood_asset"]]["href"], masks))
    elif source == "files":
        # Local files are filtered by the same window as STAC items. A file
        # without a datetime cannot be placed in time, and is refused rather
        # than assumed to belong to the event.
        for entry in spec.get("files") or []:
            path = resolve(cfg, entry["path"])
            if not entry.get("datetime"):
                raise NotEvaluated(f"{path.name}: no datetime in the config; a file "
                                   "cannot be placed in the temporal window without one")
            when = datetime.fromisoformat(str(entry["datetime"]).replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if not window[0] <= when <= window[1]:
                continue
            masks = [(str(resolve(cfg, m["path"])), m["values"]) for m in entry.get("nodata_masks") or []]
            observations.append((str(entry["datetime"]), str(path), masks))
    else:
        raise NotEvaluated(f"unknown source {source!r}")
    if not observations:
        raise NotEvaluated(spec.get("not_evaluated_reason")
                           or "no observation of this product in the temporal window")

    grid = None
    union = None
    used = []
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for label, href, masks in observations:
            with rasterio.open(href) as src:
                if grid is None:
                    grid = _Grid(src, bbox)
                    union = np.full(grid.shape, NODATA, dtype="uint8")
                win = grid.window_in(src)
                obs = _harmonise(src.read(1, window=win, boundless=True,
                                          fill_value=encoding["nodata"][0]), encoding)
            for mask_href, values in masks:
                with rasterio.open(mask_href) as msrc:
                    m = msrc.read(1, window=grid.window_in(msrc), boundless=True,
                                  fill_value=255)
                obs[np.isin(m, values)] = NODATA
            seen = obs != NODATA
            used.append({"observation": label, "valid_pixels": int(seen.sum()),
                         "flood_pixels": int((obs == FLOOD).sum())})
            union[(union == NODATA) & (obs == NONFLOOD)] = NONFLOOD
            union[obs == FLOOD] = FLOOD

    contributing = [u for u in used if u["valid_pixels"]]
    if not contributing:
        raise NotEvaluated(f"{len(used)} observation(s) in the window, none with valid "
                           "pixels over the AOI")
    return {"array": union, "crs": grid.crs, "transform": grid.transform,
            "shape": grid.shape, "resolution": grid.res,
            "observations": used, "observations_with_data": len(contributing),
            "skipped_items": skipped,
            "window": [window[0].isoformat(), window[1].isoformat()]}
