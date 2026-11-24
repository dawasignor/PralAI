"""Modules 9 and 10 (user decision 2026-11-02): the hazard-and-exposure map,
the people in the flood, the persona word limits, and the agent conversation
that writes the modules.

No network and no model: OpenStreetMap, the map tiles and the sea mask are
replaced, rasters are synthetic, and the agents are a fake writer. Runs under
pytest or as a plain script.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workflows import hazard_map as hm  # noqa: E402

BOX = [100.50, 13.70, 100.56, 13.76]


def _extent(folder: Path) -> str:
    """A 60 x 60 extent over BOX: west half flooded, a strip not observed."""
    import rasterio
    from rasterio.transform import from_bounds

    codes = np.zeros((60, 60), dtype="uint8")
    codes[:, :30] = 1
    codes[:5, :] = 255
    path = folder / "extent.tif"
    with rasterio.open(path, "w", driver="GTiff", width=60, height=60, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_bounds(*BOX, 60, 60)) as dst:
        dst.write(codes, 1)
    return str(path)


def _osm(items: dict[str, list[tuple[float, float]]]) -> dict:
    facilities = {k: {"count": len(v), "located": len(v), "truncated": False,
                      "items": [{"name": f"{k} {n}", "lon": lon, "lat": lat, "osm": f"node/{n}"}
                                for n, (lon, lat) in enumerate(v)]}
                  for k, v in items.items()}
    return {"ok": True, "facilities": facilities, "missing": [], "problem": ""}


def _offline(monkey: dict, items: dict) -> None:
    """Replace OSM, the tiles and the sea mask."""
    from PIL import Image

    from core.workflows import flood_map, osm_context

    monkey["counts"] = osm_context.counts
    monkey["stitch"] = osm_context._stitch
    monkey["codes"] = flood_map.shown_codes
    monkey["minor"] = flood_map._minor_floods
    osm_context.counts = lambda bbox, locate_max=0, groups=None: _osm(items)
    def stitch(frame, z, cache, ua):
        # A blank image with the real frame's pixel geometry, so markers land on it.
        x0, y0 = osm_context._px(frame[0], frame[3], z)
        x1, y1 = osm_context._px(frame[2], frame[1], z)
        return Image.new("RGB", (int(x1 - x0), int(y1 - y0)), "white"), (x0, y0)

    osm_context._stitch = stitch
    flood_map.shown_codes = lambda tif, bbox, frame, size: np.pad(
        np.ones((size[1], size[0] // 2), dtype="uint8"), ((0, 0), (0, size[0] - size[0] // 2)))
    flood_map._minor_floods = lambda *a, **k: []


def _restore(monkey: dict) -> None:
    from core.workflows import flood_map, osm_context

    osm_context.counts, osm_context._stitch = monkey["counts"], monkey["stitch"]
    flood_map.shown_codes, flood_map._minor_floods = monkey["codes"], monkey["minor"]


# -- clusters ------------------------------------------------------------------------
def test_clusters_never_wider_than_2_km() -> None:
    rng = np.random.default_rng(7)
    pts = [{"lon": 100.5 + float(x), "lat": 13.7 + float(y)}
           for x, y in rng.uniform(0, 0.08, size=(300, 2))]
    cl = hm.clusters(pts)
    assert sum(c["count"] for c in cl) == 300
    lat0 = float(np.mean([p["lat"] for p in pts]))
    for c in cl:
        assert c["radius_m"] <= 1000.0
        mx, my = hm._metres(np.array([pts[i]["lon"] for i in c["members"]]),
                            np.array([pts[i]["lat"] for i in c["members"]]), lat0)
        sx, sy = hm._metres(np.array([c["lon"]]), np.array([c["lat"]]), lat0)
        assert np.hypot(mx - sx, my - sy).max() <= 1000.0 + 1e-6
    assert hm.clusters(pts) == cl                     # deterministic


def test_clusters_group_a_dense_patch() -> None:
    pts = [{"lon": 100.50 + i * 1e-4, "lat": 13.70} for i in range(10)] + \
          [{"lon": 100.60, "lat": 13.80}]
    cl = hm.clusters(pts)
    assert sorted(c["count"] for c in cl) == [1, 10]


# -- the tool ------------------------------------------------------------------------
def test_map_counts_in_flood_out_and_not_observed() -> None:
    monkey: dict = {}
    items = {"hospitals": [(100.51, 13.73), (100.55, 13.73), (100.55, 13.759)],
             "water_facilities": [(100.52, 13.72)],
             "food_grocery_shops": [(100.51, 13.72), (100.5105, 13.7205), (100.55, 13.72)],
             "marketplaces": [(100.511, 13.721)]}
    _offline(monkey, items)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = hm.map_hazard_exposure(BOX, _extent(Path(tmp)), "GFM",
                                         output_dir=tmp, event_id="t")
            ex = out["exposure"]
            h = ex["hospitals"]["in_hazard"]["flood_extent"]
            assert (h["in"], h["out"], h["not_observed"]) == (1, 1, 1)
            assert ex["drinking_water"]["in_hazard"]["flood_extent"]["in"] == 1
            assert ex["food"]["located"] == 4
            assert sum(c["count"] for c in out["food_clusters"]) == 4
            assert {"essential_services", "health"} <= set(out["maps"])
            for m in out["maps"].values():
                assert Path(m["png"]).is_file()
            assert out["maps"]["health"]["layers"] == ["hospitals"]
    finally:
        _restore(monkey)


def test_a_new_hazard_is_one_registry_entry() -> None:
    monkey: dict = {}
    _offline(monkey, {"hospitals": [(100.51, 13.73), (100.55, 13.73)]})

    def draw(ctx):
        rgba = np.zeros((ctx.size[1], ctx.size[0], 4), dtype="uint8")
        return {"rgba": rgba, "legend": [((0, 0, 255, 200), "test hazard")]}

    def test(ctx, lons, lats):
        return np.where(lons > 100.53, "in", "out")

    hm.HAZARD_LAYERS["test_hazard"] = hm.HazardLayer("a test hazard", "synthetic", draw, test)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = hm.map_hazard_exposure(BOX, _extent(Path(tmp)), "GFM",
                                         hazards=["flood_extent", "test_hazard"],
                                         maps={"health": ["hospitals"]},
                                         output_dir=tmp, event_id="t")
            h = out["exposure"]["hospitals"]["in_hazard"]
            assert h["test_hazard"]["in"] == 1 and h["flood_extent"]["in"] == 1
    finally:
        del hm.HAZARD_LAYERS["test_hazard"]
        _restore(monkey)


def test_unknown_layers_are_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ext = _extent(Path(tmp))
        for kwargs in ({"hazards": ["landslide"]}, {"maps": {"x": ["schools"]}}):
            try:
                hm.map_hazard_exposure(BOX, ext, **kwargs)
            except ValueError:
                continue
            raise AssertionError(f"{kwargs} was accepted")


# -- people in the flood -------------------------------------------------------------
def test_people_split_by_flooded_share() -> None:
    import rasterio
    from rasterio.transform import from_bounds

    from core.workflows.population import _in_extent

    with tempfile.TemporaryDirectory() as tmp:
        ext = _extent(Path(tmp))
        people = np.full((6, 6), 10.0)                 # 360 people on a 6 x 6 grid over BOX
        out = _in_extent(people, None, from_bounds(*BOX, 6, 6), rasterio.crs.CRS.from_epsg(4326), ext)
    # West half flooded, the top 5/60 not observed (and that strip is not flood).
    flooded = 360 * 0.5 * (55 / 60)
    assert abs(out["people_in_flood"] - flooded) <= 1, out
    assert abs(out["people_not_observed"] - 360 * 5 / 60) <= 1, out


# -- persona word limits -------------------------------------------------------------
def test_slot_limit_by_persona() -> None:
    from core.report_render import slot_limit

    opts = {"max_words": "110", "flood_bot": "50"}
    assert slot_limit(opts, "flood_bot") == 50
    assert slot_limit(opts, "chanakya") == 110
    assert slot_limit(opts, "norwood") == 110
    assert slot_limit(opts, "") == 110


# -- the modules ---------------------------------------------------------------------
def _store(tmp: str):
    from core.experiment import Experiment

    store = Experiment("hev-2026", root=Path(tmp))
    store.create(place="test", window="2026-09-24 to 2026-09-30")
    return store


def test_templates_load_and_render_with_nothing_stored() -> None:
    from core import report_render

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        for slot in ("essential_services_hev", "human_health_risks_hev"):
            text = report_render.template(slot)
            for part in ("## 1. Hazard", "## 2. Exposure", "## 3. Vulnerability",
                         "## 4. Overall risk", "## 5. Recommended actions"):
                assert part in text, (slot, part)
            path = report_render.render(store, slot, persona="flood_bot")
            body = path.read_text(encoding="utf-8")
            assert "Not known yet" in body
            side = json.loads(path.with_suffix(".facts.json").read_text(encoding="utf-8"))
            limits = {s["slot"]: s["max_words"] for s in side["llm_slots"]}
            assert limits["hazard_reading"] == 50, limits
            assert set(limits) == {"hazard_reading", "vulnerability_points", "overall_risks",
                                   "actions"}


def test_hints_are_read_and_never_rendered() -> None:
    from core import report_render
    from core.pipelines import hev

    text = hev.hints("human_health_risks_hev")
    for risk in ("Sewage and infection", "drinking water", "malaria", "Mould"):
        assert risk.lower() in text.lower(), risk
    assert "(WHO 2025a)" in text and "(WHO 2009)" in text
    assert "Hints for Chanakya" not in report_render.template("human_health_risks_hev")


def test_the_conversation_writes_every_slot() -> None:
    from core.pipelines import hev
    from core.pipelines.base import Run

    calls = []

    def writer(call):
        calls.append(call)
        if call.agent == "norwood":
            return "The map shows the flood over half the AOI."
        if "recommended actions" in call.task and '"actions"' in call.task:
            return json.dumps({"actions": "1. Keep drinking water safe (WHO 2005)."})
        return json.dumps({"hazard_reading": "Half the land is flooded.",
                           "vulnerability_points": "1. INFORM is national. 2. Floods recur.",
                           "overall_risks": "1. Contaminated water."})

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        run = Run("test", store.slug, store, BOX, "2026-09-24", "2026-09-30", {}, writer,
                  True, lambda e: {"status": "failed"})
        out = hev.module("human_health_risks_hev", "chanakya").action(run)
        assert out["status"] == "ok", out
        assert [c.agent for c in calls] == ["norwood", "citadel", "citadel"]
        assert "hand-over of the flood map" in calls[1].task
        assert "Hints for Chanakya" in calls[1].task
        assert calls[2].effort == "high" and calls[2].max_tokens >= 24000
        body = store.path("human_health_risks_hev").read_text(encoding="utf-8")
        assert "Keep drinking water safe" in body and "Contaminated water" in body
        assert store.deliverable("human_health_risks_hev_handoff")


# -- 2026-11-18: no-flood extent, figures to the chat, layout ------------------------
def test_a_no_flood_extent_is_still_an_observation() -> None:
    """The map and the population run on "no flood mapped"; depth does not;
    an unreadable map is refused by both."""
    from core.pipelines import flood_analyst as fa
    from core.pipelines.base import MissingInput

    def with_extent(ext):
        saved = fa.stored_extent
        fa.stored_extent = lambda run: ext
        return saved

    none = {"status": "no_flood_mapped", "label": "GFM", "raster": {"path": "x.tif"}}
    saved = with_extent(none)
    try:
        assert fa.observed_extent(None) is none
        try:
            fa.mapped_extent(None)
            raise AssertionError("mapped_extent took a no-flood extent")
        except MissingInput:
            pass
        fa.stored_extent = lambda run: {"status": "not_known", "label": "GFM", "reason": "404"}
        try:
            fa.observed_extent(None)
            raise AssertionError("observed_extent took an unreadable map")
        except MissingInput as exc:
            assert "not known" in str(exc)
    finally:
        fa.stored_extent = saved


def test_every_module_figure_reaches_the_chat() -> None:
    from core import figure_refs
    from core.pipelines import hev

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = type("S", (), {"dir": str(root)})()
        (root / "figures").mkdir()
        for name in ("inform_meters", "inform_trend", "hazard_exposure_x"):
            (root / "figures" / f"{name}.png").write_bytes(b"\x89PNG")
        (root / "figures" / "index.json").write_text(json.dumps(
            [{"png": str(root / "figures" / "inform_meters.png"), "caption": "INFORM meters"}]))
        text = ("A\n\n![hev inform meters](figures/inform_meters.png)\n\n"
                "![t](figures/inform_trend.png)\n\n![m](figures/hazard_exposure_x.png)\n\n"
                "![gone](figures/missing.png)\n\nB")
        record = type("R", (), {"place": type("P", (), {"name": "Kandy", "display": "Kandy"})(),
                                "window": type("W", (), {"start": "2025-11-26"})()})()
        saved = (figure_refs.OUTPUTS, figure_refs.RUN_INDEX, figure_refs._served)
        figure_refs.OUTPUTS, figure_refs.RUN_INDEX = root, root / "run_figures.json"
        figure_refs._served = lambda path, outputs: True
        lines: list[str] = []
        try:
            out, figs = hev._chat_figures(text, store, record, lines)
        finally:
            figure_refs.OUTPUTS, figure_refs.RUN_INDEX, figure_refs._served = saved
    names = {f["figure"] for f in figs}
    assert names == {"inform_meters", "inform_trend", "hazard_exposure_x"}, names
    assert "<image - run-kandy-2025-inform_meters>" in out
    assert "<image - run-kandy-2025-inform_trend>" in out
    assert "](" not in out, "no file path may reach the chat"
    assert next(f for f in figs if f["figure"] == "inform_meters")["caption"] == "INFORM meters"
    assert any("figure-missing" in ln for ln in lines)


def test_layout_rules() -> None:
    from core.outputs import NOT_KNOWN
    from core.report_render import _merge_not_known, _tidy, cut_words, shape_points

    run_on = ("National regime, not AOI: 1) INFORM scores vulnerability 2.6 and coping 5.0. "
              "2) Recurrence: 70 floods. Answers Risks 2 and 3. 3) Trend improved 4.1 to 2.7.")
    shaped = shape_points(run_on)
    assert shaped.split("\n\n")[0] == "National regime, not AOI:"
    points = shaped.split("\n\n")[1].split("\n")
    assert [p[:3] for p in points] == ["1. ", "2. ", "3. "], points
    assert "Answers Risks 2 and 3." in points[1] and "2.6" in points[0]
    assert shape_points("Vulnerability 2.6 is lower than 5.0. Nothing to split.") == \
        "Vulnerability 2.6 is lower than 5.0. Nothing to split."
    assert shape_points("1) a\n2) b") == "1. a\n2. b"
    # The Kandy slot: a list opening the slot, the rest inline on one line.
    assert shape_points("1) National regime: INFORM 2.6. 2) Recurrence: 70 floods. "
                        "3) Trend improved.") == \
        "1. National regime: INFORM 2.6.\n2. Recurrence: 70 floods.\n3. Trend improved."
    # One point per paragraph is one tight list.
    assert shape_points("1. First.\n\n2. Second.\n\n3. Third.") == "1. First.\n2. Second.\n3. Third."
    assert shape_points("Lead.\n\n1. a\n\n2. b\n\nAfter.") == "Lead.\n\n1. a\n2. b\n\nAfter."

    text = "1. one two three\n2. four five six\n3. seven eight nine"
    assert cut_words(text, 12) == text                       # within the limit: untouched
    # Over the limit: kept whole and flagged, never cut (user, 2026-11-22).
    assert cut_words(text, 9) == text + "\n\n_(Over its 9-word limit: 12 words.)_"
    assert "..." not in cut_words("a b c d e f", 3)

    body = (f"## 1. Hazard\n\n\x00nk:hev_map_essential_services\x00{NOT_KNOWN} no flood\n\n"
            f"\x00nk:hev_exposure_essential_services\x00{NOT_KNOWN} no flood\n\n"
            f"\x00nk:hev_food_clusters\x00{NOT_KNOWN} no flood\n\n"
            f"\x00nk:hev_people\x00{NOT_KNOWN} other reason\n\n"
            f"## 2. Exposure\n\n\x00nk:hev_people\x00{NOT_KNOWN} no flood")
    merged = _tidy(_merge_not_known(body))
    hazard, exposure = merged.split("## 2. Exposure")
    assert hazard.count("Not known yet") == 2, hazard
    assert ("Not known yet: the map, the facility table and the food clusters -- no flood"
            in hazard), hazard
    assert f"{NOT_KNOWN} no flood" in exposure             # per section, not across
    assert "\x00" not in merged and "\n\n\n" not in merged


def test_flows_name_their_module() -> None:
    from core.spec import flow_menu, load_flow

    assert load_flow("citadel", "essential_services_hev").module == "essential_services_hev"
    assert load_flow("citadel", "human_health_risks_hev").module == "human_health_risks_hev"
    assert load_flow("norwood", "flood_map_handoff").module == ""
    assert "human_health_risks_hev" in flow_menu("citadel")


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__} {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
