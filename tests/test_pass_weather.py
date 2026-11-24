"""Cloud and wind at the acquisitions, in local time (user decision 2026-11-22).

Cloud (W1) is read at the optical passes, wind (W2) at the radar passes, each
interpolated between the model hours either side and averaged; every time is
converted to the event's local zone before it is matched or counted
(guardrail). No model, no network. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pass_weather import (LocalTimeError, NoPassWeather,  # noqa: E402
                               pass_conditions, passes)

BBOX = [79.945, 6.88, 80.155, 7.0]          # centre 80.05 E


def weather(cloud=None, wind=None, hours=72, start="2025-11-24") -> dict:
    """Hourly UTC stamps from `start` 00:00Z; values by hour index."""
    from datetime import datetime, timedelta, timezone

    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    times = [(t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%MZ") for h in range(hours)]
    return {"meta": {"storage_tz": "UTC", "local_utc_offset_h": 5.5, "bbox": BBOX},
            "hourly": {"time": times, "vars": {
                "cloud_cover_model": {"mean": cloud or [50.0] * hours},
                "wind_speed_10m": {"mean": wind or [4.0] * hours}}}}


def availability(days=("2025-11-25",), radar=(), hls=(), zone="Asia/Colombo") -> dict:
    def product(pid, stamps):
        return {"id": pid, "days": {"x": {"acquisitions": [{"utc": u} for u in stamps]}}}
    return {"aoi": {"bbox": BBOX},
            "period": {"days": list(days), "timezone": zone},
            "products": [product("gfm", radar), product("dswx_s1", radar),
                         product("dswx_hls", hls), {"id": "mcdwd", "days": {}},
                         {"id": "vcdwd", "days": {}}]}


def test_a_pass_belongs_to_the_window_by_its_local_date() -> None:
    from zoneinfo import ZoneInfo

    # 19:00Z on 24 Nov is 00:30 on 25 Nov in Colombo; 18:00Z is 23:30 on the 24th.
    av = availability(radar=["2025-11-24T19:00:00Z", "2025-11-24T18:00:00Z"])
    got = passes(av, ZoneInfo("Asia/Colombo"), 80.05)["gfm"]
    assert [p["local"].isoformat(timespec="minutes") for p in got] == \
        ["2025-11-25T00:30+05:30"], got


def test_nominal_overpass_is_solar_time_turned_into_civil_local_time() -> None:
    from zoneinfo import ZoneInfo

    got = passes(availability(), ZoneInfo("Asia/Colombo"), 80.05)
    terra, aqua = got["mcdwd"]
    assert terra["local"].strftime("%H:%M %z") == "10:39 +0530", terra
    assert aqua["local"].strftime("%H:%M") == "13:39" and got["vcdwd"][0]["local"].strftime(
        "%H:%M") == "13:39"


def test_values_are_interpolated_between_local_hours() -> None:
    # Radar pass at 00:25:15Z = 05:55:15 local, between local 05:30 (00Z) and 06:30 (01Z).
    hours = 72
    wind = [0.0] * hours
    wind[24], wind[25] = 2.0, 8.0                  # 25 Nov 00Z and 01Z
    out = pass_conditions(weather(wind=wind),
                          availability(radar=["2025-11-25T00:25:15Z"]))
    expected = 2.0 + 6.0 * (25 * 60 + 15) / 3600
    assert abs(out["wind"]["value"] - expected) < 1e-3, out["wind"]
    assert out["wind"]["list"][0]["local"] == "2025-11-25T05:55+05:30"


def test_cloud_reads_only_optical_passes_and_wind_only_radar() -> None:
    hours = 72
    cloud = [100.0] * hours
    for h in range(24, 48):                        # 25 Nov UTC: clear except at the radar pass
        cloud[h] = 0.0
    cloud[24] = cloud[25] = 100.0                  # cloudy around the 00:25Z radar pass
    out = pass_conditions(weather(cloud=cloud),
                          availability(radar=["2025-11-25T00:25:00Z"],
                                       hls=["2025-11-25T05:10:00Z"]))
    assert out["cloud"]["value"] == 0.0, out["cloud"]          # radar's cloud ignored
    assert {tuple(r["products"]) for r in out["wind"]["list"]} == {("gfm", "dswx_s1")}


def test_one_sentinel1_pass_is_counted_once() -> None:
    out = pass_conditions(weather(), availability(
        radar=["2025-11-25T00:25:15Z"]))           # GFM and DSWx-S1 both list it
    assert out["wind"]["passes"] == 1 and out["wind"]["list"][0]["products"] == ["gfm", "dswx_s1"]


def test_local_time_must_be_established() -> None:
    for bad, why in ((availability(zone=""), "no zone"),
                     (availability(zone="Mars/Olympus"), "unknown zone")):
        try:
            pass_conditions(weather(), bad)
            raise AssertionError(f"{why} was accepted")
        except LocalTimeError:
            pass
    w = weather()
    w["meta"]["storage_tz"] = "Asia/Colombo"
    try:
        pass_conditions(w, availability(radar=["2025-11-25T00:25:00Z"]))
        raise AssertionError("hours not stored in UTC were accepted")
    except LocalTimeError:
        pass
    w = weather()
    w["hourly"]["time"] = [t.rstrip("Z") for t in w["hourly"]["time"]]
    try:
        pass_conditions(w, availability(radar=["2025-11-25T00:25:00Z"]))
        raise AssertionError("hours with no zone were accepted")
    except LocalTimeError:
        pass


def test_a_display_offset_that_disagrees_is_noted_not_used() -> None:
    w = weather()
    w["meta"]["local_utc_offset_h"] = 0.0          # Module 2's default display setting
    out = pass_conditions(w, availability(radar=["2025-11-25T00:25:00Z"]))
    assert out["notes"] and "UTC+0" in out["notes"][0]


def test_gaps_are_withheld_and_none_left_is_not_known() -> None:
    hours = 72
    wind = [4.0] * hours
    wind[24] = None                                 # the hour before the 00:25Z pass
    out = pass_conditions(weather(wind=wind), availability(
        radar=["2025-11-25T00:25:00Z", "2025-11-25T12:49:00Z"]))
    assert out["wind"]["passes"] == 1 and out["wind"]["withheld"] == 1
    try:
        pass_conditions(weather(wind=[None] * hours),
                        availability(radar=["2025-11-25T00:25:00Z"]))
        raise AssertionError("no pass with data still gave a wind value")
    except NoPassWeather:
        pass


def test_the_ranking_inputs_need_module_1() -> None:
    from core.workflows.mcdm import indices_from_payloads

    try:
        indices_from_payloads(weather(), None, {}, availability=None)
        raise AssertionError("indices were built without the passes")
    except ValueError as exc:
        assert "data_availability" in str(exc)


def test_kelani_values_where_the_session_exists() -> None:
    root = Path(__file__).resolve().parents[1] / "sessions" / "kelani-basin-2025" / "inputs"
    if not (root / "weather_grid.json").is_file() or not (root / "data_availability.json").is_file():
        return
    load = lambda n: json.loads((root / f"{n}.json").read_text(encoding="utf-8"))["payload"]  # noqa: E731
    out = pass_conditions(load("weather_grid"), load("data_availability"))
    assert out["timezone"] == "Asia/Colombo"
    assert out["cloud"]["passes"] == 20 and abs(out["cloud"]["value"] - 62.8403) < 1e-3, out["cloud"]
    assert out["wind"]["passes"] == 5 and abs(out["wind"]["value"] - 3.2444) < 1e-3, out["wind"]
    assert all(r["local"].endswith("+05:30") for r in out["cloud"]["list"] + out["wind"]["list"])


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
