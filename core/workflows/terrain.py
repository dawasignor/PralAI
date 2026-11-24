"""Terrain from the Copernicus DEM GLO-30 archive.

Sentinel-2 carries no elevation information -- it is a multispectral imager
with no stereo pair and no DEM product -- so slope cannot come from it. This is
a separate workflow on purpose, and not only because the source differs:

* Terrain does not change between flood events, so a result is cacheable
  indefinitely, which is not true of anything optical.
* Bundling it into the Sentinel-2 workflow would mean a cloudy season takes
  slope down with it. Two sources, two failure modes, two functions.

Data comes from the public Copernicus DEM GLO-30 bucket on AWS Open Data --
an already-built archive, read as a windowed COG, with nothing rebuilt locally.
Reference in docs/SOURCES.md.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any

import numpy as np

from . import workflow
from . import _raster as R
from . import landmask

# Public, anonymous, no request-payer. One COG per 1-degree tile.
DEM_BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"

# Slope is computed by Horn's (1981) method (`slope_degrees`); these five
# classes are the breaks applied to it. User decision 2026-11-21: back to these
# breaks after two days (19-21 Nov) on 5-degree bands after Ozdemir (2009).
# Horn defines the slope computation, not the breaks.
SLOPE_CLASSES = [
    (0.0, 2.0, 1, "flat"),
    (2.0, 5.0, 2, "gentle"),
    (5.0, 15.0, 3, "moderate"),
    (15.0, 30.0, 4, "steep"),
    (30.0, 91.0, 5, "very_steep"),
]
# The ranking's L2 criterion counts slope >= 15 degrees as steep: the steep
# and very_steep classes (restored 2026-11-21; >= 30 on 19-21 Nov).
STEEP_FROM_DEG = 15.0
STEEP_CLASSES = ("steep", "very_steep")
# Results stored on 19-21 Nov carry 5-degree bands (`deg_15_20` ... ); the
# same >= 15 share is the sum of the bands from 15 up.
STEEP_BANDS_19_21_NOV = ("deg_15_20", "deg_20_25", "deg_25_30", "deg_30_35", "deg_35_40",
                         "deg_40_45", "deg_45_50", "deg_50_55", "deg_55_plus")


def tile_url(latitude: float, longitude: float) -> str:
    """COG URL for the 1-degree GLO-30 tile containing a point.

    Tiles are named for their south-west corner, zero-padded:
    Copernicus_DSM_COG_10_N25_00_E091_00_DEM.
    """
    lat = math.floor(latitude)
    lon = math.floor(longitude)
    ns = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
    ew = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
    name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
    return f"{DEM_BUCKET}/{name}/{name}.tif"


def slope_degrees(dem: np.ndarray, latitude: float,
                  resolution_deg: float,
                  resolution_y_deg: float | None = None) -> np.ndarray:
    """Slope in degrees by Horn's 3x3 method.

    GLO-30 is stored in EPSG:4326, so the grid spacing is angular and has to be
    converted to metres before a gradient means anything -- a degree of
    longitude is 111 km at the equator and 100 km at 25 degrees north. Doing
    this with a cosine factor rather than a reprojection keeps the dependency
    list at rasterio + numpy, and the error over a flood-sized AOI is far below
    the DEM's own vertical accuracy.

    The outer ring cannot be evaluated by a 3x3 kernel and is returned as NaN;
    callers pass pad=1 to plan_grid so the AOI itself survives intact.

    `resolution_y_deg` is the row spacing when it differs from the column
    spacing -- GLO-30 north of 50 deg (BUG-018). Omitted, the pixel is square.
    """
    metres_per_degree = 111320.0
    res_y = resolution_deg if resolution_y_deg is None else resolution_y_deg
    dy = res_y * metres_per_degree
    dx = resolution_deg * metres_per_degree * math.cos(math.radians(latitude))

    a, b, c = dem[:-2, :-2], dem[:-2, 1:-1], dem[:-2, 2:]
    d, f = dem[1:-1, :-2], dem[1:-1, 2:]
    g, h, i = dem[2:, :-2], dem[2:, 1:-1], dem[2:, 2:]

    dz_dx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx)
    dz_dy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * dy)

    out = np.full(dem.shape, np.nan, dtype="float32")
    out[1:-1, 1:-1] = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    return out


def classify_slope(slope: np.ndarray) -> np.ndarray:
    codes = np.zeros(slope.shape, dtype="uint8")
    for low, high, code, _ in SLOPE_CLASSES:
        codes[np.isfinite(slope) & (slope >= low) & (slope < high)] = code
    return codes


@workflow(
    "fetch_terrain_data",
    "Elevation and slope statistics for a bounding box from the Copernicus "
    "DEM GLO-30 archive. Terrain is static, so results are cached on disk.",
    label="Elevation and slope",
    source="Copernicus DEM GLO-30",
    data_source_uncertainty=(
        "GLO-30 is a surface model (buildings and canopy included) from 2011-2015 "
        "radar, with relative vertical error under 2 m on gentle slopes and under 4 "
        "m on steep ones (90 % of points). Slope is overstated at building and tree "
        "edges and understated where a canopy flattens the true ground."),
    method_uncertainty=(
        "Slope from the 30 m DEM in fixed classes over land, sea excluded; class "
        "shares near a class boundary move with small elevation errors."),
)
def fetch_terrain_data(
    bbox: list[float],
    output_dir: str = "outputs",
    event_id: int | str | None = None,
    write_rasters: bool = True,
    use_cache: bool = True,
    exclude_water: tuple[str, ...] | list[str] = landmask.DEFAULT_EXCLUDE,
) -> dict[str, Any]:
    """Slope and elevation for an AOI, over land.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.
        output_dir, event_id: where COGs are written.
        write_rasters: False computes statistics without writing any file.
        use_cache: reuse an identical previous run. The cache key is the bbox
            AND the exclusion, which is sound because the DEM itself is a fixed
            2019-2021 archive.
        exclude_water: Water Body Mask classes removed before any statistic is
            computed -- `("ocean",)` by default. A coastal bbox contains sea,
            and sea is flat and at zero elevation, so leaving it in puts the
            Indian Ocean in the "flat" slope class and drags the elevation
            distribution toward zero. Lakes and rivers are KEPT by default:
            they are part of the catchment and the Kelani's own channel is one
            of them. Pass `()` to keep everything.

    Returns elevation and slope statistics plus the slope class breakdown, all
    over the land area, with `aoi.land` naming the denominator they use.
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must be [west, south, east, north], got {bbox}")

    west, south, east, north = bbox
    # One tile per degree, and an AOI is free to cross as many edges as it
    # likes. The Kelani basin sits on a quadruple corner -- it crosses 80E and
    # 7N at once -- and needs all four surrounding tiles. Tiles are enumerated
    # from the archive's own naming function rather than reimplemented here.
    urls = R.tiles_for_bbox(bbox, tile_url, step=1.0)
    multi = len(urls) > 1

    # The exclusion is part of the key. A raster masked to land and one that
    # still holds the sea are different rasters of the same bbox, and reusing
    # one for the other is silent.
    key_text = ",".join(f"{v:.6f}" for v in bbox) + "|" + ",".join(landmask.classes(exclude_water))
    key = hashlib.sha1(key_text.encode()).hexdigest()[:12]
    prefix = f"{output_dir}/{event_id or 'terrain'}"
    slope_path = f"{prefix}/slope_{key}.tif"
    dem_path = f"{prefix}/elevation_{key}.tif"

    centre_lat = (south + north) / 2
    url = urls[0] if len(urls) == 1 else f"{len(urls)} tiles mosaicked"

    # A cache hit must return the same keys as a cold run, so slope and
    # elevation are cached together or not at all.
    cached = (use_cache and write_rasters
              and os.path.exists(slope_path) and os.path.exists(dem_path))

    if cached:
        slope, plan = _read_cached(slope_path, centre_lat)
        dem, _ = _read_cached(dem_path, centre_lat)
    elif multi:
        # pad=1 so Horn's kernel has a ring to consume without eating the AOI.
        plan = R.plan_mosaic(urls, bbox, pad=1)
        dem = R.read_band_mosaic(urls, plan)
        slope = slope_degrees(dem, centre_lat, plan.resolution, plan.resolution_y)
    else:
        plan = R.plan_grid(urls[0], bbox, pad=1)
        dem = R.read_band(urls[0], plan)
        slope = slope_degrees(dem, centre_lat, plan.resolution, plan.resolution_y)

    # Slope is computed on the FULL DEM and masked afterwards, never the other
    # way round. Masking first would strip the sea from a coastal pixel's 3x3
    # neighbourhood and return NaN for the one place the land-to-sea gradient
    # is real; computing first and masking second keeps that pixel's slope and
    # still reports nothing about the water itself.
    keep, land = landmask.land_mask(bbox, plan, exclude=exclude_water)
    slope = np.where(keep, slope, np.nan)
    dem = np.where(keep, dem, np.nan)

    codes = classify_slope(slope)
    # classify_slope leaves NaN unclassified, which class_fractions already
    # counts separately -- so the excluded sea lands in `unclassified_pixels`
    # rather than in a class, and every fraction below is of the LAND.
    legend = {code: label for _, _, code, label in SLOPE_CLASSES}

    result: dict[str, Any] = {
        # `area_km2` is THE REPORTING DOMAIN and it is the land, matching
        # fetch_lulc_data and fetch_permanent_water exactly. Three payloads
        # feeding one brief must not mean three different things by the same
        # key -- that is the denominator confusion this mask exists to end,
        # reintroduced one level up. The bounding box is `bbox_area_km2`.
        "aoi": {"bbox": bbox, "land": land,
                "area_km2": land["land_area_km2"],
                "land_area_km2": land["land_area_km2"],
                "bbox_area_km2": land["aoi_area_km2"]},
        "grid": plan.as_dict(),
        "slope": {
            "unit": "degrees",
            "method": "Horn 1981, 3x3, geographic spacing scaled by cos(lat)",
            "stats": R.summarise(slope, value_range=(0.0, 90.0), bins=18),
            "classes": R.class_fractions(codes, legend, plan),
        },
        "source": {"dataset": "Copernicus DEM GLO-30", "url": url,
                   "tiles": urls, "mosaicked": multi, "cached": cached},
    }

    result["elevation"] = {
        "unit": "m",
        "stats": R.summarise(
            dem, value_range=(float(np.nanmin(dem)), float(np.nanmax(dem))),
            bins=18),
    }

    if write_rasters:
        result["slope"]["raster"] = R.write_float(slope_path, slope, plan)
        result["slope"]["classes"]["raster"] = R.write_classes(
            f"{prefix}/slope_class.tif", codes, plan)
        result["elevation"]["raster"] = R.write_float(dem_path, dem, plan)

    return result


def _read_cached(path: str, centre_lat: float) -> tuple[np.ndarray, R.GridPlan]:
    """Read a previously written terrain COG back onto its own grid."""
    import rasterio

    with R.env(), rasterio.open(path) as src:
        arr = src.read(1).astype("float32")
        arr[arr == src.nodata] = np.nan
        res, res_y = abs(src.transform.a), abs(src.transform.e)
        plan = R.GridPlan(
            None, src.transform, src.crs, (src.height, src.width), res,
            resolution_y=res_y,
            pixel_area_km2=R.geographic_pixel_km2(res, res_y, centre_lat),
            units="deg" if src.crs.is_geographic else "m")
    return arr, plan
