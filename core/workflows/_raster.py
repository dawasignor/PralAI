"""Shared raster helpers for the Earth-observation workflows.

Deliberately thin: rasterio + numpy only. No odc-stac, no xarray, no
reprojection stack. Everything these workflows need is a windowed read of a
COG over HTTP, and rasterio does that natively through GDAL's /vsicurl.

Two ideas carry most of the weight here:

* **The 20 m grid is the working grid.** Sentinel-2's 10 m and 20 m bands
  share one CRS and one origin, and 10980 is exactly 2 x 5490, so a 10 m band
  decimated 2:1 lands exactly on the 20 m grid. That is an averaging read, not
  a reprojection -- no resampling error, no extra dependency.
* **Rasters go to disk, statistics go into the JSON.** An AOI of a few
  hundred km^2 at 20 m is millions of pixels. Monty's return value is read by
  a model, so the array is written as a COG and the JSON carries a path.

**Reading `min` and `max`.** Reflectance is clamped to [0, 1] before any index
is computed, so a band that is dark enough to go negative becomes exactly zero
-- and a normalised difference against zero is exactly +-1 regardless of the
other band. Those pixels pile up on the bound. Over Edinburgh that was 73 of
339153 pixels: invisible to a mean, but `min` and `max` exist to find the most
extreme pixel, so they find precisely those. Use `p10` and `p90` to describe a
distribution and `min`/`max` only to detect saturation, which `summarise`
counts explicitly in `coverage.saturated_pixels`.
"""

from __future__ import annotations

import math
import os
from typing import Any

import numpy as np

# GDAL settings for reading COGs over HTTP. Without these every open() does a
# directory listing of the bucket prefix, which turns a 2-second read into a
# 30-second one.
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": str(64 * 1024 * 1024),
}

# Index values are stored as int16 scaled by 10000 -- the same convention
# Sentinel-2 uses for reflectance. Half the size of float32, exact to 4 dp,
# and -32768 is unambiguously "no data" because valid indices are in [-1, 1].
INDEX_SCALE = 10000
INDEX_NODATA = -32768


def env():
    """rasterio.Env preloaded with the HTTP-COG settings."""
    import rasterio

    return rasterio.Env(**GDAL_ENV)


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------
class GridPlan:
    """The AOI window on one reference raster, plus the metadata to reuse it.

    Built once from the 20 m SCL band and then applied to every other band, so
    every index in a run shares one grid, one shape and one transform. That is
    what makes the outputs stackable without any alignment step.
    """

    def __init__(self, window, transform, crs, shape, resolution, *,
                 pixel_area_km2: float | None = None, units: str = "m",
                 coverage: float = 1.0, resolution_y: float | None = None):
        self.window = window
        self.transform = transform
        self.crs = crs
        self.shape = shape                     # (height, width)
        # `resolution` is the COLUMN (x) spacing, `resolution_y` the row
        # spacing, both in `units`. They differ on Copernicus GLO-30 north of
        # 50 deg, where a pixel is 1.5 x 1 arc-seconds (2 x 1 above 60, and
        # so on): one number for both axes made every terrain area 1.5x too
        # large over Edinburgh and stretched Horn's north-south step by the
        # same factor (BUG-018).
        self.resolution = resolution           # in `units`
        self.resolution_y = resolution if resolution_y is None else resolution_y
        self.units = units
        # Share of the REQUESTED window that survived clamping to the raster.
        # Below 1.0 the returned grid is smaller than the AOI that was asked
        # for, and every area and fraction computed on it describes the smaller
        # region. Callers decide whether that is acceptable; nothing here can.
        self.coverage = coverage
        # (west, north) overhang in pixels, negative when the AOI extended
        # past the raster's edge and the window had to be cut. Zero means the
        # window sits exactly where the bbox asked for it.
        self.offset_px: tuple[int, int] = (0, 0)
        # A projected grid gets its pixel area from the resolution directly. A
        # geographic one cannot -- `resolution` is then in degrees, and squaring
        # a degree gives a number that is not an area. Callers on EPSG:4326
        # (the Copernicus DEM, JRC surface water) pass the metric area in.
        self._pixel_area_km2 = pixel_area_km2

    @property
    def pixel_area_km2(self) -> float:
        if self._pixel_area_km2 is not None:
            return self._pixel_area_km2
        if self.units != "m":
            raise ValueError(
                f"grid is in {self.units}, not metres; pass pixel_area_km2 "
                "explicitly rather than squaring an angular resolution"
            )
        return (self.resolution * self.resolution_y) / 1e6

    def bounds_wgs84(self) -> list[float]:
        """The grid's ACTUAL extent as [west, south, east, north] in WGS84.

        Not the bbox that was requested -- the one that survived snapping to
        whole pixels and clamping to the raster. Compare the two to see what a
        `coverage` below 1.0 actually cost.
        """
        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds

        left, bottom, right, top = array_bounds(
            self.shape[0], self.shape[1], self.transform)
        return [round(v, 6) for v in
                transform_bounds(self.crs, "EPSG:4326", left, bottom, right, top)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "crs": str(self.crs),
            "resolution": self.resolution,
            "resolution_y": self.resolution_y,
            "resolution_units": self.units,
            "pixel_area_km2": round(self.pixel_area_km2, 8),
            "shape": [int(self.shape[0]), int(self.shape[1])],
            "transform": [round(v, 8) for v in self.transform[:6]],
            "coverage": round(self.coverage, 4),
            "offset_px": list(self.offset_px),
        }


