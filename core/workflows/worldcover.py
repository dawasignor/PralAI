"""ESA WorldCover 2021 v200: the land-cover fallback.

Sentinel-2 (`fetch_lulc_data`) stays first. It measures the land in the weeks
before the event, which is what the MCDM needs, but over a monsoon AOI it can
fail its 0.7 valid-fraction threshold: over `kelani-lower-2025` the best
pre-event scene is 0.6076 clear land. The threshold is not
lowered. WorldCover is the fallback for that case, and only for that case.

**What it is and what it is not.** A global 10 m map from Sentinel-1 and
Sentinel-2, eleven classes, 76.7 +/- 0.5% overall accuracy (the validation
report, docs/SOURCES.md). It is spatially accurate, cloud-free by
construction, and **one snapshot of 2021**: it does not describe the land in
the weeks before the event, so it does not address temporal relevance. That is
recorded as an input uncertainty on every payload, never left for a reader to
infer.

Its classes are not the Sentinel-2 indices. Tree cover (class 10) is not
"dense NDVI", and built-up (class 50) is not "BUI > 0.2"; the fallback in
`mcdm.indices_from_payloads` says so where it substitutes one for the other.

Served from ESA's public bucket over plain HTTPS with range requests: no
account, no signing. 3x3 degree COG tiles named for their south-west corner,
EPSG:4326, pixel-is-area on the whole degree -- the same lattice discipline as
the DEM, so a multi-tile AOI is mosaicked by `_raster.plan_mosaic`.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import numpy as np

from . import tool
from . import _raster as R
from . import landmask

WC_BUCKET = "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
WC_VERSION = "v200"
WC_YEAR = 2021
TILE_DEG = 3.0
NODATA = 0

# The map's own legend (read from the tile tags, product_version V2.0.0).
LEGEND: dict[int, str] = {
    10: "tree_cover", 20: "shrubland", 30: "grassland", 40: "cropland",
    50: "built_up", 60: "bare_sparse_vegetation", 70: "snow_and_ice",
    80: "permanent_water_bodies", 90: "herbaceous_wetland", 95: "mangroves",
    100: "moss_and_lichen",
}

OVERALL_ACCURACY = (76.7, 0.5)          # %, +/-; PVR v2.0 Table 2, global

# Per class, global: (user's, +/-, producer's, +/-) in %. Product Validation
# Report v2.0 (2022-10-25), Table 2, p. 15. Copied, never derived.
ACCURACY: dict[int, tuple[float, float, float, float]] = {
    10: (80.0, 0.7, 91.9, 0.5),
    20: (49.1, 2.1, 46.9, 2.3),
    30: (71.9, 1.0, 66.7, 1.1),
    40: (80.6, 1.5, 79.3, 1.5),
    50: (65.9, 3.3, 73.2, 2.6),
    60: (92.1, 0.9, 82.5, 1.2),
    70: (93.0, 2.4, 99.1, 0.4),
    80: (89.4, 1.8, 86.4, 1.7),
    90: (30.5, 4.3, 44.6, 5.4),
    95: (74.1, 13.2, 46.2, 16.0),
    100: (57.5, 3.8, 46.4, 3.5),
}

DATASET_ID = "esa-worldcover-2021-v200"
SOURCES = [
    {"id": DATASET_ID, "title": "ESA WorldCover 10 m 2021 v200",
     "publisher": "ESA WorldCover consortium (Zanaga et al. 2022)",
     "url": "https://doi.org/10.5281/zenodo.7254221", "vintage": "2021",
     "retrieved_at": "", "licence": "CC BY 4.0", "role": "major"},
    {"id": "esa-worldcover-pvr-v2", "title":
     "WorldCover Product Validation Report (D12-PVR) v2.0",
     "publisher": "ESA WorldCover consortium (Tsendbazar et al. 2022)",
     "url": f"{WC_BUCKET}/v200/2021/docs/WorldCover_PVR_V2.0.pdf",
     "vintage": "2022-10-25", "retrieved_at": "", "licence": "",
     "role": "supporting"},
]


def tile_url(latitude: float, longitude: float) -> str:
    """URL of the 3-degree tile containing a point. Named for the SOUTH-WEST
    corner: N06E078 covers 6-9N, 78-81E."""
    south = math.floor(latitude / TILE_DEG) * int(TILE_DEG)
    west = math.floor(longitude / TILE_DEG) * int(TILE_DEG)
    ns = f"{'N' if south >= 0 else 'S'}{abs(south):02d}"
    ew = f"{'E' if west >= 0 else 'W'}{abs(west):03d}"
    return (f"{WC_BUCKET}/{WC_VERSION}/{WC_YEAR}/map/"
            f"ESA_WorldCover_10m_{WC_YEAR}_{WC_VERSION}_{ns}{ew}_Map.tif")


def _tags(href: str) -> dict[str, str]:
    """The tile's own metadata -- the vintage is read, not assumed."""
    import rasterio

    with R.env(), rasterio.open(href) as src:
        return dict(src.tags())


