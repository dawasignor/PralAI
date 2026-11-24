"""The sources under an answer, in each persona's register (core/attribution.py).

    python tests/test_attribution.py        # no model, no network, no cost

User decision 2026-11-01: an answer that rests on fetched data names the
source and its date -- plainly for Flood bot ("OpenStreetMap, updated on
1 Nov 2026"), in more detail for Chanakya and Norwood -- with dates and no
times. Held here: what counts as used (this turn's fetches and the stored
payloads read), the derived results left out, each register, and no block for
an answer that used no data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import attribution as A                                # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail and not ok else ""))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(label)


ZONE = "Asia/Bangkok"
OSM = {"retrieved_at": "2026-11-01T11:16:00Z"}
WEATHER = {"meta": {"window_utc": ["2026-09-24T00:00Z", "2026-09-30T23:00Z"],
                    "source": {"model": "ecmwf_ifs", "retrieved_at": "2026-11-01T11:16:00Z"}}}
SCENE = {"scene": {"id": "S2B_T47PPR_20260803", "datetime": "2026-08-03T03:45:00Z"}}


def test_used() -> None:
    mr = {"results": {
        "r1": {"task": "where_am_i", "status": "success", "output": OSM},
        "r2": {"task": "rank_flood_products", "status": "success", "output": {}},
        "r3": {"task": "fetch_terrain_data", "status": "error", "output": None}}}
    A.begin()
    A.note_read("fetch_lulc_data", SCENE)
    got = [n for n, _ in A.used(mr)]
    # The ranking and the matrix stay (2026-11-22): their score evidence is a
    # source a reader can check. Other derived results still do not.
    check("this turn's fetches, then payloads read, by store name",
          got == ["where_am_i", "rank_flood_products", "lulc"], str(got))
    mr2 = {"results": {"r1": {"task": "sensitivity_analysis", "status": "success", "output": {}}}}
    A.begin()
    check("a derived result has no source of its own", A.used(mr2) == [], str(A.used(mr2)))
    check("a failed fetch is not a source", "terrain" not in got)
    A.begin()
    check("each turn starts empty", A.used(None) == [])


def test_flood_bot() -> None:
    text = A.render([("where_am_i", OSM), ("weather_grid", WEATHER), ("lulc", SCENE)],
                    "flood_bot", ZONE)
    check("the plain heading", "**Where this information comes from**" in text, text)
    check("OpenStreetMap, updated on a readable date",
          "- OpenStreetMap, updated on 1 Nov 2026" in text, text)
    check("the weather days", "weather for 24-30 Sep 2026" in text, text)
    check("the scene date", "image taken 3 Aug 2026" in text, text)
    check("no clock time in the plain register", "UTC" not in text and ":" not in
          text.replace("**", "").split("comes from")[1], text)


def test_technical() -> None:
    for persona in ("chanakya", "norwood", ""):
        text = A.render([("where_am_i", OSM)], persona, ZONE)
        check(f"{persona or 'no persona'}: the full source line and its date",
              "**Sources**" in text and "(c) OpenStreetMap contributors" in text
              and "retrieved 1 Nov 2026" in text, text)


def test_nothing_used() -> None:
    check("no data, no block", A.render([], "flood_bot", ZONE) == "")


if __name__ == "__main__":
    test_used()
    test_flood_bot()
    test_technical()
    test_nothing_used()
    total = PASSED + len(FAILURES)
    print(f"\n{PASSED}/{total} passed")
    sys.exit(1 if FAILURES else 0)
