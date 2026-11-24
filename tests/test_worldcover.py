"""fetch_worldcover and the land-cover fallback, on a synthetic tile.

    python tests/test_worldcover.py         # no model, no network, no cost
    python tests/test_worldcover.py --live  # also read the real tile over the locked AOI

WorldCover is the land-cover fallback for when
Sentinel-2 fails its 0.7 threshold. Held still here:

    1. tiles     -- the 3-degree tile name follows the south-west corner
    2. shares    -- class shares are of the LAND: the sea is excluded first,
                    nodata lowers valid_fraction and is never a class
    3. refusal   -- a code outside the published legend is refused
    4. blocks    -- the payload carries its own uncertainty (the 2021 snapshot,
                    the published accuracy of each class present) and sources,
                    so the tool contract adds nothing
    5. evidence  -- the fallback needs a recorded BelowValidFraction under
                    settings no stricter than the defaults, for this bbox
    6. indices   -- veg_pct and urban_pct come from tree cover and built-up,
                    named as the fallback; Sentinel-2 still wins when present
    7. typed     -- the Sentinel-2 cloud refusal is BelowValidFraction, and
                    Monty records its class and parameters
    8. live      -- (--live) the real 2021 tile over kelani-lower-2025
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import logbook, workflows                          # noqa: E402
from core.monty_graph import run_monty                      # noqa: E402
from core.workflows import mcdm, sentinel, worldcover as wc  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
RES = 1 / 12000                      # WorldCover's own pixel, in degrees
BBOX = [79.94, 6.94, 79.97, 6.97]    # 360 x 360 pixels


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def write_tile(path: Path, codes: np.ndarray) -> None:
    """A tile on WorldCover's lattice, origin on the whole degree (pixel-is-area)."""
    import rasterio
    from rasterio.transform import from_origin

    height, width = codes.shape
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="uint8", crs="EPSG:4326", nodata=0,
                       transform=from_origin(79.9, 7.0, RES, RES)) as dst:
        dst.write(codes, 1)
        dst.update_tags(time_start="2021-01-01T00:00:00Z",
                        time_end="2021-12-31T23:59:59Z", product_version="V2.0.0")


