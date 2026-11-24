"""Permanent water from the JRC Global Surface Water dataset.

Permanent water is a *baseline*, and a baseline has to be independent of the
event being studied. Deriving it from the same Sentinel-2 scenes that measure
the flood would be circular: over a 90-day monsoon lookback, "water in every
observation we managed to get" describes the flood, not the river.

So it comes from JRC Global Surface Water instead -- 36 years of Landsat,
30 m, and the `occurrence` band gives the percentage of valid observations in
which each pixel was water. That is the actual definition of permanent water,
it is fixed, and it returns the same answer on every run.

Served directly from the JRC's own Google Cloud Storage bucket over plain
HTTPS with range requests. No account and no asset signing.
Reference in docs/SOURCES.md.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import workflow
from . import _raster as R
from . import landmask

GSW_BUCKET = "https://storage.googleapis.com/global-surface-water/downloads2021"
GSW_VERSION = "v1_4_2021"

# occurrence is 0-100 (% of valid observations that were water). 255 is fill.
GSW_FILL = 255

# Pekel et al. treat >= 90% occurrence as permanent. 25-75% is the seasonal
# band; below 25 is ephemeral. These are the paper's own breakpoints, not ours.
PERMANENT_THRESHOLD = 90.0
SEASONAL_THRESHOLD = 25.0


def tile_url(latitude: float, longitude: float, band: str = "occurrence") -> str:
    """URL of the 10-degree GSW tile containing a point.

    Tiles are named for their NORTH-WEST corner, which is why longitude floors
    and latitude ceilings: occurrence_90E_30Nv1_4_2021.tif covers 90-100E,
    20-30N.
    """
    left = math.floor(longitude / 10) * 10
    top = math.ceil(latitude / 10) * 10
    ew = f"{abs(left)}{'E' if left >= 0 else 'W'}"
    ns = f"{abs(top)}{'N' if top >= 0 else 'S'}"
    return f"{GSW_BUCKET}/{band}/{band}_{ew}_{ns}{GSW_VERSION}.tif"


@workflow(
    "fetch_permanent_water",
    "Permanent and seasonal surface water extent for a bounding box from the "
    "JRC Global Surface Water occurrence layer (1984-2021, 30 m).",
    label="JRC water bodies",
    source="JRC Global Surface Water",
    data_source_uncertainty=(
        "Water frequency from 36 years of Landsat (1984-2021) at 30 m: it misses "
        "rivers narrower than about 30 m and any change after 2021, and cloudy "
        "monsoon seasons give it fewer looks."),
    method_uncertainty=(
        "Permanent means water in at least 90 % of observations and seasonal 25-90 "
        "% (Pekel et al.), with the sea removed. Both thresholds are conventions, "
        "so the baseline shifts if they shift."),
)
def fetch_permanent_water(
    bbox: list[float],
    permanent_threshold: float = PERMANENT_THRESHOLD,
    seasonal_threshold: float = SEASONAL_THRESHOLD,
    output_dir: str = "outputs",
    event_id: int | str | None = None,
    write_rasters: bool = True,
    exclude_water: tuple[str, ...] | list[str] = landmask.DEFAULT_EXCLUDE,
) -> dict[str, Any]:
    """Permanent INLAND water baseline for an AOI.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.
        permanent_threshold: occurrence % at or above which a pixel counts as
            permanent water. Default 90, following Pekel et al.
        seasonal_threshold: lower bound of the seasonal band. Default 25.
        output_dir, event_id: where COGs are written.
        write_rasters: False computes statistics without writing any file.
        exclude_water: Water Body Mask classes removed before anything is
            counted -- `("ocean",)` by default, and this workflow is the one
            that needs it most. JRC surface water records the sea as water
            occurring 100% of the time, so on a coastal AOI the sea would be
            counted as "permanent water" and a flood-baseline figure meant to
            say "this much of the catchment is normally wet" would instead be
            reporting the Indian Ocean. Lakes and rivers are KEPT: they are
            the baseline.

    Returns areas and fractions for permanent, seasonal and ephemeral water
    over LAND, plus the occurrence statistics over water pixels only.
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must be [west, south, east, north], got {bbox}")

    west, south, east, north = bbox
    # 10-degree tiles, and an AOI on a boundary needs both sides. The Kelani
    # basin crosses 80E, so it spans the 70E and 80E tiles.
    urls = R.tiles_for_bbox(bbox, tile_url, step=10.0)
    multi = len(urls) > 1
    url = urls[0] if not multi else f"{len(urls)} tiles mosaicked"

    if multi:
        plan = R.plan_mosaic(urls, bbox)
        occurrence = R.read_band_mosaic(urls, plan, nodata=GSW_FILL)
    else:
        plan = R.plan_grid(urls[0], bbox)
        occurrence = R.read_band(urls[0], plan, nodata=GSW_FILL)

    # The reporting domain is the land, not the bounding box. Everything below
    # is counted inside `keep`, so the sea is neither water nor dry land -- it
    # is simply not part of what this workflow describes.
    keep, land = landmask.land_mask(bbox, plan, exclude=exclude_water)

    observed = np.isfinite(occurrence) & keep
    permanent = observed & (occurrence >= permanent_threshold)
    seasonal = observed & (occurrence >= seasonal_threshold) & ~permanent
    ephemeral = observed & (occurrence > 0) & ~permanent & ~seasonal

    # THE DENOMINATOR, and it is the land area -- not `occurrence.size`.
    total = int(keep.sum())
    area = plan.pixel_area_km2

    def band(mask: np.ndarray) -> dict[str, Any]:
        n = int(mask.sum())
        return {"pixels": n,
                "area_km2": round(n * area, 4),
                # Named for what it is a fraction OF. It used to be
                # `aoi_fraction` and was a fraction of the bounding box; on a
                # coastal AOI those differ by the whole sea, and a renamed
                # field is louder than a footnote.
                "land_fraction": round(n / total, 6) if total else 0.0}

    # Statistics over water pixels only -- the AOI mean occurrence is
    # meaningless when most of the AOI is dry land sitting at zero.
    water_only = np.where(keep & (occurrence > 0), occurrence, np.nan)

    result: dict[str, Any] = {
        "aoi": {"bbox": bbox,
                # `area_km2` is the reporting domain, which is now the land.
                # The bounding box area is `land.aoi_area_km2`, kept beside it
                # so the two can never be confused for one another.
                "area_km2": round(total * area, 4),
                "land_area_km2": round(total * area, 4),
                "bbox_area_km2": land["aoi_area_km2"],
                "land": land},
        "grid": plan.as_dict(),
        "thresholds": {"permanent": permanent_threshold,
                       "seasonal": seasonal_threshold, "unit": "% occurrence"},
        "water": {
            "permanent": band(permanent),
            "seasonal": band(seasonal),
            "ephemeral": band(ephemeral),
            "never_water": band(observed & (occurrence == 0)),
            "unobserved": band(keep & ~np.isfinite(occurrence)),
            "fractions_are_of": "land_area_km2 (the AOI minus the excluded "
                                "water classes), not the bounding box",
        },
        "occurrence_stats_over_water": R.summarise(
            water_only, value_range=(0.0, 100.0), bins=20),
        "source": {"dataset": "JRC Global Surface Water v1.4 (2021)",
                   "band": "occurrence", "url": url,
                   # Which tiles the answer came from, and whether it was
                   # pasted. A mosaicked statistic is a different provenance
                   # claim from a single-tile one and a thesis has to be able
                   # to say which it is.
                   "tiles": urls, "mosaicked": multi},
    }

    if write_rasters:
        prefix = f"{output_dir}/{event_id or 'water'}"
        # occurrence is a percentage, so it is written as-is rather than
        # through write_index -- there is no [-1, 1] scaling to apply.
        codes = np.zeros(occurrence.shape, dtype="uint8")
        codes[ephemeral] = 1
        codes[seasonal] = 2
        codes[permanent] = 3
        result["water"]["raster"] = R.write_classes(
            f"{prefix}/water_class.tif", codes, plan)
        result["water"]["legend"] = {"1": "ephemeral", "2": "seasonal",
                                     "3": "permanent"}

    return result
