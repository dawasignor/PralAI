"""Ruling the sea out of an AOI, so land statistics describe land.

An AOI is a rectangle and a coastline is not, so any coastal bbox contains
ocean. Ocean is not a land cover, and leaving it in silently corrupts every
statistic computed over the box: NDVI over open water sits near zero and drags
the mean down, MNDWI saturates, slope is flat by definition, elevation is zero,
and the "flat" terrain class swallows the sea. None of it is wrong arithmetic;
all of it is arithmetic about the wrong domain.

**The mask is the Copernicus DEM's own Water Body Mask.** It ships beside the
GLO-30 tiles this repo already reads, in the same bucket, on the same lattice,
at the same resolution -- so it is not a new dataset, a new dependency or a new
alignment problem. It also draws the one distinction that matters here:

    0  land          2  lake
    1  ocean         3  river

**Only `ocean` is excluded by default, and that is a judgement worth stating.**
A lake and a river are part of the catchment: the Kelani's own channel is
code 3, and dropping it would delete the river from a river-basin analysis. The
sea is not part of the catchment at any water level. `exclude` is the parameter
that says so out loud, so a caller who wants inland water gone has to ask.

**Verified against the data, not the specification.** In tile N06_00_E079_00,
code 1 is 100% of every longitude band west of 79.83 -- open Indian Ocean --
and its mean elevation is exactly 0.00 m, while code 0 appears only in the
easternmost band, where Sri Lanka's west coast is. A legend that agrees with
geography and with a second variable is a legend worth trusting.

**Reprojection happens here, and only here.** The repo has no reprojection
stack because Sentinel-2's own grids are exact multiples in one CRS. That
argument does not reach across archives: this mask is EPSG:4326 at one arc
second and a Sentinel-2 plan is UTM at 20 m, and no amount of decimation makes
those the same lattice. So the mask is warped with nearest-neighbour onto
whatever grid the caller is working on -- a categorical mask, resampled by a
method that cannot invent a class. The cost is that the coastline is accurate
to about one 30 m source pixel, which is recorded in the returned metadata
rather than assumed away.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from . import _raster as R

DEM_BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"

# Verified spatially and against elevation -- see the module docstring.
WBM_CLASSES: dict[int, str] = {0: "land", 1: "ocean", 2: "lake", 3: "river"}

# Returned where no tile covered the pixel. Kept rather than excluded: "the
# mask does not reach here" and "this is sea" are different claims, and
# throwing away ground because a mask tile is missing would be the same class
# of error this module exists to prevent.
UNKNOWN = 255

DEFAULT_EXCLUDE: tuple[str, ...] = ("ocean",)


def wbm_url(latitude: float, longitude: float) -> str:
    """Water Body Mask URL for the 1-degree tile containing a point.

    The auxiliary layers live inside the DEM tile's own folder, so this mirrors
    `terrain.tile_url` rather than reimplementing the naming.
    """
    import math

    lat = math.floor(latitude)
    lon = math.floor(longitude)
    ns = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
    ew = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
    stem = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00"
    return f"{DEM_BUCKET}/{stem}_DEM/AUXFILES/{stem}_WBM.tif"


def water_codes(bbox: list[float], plan: R.GridPlan) -> np.ndarray:
    """The Water Body Mask on `plan`'s grid, as class codes.

    Mosaicked across whatever 1-degree tiles the AOI touches, then warped onto
    the caller's grid. Nearest-neighbour throughout: a class code interpolated
    halfway between `land` and `ocean` would be `ocean`, which is not a
    rounding error but a coastline moved.
    """
    from rasterio.warp import Resampling, reproject

    urls = R.tiles_for_bbox(bbox, wbm_url, step=1.0)
    source = R.plan_mosaic(urls, bbox)
    codes = R.read_classes_mosaic(urls, source, fill=UNKNOWN)

    # Same CRS, same lattice, same shape -- nothing to warp.
    if (source.crs == plan.crs
            and source.shape == plan.shape
            and max(abs(a - b) for a, b in
                    zip(source.transform[:6], plan.transform[:6])) < 1e-12):
        return codes.astype("uint8")

    out = np.full(plan.shape, UNKNOWN, dtype="uint8")
    reproject(
        source=codes.astype("uint8"), destination=out,
        src_transform=source.transform, src_crs=source.crs,
        dst_transform=plan.transform, dst_crs=plan.crs,
        src_nodata=UNKNOWN, dst_nodata=UNKNOWN,
        resampling=Resampling.nearest)
    return out


def inside_aoi(bbox: list[float], plan: R.GridPlan, *, densify: int = 64) -> np.ndarray:
    """True for every pixel whose CENTRE lies inside the user's AOI.

    A fetch may arrive in any extent -- a padded DEM window (Horn's kernel
    needs a ring outside the AOI), a Sentinel-2 window in UTM, which is the
    envelope of the lat/lon box and so carries slivers beyond it, a tile
    clamped at its edge -- but every statistic is computed inside the AOI the
    user defined and nowhere else (user decision 2026-10-26, BUG-018). The
    bbox is densified before it is reprojected, so its edges bend as they do
    in the grid's CRS rather than being cut as straight chords.
    """
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom

    west, south, east, north = (float(v) for v in bbox)
    if plan.crs is None or getattr(plan.crs, "is_geographic", False):
        # Closed interval, compared directly: GLO-30 and the Water Body Mask
        # are pixel-is-point, so on a whole-degree AOI the edge pixels'
        # centres sit EXACTLY on the boundary, and a rasterizer drops some of
        # those edges and not others. A pixel centred on the edge is inside.
        t, eps = plan.transform, 1e-9
        lons = t.c + (np.arange(plan.shape[1]) + 0.5) * t.a
        lats = t.f + (np.arange(plan.shape[0]) + 0.5) * t.e
        cols = (lons >= west - eps) & (lons <= east + eps)
        rows = (lats >= south - eps) & (lats <= north + eps)
        return rows[:, None] & cols[None, :]
    ring = []
    for i in range(densify):
        ring.append((west + (east - west) * i / densify, south))
    for i in range(densify):
        ring.append((east, south + (north - south) * i / densify))
    for i in range(densify):
        ring.append((east - (east - west) * i / densify, north))
    for i in range(densify):
        ring.append((west, north - (north - south) * i / densify))
    ring.append(ring[0])
    geom = {"type": "Polygon", "coordinates": [ring]}
    if plan.crs and str(plan.crs).upper() not in ("EPSG:4326", "OGC:CRS84"):
        geom = transform_geom("EPSG:4326", plan.crs, geom)
    # geometry_mask is True OUTSIDE the shape; all_touched=False keeps a pixel
    # only when its centre is inside.
    return ~geometry_mask([geom], out_shape=plan.shape, transform=plan.transform,
                          all_touched=False)


def classes(exclude: Any) -> tuple[str, ...]:
    """`exclude_water` as the tuple of class names it must be.

    A model sends what it thinks the name means: `false` for Bangkok
    (2026-11-01), which crashed `land_mask` and would have switched the land
    mask off in fetch_lulc_data. The sea is out of every land statistic (user
    rule), so a boolean or null is not a choice to honour: the default stands,
    and `land_mask` records what was sent. One class name as a string is
    that class. An explicit empty list is a deliberate "exclude nothing".
    """
    if isinstance(exclude, str):
        return (exclude,)
    if exclude is None or isinstance(exclude, bool):
        return DEFAULT_EXCLUDE
    return tuple(exclude)


def land_mask(bbox: list[float], plan: R.GridPlan, *,
              exclude: tuple[str, ...] | list[str] = DEFAULT_EXCLUDE
              ) -> tuple[np.ndarray, dict[str, Any]]:
    """`(keep, report)` -- a boolean mask of the reporting domain, and its census.

    `keep` is True where a statistic may be computed. `report` is what has to
    travel with every number derived from it, because a fraction is meaningless
    without the area it is a fraction OF:

        aoi_area_km2      the whole bounding box, a geometric fact
        land_area_km2     the box minus the excluded classes -- THE DENOMINATOR
        excluded_km2      what was removed, per class
        unknown_km2       pixels no mask tile covered, which are KEPT

    **This is the denominator every land statistic is now taken against**, and
    it is not the bbox. Reporting a fraction of the bbox while computing it
    over the land is the same mistake as reporting a fraction of the AOI while
    computing it over the cloud-free part -- which this project has made twice.
    """
    # Normalised here as well as at each tool's entry (see `classes`), so the
    # report can say when a boolean or null was replaced by the default.
    sent = exclude
    exclude = classes(exclude)
    replaced = exclude is DEFAULT_EXCLUDE and (sent is None or isinstance(sent, bool))
    unknown_names = [name for name in exclude if name not in WBM_CLASSES.values()]
    if unknown_names:
        raise ValueError(
            f"cannot exclude {unknown_names}: the Water Body Mask knows only "
            f"{sorted(set(WBM_CLASSES.values()))}")

    codes = water_codes(bbox, plan)
    drop_codes = [code for code, name in WBM_CLASSES.items() if name in exclude]

    # The AOI first: a pixel outside it is no part of any statistic, land or
    # water (BUG-018). Every class count below is inside the AOI only.
    inside = inside_aoi(bbox, plan)
    keep = inside.copy()
    for code in drop_codes:
        keep &= codes != code

    pixel = plan.pixel_area_km2
    per_class = {
        name: round(float(((codes == code) & inside).sum()) * pixel, 4)
        for code, name in sorted(WBM_CLASSES.items())
    }
    unknown_px = int(((codes == UNKNOWN) & inside).sum())
    total = float(inside.sum())
    outside_px = int((~inside).sum())

    report = {
        "mask_source": "Copernicus DEM GLO-30 Water Body Mask (AUXFILES/*_WBM)",
        "mask_resolution": "1 arc second (about 30 m)",
        "classes": {str(k): v for k, v in sorted(WBM_CLASSES.items())},
        "excluded_classes": list(exclude),
        **({"exclude_water_sent": sent,
            "exclude_water_note": f"exclude_water={sent!r} is not a list of classes; "
                                  f"the default {list(DEFAULT_EXCLUDE)} was used"}
           if replaced else {}),
        "aoi_area_km2": round(total * pixel, 4),
        "land_area_km2": round(float(keep.sum()) * pixel, 4),
        "land_fraction_of_aoi": round(float(keep.sum()) / max(1.0, total), 6),
        "area_km2_by_class": per_class,
        "excluded_km2": round(float((inside & ~keep).sum()) * pixel, 4),
        # What the fetch read beyond the AOI (padding ring, UTM envelope
        # slivers, tile snapping) and dropped before any statistic.
        "outside_aoi_px": outside_px,
        "clip": "pixel centre inside the user's AOI (bbox densified into the grid CRS)",
        "unknown_px": unknown_px,
        "unknown_km2": round(unknown_px * pixel, 4),
        # Said plainly, because the alternative is a reader assuming either way.
        "unknown_policy": ("pixels no mask tile covered are KEPT -- 'the mask "
                           "does not reach here' is not 'this is sea'"),
        "coastline_accuracy": ("the mask is 30 m data; on a finer grid its "
                               "coastline is nearest-neighbour resampled and "
                               "accurate to about one 30 m pixel"),
    }
    return keep, report
