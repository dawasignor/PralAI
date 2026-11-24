"""What Argus is shown of a Monty result.

    pytest tests/test_result_view.py
    python tests/test_result_view.py

The old view was `json.dumps(results)[:4000]`, which cut fetch_weather
inside its hourly arrays: the storm totals never reached the answer. Held still
here, on synthetic payloads (no network, no model): the weather result is the
thesis renderer's text and carries the event summary however long the hourly
series; a long report is cut at whole sections and names what it left out; any
other workflow is condensed, long lists summarised; a cut is never silent; a
renderer that fails falls back to condensed JSON; a failure keeps its error.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import result_view as rv                              # noqa: E402


def weather_payload(hours: int) -> dict:
    """The smallest payload weather_markdown renders, with a long hourly series
    placed BEFORE the summary -- the order that broke the old prefix cut."""
    times = [f"2025-11-{24 + h // 24:02d}T{h % 24:02d}:00Z" for h in range(hours)]
    return {
        "meta": {"event_id": "test", "bbox": [79.8, 6.78, 80.2, 7.08],
                 "window_utc": [times[0], times[-1]], "antecedent_days": 30,
                 "fetch_window_utc": [times[0], times[-1]],
                 "source": {"model": "ecmwf_ifs -- test", "model_id": "ecmwf_ifs",
                            "native_res_km": 9, "retrieved_at": "2026-10-14T00:00:00Z"}},
        "grid": {"points": []},
        "hourly": {"time": times, "vars": {"precipitation": {"unit": "mm", "mean": [1.0] * hours}}},
        "acquisitions": [],
        "event_summary": {"storm_total_mm": {"aoi_mean": 390.97}, "max_gust_ms": 22.8},
        "derived": {}, "qc": {"warnings": []},
    }


def wrap(task: str, output, status: str = "success") -> dict:
    return {"result_1": {"task": task, "status": status, "output": output}}


def test_weather_summary_survives_a_long_series() -> None:
    view = rv.results_view(wrap("fetch_weather", weather_payload(24 * 40)))
    assert "rendered by the thesis report" in view
    assert "390.97" in view or "391.0" in view, view[:600]
    assert len(view) <= rv.REPORT_BUDGET + 500


def test_report_cut_at_sections_and_named() -> None:
    doc = "intro\n## One\n" + "a" * 5000 + "\n## Two\n" + "b" * 5000 + "\n## Three\n" + "c" * 5000
    fitted = rv.fit_sections(doc, budget=11_000)
    assert "## One" in fitted and "## Two" in fitted and "## Three" not in fitted
    assert "[TRUNCATED: 1 section(s) not shown -- Three." in fitted
    assert rv.fit_sections("short", budget=100) == "short"


def test_condense_summarises_long_lists() -> None:
    out = rv.condense({"series": list(range(100)) + [None], "few": [1, 2], "x": 3})
    assert out["few"] == [1, 2] and out["x"] == 3
    assert out["series"].startswith("[101 values withheld: min 0, max 99, mean 49.5, 1 null]")


def test_generic_view_marks_a_cut() -> None:
    huge = {f"k{i}": "v" * 50 for i in range(1000)}
    view = rv.results_view(wrap("fetch_lulc_data", huge))
    assert "[TRUNCATED:" in view and "monty_result" in view


def test_failed_renderer_falls_back() -> None:
    # The real renderers render a missing field as "--" rather than raise, so a
    # failing one is stood in to exercise the fallback.
    def broken(payload):
        raise KeyError("meta")

    original = rv.RENDERERS["fetch_weather"]
    rv.RENDERERS["fetch_weather"] = broken
    try:
        view = rv.results_view(wrap("fetch_weather", {"not": "a weather payload"}))
    finally:
        rv.RENDERERS["fetch_weather"] = original
    assert "could not be rendered: KeyError" in view
    assert '"not": "a weather payload"' in view


def test_failure_keeps_its_error() -> None:
    res = {"result_1": {"task": "fetch_lulc_data", "status": "error",
                        "error": "runtime_error", "message": "no scene cleared 0.7"}}
    view = rv.results_view(res)
    assert "error" in view and "no scene cleared 0.7" in view


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