class Fixture:
    """Point the tool at a local tile, and give it a land mask whose western
    third is sea. Columns of the AOI: sea (coded 80 by WorldCover, as the map
    does over open water), then tree cover, then built-up; one nodata row."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        # The tile spans 79.9-80.0 E, 6.9-7.0 N; the AOI sits inside it.
        codes = np.full((1200, 1200), 10, dtype="uint8")
        col = lambda lon: int(round((lon - 79.9) / RES))          # noqa: E731
        row = lambda lat: int(round((7.0 - lat) / RES))           # noqa: E731
        c0, c1, c2, c3 = col(79.94), col(79.95), col(79.96), col(79.97)
        codes[:, c0:c1] = 80
        codes[:, c1:c2] = 10
        codes[:, c2:c3] = 50
        codes[row(6.97), c1:c3] = 0                                # one nodata row
        self.path = Path(self.tmp.name) / "tile.tif"
        write_tile(self.path, codes)
        self.saved = (wc.tile_url, wc.landmask.land_mask)
        wc.tile_url = lambda lat, lon: str(self.path)

        def fake_land_mask(bbox, plan, *, exclude=("ocean",)):
            keep = np.ones(plan.shape, dtype=bool)
            if "ocean" in exclude:
                keep[:, : plan.shape[1] // 3] = False
            area = plan.pixel_area_km2
            return keep, {"aoi_area_km2": round(keep.size * area, 4),
                          "land_area_km2": round(float(keep.sum()) * area, 4),
                          "excluded_km2": round(float((~keep).sum()) * area, 4),
                          "excluded_classes": list(exclude)}

        wc.landmask.land_mask = fake_land_mask
        return self

    def __exit__(self, *exc):
        wc.tile_url, wc.landmask.land_mask = self.saved
        self.tmp.cleanup()


def test_tiles() -> None:
    url = wc.tile_url(6.95, 79.95)
    check("Sri Lanka falls in tile N06E078", url.endswith("_N06E078_Map.tif"), url)
    check("south-west corner naming in the other hemispheres",
          wc.tile_url(-0.5, -0.5).endswith("_S03W003_Map.tif")
          and wc.tile_url(13.5, 100.5).endswith("_N12E099_Map.tif"))


def test_shares() -> None:
    with Fixture():
        out = wc.fetch_worldcover(BBOX, write_rasters=False)
        f = out["classes"]["fractions"]
        check("the sea is excluded before anything is counted",
              f["permanent_water_bodies"] == 0.0, str(f))
        check("shares are of classified land: tree and built-up half each",
              abs(f["tree_cover"] - 0.5) < 0.01 and abs(f["built_up"] - 0.5) < 0.01,
              str({k: v for k, v in f.items() if v}))
        vf = out["coverage"]["valid_fraction"]
        check("a nodata row lowers valid_fraction and is never a class",
              0.99 < vf < 1.0, str(vf))
        check("the land area is the denominator, not the bounding box",
              out["aoi"]["land_area_km2"] < out["aoi"]["bbox_area_km2"])
        check("the vintage is read from the tile, not assumed",
              out["source"]["time_start"].startswith("2021-01-01"))

        keep_all = wc.fetch_worldcover(BBOX, write_rasters=False, exclude_water=[])
        check("with nothing excluded, the map's own water class is counted",
              abs(keep_all["classes"]["fractions"]["permanent_water_bodies"] - 1 / 3) < 0.01)

        with tempfile.TemporaryDirectory() as raw:
            out = wc.fetch_worldcover(BBOX, output_dir=raw, event_id="t")
            check("the class raster is written with its legend",
                  Path(out["classes"]["raster"]["path"]).is_file()
                  and out["classes"]["raster_legend"]["50"] == "built_up")


def test_unknown_code_refused() -> None:
    with Fixture() as fx:
        codes = np.full((1200, 1200), 10, dtype="uint8")
        codes[500:600, 500:600] = 77
        write_tile(fx.path, codes)
        try:
            wc.fetch_worldcover(BBOX, write_rasters=False, exclude_water=[])
            check("a code outside the legend is refused", False)
        except ValueError as exc:
            check("a code outside the legend is refused", "77" in str(exc), str(exc)[:80])


def test_blocks() -> None:
    with Fixture():
        out = wc.fetch_worldcover(BBOX, write_rasters=False)
    keys = [i["key"] for i in out["uncertainty"]["input"]]
    check("input uncertainty names the 2021 snapshot and the overall accuracy",
          "snapshot_year" in keys and "overall_accuracy" in keys, str(keys))
    check("and the published accuracy of each class present -- only those",
          "accuracy_tree_cover" in keys and "accuracy_built_up" in keys
          and "accuracy_mangroves" not in keys)
    check("built-up accuracy is the report's figure",
          out["accuracy"]["per_class"]["built_up"]["users_pct"] == 65.9)
    check("tool uncertainty carries valid fraction, coverage and the sea",
          {"valid_fraction", "coverage", "sea_excluded"}
          <= {i["key"] for i in out["uncertainty"]["tool"]})
    check("sources are in the contract shape",
          workflows._contract_sources(out["sources"])
          and out["sources"][0]["licence"] == "CC BY 4.0")
    _, notes = workflows.apply_contract(out, {})
    check("so the contract wrapper has nothing to add", notes == [], str(notes))
    desc = workflows.get("fetch_worldcover").description
    check("the registered description says fallback and 2021",
          "FALLBACK" in desc and "2021" in desc)


def _failure(fixed: bool = True, **params) -> dict:
    """A recorded Sentinel-2 cloud failure. `fixed` -- recorded since the
    threshold and lookback were fixed (2026-11-01), so its message says so;
    False is an older record, whose params are what actually ran."""
    message = ("BelowValidFraction: no Sentinel-2 scene ... The threshold (0.7) "
               "and the lookback (90 days) are fixed; use the WorldCover fallback."
               if fixed else "BelowValidFraction: no Sentinel-2 scene ...")
    return {"name": "lulc", "params": {"bbox": BBOX, **params},
            "error": {"exception": "BelowValidFraction", "error_type": "runtime_error",
                      "message": message}}


def test_evidence() -> None:
    ok = mcdm.s2_failed_at_defaults
    check("no record is no evidence", ok(None, BBOX)[0] is False)
    check("a recorded cloud failure at the defaults is evidence", ok(_failure(), BBOX)[0])
    # fetch_lulc_data runs at 0.7 whatever it is sent (user decision
    # 2026-11-01), so a threshold in the recorded params is not what ran.
    check("a failure recorded with another threshold is still evidence (0.7 ran)",
          ok(_failure(min_valid_fraction=0.8), BBOX)[0])
    check("a failure recorded with a shorter lookback is still evidence (90 ran)",
          ok(_failure(lookback_days=30), BBOX)[0])
    check("an OLD record (before the lock) with a shorter lookback is not: 30 ran",
          ok(_failure(fixed=False, lookback_days=30), BBOX)[0] is False)
    check("an OLD record with a stricter threshold is not: 0.8 ran",
          ok(_failure(fixed=False, min_valid_fraction=0.8), BBOX)[0] is False)
    check("an OLD record at the defaults is still evidence",
          ok(_failure(fixed=False), BBOX)[0])
    check("a failure with a looser threshold or longer lookback is",
          ok(_failure(min_valid_fraction=0.5, lookback_days=180), BBOX)[0])
    check("a failure without mosaicking is not",
          ok(_failure(mosaic=False), BBOX)[0] is False)
    check("a failure for another bbox is not",
          ok(_failure(), [80, 7, 80.1, 7.1])[0] is False)
    tiling = _failure()
    tiling["error"]["exception"] = "ValueError"
    check("an untyped failure (network, an unforeseen tiling error) is not",
          ok(tiling, BBOX)[0] is False)
    mosaic = _failure()
    mosaic["error"]["exception"] = "NoUsableMosaic"
    check("a tiling failure left after dropping the other UTM zone is evidence",
          ok(mosaic, BBOX)[0])
    check("step_down_to in the record is ignored: there is no step-down",
          ok(_failure(step_down_to=[]), BBOX)[0]
          and ok(_failure(step_down_to=[0.6]), BBOX)[0]
          and ok(_failure(step_down_to=[0.6, 0.5]), BBOX)[0])


def _weather() -> dict:
    h = 24
    return {"meta": {"window_utc": ["2025-11-24T00:00Z", "2025-11-24T23:00Z"],
                     "storage_tz": "UTC", "bbox": [79.8, 6.78, 80.2, 7.08]},
            "hourly": {"time": [f"2025-11-24T{i:02d}:00Z" for i in range(h)], "vars": {
                "cloud_cover_model": {"mean": [80.0] * h},
                "wind_speed_10m": {"mean": [3.0] * h}}},
            "event_summary": {"max_gust_ms": 12.0}}



def _avail() -> dict:
    return {"aoi": {"bbox": [79.8, 6.78, 80.2, 7.08]},
            "period": {"days": ["2025-11-24"], "timezone": "Asia/Colombo"},
            "products": [{"id": "gfm", "days": {"2025-11-24": {"acquisitions": [
                             {"utc": "2025-11-24T00:25:15Z"}]}}},
                         {"id": "dswx_hls", "days": {}}, {"id": "mcdwd", "days": {}},
                         {"id": "vcdwd", "days": {}}, {"id": "dswx_s1", "days": {}}]}


def _terrain() -> dict:
    return {"slope": {"classes": {"fractions": {"steep": 0.01, "very_steep": 0.0}}}}


def test_indices() -> None:
    with Fixture():
        payload = wc.fetch_worldcover(BBOX, write_rasters=False)
    out = mcdm.indices_from_payloads(_weather(), None, _terrain(), availability=_avail(),
                                     worldcover=payload, lulc_failure=_failure())
    f = payload["classes"]["fractions"]
    check("veg_pct is the tree-cover share, urban_pct the built-up share",
          out["indices"]["veg_pct"] == round(f["tree_cover"] * 100, 6)
          and out["indices"]["urban_pct"] == round(f["built_up"] * 100, 6))
    check("both are named as coming from fetch_worldcover",
          out["index_source"]["veg_pct"] == "fetch_worldcover"
          and out["index_source"]["urban_pct"] == "fetch_worldcover"
          and out["index_source"]["steep_pct"] == "fetch_terrain_data")
    dec = out["uncertainty"]["decision"]
    check("a decision-level item states the fallback and the definitional gap",
          dec and dec[0]["key"] == "land_cover_fallback"
          and "not" in dec[0]["statement"] and "lowered" in dec[0]["statement"])
    notes = " ".join(p["note"] for p in out["provenance"])
    check("the provenance says tree cover is not dense NDVI, built-up not BUI",
          "not dense NDVI" in notes and "not BUI" in notes)
    reductions = " ".join(p["reduction"] for p in out["provenance"])
    check("cloud and wind are read at the passes, in local time, not over the window",
          "optical pass" in reductions and "radar pass" in reductions
          and "Asia/Colombo" in reductions and "192" not in notes)
    try:
        mcdm.indices_from_payloads(_weather(), None, _terrain(), availability=_avail(),
                                   worldcover=payload)
        check("without a recorded failure the fallback raises", False)
    except ValueError as exc:
        check("without a recorded failure the fallback raises",
              "threshold is not lowered" in str(exc))


def test_typed_refusal() -> None:
    check("BelowValidFraction is a ValueError, and not a tiling failure",
          issubclass(sentinel.BelowValidFraction, ValueError)
          and not issubclass(sentinel.BelowValidFraction, sentinel.NoCoveringScene))

    def cloudy(bbox: list, min_valid_fraction: float = 0.7) -> dict:
        raise sentinel.BelowValidFraction("no Sentinel-2 scene reaches valid fraction",
                                          [], min_valid_fraction=min_valid_fraction)

    workflows.tool("t_cloudy", "test fake")(cloudy)
    try:
        sink: list[str] = []
        with logbook.collect(sink):
            r = run_monty({"event_id": 1, "tasks": [
                {"function": "t_cloudy", "params": {"bbox": BBOX}}]})["results"]["result_1"]
    finally:
        workflows.REGISTRY.pop("t_cloudy", None)
    err = r["error"]
    check("Monty records the exception class and the parameters",
          err.get("exception") == "BelowValidFraction" and err["params"]["bbox"] == BBOX,
          str({k: err.get(k) for k in ("exception", "params")}))
    check("the failure shape is still the five keys",
          set(r) == {"task", "output", "status", "message", "error"})


def test_live() -> None:
    out = wc.fetch_worldcover([79.94, 6.905, 80.09, 6.978], write_rasters=False)
    f = {k: v for k, v in out["classes"]["fractions"].items() if v}
    print(f"  live kelani-lower-2025: land {out['aoi']['land_area_km2']} km2, "
          f"valid {out['coverage']['valid_fraction']}, {f}")
    check("live: the real tile reads, all land, every pixel classified",
          out["coverage"]["valid_fraction"] > 0.99
          and abs(sum(out["classes"]["fractions"].values()) - 1) < 0.001)


def test_exclude_water_values() -> None:
    """Monty sent exclude_water=false for Bangkok (2026-11-01) and the land
    mask crashed on it; in fetch_lulc_data it would have switched the mask
    off. The sea stays out of every land statistic."""
    from core.workflows import landmask
    check("a boolean or null keeps the default (the sea excluded)",
          all(landmask.classes(v) == landmask.DEFAULT_EXCLUDE for v in (False, True, None)))
    check("one class name as a string is that class",
          landmask.classes("lake") == ("lake",))
    check("a list is kept, and an explicit empty list means exclude nothing",
          landmask.classes(["ocean", "lake"]) == ("ocean", "lake")
          and landmask.classes([]) == ())


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        test_tiles()
        test_shares()
        test_unknown_code_refused()
        test_blocks()
        test_evidence()
        test_indices()
        test_typed_refusal()
        test_exclude_water_values()
        if "--live" in sys.argv:
            test_live()
    finally:
        logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
