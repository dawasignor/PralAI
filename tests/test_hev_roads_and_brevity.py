"""Module 9 roads and bridges, and the interpret-never-repeat rule
(user, 2026-11-22).

- The road check names the main roads (motorway to secondary) the flood
  reached or the product did not see; Module 9 shows them with the bridges
  and the hospitals with a flooded road nearby.
- The road check runs for Module 9 only: it stays switched off in the
  catalogue, the routing and Module 10.
- An agent-written part over its word limit is sent back once to be
  shortened; still over, it is shown whole and flagged -- never cut.
- Every agent task carries the interpret-never-repeat and connectivity rules.

No network and no model. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BOX = [100.50, 13.70, 100.56, 13.76]


def _extent(folder: Path) -> str:
    """West half flooded (1), east half dry (0), the top 5 rows not observed (255)."""
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


def _store(tmp: str):
    from core.experiment import Experiment

    store = Experiment("hev-2026", root=Path(tmp))
    store.create(place="test", window="2026-09-24 to 2026-09-30")
    return store


ROADS = [
    # A trunk road across the flooded west half, and its bridge section.
    {"id": 1, "class": "trunk", "name": "Kandy Road", "ref": "A1", "bridge": False,
     "coords": [(100.505, 13.73), (100.525, 13.73)]},
    {"id": 2, "class": "trunk", "name": "Kandy Road", "ref": "A1", "bridge": True,
     "coords": [(100.525, 13.73), (100.528, 13.73)]},
    # A primary road on dry ground only: not listed.
    {"id": 3, "class": "primary", "name": "Dry Road", "ref": "", "bridge": False,
     "coords": [(100.540, 13.72), (100.555, 13.72)]},
    # A secondary road in the unobserved strip: listed, state not known.
    {"id": 4, "class": "secondary", "name": "", "ref": "B7", "bridge": False,
     "coords": [(100.535, 13.758), (100.555, 13.758)]},
    # A residential street in the flood: counted by class, not a main road.
    {"id": 5, "class": "residential", "name": "Lane 3", "ref": "", "bridge": False,
     "coords": [(100.505, 13.71), (100.515, 13.71)]},
]
HOSPITALS = [{"name": "General", "lon": 100.51, "lat": 13.731, "osm": "node/9"}]


def _road_check(ext: str) -> dict:
    from core.workflows import impact

    saved = impact._roads_and_hospitals
    impact._roads_and_hospitals = lambda bbox: (ROADS, HOSPITALS)
    try:
        return impact.assess_road_access(BOX, ext)
    finally:
        impact._roads_and_hospitals = saved


def test_main_roads_by_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = _road_check(_extent(Path(tmp)))
    main = {m["road"]: m for m in out["main_roads"]}
    assert set(main) == {"A1 Kandy Road", "B7"}, main
    assert main["A1 Kandy Road"]["flooded_km"] > 2 and main["A1 Kandy Road"]["class"] == "trunk"
    assert main["B7"]["flooded_km"] == 0 and main["B7"]["not_observed_km"] > 1
    assert out["main_roads"][0]["road"] == "A1 Kandy Road"          # most flooded first
    assert len(out["bridges_in_flood_extent"]) == 1
    assert out["hospitals_access_at_risk"] == 1


def test_block_shows_roads_bridges_and_hospitals() -> None:
    from core import report_render
    from core.module_blocks import b_hev_roads_bridges

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        ext = _extent(Path(tmp))
        store.cache_input("assess_road_access", _road_check(ext))
        ctx = report_render.Ctx(store)
        saved = ctx.observed
        ctx.observed = lambda: ({"status": "flood_mapped", "raster": {"path": ext}}, "")
        fact = b_hev_roads_bridges(ctx)
        text = fact.text
        assert "| A1 Kandy Road | trunk |" in text and "| B7 | secondary |" in text, text
        assert "Dry Road" not in text and "Lane 3" not in text
        assert "Bridges in the flood extent" in text and "deck height not known" in text
        assert "Hospitals with a flooded road within 250 m" in text and "1 of 1" in text
        assert "never open" in text

        # A check of another extent, or one stored before the main-road list: not known.
        ctx.observed = lambda: ({"status": "flood_mapped", "raster": {"path": "other.tif"}}, "")
        assert not b_hev_roads_bridges(ctx).known
        ctx.observed = saved
        old = _road_check(ext)
        del old["main_roads"]
        store.cache_input("assess_road_access", old)
        ctx = report_render.Ctx(store)
        ctx.observed = lambda: ({"status": "flood_mapped", "raster": {"path": ext}}, "")
        assert not b_hev_roads_bridges(ctx).known


def test_road_check_is_module_9_only_and_stays_off() -> None:
    from core import workflows
    from core.pipelines import flood_analyst as fa
    from core.pipelines import hev
    from core.pipelines.base import Run

    assert "assess_road_access" in [s.name for s in hev.data_steps("essential_services_hev")]
    assert "assess_road_access" not in [s.name for s in hev.data_steps("human_health_risks_hev")]
    assert "assess_road_access" in [s.name for s in hev.steps("essential_services_hev")]
    # Still switched off for the catalogue, the routing and the chat.
    assert "assess_road_access" in workflows.INACTIVE
    assert "assess_road_access" not in workflows.names()
    assert workflows.resolve("assess_road_access") == (None, "inactive")

    calls = []

    def executor(entry):
        calls.append(entry)
        return {"status": "success", "output": {"main_roads": [], "extent_raster": "x.tif"}}

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        saved = fa.stored_extent
        fa.stored_extent = lambda run: {"status": "no_flood_mapped", "label": "GFM",
                                        "raster": {"path": "x.tif"}}
        try:
            run = Run("test", store.slug, store, BOX, "2026-09-24", "2026-09-30", {}, None,
                      False, executor)
            step = next(s for s in hev.data_steps() if s.name == "assess_road_access")
            out = step.action(run)
        finally:
            fa.stored_extent = saved
    assert out["status"] == "ok", out
    assert calls and calls[0]["workflow"].name == "assess_road_access"
    assert calls[0]["params"]["extent_raster"] == "x.tif"            # observed, not only mapped
    assert "depth_raster" not in calls[0]["params"]


def _module_run(tmp: str, writer):
    from core.pipelines import hev
    from core.pipelines.base import Run

    store = _store(tmp)
    run = Run("test", store.slug, store, BOX, "2026-09-24", "2026-09-30", {}, writer,
              True, lambda e: {"status": "failed"})
    out = hev.module("essential_services_hev", "norwood").action(run)
    return store, run, out


LONG = " ".join(["word"] * 200)


def test_over_limit_is_shortened_once() -> None:
    calls = []

    def writer(call):
        calls.append(call)
        if call.agent == "norwood":
            return "The map shows the flood."
        if "over their word limits" in call.task:
            return json.dumps({"overall_risks": "1. The main road to the hospital is cut."})
        if '"actions"' in call.task and "recommended actions" in call.task:
            return json.dumps({"actions": "1. Open a detour to the hospital."})
        return json.dumps({"hazard_reading": "Most of the land was not seen.",
                           "vulnerability_points": "1. National regime. 2. Floods recur.",
                           "overall_risks": LONG})

    with tempfile.TemporaryDirectory() as tmp:
        store, run, out = _module_run(tmp, writer)
        body = store.path("essential_services_hev").read_text(encoding="utf-8")
    assert out["status"] == "ok", out
    shorten = [c for c in calls if "over their word limits" in c.task]
    assert len(shorten) == 1 and "overall_risks" in shorten[0].task
    assert "hazard_reading" not in shorten[0].task.split("over their word limits")[1].split("Reply")[0]
    assert "The main road to the hospital is cut." in body and LONG not in body
    assert " ..." not in body and "Over its" not in body
    # Every agent task carries the rules.
    for c in calls[1:3]:
        assert "Do NOT repeat them" in c.task and "Connectivity" in c.task, c.task[:200]


def test_still_over_is_whole_and_flagged() -> None:
    def writer(call):
        if call.agent == "norwood":
            return "The map shows the flood."
        if "over their word limits" in call.task:
            return json.dumps({"overall_risks": LONG + " extra"})       # longer: not taken
        if '"actions"' in call.task and "recommended actions" in call.task:
            return json.dumps({"actions": "1. Act."})
        return json.dumps({"hazard_reading": "Seen.", "vulnerability_points": "1. a 2. b",
                           "overall_risks": LONG})

    with tempfile.TemporaryDirectory() as tmp:
        store, run, out = _module_run(tmp, writer)
        body = store.path("essential_services_hev").read_text(encoding="utf-8")
    assert LONG in body and "_(Over its 160-word limit: 200 words.)_" in body
    assert any(i.get("key") == "essential_services_hev_over_limit" for i in run.items), run.items


def test_template_and_flow_card_carry_the_rules() -> None:
    from core import report_render
    from core.pipelines import hev

    text = report_render.template("essential_services_hev")
    assert "{{fact:hev_roads_bridges}}" in text
    assert text.index("{{fact:hev_roads_bridges}}") < text.index("{{llm:hazard_reading")
    hint = hev.hints("essential_services_hev")
    assert "Interpret, never repeat" in hint and "Connectivity and network disruption" in hint
    assert "hev_roads_bridges" in hev.HAZARD_FACTS


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
