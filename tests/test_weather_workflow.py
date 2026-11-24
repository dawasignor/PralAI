#!/usr/bin/env python
"""Monty's task-batch contract, and the retired fetch_weather_data.

    python tests/test_weather_workflow.py            # live: hits Open-Meteo
    python tests/test_weather_workflow.py --offline  # contract only, no network

Checks, in order:

    1. registry     -- fetch_weather_data is retired (not routable, file
                       kept) and fetch_weather replaces it
    2. contract     -- result_1..result_N shape, batching, failure isolation
    3. live         -- the retired point function, called directly, still
                       returns what it claims (kept until ledger row B1 is
                       deleted; tests/test_weather_grid.py is its successor)
    4. self-heal    -- Argos fixes a wrong parameter name of fetch_weather
                       and re-sends

The point tool was retired on 2026-10-25; this file is
ledger row B2 and goes when B1 is deleted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import os
os.environ.setdefault("MEMORY_WRITE", "0")
os.environ.setdefault("EXCHANGE_LOG", "off")
os.environ.setdefault("EXCHANGE_LOG_ECHO", "0")

LAT, LON = 55.9533, -3.1883          # Edinburgh
BBOX = [-3.25, 55.90, -3.10, 56.00]  # around it, for fetch_weather
START, END = "2026-08-18", "2026-08-20"
SERIES = ["cloud_cover", "hourly_rain", "daily_rain", "wind_speed",
          "shortwave_radiation", "soil_moisture_surface",
          "soil_moisture_root_zone"]


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def fail(msg: str):
    raise SystemExit(f"\n  FAIL: {msg}")


# ---------------------------------------------------------------------------
# 1. registry
# ---------------------------------------------------------------------------
def check_registry():
    from core import workflows
    from core.workflows import weather

    if "fetch_weather_data" in workflows.names():
        fail("fetch_weather_data is still routable; it was retired (D-6)")
    if workflows.RETIRED.get("fetch_weather_data", ("",))[0] != "fetch_weather":
        fail(f"fetch_weather_data is not recorded as retired for fetch_weather: "
             f"{workflows.RETIRED}")
    if not callable(weather.fetch_weather_data):
        fail("the retired function no longer imports; its file must be kept")
    entry = workflows.get("fetch_weather")
    if entry is None or not entry.description:
        fail("fetch_weather is not registered with a description")
    print(f"  registered : {workflows.names()}")
    print(f"  retired    : {sorted(workflows.RETIRED)}")
    print("\n  OK: the point tool is retired and fetch_weather replaces it.")


# ---------------------------------------------------------------------------
# 2. contract -- shape, batching, failure isolation (no network)
# ---------------------------------------------------------------------------
def check_contract():
    from core import workflows
    from core.monty_graph import run_monty

    # A local workflow so the contract can be tested without the network.
    if "echo_probe" not in workflows.names():
        @workflows.workflow("echo_probe", "Test helper: echoes its argument.")
        def echo_probe(value):
            if value == "boom":
                raise ValueError("probe exploded on purpose")
            return {"echoed": value}

    state = run_monty({"event_id": 99, "tasks": [
        {"function": "echo_probe", "params": {"value": "one"}},
        {"function": "echo_probe", "params": {"value": "boom"}},
        {"function": "echo_probe", "params": {"wrong_name": "x"}},
        {"function": "no_such_function", "params": {}},
    ]})
    results = state["results"]

    if list(results) != ["result_1", "result_2", "result_3", "result_4"]:
        fail(f"keys should be result_1..result_4, got {list(results)}")

    r1 = results["result_1"]
    # The tool contract adds uncertainty, sources and run
    # to every successful payload; what the tool itself returned is unchanged.
    own = {k: v for k, v in (r1["output"] or {}).items()
           if k not in workflows.CONTRACT_KEYS}
    if r1["status"] != "success" or own != {"echoed": "one"}:
        fail(f"result_1 wrong: {r1}")
    if not set(workflows.CONTRACT_KEYS) <= set(r1["output"]):
        fail(f"result_1 lacks the contract blocks: {sorted(r1['output'])}")
    if r1["message"] != "echo_probe completed successfully":
        fail(f"success message should name the task, got {r1['message']!r}")
    if r1["error"] is not None:
        fail("a successful task must carry error=None")

    expected = {"result_2": "runtime_error",
                "result_3": "signature_mismatch",
                "result_4": "unknown_function"}
    for key, error_type in expected.items():
        got = results[key]["error"]["error_type"]
        if got != error_type:
            fail(f"{key} should be {error_type}, got {got}")
        if results[key]["output"] is not None:
            fail(f"{key} failed but still carries an output")
        # A failure carries `message` too, mirroring error["message"]. It used
        # to be omitted, so reading result["message"] raised KeyError on
        # exactly the path a caller most needs to report.
        if "message" not in results[key]:
            fail(f"{key} has no top-level message; failure and success "
                 "results must have the same shape")
        if results[key]["message"] != results[key]["error"]["message"]:
            fail(f"{key} message {results[key]['message']!r} does not mirror "
                 f"error.message {results[key]['error']['message']!r}")

    print(f"  result_1 : success -- {r1['message']}")
    for key, error_type in expected.items():
        print(f"  {key} : error   -- {error_type} -- "
              f"{results[key]['message'][:44]}")

    # Failure isolation: a later task still ran after an earlier one raised.
    if results["result_1"]["status"] != "success":
        fail("failure isolation broken")

    # The batch-level error is the FIRST failure, so Argos fixes one thing.
    if state["error"]["error_type"] != "runtime_error":
        fail(f"batch error should be the first failure, got {state['error']}")

    # Bad payloads never reach execution.
    empty = run_monty({"event_id": 1, "tasks": []})
    if empty["error"]["error_type"] != "bad_payload":
        fail(f"empty task list should be bad_payload, got {empty['error']}")

    print("\n  OK: contract holds -- ordered results, isolated failures, "
          "first-failure reporting.")


# ---------------------------------------------------------------------------
# 3. live Open-Meteo call
# ---------------------------------------------------------------------------
def check_live():
    # Called directly: it is no longer routable, but the file is kept and
    # still has to do what it says until the user approves its deletion.
    from core.workflows.weather import fetch_weather_data

    output = fetch_weather_data(latitude=LAT, longitude=LON,
                                start_date=START, end_date=END)
    series = output["series"]
    if sorted(series) != sorted(SERIES):
        fail(f"expected {sorted(SERIES)}, got {sorted(series)}")

    print(f"  resolved to: {output['location']['latitude']}, "
          f"{output['location']['longitude']} "
          f"(elev {output['location']['elevation_m']} m)")
    for name in SERIES:
        s = series[name]
        values = [v for v in s["values"] if v is not None]
        span = f"{min(values):g}..{max(values):g}" if values else "no data"
        print(f"  {name:<20} n={len(s['values']):>3} "
              f"range={span:<16} unit={s['unit']}")
        if len(s["time"]) != len(s["values"]):
            fail(f"{name}: {len(s['time'])} timestamps vs {len(s['values'])} values")

    # 3 days requested -> 72 hourly points, 3 daily points.
    if len(series["cloud_cover"]["values"]) != 72:
        fail(f"expected 72 hourly points, got {len(series['cloud_cover']['values'])}")
    if len(series["daily_rain"]["values"]) != 3:
        fail(f"expected 3 daily points, got {len(series['daily_rain']['values'])}")

    # Variables are read back by index, so a mis-ordered request would silently
    # mislabel every series. These two checks would catch that.
    ghi = series["shortwave_radiation"]["values"]
    if any(v for v in ghi[0:5] if v):
        fail(f"GHI should be 0 overnight, got {ghi[0:5]} -- index mapping wrong?")
    if not max(v or 0 for v in ghi) > 100:
        fail("GHI never rises during the day -- index mapping wrong?")

    hourly, daily = series["hourly_rain"]["values"], series["daily_rain"]["values"]
    rolled = [round(sum(v or 0 for v in hourly[i * 24:(i + 1) * 24]), 2)
              for i in range(3)]
    if rolled != [round(v, 2) for v in daily]:
        fail(f"hourly rain sums {rolled} != daily rain {daily}")

    # Soil moisture is appended to the hourly request, so it is the pair most
    # exposed to an index shift. Volumetric soil water is a physical fraction:
    # it cannot leave [0, 1], and porosity keeps real soils under ~0.7.
    for name in ("soil_moisture_surface", "soil_moisture_root_zone"):
        values = [v for v in series[name]["values"] if v is not None]
        if not values:
            fail(f"{name} returned no values")
        if not all(0.0 <= v <= 1.0 for v in values):
            fail(f"{name} outside [0, 1] m^3/m^3: {min(values)}..{max(values)}"
                 " -- index mapping wrong?")
        if max(values) > 0.7:
            fail(f"{name} peaks at {max(values)}, above plausible porosity"
                 " -- index mapping wrong?")

    # Both layers describe one soil column, so they track each other. An index
    # shift would decouple them, or swap one for a variable in other units --
    # wind in km/h or GHI in W/m^2 would fail the range check above outright.
    surface = [v for v in series["soil_moisture_surface"]["values"] if v is not None]
    root = [v for v in series["soil_moisture_root_zone"]["values"] if v is not None]
    gap = max(abs(a - b) for a, b in zip(surface, root))
    if gap > 0.3:
        fail(f"soil layers differ by up to {gap} m^3/m^3 -- index mapping wrong?")

    # Times are local to the AOI; the zone is resolved from the coordinates.
    # The OFFSET is no longer asserted (user decision 2026-10-26, BUG-008):
    # this retired tool takes today's offset from Open-Meteo, not the event's,
    # so after UK summer time ended (2026-10-25) August read +0. That is
    # BUG-017, fixed for the live tools in core/dating.py; this tool is kept
    # only until ledger row B1 deletes it, and a real fix would matter only if
    # it came back.
    loc = output["location"]
    if loc["timezone"] != "Europe/London":
        fail(f"expected the Europe/London zone, got {loc}")
    if loc["timezone_requested"] != "auto":
        fail(f"default should request auto, got {loc['timezone_requested']}")
    first = series["daily_rain"]["time"][0]
    if not first.startswith(START):
        fail(f"daily stamp should be {START}, got {first}")
    print(f"\n  timezone resolved to {loc['timezone']} "
          f"({loc['timezone_abbreviation']}); first day {first}")

    print(f"  GHI 0 overnight, peaks at {max(v or 0 for v in ghi):g} W/m^2")
    print(f"  hourly rain summed by day {rolled} == daily_rain {daily}")
    print(f"  soil moisture within [0, 1]; layers agree to {gap:.3f} m^3/m^3")
    print("\n  OK: live call correct, and each series is the variable it claims.")


# ---------------------------------------------------------------------------
# 4. self-healing through the full graph
# ---------------------------------------------------------------------------
def check_self_heal():
    import core.llm as llm_mod
    from core.graph import build_graph
    from core.llm import LLMClient, LLMResponse
    from core.state import new_state

    # Argos' turns, hand-written: a wrong parameter name, then the fix.
    replies = iter([
        json.dumps({"next": "monty", "brief": "get the weather", "event_id": 42,
                    "tasks": [{"function": "fetch_weather",
                               "params": {"box": BBOX, "start_date": START,
                                          "end_date": END,
                                          "write_figure": False}}]}),
        json.dumps({"diagnosis": "signature wants bbox, I sent box",
                    "fix": "params",
                    "patched_tasks": [{"function": "fetch_weather",
                                       "params": {"bbox": BBOX,
                                                  "start_date": START,
                                                  "end_date": END,
                                                  "write_figure": False}}],
                    "brief": "retry with the real parameter names"}),
        json.dumps({"next": "finish", "answer": "Daily rain retrieved."}),
    ])
    modes = []
    llm_mod._build_client = lambda slot: None

    def canned(self, *, system, messages, tools=None, max_tokens=None, stream=None):
        modes.append("debug" if "You are now a **debugger**" in system else "route")
        return LLMResponse(text=next(replies), stop_reason="end_turn")

    LLMClient.complete = canned
    state = build_graph().invoke(new_state("Get rainfall for the event."),
                                 {"recursion_limit": 40})

    if modes != ["route", "debug", "route"]:
        fail(f"expected route/debug/route, got {modes}")
    if state["monty_attempts"] != 2:
        fail(f"expected 2 attempts, got {state['monty_attempts']}")
    if state["monty_result"]["status"] != "success":
        fail(f"retry did not recover: {state['monty_result']}")
    if state["steps"] != 2:
        fail(f"a retry must not spend a delegation step; steps={state['steps']}")

    output = state["monty_result"]["results"]["result_1"]["output"]
    total = output["event_summary"]["storm_total_mm"]
    print(f"  attempt 1  : signature_mismatch (box)")
    print(f"  argos      : {state['monty_debug']['diagnosis']}")
    print(f"  attempt 2  : success -- storm total {total}")
    print("\n  OK: self-healed on the task list, without spending a step.")


# ---------------------------------------------------------------------------
def main() -> int:
    offline = "--offline" in sys.argv

    hr("1. REGISTRY")
    check_registry()

    hr("2. BATCH CONTRACT  (no network)")
    check_contract()

    if offline:
        hr("SKIPPED: live Open-Meteo call and self-heal (--offline)")
        print("  Rerun without --offline to exercise the API.")
        return 0

    hr("3. LIVE OPEN-METEO CALL")
    print(f"  {LAT}, {LON} from {START} to {END}")
    check_live()

    hr("4. SELF-HEAL  (Argos fixes a wrong parameter name)")
    check_self_heal()

    hr("RESULT")
    print("  the batch contract holds, the retired point function still works, "
          "and fetch_weather self-heals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
