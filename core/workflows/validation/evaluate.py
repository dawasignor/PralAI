"""Step 4: compare on each product's native grid.

1. The reference is aggregated to the product's own pixels: for each product
   pixel, the share of its in-domain area the reference calls flood. A pixel is
   reference flood when that share exceeds `evaluation.flood_fraction_threshold`
   (default 0.5).
2. A product pixel belongs to the domain when more than
   `evaluation.domain_fraction_threshold` of its area is inside the 10 m domain
   -- a 20 m pixel half on the sea is not compared as if it were all land.
3. Pixels the product has no data for are left out, and **coverage** -- the
   share of in-domain product pixels with valid product data -- travels with
   every metric. An F1 over 30% of the domain is not the F1 of the domain.

The aggregation is `Resampling.average` over the 10 m reference with the
outside of the domain as NaN, so the fraction is of the domain part of each
pixel only.

**The threshold is strict ("exceeds"), as the plan says.** On a product grid
that happens to align with the 10 m reference lattice, a pixel straddling a
flood edge holds exactly half flood and is called non-flood, which biases the
reference's flood area down at every edge. A product grid that does not align
(GFM's Equi7 against UTM) has continuous fractions and almost no exact ties.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .metrics import FLOOD, NODATA, NONFLOOD, evaluate


def _average_to(values: np.ndarray, grid: dict[str, Any], product: dict[str, Any]) -> np.ndarray:
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    out = np.full(product["shape"], np.nan, dtype="float32")
    reproject(source=values, destination=out,
              src_transform=grid["transform"], src_crs=grid["crs"],
              dst_transform=product["transform"], dst_crs=product["crs"],
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
    return out


def on_native_grid(product: dict[str, Any], grid: dict[str, Any],
                   domain: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    thresholds = cfg["evaluation"]
    flood_thr = float(thresholds["flood_fraction_threshold"])
    domain_thr = float(thresholds["domain_fraction_threshold"])

    ref10 = domain["reference"]
    flood_src = np.where(ref10 == FLOOD, 1.0, np.where(ref10 == NONFLOOD, 0.0, np.nan)).astype("float32")
    domain_src = domain["domain"].astype("float32")

    flood_frac = _average_to(flood_src, grid, product)
    domain_frac = _average_to(domain_src, grid, product)

    in_domain = np.isfinite(domain_frac) & (domain_frac > domain_thr) & np.isfinite(flood_frac)
    reference = np.full(product["shape"], NODATA, dtype="uint8")
    reference[in_domain] = np.where(flood_frac[in_domain] > flood_thr, FLOOD, NONFLOOD)

    values = product["array"]
    valid = in_domain & (values != NODATA)
    domain_px = int(in_domain.sum())

    # A product pixel's GROUND area, not its nominal one. GFM's Equi7 grid is
    # azimuthal equidistant, centred on 94E 47N for Asia; over Sri Lanka, some
    # 4,900 km away, a nominal 20 m pixel covers about 10% less ground. The sum
    # of the domain fractions is the domain's area in product-pixel units, and
    # the 10 m UTM grid knows that area in m2 -- their ratio is the true pixel
    # area here. The counts, and so every metric, are unaffected; the km2 are.
    nominal_m2 = abs(product["resolution"][0] * product["resolution"][1])
    covered = float(np.nansum(domain_frac))
    domain_m2 = float(np.count_nonzero(domain["domain"])) * grid["resolution"] ** 2
    pixel_m2 = domain_m2 / covered if covered else nominal_m2

    result = evaluate(values, reference, valid)
    result.update({
        "coverage": (int(valid.sum()) / domain_px) if domain_px else None,
        "domain_pixels": domain_px,
        "compared_pixels": int(valid.sum()),
        "pixel_size_m": [abs(product["resolution"][0]), abs(product["resolution"][1])],
        "pixel_ground_m2": round(pixel_m2, 2),
        "pixel_nominal_m2": round(nominal_m2, 2),
        "compared_km2": round(int(valid.sum()) * pixel_m2 / 1e6, 3),
        "product_flood_km2_in_domain": round(int((valid & (values == FLOOD)).sum()) * pixel_m2 / 1e6, 3),
        "reference_flood_km2_in_compared": round(int((valid & (reference == FLOOD)).sum()) * pixel_m2 / 1e6, 3),
        "reference_flood_km2_in_domain": round(int((in_domain & (reference == FLOOD)).sum()) * pixel_m2 / 1e6, 3),
    })
    return result