def geographic_pixel_km2(res_x_deg: float, res_y_deg: float, latitude: float) -> float:
    """The ground area of one EPSG:4326 pixel at `latitude`, in km2.

    Each axis is converted with its own spacing; squaring one of them is
    exactly the error BUG-018 was.
    """
    return (res_x_deg * 111320.0 * math.cos(math.radians(latitude))
            * res_y_deg * 111320.0) / 1e6


def plan_grid(href: str, bbox: list[float], *, pad: int = 0) -> GridPlan:
    """Window `href` to `bbox` (WGS84 west/south/east/north).

    The window is snapped to whole pixels. `pad` widens it by N pixels, which
    slope needs -- a 3x3 kernel cannot be evaluated on the outermost ring.
    """
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    with env(), rasterio.open(href) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox)
        win = from_bounds(left, bottom, right, top, src.transform)
        win = win.round_offsets().round_lengths()

        if pad:
            win = Window(win.col_off - pad, win.row_off - pad,
                         win.width + 2 * pad, win.height + 2 * pad)

        # Clamp to the raster -- an AOI on a tile edge would otherwise ask for
        # negative offsets and rasterio would pad with nodata silently.
        #
        # THE OVERHANG MUST COME OUT OF THE SIZE, NOT JUST THE OFFSET. Snapping
        # a negative `col_off` to 0 and leaving `width` alone does not truncate
        # the window, it TRANSLATES it: the read keeps its full size and slides
        # east, describing a region the caller never asked for while
        # `width * height` still equals `requested_pixels` -- so coverage
        # computed 1.0 and `min_aoi_coverage=1.0` waved it through.
        #
        # Measured on the Kelani basin: the AOI's west edge sits at easting
        # 374,790 in UTM 44N and tile T44NMN begins at 399,960, so the window
        # was clamped 1,258 pixels (25 km, 0.23 deg) east of the AOI at full
        # width, and every composition statistic described that shifted
        # rectangle. `bounds_wgs84()` reported the shift correctly the whole
        # time; nothing compared it to what was requested.
        requested_width = int(round(win.width))
        requested_height = int(round(win.height))
        requested_pixels = max(1, requested_width * requested_height)

        col_off = max(0, int(win.col_off))
        row_off = max(0, int(win.row_off))
        # How much fell off the west/north edge. `min(0, ...)` is the overhang
        # as a negative number, so adding it shortens the window by exactly the
        # part that was cut.
        west_overhang = min(0, int(win.col_off))
        north_overhang = min(0, int(win.row_off))

        width = int(min(requested_width + west_overhang, src.width - col_off))
        height = int(min(requested_height + north_overhang, src.height - row_off))
        if width <= 0 or height <= 0:
            raise ValueError(
                f"AOI {bbox} does not overlap the raster at {href}"
            )

        # How much of the AOI the clamp kept. Reported rather than swallowed:
        # a truncated window still produces plausible statistics, and they
        # describe a region the caller never asked for.
        coverage = min(1.0, (width * height) / requested_pixels)

        win = Window(col_off, row_off, width, height)
        resolution = abs(src.transform.a)
        resolution_y = abs(src.transform.e)

        # On a geographic grid the resolution is angular, so the metric pixel
        # area has to be derived at the AOI's latitude before any area or
        # fraction is reported.
        geographic = bool(src.crs and src.crs.is_geographic)
        area = None
        if geographic:
            mid_lat = (bbox[1] + bbox[3]) / 2
            area = geographic_pixel_km2(resolution, resolution_y, mid_lat)

        plan = GridPlan(
            window=win,
            transform=src.window_transform(win),
            crs=src.crs,
            shape=(height, width),
            resolution=resolution,
            resolution_y=resolution_y,
            pixel_area_km2=area,
            units="deg" if geographic else "m",
            coverage=coverage,
        )
        # Where the window landed, not just how big it is. Coverage is a count
        # of pixels and a translated window keeps its count -- so a pixel-ratio
        # check cannot see a shift, and for a year nothing compared position at
        # all. These are the offsets a caller should assert on.
        plan.offset_px = (west_overhang, north_overhang)
        return plan


