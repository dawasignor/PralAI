"""Step 2: the evaluation domain -- where reference and products both mean something.

    domain = AOI
             minus the sea                (Water Body Mask)
             minus JRC permanent water    (plan step 2, from both maps)
             minus reference exclusions   (e.g. UNOSAT's cloud-obstruction layer)
             (intersected with the reference's analysed extent, when it has one)

**The UNOSAT FL20251128LKA flood layer ships without an analysed extent**, so
inside the AOI "UNOSAT did not map flood here" and "UNOSAT did not look here"
cannot be told apart; both count as non-flood. The payload says so in
`assumptions`, and Entro reads it there. When a reference does carry an
analysed extent, `reference.analysed_extent_layer` names it and it is applied.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .metrics import FLOOD, NODATA, NONFLOOD


def _aoi_mask(bbox: list[float], grid: dict[str, Any]) -> np.ndarray:
    """The WGS84 rectangle on the projected grid: its edges are densified,
    because a straight line in degrees is not straight in UTM."""
    from rasterio.features import rasterize
    from rasterio.warp import transform

    west, south, east, north = bbox
    steps = np.linspace(0.0, 1.0, 64)
    lons = np.concatenate([west + (east - west) * steps, np.full(64, east),
                           east - (east - west) * steps, np.full(64, west)])
    lats = np.concatenate([np.full(64, south), south + (north - south) * steps,
                           np.full(64, north), north - (north - south) * steps])
    xs, ys = transform("EPSG:4326", grid["crs"], lons.tolist(), lats.tolist())
    ring = list(zip(xs, ys)) + [(xs[0], ys[0])]
    return rasterize([({"type": "Polygon", "coordinates": [ring]}, 1)],
                     out_shape=(grid["height"], grid["width"]),
                     transform=grid["transform"], fill=0, dtype="uint8") == 1


def _sea(bbox: list[float], grid: dict[str, Any], exclude: list[str]) -> np.ndarray:
    from .. import _raster as R, landmask

    plan = R.GridPlan(None, grid["transform"], grid["crs"],
                      (grid["height"], grid["width"]), grid["resolution"])
    codes = landmask.water_codes(list(bbox), plan)
    drop = [code for code, name in landmask.WBM_CLASSES.items() if name in exclude]
    return np.isin(codes, drop)


def _permanent_water(cfg: dict[str, Any], grid: dict[str, Any]) -> tuple[np.ndarray, dict]:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    from .config import resolve

    spec = (cfg.get("domain") or {}).get("permanent_water") or {}
    path = resolve(cfg, spec.get("raster"))
    if path is None or not path.is_file():
        raise ValueError("domain.permanent_water.raster is not set or missing; the plan "
                         "removes JRC permanent water from both maps, so it is required. "
                         "fetch_permanent_water writes it as water_class.tif")
    classes = spec.get("classes")
    if not classes:
        raise ValueError("domain.permanent_water.classes is not set (JRC water_class: "
                         "3 = permanent, >= 90% occurrence)")
    out = np.zeros((grid["height"], grid["width"]), dtype="uint8")
    with rasterio.open(path) as src:
        reproject(source=rasterio.band(src, 1), destination=out,
                  dst_transform=grid["transform"], dst_crs=grid["crs"],
                  resampling=Resampling.nearest, dst_nodata=0)
    mask = np.isin(out, classes)
    return mask, {"raster": str(path), "classes": list(classes)}


def build(cfg: dict[str, Any], grid: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """The domain mask, the reference restricted to it (1/0/255), and the census."""
    bbox = cfg["aoi"]["bbox"]
    pixel_km2 = (grid["resolution"] ** 2) / 1e6
    aoi = _aoi_mask(bbox, grid)

    exclude = (cfg.get("domain") or {}).get("exclude_wbm_classes", ["ocean"])
    try:
        sea = _sea(bbox, grid, exclude) if exclude else np.zeros_like(aoi)
        sea_note = f"Water Body Mask classes {exclude} removed"
    except Exception as exc:                                   # noqa: BLE001
        # The sea is excluded from every AOI statistic in this repo. Without
        # the mask the domain would include it, and the run is refused rather
        # than reported over sea.
        raise RuntimeError(f"the sea mask could not be built ({type(exc).__name__}: "
                           f"{exc}); refusing to evaluate over the sea") from exc
    permanent, permanent_meta = _permanent_water(cfg, grid)
    excluded = reference["excluded"]
    # The reference's analysed extent. Documented here since the start and
    # never applied until 2026-11-18 (found while configuring EMSR851; UNOSAT
    # has none, so Kelani was unaffected). Without it, an AOI larger than the
    # reference -- Gampola, by the user's choice -- counts every pixel the
    # reference never looked at as "not flooded".
    analysed = reference.get("analysed")
    outside = ~analysed if analysed is not None else np.zeros_like(aoi)

    domain = aoi & ~sea & ~permanent & ~excluded & ~outside
    if not domain.any():
        raise ValueError("the evaluation domain is empty after the exclusions")

    ref = np.full(domain.shape, NODATA, dtype="uint8")
    ref[domain] = np.where(reference["flood"][domain], FLOOD, NONFLOOD)

    def km2(mask: np.ndarray) -> float:
        return round(float(np.count_nonzero(mask)) * pixel_km2, 3)

    return {
        "domain": domain,
        "reference": ref,
        "census": {
            "grid": {"crs": grid["crs"], "resolution_m": grid["resolution"],
                     "shape": [grid["height"], grid["width"]]},
            "aoi_km2": km2(aoi),
            "sea_removed_km2": km2(aoi & sea),
            "permanent_water_removed_km2": km2(aoi & ~sea & permanent),
            "reference_exclusions_removed_km2": km2(aoi & ~sea & ~permanent & excluded),
            "outside_reference_analysed_km2": km2(aoi & ~sea & ~permanent & ~excluded & outside),
            "domain_km2": km2(domain),
            "reference_flood_km2": km2(domain & reference["flood"]),
            "reference_flood_share": round(float(np.count_nonzero(domain & reference["flood"]))
                                           / float(np.count_nonzero(domain)), 6),
            "sea": sea_note,
            "permanent_water": permanent_meta,
        },
    }
