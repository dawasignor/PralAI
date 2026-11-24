"""The session folder and its fact sheet.

    pytest tests/test_fact_sheet.py
    python tests/test_fact_sheet.py         # no model, no network, no cost

A ranking turn once answered that the five event indices were "not present in
the session" when weather, land cover and terrain had all been fetched on
earlier turns: nothing kept them. Held still here, on synthetic results in a
temporary sessions folder: a session gets its own folder and the record lives
in it; a stored result survives into the next turn's fact sheet; with weather,
land cover and terrain stored the five indices are READY and equal the
ranking's own computation; with one missing the sheet says MISSING and names
the workflow; a new AOI moves the old results aside rather than deleting them;
every agent prompt carries the sheet; rasters are routed into the folder only
for a workflow that takes `output_dir`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import fact_sheet, session, session_store            # noqa: E402
from core.session import Place, SessionRecord, Window          # noqa: E402


def weather() -> dict:
    hours = 48
    times = [f"2025-11-{24 + h // 24:02d}T{h % 24:02d}:00Z" for h in range(hours)]
    return {
        "meta": {"event_id": "t", "bbox": [79.8, 6.78, 80.2, 7.08],
                 "window_utc": [times[0], times[-1]], "storage_tz": "UTC",
                 "source": {"model": "ecmwf_ifs -- test", "native_res_km": 9}},
        "grid": {"n_points": 4, "spacing_deg": 0.1},
        "hourly": {"time": times, "vars": {
            "precipitation": {"mean": [2.0] * hours},
            "cloud_cover_model": {"mean": [90.0] * hours},
            "wind_speed_10m": {"mean": [4.0] * hours, "p90": [5.0] * hours,
                               "area_frac_gt_5": [0.0] * hours}}},
        "event_summary": {"storm_total_mm": {"aoi_mean": 96.0, "max_point": 110.0},
                          "max_intensity_mm": {"24h": 48.0}, "rain_bursts": []},
        "qc": {"gaps": {"window_complete": True, "antecedent_complete": True,
                        "withheld": [], "not_served": []}, "warnings": []},
    }


def availability(days=("2025-11-24", "2025-11-25")) -> dict:
    """Module 1 as the ranking reads it (2026-11-22): local zone, local days,
    one radar pass, one HLS pass; MODIS / VIIRS at their nominal overpass."""
    return {"aoi": {"bbox": [79.8, 6.78, 80.2, 7.08]},
            "period": {"days": list(days), "timezone": "Asia/Colombo", "utc_offset_h": 5.5},
            "products": [
                {"id": "gfm", "days": {days[0]: {"acquisitions": [
                    {"utc": f"{days[0]}T00:25:15Z"}]}}},
                {"id": "dswx_s1", "days": {days[0]: {"acquisitions": [
                    {"utc": f"{days[0]}T00:25:30Z"}]}}},
                {"id": "dswx_hls", "days": {days[0]: {"acquisitions": [
                    {"utc": f"{days[0]}T05:16:48Z"}]}}},
                {"id": "mcdwd", "days": {}}, {"id": "vcdwd", "days": {}}]}


def lulc() -> dict:
    return {
        "aoi": {"land_area_km2": 1320.7},
        "scene": {"datetime": "2025-10-31T05:16Z", "days_before_start": 24,
                  "aoi_valid_fraction": 0.74, "is_mosaic": True},
        "indices": {
            "ndvi": {"classes": {"fractions": {"water_or_wet": 0.01,
                                               "dense_vegetation": 0.6126}},
                     "stats": {"coverage": {"valid_fraction": 0.74}}},
            "bui": {"stats": {"mean": -0.8, "histogram": {
                "bin_edges": [-1.0, 0.0, 0.2, 1.0], "counts": [90, 8, 2]}}}},
    }


def terrain() -> dict:
    return {"aoi": {"land_area_km2": 1329.0},
            "elevation": {"stats": {"mean": 43.0, "min": -9.9, "max": 458.2}},
            "slope": {"stats": {"mean": 6.65, "p90": 14.9},
                      "classes": {"fractions": {"flat": 0.5, "steep": 0.0921,
                                                "very_steep": 0.0064}}}}


def record() -> SessionRecord:
    rec = SessionRecord(session_id="s-Test42")
    rec.place = Place(display="Kelani basin", bbox=(79.8, 6.783, 80.217, 7.083))
    rec.window = Window(start="2025-11-24", end="2025-12-01")
    return rec


class Sandbox:
    """A temporary SESSIONS_DIR for one test."""

    def __enter__(self) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = session.SESSIONS_DIR
        session.SESSIONS_DIR = Path(self.tmp.name)
        return session.SESSIONS_DIR

    def __exit__(self, *exc) -> None:
        session.SESSIONS_DIR = self.saved
        self.tmp.cleanup()


BBOX = [79.8, 6.783, 80.217, 7.083]


def worldcover() -> dict:
    return {"aoi": {"bbox": BBOX, "land_area_km2": 1320.0},
            "classes": {"fractions": {"tree_cover": 0.55, "built_up": 0.12,
                                      "cropland": 0.33}},
            "coverage": {"valid_fraction": 1.0, "valid_fraction_is_of": "land"},
            "accuracy": {"overall_pct": 76.7, "per_class": {
                "tree_cover": {"users_pct": 80.0, "producers_pct": 91.9},
                "built_up": {"users_pct": 65.9, "producers_pct": 73.2}}},
            "source": {"time_start": "2021-01-01T00:00:00Z",
                       "time_end": "2021-12-31T23:59:59Z"}}


def s2_failure(**params) -> dict:
    return {"error_type": "runtime_error", "exception": "BelowValidFraction",
            "message": "BelowValidFraction: no Sentinel-2 scene reaches valid "
                       "fraction 0.7 over the AOI (12 probed; best was 0.6076)",
            "params": {"bbox": BBOX, **params}}


def _sheet_indices(store) -> str:
    text = fact_sheet.build(record(), store)
    return text.split("## Ranking inputs", 1)[1].split("##", 1)[0]


def test_worldcover_fallback_needs_a_recorded_failure() -> None:
    """D-8: WorldCover counts only when Sentinel-2 is on record as failing."""
    with Sandbox():
        store = session_store.for_record(record())
        store.create()
        for name, payload in (("weather_grid", weather()), ("terrain", terrain()),
                              ("worldcover", worldcover()),
                              ("data_availability", availability())):
            store.cache_input(name, payload)
        block = _sheet_indices(store)
        assert "status: MISSING" in block and "fetch_lulc_data" in block, block
        assert "land_cover_fallback: not usable" in block, block

        store.record_failure("lulc", s2_failure(), params=s2_failure()["params"])
        block = _sheet_indices(store)
        assert "status: READY" in block, block
        assert "land_cover_source: fetch_worldcover (FALLBACK" in block, block
        assert "veg_pct: 55.0" in block and "from fetch_worldcover" in block, block
        assert "urban_pct: 12.0" in block, block
        # steep + very_steep, slope >= 15 degrees (restored 2026-11-21).
        assert "steep_pct: 9.85" in block and "from fetch_terrain_data" in block, block


def test_worldcover_fallback_refused_when_stricter_or_elsewhere() -> None:
    with Sandbox():
        store = session_store.for_record(record())
        store.create()
        for name, payload in (("weather_grid", weather()), ("terrain", terrain()),
                              ("worldcover", worldcover()),
                              ("data_availability", availability())):
            store.cache_input(name, payload)
        # A shorter lookback: the threshold no longer counts, since the tool
        # runs at 0.7 whatever it is sent (user decision 2026-11-01).
        stricter = s2_failure(lookback_days=30)
        store.record_failure("lulc", stricter, params=stricter["params"])
        block = _sheet_indices(store)
        assert "MISSING" in block and "stricter than the default" in block, block

        other = s2_failure()
        other["params"]["bbox"] = [80.0, 7.0, 80.1, 7.1]
        store.record_failure("lulc", other, params=other["params"])
        assert "different bounding box" in _sheet_indices(store)

        tiling = {**s2_failure(), "exception": "ValueError"}
        store.record_failure("lulc", tiling, params=tiling["params"])
        assert "not the cloud threshold" in _sheet_indices(store)

        relaxed = s2_failure(min_valid_fraction=0.5, lookback_days=120)
        store.record_failure("lulc", relaxed, params=relaxed["params"])
        assert "status: READY" in _sheet_indices(store),             "a failure under MORE permissive settings is evidence for the fallback"


def test_sentinel2_wins_and_success_clears_the_failure() -> None:
    with Sandbox():
        store = session_store.for_record(record())
        store.create()
        store.record_failure("lulc", s2_failure(), params=s2_failure()["params"])
        for name, payload in (("weather_grid", weather()), ("terrain", terrain()),
                              ("worldcover", worldcover()), ("lulc", lulc()),
                              ("data_availability", availability())):
            store.cache_input(name, payload)
        assert store.failure("lulc") is None, "a success must clear the failure"
        block = _sheet_indices(store)
        assert "land_cover_source: fetch_lulc_data (Sentinel-2)" in block, block
        assert "veg_pct: 61.26" in block, block


def test_monty_records_the_failure() -> None:
    """A failed fetch_lulc_data lands in inputs/_failures/, with its params."""
    from core import graph
    from core.state import new_state

    def fake_run_monty(request, attempt=1, debug=None):
        return {"results": {"result_1": {
            "task": "fetch_lulc_data", "output": None, "status": "error",
            "message": "BelowValidFraction: no Sentinel-2 scene ...",
            "error": {**s2_failure(), "stage": "execute_tasks",
                      "task": "fetch_lulc_data"}}},
            "tasks": request["tasks"], "error": {"error_type": "runtime_error"},
            "exchange_log": [], "execution_logs": []}

    original = graph.run_monty
    graph.run_monty = fake_run_monty
    try:
        with Sandbox():
            rec = record()
            state = dict(new_state("land cover"))
            state["session"] = rec
            state["monty_request"] = {"event_id": 1, "tasks": [
                {"function": "fetch_lulc_data", "params": {"bbox": BBOX}}]}
            graph.monty_node(state)
            saved = session_store.for_record(rec).failure("lulc")
            assert saved and saved["error"]["exception"] == "BelowValidFraction", saved
            assert saved["params"]["bbox"] == BBOX, saved
            assert "traceback" not in saved["error"]
    finally:
        graph.run_monty = original


def test_session_gets_a_folder() -> None:
    with Sandbox() as root:
        rec = record()
        session.save(rec)
        assert (root / "s-test42" / "session.json").is_file()
        assert session.load("s-Test42").place.display == "Kelani basin"
        store = session_store.for_record(rec)
        assert store.dir == root / "s-test42"
        assert store.rasters_dir == root / "s-test42" / "rasters"


def test_legacy_flat_record_still_loads() -> None:
    with Sandbox() as root:
        rec = record()
        (root / "s-Test42.json").write_text(
            __import__("json").dumps(rec.to_dict()), encoding="utf-8")
        assert session.load("s-Test42").place.display == "Kelani basin"


def test_indices_ready_and_equal_the_ranking() -> None:
    from core.workflows.mcdm import indices_from_payloads

    with Sandbox():
        rec = record()
        store = session_store.for_record(rec)
        for name, payload in (("weather_grid", weather()), ("lulc", lulc()),
                              ("terrain", terrain()), ("data_availability", availability())):
            store.cache_input(name, payload)
        fact_sheet.write(rec, store)
        text = fact_sheet.read(rec)
    expected = indices_from_payloads(weather(), lulc(), terrain(),
                                     availability=availability())["indices"]
    assert "status: READY" in text, text
    for key, value in expected.items():
        assert f"{key}: {round(value, 4)}" in text, (key, text)
    assert "storm_total_mm: 96" in text and "dense_vegetation 61.3" in text, text


def test_missing_names_the_workflow() -> None:
    with Sandbox():
        rec = record()
        store = session_store.for_record(rec)
        store.cache_input("weather_grid", weather())
        store.cache_input("terrain", terrain())
        text = fact_sheet.build(rec, store)
    assert "status: MISSING -- needs" in text and "fetch_lulc_data" in text, text
    assert "veg_pct: -- (needs fetch_lulc_data)" in text, text
    # Cloud and wind are read at Module 1's passes (2026-11-22).
    assert "cloud_pct: -- (needs data_availability)" in text, text
    assert "not stored:" in text and "fetch_lulc_data" in text


def test_new_aoi_moves_results_aside() -> None:
    with Sandbox() as root:
        rec = record()
        store = session_store.for_record(rec)
        store.cache_input("weather_grid", weather())
        moved = store.archive("AOI changed")
        assert moved is not None and (moved / "weather_grid.json").is_file()
        assert store.cached_input("weather_grid") is None
        assert "Weather:" not in fact_sheet.build(rec, store)
        assert moved.parent == root / "s-test42" / "previous"


def test_every_prompt_carries_the_sheet() -> None:
    from core import graph

    with Sandbox():
        rec = record()
        store = session_store.for_record(rec)
        store.cache_input("weather_grid", weather())
        fact_sheet.write(rec, store)
        context = graph._active_context({"session": rec})
    assert "### Session fact sheet" in context and "storm_total_mm: 96" in context


def test_rasters_routed_only_where_accepted() -> None:
    from core import graph

    target = Path("sessions/x/rasters")
    task = graph._with_output_dir({"function": "fetch_terrain_data",
                                   "params": {"bbox": [0, 0, 1, 1]}}, target)
    assert task["params"]["output_dir"] == str(target)
    kept = graph._with_output_dir({"function": "fetch_terrain_data",
                                   "params": {"output_dir": "mine"}}, target)
    assert kept["params"]["output_dir"] == "mine"
    # A tool with no output_dir gets none (binding it would fail the task).
    weather = graph._with_output_dir({"function": "inform_risk", "params": {}}, target)
    assert "output_dir" not in weather["params"]
    # Module 6 draws its matrix figure since 2026-11-22, so it is routed.
    matrix = graph._with_output_dir({"function": "mcdm_decision_matrix",
                                     "params": {}}, target)
    assert matrix["params"]["output_dir"] == str(target)
    # The ranking draws its closeness meter since 2026-11-19, so it is routed.
    ranking = graph._with_output_dir({"function": "rank_flood_products",
                                      "params": {}}, target)
    assert ranking["params"]["output_dir"] == str(target)


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
