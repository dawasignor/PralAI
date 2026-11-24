"""Flood maps per product, on demand, made once (user decision 2026-11-12).

    python tests/test_flood_map_products.py     # no network (the product read is faked), no cost

    1. names     -- one stored name per product; the reverse maps back to the tool
    2. lookup    -- "the" extent is the best-ranked product's; any other by name
    3. pipeline  -- the step stores per product, reuses the same product, and
                    never takes another product's map for this one
    4. once      -- a repeat call for the same product, AOI and window returns
                    the kept map: nothing read or drawn again; another product
                    or refresh=true reads; an uncached run takes only its own
    5. context   -- the overlay takes buildings and places from a stored area
                    overview of the same box, and not from another box's
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import cache_policy, workflows  # noqa: E402
from core.outputs import Inputs  # noqa: E402
from core.workflows import flood_map as FM  # noqa: E402

results: list[tuple[str, str]] = []
BBOX = [79.94, 6.905, 80.09, 6.978]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def extent(product: str, km2: float = 1.5) -> dict:
    return {"product": product, "label": product.upper(), "status": "flood_mapped",
            "flood_km2": km2, "coverage": 0.9, "raster": {"path": f"/x/flood_extent_{product}.tif"}}


def main() -> int:
    # 1. names
    check("names: one per product", workflows.cache_name("get_flood_map", {"product": "MODIS MCDWD"})
          == "flood_map_mcdwd" and workflows.cache_name("get_flood_map", {"product": "gfm"}) == "flood_map_gfm")
    check("names: generic without params", workflows.cache_name("get_flood_map") == "get_flood_map")
    check("names: reverse", workflows.tool_of("flood_map_vcdwd") == "get_flood_map"
          and workflows.tool_of("weather_grid") == "fetch_weather")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = cache_policy.RunStore("t-run", root=tmp)
        store.create()
        store.cache_input("rank_flood_products", {"ranking": [
            {"id": "gfm", "alternative": "GFM (Sentinel-1)", "rank": 1},
            {"id": "mcdwd", "alternative": "MODIS MCDWD", "rank": 2}]})
        store.cache_input("flood_map_gfm", extent("gfm", 0.0))
        store.cache_input("flood_map_mcdwd", extent("mcdwd", 7.2))

        # 2. lookup
        inp = Inputs(store)
        check("lookup: the extent is the best-ranked product's", inp.get("extent")["product"] == "gfm")
        check("lookup: any product by name", inp.extent_for("MODIS MCDWD")["flood_km2"] == 7.2
              and inp.extent_for("vcdwd") is None)

        # 3. pipeline step
        from core.pipelines import base

        calls = []

        def executor(task):
            calls.append(task["params"]["product"])
            return {"status": "success", "output": extent(task["params"]["product"], 3.3)}

        run = SimpleNamespace(store=store, refresh=(), cached_only=False, executor=executor,
                              note=lambda *a: None, bbox=BBOX, start="2025-11-24", end="2025-12-01")
        step = base.tool("get_flood_map", lambda r: {"product": "vcdwd", "bbox": BBOX,
                                                     "start": "2025-11-24", "end": "2025-12-01"})
        first = step.action(run)
        again = step.action(run)
        check("pipeline: stored per product, then reused",
              first["status"] == "ok" and "flood_map_vcdwd" in first["detail"]
              and again["status"] == "cached" and calls == ["vcdwd"], f"{first} {again} {calls}")
        store.cache_input("get_flood_map", extent("gfm"), params={"bbox": BBOX})
        step2 = base.tool("get_flood_map", lambda r: {"product": "dswx_s1", "bbox": BBOX,
                                                      "start": "2025-11-24", "end": "2025-12-01"})
        got = step2.action(run)
        check("pipeline: another product's older record is not taken",
              got["status"] == "ok" and calls[-1] == "dswx_s1", f"{got} {calls}")

        # 4. once
        made = []
        real_extent, real_draw = FM.__dict__.get("extent_from_config"), FM._draw_overlay

        def fake_extent(cfg, key, **kw):
            made.append(key)
            raster = Path(kw["output_dir"]) / (kw["event_id"] or "flood_map") / f"flood_extent_{key}.tif"
            raster.parent.mkdir(parents=True, exist_ok=True)
            raster.write_bytes(b"x")
            return {"product": key, "label": key, "status": "flood_mapped", "flood_km2": 1.0,
                    "coverage": 0.8, "window": ["2025-11-24", "2025-12-01"],
                    "raster": {"path": str(raster)}}

        def fake_draw(payload, bbox, folder, aoi_fraction, osm_context=""):
            png = folder / f"flood_overlay_{payload['product']}.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            png.write_bytes(b"png")
            return {"ok": True, "png": str(png)}

        import core.workflows.impact as impact
        real_impact = impact.extent_from_config
        impact.extent_from_config = fake_extent
        FM._draw_overlay = fake_draw
        try:
            kw = dict(bbox=BBOX, start="2025-11-24", end="2025-12-01", output_dir=str(tmp / "r"),
                      event_id="ev", permanent_water_raster="/x/water.tif")
            a = FM.get_flood_map("mcdwd", **kw)
            b = FM.get_flood_map("MODIS MCDWD", **kw)
            check("once: repeat call returns the kept map, nothing made again",
                  made == ["mcdwd"] and b.get("reused") and b["figure"]["path"] == a["figure"]["path"]
                  and a["figure"]["name"] == "flood_overlay_mcdwd", str(made))
            FM.get_flood_map("vcdwd", **kw)
            FM.get_flood_map("mcdwd", refresh=True, **kw)
            check("once: another product, or refresh, is made", made == ["mcdwd", "vcdwd", "mcdwd"], str(made))
            with cache_policy.use(cache_policy.OFF, run="rq5-x-r02"):
                FM.get_flood_map("mcdwd", **kw)
                FM.get_flood_map("mcdwd", **kw)
            check("once: an uncached run makes its own once, never another run's",
                  made == ["mcdwd", "vcdwd", "mcdwd", "mcdwd"], str(made))
        finally:
            impact.extent_from_config = real_impact
            FM._draw_overlay = real_draw

        # 5. context
        where = tmp / "where_am_i.json"
        where.write_text(json.dumps({"params": {"bbox": BBOX}, "payload": {
            "aoi": {"bbox": BBOX},
            "facilities": {"categories": {"hospitals": {"items": [{"lon": 80.0, "lat": 6.93}]}}},
            "counts": {"missing": []},
            "admin": {"major_places": [{"name": "Kaduwela", "type": "city", "lon": 79.98, "lat": 6.93}]}}}),
            encoding="utf-8")
        same = FM._stored_context(str(where), BBOX)
        other = FM._stored_context(str(where), [79.0, 6.0, 80.0, 7.0])
        check("context: same box reused, another box not",
              same and same["facilities"]["hospitals"]["items"] and same["major_places"][0]["name"] == "Kaduwela"
              and other is None)

    failed = [n for s, n in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
