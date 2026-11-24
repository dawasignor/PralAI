"""get_flood_map: a flood map for any AOI, without a validation config (BUG-021).

    python tests/test_flood_map.py        # no model, no network, no cost

Covers the product catalogue (ids and ranking labels), OPERA through the
Earthdata login (BUG-029: the downloaded files are what the extent is read
from; a refused login is an honest NOT KNOWN), the overlay layer
(flood red, unobserved land dark grey, sea and the outside of the AOI left to the
tiles), and that the pipelines call get_flood_map, not the validation path.
The live fetch is checked by the Edinburgh and Kelani reruns, not here.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                             # noqa: E402
import rasterio                                                # noqa: E402
from rasterio.transform import from_origin                     # noqa: E402

from core.workflows import flood_map as FM                     # noqa: E402
from core.workflows.validation import domain                   # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
BBOX = [79.94, 6.905, 80.09, 6.978]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def test_catalogue() -> None:
    check("every ranking label maps to a catalogue id",
          all(v in FM.PRODUCTS for v in FM.BY_LABEL.values()), str(FM.BY_LABEL))
    check("GFM is read from the EODC STAC API with its exclusion mask",
          FM.PRODUCTS["gfm"]["source"] == "stac"
          and FM.PRODUCTS["gfm"]["nodata_masks"][0]["asset"] == "exclusion_mask")
    check("MODIS and VIIRS come from GIBS as 2-Day composites",
          all(FM.PRODUCTS[k]["source"] == "gibs" and FM.PRODUCTS[k]["layer"].endswith("2-Day")
              for k in ("mcdwd", "vcdwd")))
    try:
        FM.get_flood_map("sentinel-3", BBOX, "2025-11-24", "2025-12-01")
        check("an unknown product is refused", False)
    except ValueError as exc:
        check("an unknown product is refused", "not in the catalogue" in str(exc), str(exc)[:80])


def test_opera(tmp: Path) -> None:
    from core.workflows import impact, opera

    water = tmp / "water_class.tif"
    with rasterio.open(water, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(79.9, 7.0, 0.05, 0.05)) as dst:
        dst.write(np.zeros((4, 4), "uint8"), 1)
    saved = opera.fetch, impact.extent_from_config

    def refused(*a, **k):
        raise opera.LoginRefused("HTTP 401 for x.tif: the Earthdata login was not accepted.")

    opera.fetch = refused
    try:
        out = FM.get_flood_map("OPERA DSWx-S1", BBOX, "2025-11-24", "2025-12-01",
                               output_dir=str(tmp), event_id="t", permanent_water_raster=str(water))
    finally:
        opera.fetch = saved[0]
    check("OPERA (by its ranking label) with a refused login is NOT KNOWN, not another product",
          out["status"] == "not_known" and out["product"] == "dswx_s1", out.get("status"))
    check("... and says the Earthdata login was not accepted",
          "Earthdata login was not accepted" in (out.get("reason") or ""), out.get("reason", ""))
    check("... and draws no overlay from nothing", "figure" not in out)
    check("the config is recorded as built from the call",
          out.get("config", {}).get("catalogue_id") == "dswx_s1")

    seen: dict = {}
    opera.fetch = lambda key, bbox, start, end, folder, s=None: {
        "files": [{"path": str(tmp / "a_B01_WTR.tif"), "datetime": "2025-11-25T05:01:41Z",
                   "granule": "g", "tile": "44NLN", "status": "downloaded"}],
        "superseded": ["old"], "no_wtr_link": [], "credential": "EARTHDATA_TOKEN"}

    def capture(cfg, key, **kw):
        seen.update(cfg=cfg, key=key)
        return {"status": "not_known", "reason": "captured", "product": key, "label": "x"}

    # The tool imports extent_from_config at call time, from impact.
    impact.extent_from_config = capture
    try:
        out = FM.get_flood_map("dswx_hls", BBOX, "2025-11-24", "2025-12-01", output_dir=str(tmp),
                               event_id="t2", permanent_water_raster=str(water), write_figure=False)
    finally:
        opera.fetch, impact.extent_from_config = saved
    spec = (seen.get("cfg") or {}).get("products", {}).get("dswx_hls", {})
    check("with the login, the downloaded water layers are what the extent is read from",
          spec.get("source") == "files" and spec["files"][0]["path"].endswith("a_B01_WTR.tif")
          and spec["files"][0]["datetime"] == "2025-11-25T05:01:41Z", str(spec)[:160])
    check("a superseded reprocessing is noted, not silently dropped",
          any("superseded" in n for n in out.get("notes") or []), str(out.get("notes")))


def test_overlay(tmp: Path) -> None:
    # A 10 x 10 extent on a UTM grid: a flooded block, an unobserved block.
    tif = tmp / "flood_extent_gfm.tif"
    codes = np.zeros((10, 10), "uint8")
    codes[2:5, 2:5] = 1
    codes[6:9, 6:9] = 255
    with rasterio.open(tif, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8",
                       crs="EPSG:32644", transform=from_origin(386000, 768000, 100, 100),
                       nodata=255) as dst:
        dst.write(codes, 1)
    from rasterio.warp import transform_bounds

    bbox = list(transform_bounds("EPSG:32644", "EPSG:4326", 386000, 767000, 387000, 768000))
    real = domain._sea
    domain._sea = lambda b, g, e: np.zeros((g["height"], g["width"]), bool)    # no network
    try:
        rgba = FM._overlay(str(tif), bbox, bbox, (200, 200))
    finally:
        domain._sea = real
    colours = {tuple(int(v) for v in c) for c in rgba.reshape(-1, 4)}
    check("flooded pixels are the flood red", FM.FLOOD_RGBA in colours)
    check("unobserved land is grey, never left looking dry", FM.UNOBSERVED_RGBA in colours)
    check("dry observed land is left clear", (0, 0, 0, 0) in colours)
    blue = (rgba == FM.FLOOD_RGBA).all(axis=2).mean()
    check("the flooded share survives the reprojection (9 of 100 cells)",
          0.06 < blue < 0.12, f"{blue:.3f}")

    # BUG-063: a one-pixel patch far from the 3x3 block cannot be seen at a
    # small figure size, so it gets a red pin; the block does not.
    codes[8, 1] = 1
    codes[6:9, 6:9] = 0
    with rasterio.open(tif, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8",
                       crs="EPSG:32644", transform=from_origin(386000, 768000, 100, 100),
                       nodata=255) as dst:
        dst.write(codes, 1)
    domain._sea = lambda b, g, e: np.zeros((g["height"], g["width"]), bool)
    try:
        pins = FM._minor_floods(str(tif), bbox, bbox, (30, 30))
    finally:
        domain._sea = real
    check("a flood patch too small to see is pinned, a visible one is not",
          len(pins) == 1 and pins[0]["patches"] == 1
          and abs(pins[0]["lat"] - bbox[1]) < abs(pins[0]["lat"] - bbox[3]), str(pins))


def test_pipelines() -> None:
    from core.pipelines import flood_analyst, flood_bot

    for build in (flood_analyst.build, flood_bot.build):
        names = [s.name for s in build().steps]
        check(f"{build.__module__.split('.')[-1]} maps the flood with get_flood_map",
              "get_flood_map" in names and "get_flood_extent" not in names, str(names[:3]))


def main() -> None:
    test_catalogue()
    with tempfile.TemporaryDirectory() as tmp:
        test_opera(Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_overlay(Path(tmp))
    test_pipelines()
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
