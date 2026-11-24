"""The weather summarizer and the hyetograph.

    pytest tests/test_weather_summary.py
    python tests/test_weather_summary.py    # no model, no network, no cost

Argus used to be handed the whole weather document; it now gets the values
output 1 is written from, and the rainfall table is a hyetograph. Held still
here, on synthetic payloads: the summary carries the storm figures and the
ranking's cloud and wind inputs; it stays small however long the series; a
missing hour withholds its day and is never counted as dry; the hyetograph is
drawn, and drawn with a gap; the output-1 template shows the hyetograph in
place of the rainfall columns; and the result view hands Argus the summary.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import charts, result_view, weather_summary as ws    # noqa: E402
from core.reports import weather_markdown                       # noqa: E402


def payload(hours: int = 72, gap_at: tuple[int, ...] = ()) -> dict:
    times = [f"2025-11-{24 + h // 24:02d}T{h % 24:02d}:00Z" for h in range(hours)]
    rain = [None if h in gap_at else (8.0 if 30 <= h < 36 else 0.5) for h in range(hours)]
    cloud = [None if h in gap_at else 90.0 for h in range(hours)]
    wind = [None if h in gap_at else 4.0 for h in range(hours)]
    return {
        "meta": {"event_id": "test", "bbox": [79.8, 6.78, 80.2, 7.08],
                 "window_utc": [times[0], times[-1]], "antecedent_days": 30,
                 "source": {"model": "ecmwf_ifs -- test", "native_res_km": 9,
                            "retrieved_at": "2026-10-14T00:00:00Z",
                            "response_sha256": "ab" * 32}},
        "grid": {"n_points": 12, "spacing_deg": 0.1, "points": []},
        "hourly": {"time": times, "vars": {
            "precipitation": {"unit": "mm", "mean": rain, "max": rain},
            "cloud_cover_model": {"unit": "%", "mean": cloud},
            "wind_speed_10m": {"unit": "m/s", "mean": wind, "p90": wind,
                               "area_frac_gt_5": [0.0] * hours},
        }},
        "acquisitions": [],
        "event_summary": {
            "storm_total_mm": {"aoi_mean": 81.0, "max_point": 95.2},
            "max_intensity_mm": {"1h": 8.0, "24h": 55.0,
                                 "t_max_24h_utc": "2025-11-25T06:00Z"},
            "antecedent_api_mm": {"d5": 12.0, "d14": 30.0, "d30": 44.0},
            "rain_bursts": [{"start_utc": times[30], "end_utc": times[35],
                             "depth_mm": 48.0, "peak_mm_h": 8.0}],
            "max_gust_ms": 14.2, "min_pressure_msl_hpa": 1004.1,
            "dry_hours_after_last_burst": 0,
        },
        "derived": [],
        "qc": {"gaps": {"window_complete": not gap_at, "antecedent_complete": True,
                        "by_var": {}, "not_served": [],
                        "withheld": ["event_summary.storm_total_mm.aoi_mean"] if gap_at else []},
               "spatial_heterogeneity": {"precipitation_cv": 0.21},
               "warnings": ["acquisitions[] is empty: ...",
                            "2 of the window's 72 hours are at or after the retrieval "
                            "time: they are ecmwf_ifs FORECAST"]},
    }


def test_summary_carries_the_storm() -> None:
    s = ws.summarize(payload())
    assert s["rain"]["storm_total_aoi_mean_mm"] == 81.0
    assert s["rain"]["peak_mm"]["24h"] == 55.0
    assert s["rain"]["antecedent_api_mm"]["d30"] == 44.0
    assert len(s["rain"]["bursts"]) == 1 and s["rain"]["bursts"][0]["depth_mm"] == 48.0
    # Window means: weather facts, not the ranking's inputs (read at the passes
    # since 2026-11-22, core/pass_weather.py).
    assert s["cloud"]["window_mean_pct"] == {"value": 90.0, "hours": 72, "of": 72}
    assert s["wind"]["window_mean_ms"]["value"] == 4.0
    # Standing caveats are left to the document; the forecast one is not.
    assert len(s["warnings"]) == 1 and "FORECAST" in s["warnings"][0], s["warnings"]


def test_summary_stays_small() -> None:
    text = ws.markdown(ws.summarize(payload(hours=24 * 40)))
    assert len(text) < 4000, len(text)
    assert "81.0 mm" in text and "hourly series are withheld" in text
    # 40 days is past the daily list: the wettest three are named instead.
    assert "Wettest days" in text and "Daily rain" not in text


def test_gap_withholds_its_day_never_dry() -> None:
    s = ws.summarize(payload(gap_at=(5,)))
    first = s["rain"]["daily_mm"][0]
    assert first["mm"] is None and first["day"] in s["rain"]["days_withheld"], s["rain"]
    assert s["cloud"]["window_mean_pct"]["hours"] == 71
    assert "INCOMPLETE" in ws.markdown(s)


def test_hyetograph_is_drawn_with_a_gap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        drawn = charts.hyetograph(payload(gap_at=(10, 11)), Path(tmp) / "h.png")
        png = Path(drawn["png"])
        assert png.is_file() and png.stat().st_size > 10_000
        assert drawn["gap_hours"] == 2 and drawn["hours"] == 72


def test_template_shows_the_hyetograph() -> None:
    p = payload()
    p["figure"] = {"name": "hyetograph", "path": "outputs/weather/test/hyetograph.png",
                   "caption": "Rainfall hyetograph."}
    doc = weather_markdown(p)
    assert "## Rainfall hyetograph" in doc
    assert "![Rainfall hyetograph](outputs/weather/test/hyetograph.png)" in doc
    assert "| Rain (mm) |" not in doc and "| missing h |" in doc
    assert doc.index("## Rainfall hyetograph") < doc.index("## Daily rollup")
    assert "_Not drawn for this payload._" in weather_markdown(payload())


def test_result_view_hands_argus_the_summary() -> None:
    view = result_view.view("fetch_weather", payload(hours=24 * 30))
    assert "## Weather summary (output 1)" in view and "81.0 mm" in view
    assert "## Sampling grid" not in view


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