def _item(key: str, statement: str, value: Any, unit: str, basis: str,
          vintage: str = "") -> dict[str, Any]:
    return {"key": key, "statement": statement, "value": value, "unit": unit,
            "basis": basis, "vintage": vintage, "vault_note": ""}


@tool(
    "fetch_worldcover",
    "ESA WorldCover 2021 (10 m, eleven classes) for a bounding box: the share "
    "and area of each land-cover class over LAND only (sea excluded), with "
    "the published per-class accuracy. The land-cover FALLBACK, for when "
    "Sentinel-2 land cover (fetch_lulc_data) fails its cloud threshold. It is "
    "a 2021 snapshot, not the weeks before the event.",
    label="Land cover (WorldCover 2021)",
    source="ESA WorldCover 2021 v200",
    order=25,
    data_source_uncertainty=(
        "A single 2021 map with 76.7 % overall accuracy; built-up, the weakest "
        "class that matters here, has 65.9 % user's accuracy, and the map says "
        "nothing about land in the weeks before the event."),
    method_uncertainty=(
        "Class shares over land at the native 10 m, with no resampling. Standing in "
        "for Sentinel-2 in the ranking, its tree-cover and built-up classes are not "
        "the same thing as the NDVI and BUI thresholds."),
)
def fetch_worldcover(
    bbox: list[float],
    output_dir: str = "outputs",
    event_id: int | str | None = None,
    write_rasters: bool = True,
    exclude_water: tuple[str, ...] | list[str] = landmask.DEFAULT_EXCLUDE,
) -> dict[str, Any]:
    """WorldCover class shares over the land of an AOI.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.
        output_dir, event_id: where the class raster is written.
        write_rasters: False computes statistics without writing any file.
        exclude_water: Water Body Mask classes removed before anything is
            counted -- the ocean by default. WorldCover's
            own "permanent water bodies" class is KEPT: a river or a lake is
            part of the catchment.

    Fractions are of classified LAND pixels; `coverage.valid_fraction` says
    what share of the land that is. Read at the map's native 10 m with
    nearest-neighbour, so no class is invented and nothing is resampled.
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must be [west, south, east, north], got {bbox}")
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    urls = R.tiles_for_bbox(bbox, tile_url, step=TILE_DEG)
    multi = len(urls) > 1
    if multi:
        plan = R.plan_mosaic(urls, bbox)
        codes = R.read_classes_mosaic(urls, plan, fill=NODATA)
    else:
        plan = R.plan_grid(urls[0], bbox)
        codes = R.read_classes(urls[0], plan)
    codes = codes.astype("uint8")
    tags = _tags(urls[0])

    keep, land = landmask.land_mask(bbox, plan, exclude=exclude_water)
    land_px = int(keep.sum())
    if land_px == 0:
        raise ValueError(
            f"every pixel of {bbox} is excluded by exclude_water="
            f"{list(landmask.classes(exclude_water))}; there is no land here to describe")

    # Outside the land nothing is counted: the sea becomes nodata, so the
    # class shares below are shares of the land and nothing else.
    on_land = np.where(keep, codes, NODATA).astype("uint8")
    classes = R.class_fractions(on_land, LEGEND, plan, nodata=NODATA)
    classified = int(classes["classified_pixels"])
    valid_fraction = round(classified / land_px, 6)
    classes["fractions_are_of"] = ("classified LAND pixels (the AOI minus the "
                                   "excluded water classes, minus nodata)")
    unknown_codes = sorted(int(c) for c in np.unique(on_land)
                           if int(c) != NODATA and int(c) not in LEGEND)
    if unknown_codes:
        # A code the legend does not name would be counted as nothing and the
        # fractions would not sum to one. Refuse rather than drop it quietly.
        raise ValueError(f"WorldCover returned codes {unknown_codes} that are "
                         f"not in its published legend")

    present = [code for code in LEGEND
               if classes["fractions"][LEGEND[code]] > 0]
    vintage = f"{tags.get('time_start', '')[:10]}/{tags.get('time_end', '')[:10]}"

    uncertainty: dict[str, list[dict[str, Any]]] = {
        "input": [
            _item("snapshot_year",
                  f"WorldCover is one snapshot of {WC_YEAR} ({vintage}). It "
                  f"does not describe the land in the weeks before the event, "
                  f"so temporal relevance is not addressed.",
                  WC_YEAR, "year", "stated_by_source", vintage),
            _item("overall_accuracy",
                  f"Global overall accuracy {OVERALL_ACCURACY[0]} +/- "
                  f"{OVERALL_ACCURACY[1]}% (validation report v2.0, Table 2).",
                  OVERALL_ACCURACY[0], "%", "stated_by_source", "2022-10-25"),
            *[_item(f"accuracy_{LEGEND[code]}",
                    f"{LEGEND[code]}: user's accuracy {ACCURACY[code][0]} +/- "
                    f"{ACCURACY[code][1]}%, producer's {ACCURACY[code][2]} +/- "
                    f"{ACCURACY[code][3]}% (global, validation report v2.0, "
                    f"Table 2). Not measured for this AOI.",
                    ACCURACY[code][0], "% user's accuracy", "stated_by_source",
                    "2022-10-25")
              for code in present],
        ],
        "tool": [
            _item("valid_fraction",
                  f"{valid_fraction} of the land has a WorldCover class; the "
                  f"rest is nodata and is not counted.",
                  valid_fraction, "fraction of land", "measured"),
            _item("coverage", f"The tiles cover {round(plan.coverage, 4)} of "
                  f"the AOI ({len(urls)} tile(s)"
                  f"{', mosaicked' if multi else ''}).",
                  round(plan.coverage, 4), "fraction of AOI", "measured"),
            _item("sea_excluded",
                  f"{land['excluded_km2']} km2 excluded as "
                  f"{', '.join(land['excluded_classes']) or 'nothing'} by the "
                  f"Copernicus DEM Water Body Mask; shares are of "
                  f"{land['land_area_km2']} km2 of land.",
                  land["excluded_km2"], "km2", "measured"),
            _item("coastline_resampling",
                  "The 30 m Water Body Mask is nearest-neighbour resampled "
                  "onto the 10 m grid; the coastline is accurate to about one "
                  "30 m pixel. The WorldCover classes themselves are read at "
                  "native resolution, not resampled.",
                  30, "m", "assumed"),
        ],
        "pipeline": [],
        "decision": [],
    }

    result: dict[str, Any] = {
        "aoi": {"bbox": bbox,
                "area_km2": land["land_area_km2"],
                "land_area_km2": land["land_area_km2"],
                "bbox_area_km2": land["aoi_area_km2"],
                "land": land},
        "grid": plan.as_dict(),
        "classes": classes,
        "coverage": {"valid_fraction": valid_fraction,
                     "valid_fraction_is_of": "land",
                     "aoi_coverage": round(plan.coverage, 4)},
        "accuracy": {
            "overall_pct": OVERALL_ACCURACY[0],
            "overall_ci_pct": OVERALL_ACCURACY[1],
            "per_class": {LEGEND[c]: {"users_pct": a[0], "users_ci_pct": a[1],
                                      "producers_pct": a[2],
                                      "producers_ci_pct": a[3]}
                          for c, a in ACCURACY.items()},
            "scope": "global, from the validation report; not measured for "
                     "this AOI",
        },
        "source": {"dataset": f"ESA WorldCover 10 m {WC_YEAR} {WC_VERSION}",
                   "product_version": tags.get("product_version", ""),
                   "time_start": tags.get("time_start", ""),
                   "time_end": tags.get("time_end", ""),
                   "licence": "CC BY 4.0",
                   "tiles": urls, "mosaicked": multi,
                   "retrieved_at": retrieved_at},
        "uncertainty": uncertainty,
        "sources": [{**s, "retrieved_at": retrieved_at} for s in SOURCES],
    }

    if write_rasters:
        prefix = f"{output_dir}/{event_id or 'worldcover'}"
        result["classes"]["raster"] = R.write_classes(
            f"{prefix}/worldcover_class.tif", on_land, plan)
        result["classes"]["raster_legend"] = {str(k): v for k, v in LEGEND.items()}

    return result