def read_band(href: str, plan: GridPlan, *, scale: float = 1.0,
              offset: float = 0.0, nodata: float | None = None) -> np.ndarray:
    """Read one band onto `plan`'s grid as float32, nodata -> NaN.

    A band at finer resolution than the plan is read with an averaging
    decimation. Because Sentinel-2's grids are exact multiples this is a clean
    2:1 average, not an interpolation.

    `scale` and `offset` come from the STAC asset's `raster:bands` -- never
    hardcode them. Baseline 05.00+ products carry offset -0.1, and that offset
    does NOT cancel in a normalised difference: (a+o - (b+o)) / (a+o + b+o)
    has a cancelling numerator but a denominator that does not.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    with env(), rasterio.open(href) as src:
        ratio = plan.resolution / abs(src.transform.a)
        if not math.isclose(ratio, round(ratio), rel_tol=1e-9):
            raise ValueError(
                f"{href} resolution {abs(src.transform.a)} m is not an integer "
                f"factor of the {plan.resolution} m working grid"
            )
        ratio = int(round(ratio))

        win = Window(plan.window.col_off * ratio, plan.window.row_off * ratio,
                     plan.window.width * ratio, plan.window.height * ratio)
        arr = src.read(
            1, window=win, out_shape=plan.shape,
            resampling=Resampling.average if ratio > 1 else Resampling.nearest,
            boundless=False,
        ).astype("float32")

        fill = nodata if nodata is not None else src.nodata
        if fill is not None:
            arr[arr == fill] = np.nan

    return arr * scale + offset


def read_classes(href: str, plan: GridPlan, *,
                 decimation: int = 1) -> np.ndarray:
    """Read a categorical band with nearest-neighbour, preserving codes.

    `decimation` > 1 returns the same window at 1/N the resolution, served from
    the COG's built-in overviews where they exist (Sentinel-2 SCL carries
    2/4/8/16). Class codes survive because the resampling is nearest -- the
    array is coarser, not reinterpreted.

    **Only for a statistic over the window, never for per-pixel output.** A
    decimated read answers "roughly what share of this AOI is cloud" cheaply;
    it does not answer "what is at this pixel".
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    shape = plan.shape
    if decimation > 1:
        shape = (max(1, plan.shape[0] // decimation),
                 max(1, plan.shape[1] // decimation))

    with env(), rasterio.open(href) as src:
        ratio = int(round(plan.resolution / abs(src.transform.a)))
        win = Window(plan.window.col_off * ratio, plan.window.row_off * ratio,
                     plan.window.width * ratio, plan.window.height * ratio)
        return src.read(1, window=win, out_shape=shape,
                        resampling=Resampling.nearest)


# ---------------------------------------------------------------------------
# Summarising
# ---------------------------------------------------------------------------
def _f(value) -> float | None:
    """Round for JSON; NaN becomes null rather than a number that lies."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), 4)


def summarise(arr: np.ndarray, *, bins: int = 20,
              value_range: tuple[float, float] = (-1.0, 1.0),
              saturating_at: tuple[float, float] | None = None,
              domain: int | None = None) -> dict[str, Any]:
    """Statistics and a histogram for one index, ignoring NaN.

    Everything that qualifies the statistics lives under `coverage`, so a
    caveat cannot be read without the number it qualifies:

    * `valid_fraction` -- the share of pixels that survived masking. Every
      statistic below is computed over that subset, so a mean taken from 12%
      of an AOI is not the AOI's mean.
    * `saturated_pixels` -- pixels sitting exactly on a theoretical bound.
      These are the reason `min` and `max` cannot be read as descriptive
      statistics: see the note in the module docstring and
      docs/fetch_lulc_data.md.

    TODO(entro): feed `coverage` into the uncertainty score. Nothing consumes
    it yet.

    `saturating_at` is the bound past which a value cannot go, and it is not
    simply `value_range`. Pass it only where landing exactly on the bound
    means the measurement degenerated rather than that the surface really is
    that extreme:

    * **Indices: yes.** A normalised difference reaches exactly +-1 only when
      one band clamped to zero.
    * **Slope, JRC occurrence: no.** Flat ground really is 0 degrees, and a
      pixel really can be water in 100% of observations. Those bounds are
      legitimate values, not artefacts.
    * **Elevation: no.** Its range is derived from the data, so the extremes
      define the bound and every run would report two saturated pixels.

    Left None, the count is null rather than zero, because "not measured" and
    "none found" are different claims.
    """
    finite = np.isfinite(arr)
    valid = arr[finite]

    # `domain` is the number of pixels this statistic is meant to describe,
    # which is not always the array. Once the sea is masked out, the ocean
    # pixels are NaN and counting them in the denominator would report a
    # valid_fraction of 0.63 for a scene that observed 73% of the LAND -- a
    # cloud figure inflated by geography. The caller that knows the reporting
    # domain passes it; everything else keeps the array.
    total = int(arr.size if domain is None else domain)

    coverage: dict[str, Any] = {
        "valid_fraction": round(float(valid.size / total), 4) if total else 0.0,
        "valid_pixels": int(valid.size),
        "total_pixels": total,
        "denominator": ("the array" if domain is None
                        else "the reporting domain, not the whole array"),
        "saturated_pixels": None,
        "saturated_fraction": None,
    }

    if valid.size == 0:
        return {
            "coverage": coverage,
            "min": None, "max": None, "mean": None, "median": None,
            "std": None, "p10": None, "p90": None, "histogram": None,
        }

    if saturating_at is not None:
        low, high = saturating_at
        stuck = int((np.isclose(valid, low) | np.isclose(valid, high)).sum())
        coverage["saturated_pixels"] = stuck
        coverage["saturated_fraction"] = round(float(stuck / valid.size), 6)

    counts, edges = np.histogram(valid, bins=bins, range=value_range)
    return {
        "coverage": coverage,
        "min": _f(valid.min()),
        "max": _f(valid.max()),
        "mean": _f(valid.mean()),
        "median": _f(np.median(valid)),
        "std": _f(valid.std()),
        "p10": _f(np.percentile(valid, 10)),
        "p90": _f(np.percentile(valid, 90)),
        "histogram": {
            "bin_edges": [round(float(e), 3) for e in edges],
            "counts": [int(c) for c in counts],
        },
    }


def class_fractions(codes: np.ndarray, legend: dict[int, str],
                    plan: GridPlan, *,
                    nodata: int = 0) -> dict[str, Any]:
    """Pixel share and area per class code, over classified pixels only."""
    classified = codes[codes != nodata]
    denom = int(classified.size)

    fractions, areas = {}, {}
    for code, name in legend.items():
        n = int((classified == code).sum())
        fractions[name] = round(n / denom, 4) if denom else 0.0
        areas[name] = round(n * plan.pixel_area_km2, 4)

    return {
        "legend": {str(k): v for k, v in legend.items()},
        "fractions": fractions,
        "area_km2": areas,
        "classified_pixels": denom,
        "unclassified_pixels": int(codes.size - denom),
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def write_index(path: str, arr: np.ndarray, plan: GridPlan, *,
                value_range: tuple[float, float] = (-1.0, 1.0)) -> dict[str, Any]:
    """Write an index as an int16 COG scaled by 10000.

    `value_range` must be the index's real range, not assumed to be [-1, 1]:
    BUI is a difference of two normalised differences and spans [-2, 2].
    Clipping it at 1.0 would truncate a fifth of its range on disk while the
    in-memory statistics still reported the true values. int16 holds +-3.2,
    so the wider range costs nothing.
    """
    low, high = value_range
    if max(abs(low), abs(high)) * INDEX_SCALE > 32767:
        raise ValueError(f"range {value_range} overflows int16 at x{INDEX_SCALE}")

    scaled = np.full(arr.shape, INDEX_NODATA, dtype="int16")
    finite = np.isfinite(arr)
    scaled[finite] = np.clip(
        np.rint(arr[finite] * INDEX_SCALE), low * INDEX_SCALE, high * INDEX_SCALE
    ).astype("int16")
    return _write(path, scaled, plan, "int16", INDEX_NODATA,
                  scale=1 / INDEX_SCALE)


def write_classes(path: str, codes: np.ndarray, plan: GridPlan, *,
                  nodata: int = 0) -> dict[str, Any]:
    """Write a categorical map as a uint8 COG."""
    return _write(path, codes.astype("uint8"), plan, "uint8", nodata)


def write_float(path: str, arr: np.ndarray, plan: GridPlan,
                nodata: float = -9999.0) -> dict[str, Any]:
    """Write a continuous quantity (slope degrees, elevation) as float32."""
    out = np.where(np.isfinite(arr), arr, nodata).astype("float32")
    return _write(path, out, plan, "float32", nodata)


def _write(path: str, arr: np.ndarray, plan: GridPlan, dtype: str,
           nodata, *, scale: float | None = None) -> dict[str, Any]:
    import rasterio

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    profile = {
        "driver": "GTiff", "height": plan.shape[0], "width": plan.shape[1],
        "count": 1, "dtype": dtype, "crs": plan.crs,
        "transform": plan.transform, "nodata": nodata,
        "compress": "deflate", "predictor": 2,
        "tiled": True, "blockxsize": 256, "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)
        # THE SCALE GOES IN THE FILE, not only in the dict this function
        # returns. An index is stored as int16 x 10000, and a reader that does
        # not know that sees 6284 where the value is 0.6284. It was recorded in
        # the returned metadata and nowhere else, so `core/figures.render` --
        # which opens the GeoTIFF and has no access to that dict -- read
        # `src.scales == (1.0,)`, skipped the descale, and plotted raw counts
        # from -6313 to 9243 against a colour bar running -1 to 1. Every pixel
        # saturated, so every index figure came out a flat block of the extreme
        # colour and looked like a confident result.
        if scale is not None:
            dst.scales = (scale,)
        # Only levels that leave at least one pixel. Asking for 8x on a raster
        # under 8 px is an error, not a no-op, so a small AOI would crash here.
        smallest = min(plan.shape)
        levels = [f for f in (2, 4, 8) if smallest // f >= 1]
        if levels:
            dst.build_overviews(levels)

    meta = {
        "path": path.replace("\\", "/"),
        "dtype": dtype,
        "nodata": nodata,
        "size_bytes": os.path.getsize(path),
        **plan.as_dict(),
    }
    if scale is not None:
        meta["scale"] = scale
    return meta


# ---------------------------------------------------------------------------
# Mosaicking -- an AOI that crosses a tile edge
# ---------------------------------------------------------------------------
#
# Three archives raise on a multi-tile AOI: Copernicus GLO-30 (1 degree tiles),
# JRC GSW (10 degrees) and Sentinel-2 (110 km MGRS tiles). In every case the
# tiles share one resolution and one lattice, which is what makes this a paste
# rather than a reprojection -- pixels from two tiles land on the same grid by
# construction, and the only arithmetic is which row and column each belongs in.
#
# **Every mosaic is anchored to the first tile's own origin, and every other
# tile must sit a whole number of pixels from it.** There is no global lattice
# to fall back on, in either projection family:
#
# * *Projected* (Sentinel-2 on MGRS). Each UTM zone carries its own false
#   easting, so a tile origin is a whole number of pixels from its neighbours
#   inside one zone and from nothing at all outside it. T44NLN begins at
#   easting 399,960 and T44NMN at 499,980: 100,020 m apart, which is 5001
#   pixels at 20 m exactly. Cross the zone boundary and that number stops being
#   an integer, because the two grids are different map projections.
#
# * *Geographic* (GLO-30, GSW). This branch USED to snap to multiples of the
#   resolution counted from zero, on the reasoning that tile origins sit on
#   whole degrees. **They do not.** Copernicus GLO-30 is pixel-is-POINT: the
#   centre of its first pixel is at the whole degree, so the transform -- which
#   records a pixel EDGE -- sits half a pixel outside it. Tile
#   N06_00_E079_00 has `top = 7.00013889`, which is 7 + res/2.
#
#   Against a lattice through zero that is a permanent half-pixel offset, and
#   `int(round(...))` resolved it by banker's rounding: `round(299.5)` is 300
#   and `round(-3120.5)` is -3120, so the northern tile was pasted one row low.
#   Every DEM pixel in the Kelani AOI was displaced 15 m, and mosaic row 299 --
#   latitude 7.0, the tile boundary -- was written by no tile at all and came
#   back NaN. It showed up as a faint line straight across the elevation
#   figure, which is the only reason anybody noticed.
#
# The lattice check below is what makes this safe: a tile that does not sit a
# whole number of pixels from the anchor is refused rather than rounded into
# place.
#
# It is written this way, and not with `rasterio.merge`, because merge reads
# whole tiles into memory. A 1-degree GLO-30 tile is 3601x3601; four of them to
# recover an AOI of 0.35x0.30 degrees is 52 million pixels read to keep 1.2
# million. A Sentinel-2 tile is worse: 5490x5490 at 20 m, two of them 60
# million pixels to keep 3. Everything below stays windowed.


def _require_one_lattice(hrefs: list[str], crs, resolution: float,
                         anchor: tuple[float, float],
                         resolution_y: float | None = None) -> None:
    """Refuse any tile that is not on the same lattice as the anchor tile.

    Checked rather than assumed, because a tile that is off by a fraction of a
    pixel still pastes -- producing an array of exactly the right shape whose
    every value is drawn from slightly the wrong place. Nothing downstream can
    see that.
    """
    import rasterio

    ax, ay = anchor
    res_y = resolution if resolution_y is None else resolution_y
    for href in hrefs:
        with env(), rasterio.open(href) as src:
            if src.crs != crs:
                raise ValueError(
                    f"{href} is in {src.crs} but the mosaic is in {crs}. "
                    "Tiles in different projections (a Sentinel-2 AOI "
                    "straddling two UTM zones, say) cannot be pasted -- "
                    "aligning them is a reprojection, which this module "
                    "deliberately does not do."
                )
            if (abs(abs(src.transform.a) - resolution) > 1e-9
                    or abs(abs(src.transform.e) - res_y) > 1e-9):
                raise ValueError(
                    f"{href} is {abs(src.transform.a)} x {abs(src.transform.e)} "
                    f"per pixel but the mosaic is {resolution} x {res_y}; these "
                    "tiles are not one grid."
                )
            for value, origin, axis, step in ((src.transform.c, ax, "easting", resolution),
                                              (src.transform.f, ay, "northing", res_y)):
                steps = (value - origin) / step
                if abs(steps - round(steps)) > 1e-6:
                    raise ValueError(
                        f"{href} sits {steps:.4f} pixels from the mosaic "
                        f"origin in {axis} -- not a whole number, so its "
                        "pixels do not line up with the anchor tile's. "
                        "Pasting it would be wrong by a sub-pixel shift that "
                        "nothing downstream could detect."
                    )


def plan_mosaic(hrefs: list[str], bbox: list[float], *,
                pad: int = 0) -> "GridPlan":
    """A grid spanning `bbox` across several aligned tiles.

    The returned plan has no single source raster, so its `window` is relative
    to the mosaic's own origin and is meaningful only to `read_band_mosaic`
    and `read_classes_mosaic`.

    Unlike `plan_grid`, this never clamps: the grid is built from the bbox, so
    it always describes exactly the AOI that was asked for. What the tiles fail
    to cover comes back as NaN and is reported in `coverage` by the readers,
    which is a different and more honest failure than a window quietly moved.
    """
    import rasterio
    from rasterio.transform import Affine
    from rasterio.windows import Window

    if not hrefs:
        raise ValueError("plan_mosaic needs at least one tile URL")

    with env(), rasterio.open(hrefs[0]) as src:
        crs, resolution = src.crs, abs(src.transform.a)
        resolution_y = abs(src.transform.e)
        anchor = (src.transform.c, src.transform.f)
        geographic = bool(crs and crs.is_geographic)

    # Every tile is checked against the anchor BEFORE any pixel is read, so a
    # mismatch fails in a second rather than after several windowed reads and a
    # plausible-looking array.
    _require_one_lattice(hrefs[1:], crs, resolution, anchor, resolution_y)

    if geographic:
        west, south, east, north = bbox
    else:
        from rasterio.warp import transform_bounds

        # transform_bounds densifies the edges, so this is the envelope of the
        # reprojected AOI rather than the four corners -- a lat/lon rectangle
        # is not a rectangle in UTM, and taking its corners would cut the
        # bulging middle of each edge.
        west, south, east, north = transform_bounds("EPSG:4326", crs, *bbox)

    # Snap outward onto the ANCHOR TILE'S lattice, so the mosaic's pixel edges
    # are the tiles' own pixel edges. Rounding inward, or onto some other
    # lattice, shifts every tile by a sub-pixel amount -- and a sub-pixel shift
    # resolved by rounding is how a whole row of the AOI ends up belonging to
    # no tile.
    ax, ay = anchor
    rx, ry = resolution, resolution_y
    left = ax + math.floor((west - ax) / rx) * rx
    right = ax + math.ceil((east - ax) / rx) * rx
    top = ay - math.floor((ay - north) / ry) * ry
    bottom = ay - math.ceil((ay - south) / ry) * ry

    if pad:
        left -= pad * rx
        right += pad * rx
        bottom -= pad * ry
        top += pad * ry

    width = int(round((right - left) / rx))
    height = int(round((top - bottom) / ry))
    if width <= 0 or height <= 0:
        raise ValueError(f"bbox {bbox} has no extent at {rx} x {ry} per pixel")

    transform = Affine(rx, 0.0, left, 0.0, -ry, top)

    if geographic:
        area = geographic_pixel_km2(rx, ry, (south + north) / 2)
        units = "deg"
    else:
        area = (rx * ry) / 1e6
        units = "m"

    return GridPlan(
        window=Window(0, 0, width, height),
        transform=transform,
        crs=crs,
        shape=(height, width),
        resolution=resolution,
        resolution_y=resolution_y,
        pixel_area_km2=area,
        units=units,
        # Filled in by the readers, which are the only things that know how
        # much of the AOI the tiles actually covered.
        coverage=1.0,
    )


def _placements(hrefs: list[str], plan: "GridPlan", *, decimation: int = 1):
    """Where each tile lands on the mosaic, skipping tiles that miss it.

    Yields `(href, src, window, (r0, r1, c0, c1), ratio)` with the source open:
    the destination slice in mosaic pixels, and the decimation factor between
    the tile's own pixels and the mosaic's.

    `decimation` > 1 coarsens the output grid for a screening probe. A tile
    origin is a whole number of MOSAIC pixels from the anchor but not
    necessarily a whole number of DECIMATED ones, so its position is rounded
    and may land up to `decimation / 2` pixels out. That is why a decimated
    read is only ever an estimate -- the same rule that governs `read_classes`,
    and the reason an accepted scene is always re-read at decimation 1.
    """
    import rasterio
    from rasterio.windows import Window

    left, top, res = plan.transform.c, plan.transform.f, plan.resolution
    res_y = plan.resolution_y
    out_h = max(1, plan.shape[0] // decimation)
    out_w = max(1, plan.shape[1] // decimation)

    for href in hrefs:
        with env(), rasterio.open(href) as src:
            if src.crs != plan.crs:
                raise ValueError(
                    f"{href} is in {src.crs}, the mosaic in {plan.crs}; "
                    "pasting across projections would be wrong")

            src_res = abs(src.transform.a)
            ratio = res / src_res
            ratio_y = res_y / abs(src.transform.e)
            if (not math.isclose(ratio, round(ratio), rel_tol=1e-9)
                    or not math.isclose(ratio, ratio_y, rel_tol=1e-9)):
                raise ValueError(
                    f"{href} is {src_res} x {abs(src.transform.e)} per pixel, which "
                    f"is not one integer factor of the {res} x {res_y} mosaic grid")
            ratio = int(round(ratio))

            # The tile's origin in mosaic pixels. Integral by construction --
            # plan_mosaic refused the tile otherwise -- so rounding here is
            # cleaning up float noise, not a decision.
            col_f = (src.transform.c - left) / res
            row_f = (top - src.transform.f) / res_y
            if (abs(col_f - round(col_f)) > 1e-6
                    or abs(row_f - round(row_f)) > 1e-6):
                raise ValueError(
                    f"{href} sits at ({col_f:.4f}, {row_f:.4f}) mosaic pixels "
                    "-- not on the lattice, so its pixels do not line up")
            col0, row0 = int(round(col_f)), int(round(row_f))

            # The tile's extent in mosaic pixels. Floor, because a trailing
            # partial mosaic pixel has no whole set of source pixels to average
            # and would be built from a short read.
            tile_w, tile_h = src.width // ratio, src.height // ratio

            if decimation > 1:
                col0, row0 = round(col0 / decimation), round(row0 / decimation)
                tile_w = max(1, tile_w // decimation)
                tile_h = max(1, tile_h // decimation)

            c0, r0 = max(0, col0), max(0, row0)
            c1 = min(out_w, col0 + tile_w)
            r1 = min(out_h, row0 + tile_h)
            if c1 <= c0 or r1 <= r0:
                continue                      # this tile misses the AOI

            step = ratio * decimation
            window = Window(
                (c0 - col0) * step, (r0 - row0) * step,
                (c1 - c0) * step, (r1 - r0) * step)
            yield href, src, window, (r0, r1, c0, c1), ratio


def read_band_mosaic(hrefs: list[str], plan: "GridPlan", *, scale: float = 1.0,
                     offset: float = 0.0, nodata: float | None = None,
                     per_tile: list[dict] | None = None) -> np.ndarray:
    """Read one band from several aligned tiles onto `plan`'s grid.

    Pixels no tile covers stay NaN, and `plan.coverage` is updated to the
    fraction that any tile did cover -- a gap in the middle of an AOI is a
    different fact from a clean read and must not look like one.

    `per_tile`, parallel to `hrefs`, overrides `scale` / `offset` / `nodata`
    per tile. Sentinel-2 needs it: two scenes of one mosaic can carry different
    processing baselines, and the BOA offset does not cancel out of a
    normalised difference. Scaling is therefore applied inside the loop, before
    the paste, not once at the end.
    """
    from rasterio.enums import Resampling

    out = np.full(plan.shape, np.nan, dtype="float32")
    settings = per_tile or [{}] * len(hrefs)
    by_href = dict(zip(hrefs, settings))

    for href, src, window, (r0, r1, c0, c1), ratio in _placements(hrefs, plan):
        conf = by_href.get(href) or {}
        arr = src.read(
            1, window=window, out_shape=(r1 - r0, c1 - c0),
            resampling=Resampling.average if ratio > 1 else Resampling.nearest,
            boundless=False,
        ).astype("float32")

        # Averaged first, masked second -- the same order as read_band, and it
        # has the same consequence: a coarse pixel straddling the nodata edge
        # of a tile is a blend rather than a rejection. It matters only on the
        # outermost ring of a tile, which inside a mosaic is covered by the
        # neighbour anyway.
        fill = conf.get("nodata", nodata)
        if fill is not None:
            arr[arr == fill] = np.nan

        arr = arr * conf.get("scale", scale) + conf.get("offset", offset)

        # `where`: a later tile must not overwrite a valid pixel an earlier one
        # already supplied with its own nodata along a shared edge.
        patch = out[r0:r1, c0:c1]
        out[r0:r1, c0:c1] = np.where(np.isnan(patch), arr, patch)

    plan.coverage = round(
        min(1.0, float(np.isfinite(out).sum()) / max(1, out.size)), 6)
    return out


def read_classes_mosaic(hrefs: list[str], plan: "GridPlan", *,
                        decimation: int = 1, fill: int = 0) -> np.ndarray:
    """Read a categorical band from several aligned tiles, preserving codes.

    Resampling is nearest throughout, so class codes survive. Pixels no tile
    covers are left at `fill` -- 0, which in Sentinel-2's SCL already means
    "no data", so an uncovered pixel is counted as unobserved rather than
    silently becoming someone's class.

    `plan.coverage` is set to the share of the grid a tile did reach, measured
    on the decimated grid when one is used.
    """
    from rasterio.enums import Resampling

    shape = plan.shape
    if decimation > 1:
        shape = (max(1, plan.shape[0] // decimation),
                 max(1, plan.shape[1] // decimation))

    out = np.full(shape, fill, dtype="int16")

    for href, src, window, (r0, r1, c0, c1), _ in _placements(
            hrefs, plan, decimation=decimation):
        arr = src.read(1, window=window, out_shape=(r1 - r0, c1 - c0),
                       resampling=Resampling.nearest).astype("int16")
        # Real data beats a neighbour's fill. Sentinel-2 tiles overlap by
        # 9.8 km and a scene clipped by the swath edge still spans its whole
        # tile, padded with zeros -- so first-tile-wins would paste those zeros
        # over the neighbour's actual observations along every shared edge.
        patch = out[r0:r1, c0:c1]
        out[r0:r1, c0:c1] = np.where(patch == fill, arr, patch)

    # Coverage is measured on the VALUES, not on where a tile's footprint
    # landed. A tile whose swath stops short still covers its full extent on
    # paper, and counting that as coverage would report a solidly observed AOI
    # over a strip of nothing.
    plan.coverage = round(
        min(1.0, float((out != fill).sum()) / max(1, out.size)), 6)
    return out


def tiles_for_bbox(bbox: list[float], url_for, *, step: float,
                   anchor: str = "floor") -> list[str]:
    """Every tile URL needed to cover `bbox`, without duplicates.

    `url_for(lat, lon)` is the archive's own naming function -- this does not
    reimplement it. `step` is the tile size in degrees; `anchor` says whether
    the archive names tiles by their southern edge ("floor", Copernicus) or
    their northern one ("ceil", JRC surface water).
    """
    west, south, east, north = bbox
    # A point just inside the far edge, so a bbox ending exactly on a boundary
    # does not pull in the tile beyond it.
    eps = step * 1e-9
    lons, lon = [], math.floor(west / step) * step
    while lon < east - eps:
        lons.append(lon + step / 2)
        lon += step
    lats, lat = [], math.floor(south / step) * step
    while lat < north - eps:
        lats.append(lat + step / 2)
        lat += step

    seen, urls = set(), []
    for la in lats or [(south + north) / 2]:
        for lo in lons or [(west + east) / 2]:
            url = url_for(la, lo)
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls
