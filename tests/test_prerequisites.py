"""Suggesting the data a question stands on.

    python tests/test_prerequisites.py      # no model, no network, no cost

The weather grid and the land cover are what most questions need. Argus is
shown which of them the session has fetched, and may suggest a missing one as
the next step; Python checks the suggestion. What is held still here: the
status Argus is shown, the rules a suggested workflow must pass, and that the
session remembers what it fetched -- and forgets it when the place changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import prerequisites                                # noqa: E402
from core.session import Place, SessionRecord, Window         # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def record(*fetched: str) -> SessionRecord:
    rec = SessionRecord(session_id="p")
    rec.place = Place(name="Kelani", display="Kelani River, Sri Lanka",
                      bbox=(79.87, 6.84, 80.55, 7.04))
    rec.window = Window(start="2025-11-24", end="2025-12-01")
    rec.workflows_run = list(fetched)
    return rec


def test_block() -> None:
    block = prerequisites.prompt_block(record())
    check("every prerequisite is listed, by workflow name",
          "`fetch_weather`" in block and "`fetch_lulc_data`" in block
          and "`data_availability`" in block)
    check("with nothing fetched, each says NOT fetched",
          block.count("NOT fetched yet") == len(prerequisites.PREREQUISITES), block)
    availability = next(line for line in block.splitlines() if "`data_availability`" in line)
    check("product availability is pointed at the ranking question",
          "before ranking" in availability)
    block = prerequisites.prompt_block(record("fetch_lulc_data"))
    lulc = next(line for line in block.splitlines() if "`fetch_lulc_data`" in line)
    weather = next(line for line in block.splitlines() if "`fetch_weather`" in line)
    check("fetched land cover is marked FETCHED, weather still NOT",
          "FETCHED" in lulc and "NOT fetched" in weather)
    check("the block tells Argus to suggest, not to fetch unasked",
          "next_step.workflow" in block and "did not ask you to fetch" in block)
    check("rainfall routes to the weather grid; built-up to the land cover",
          "rainfall" in weather and "built-up" in lulc)


def test_valid_workflow() -> None:
    check("a registered, unfetched workflow is accepted",
          prerequisites.valid_workflow("fetch_lulc_data", record()) == "fetch_lulc_data")
    check("data already fetched is never suggested again",
          prerequisites.valid_workflow("fetch_lulc_data",
                                       record("fetch_lulc_data")) == "")
    check("an invented workflow name is dropped, not matched",
          prerequisites.valid_workflow("fetch_lulc", record()) == "")
    check("empty stays empty", prerequisites.valid_workflow("", record()) == "")
    check("any registered workflow may be suggested, not only the two",
          prerequisites.valid_workflow("fetch_terrain_data", record()) == "fetch_terrain_data")
    # A record from before the rename (2026-10-25) says fetch_weather_grid.
    old = record("fetch_weather_grid")
    weather = next(line for line in prerequisites.prompt_block(old).splitlines()
                   if "`fetch_weather`" in line)
    check("an old record's fetch_weather_grid counts as the weather fetched",
          "FETCHED" in weather, weather)
    check("the old name suggested is read as the new one, and not re-offered",
          prerequisites.valid_workflow("fetch_weather_grid", record()) == "fetch_weather"
          and prerequisites.valid_workflow("fetch_weather_grid", old) == "")
    check("the retired point tool is never suggested",
          prerequisites.valid_workflow("fetch_weather_data", record()) == "")


def test_session() -> None:
    rec = record("fetch_weather")
    restored = SessionRecord.from_dict(rec.to_dict())
    check("the fetched list survives a save and a load",
          restored.workflows_run == ["fetch_weather"])
    check("an old record with no such field loads as nothing fetched",
          SessionRecord.from_dict({"session_id": "old"}).workflows_run == [])
    check("the header says what has been fetched",
          "Data fetched for this AOI and window: fetch_weather" in rec.header())
    check("and says so when nothing has",
          "Data fetched for this AOI and window: none yet" in record().header())


def main() -> None:
    test_block()
    test_valid_workflow()
    test_session()
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
