"""The 2026-11-02 console fixes: one plain line on every tool and workflow
(served and in the vault), the data inputs "Check inputs" lists, nothing of
the machine in what the console is sent, and a numeric event id never
breaking a tool.

No model and no network. Runs under pytest or as a plain script.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import one_liners, thesis_path, workflows  # noqa: E402


def test_every_available_tool_and_step_has_a_line() -> None:
    missing = [n for n in workflows.names() if not one_liners.TOOLS.get(n)]
    assert not missing, missing
    steps = [s["id"] for s in thesis_path.STEPS]
    assert not [s for s in steps if not one_liners.WORKFLOWS.get(s)], steps


def test_lines_are_served() -> None:
    listing = {w["id"]: w for w in workflows.listing()}
    assert listing["where_am_i"]["summary"] == one_liners.TOOLS["where_am_i"]
    assert {n for n, w in listing.items() if w["dataInput"]} == set(one_liners.DATA_INPUTS)
    view = {s["id"]: s for s in thesis_path.view(None)}
    assert view["essential"]["owner"] == "chanakya" and view["health"]["owner"] == "chanakya"
    assert {s["owner"] for k, s in view.items() if k not in ("essential", "health")} == {"norwood"}
    assert all(s["summary"] for s in view.values())


def test_the_vault_carries_them() -> None:
    from core import vault

    index = vault.build_index()
    moc = vault.read_note(index, "Workflows-MOC")["content"]
    for name in workflows.names():
        assert one_liners.TOOLS[name] in moc, name
    for sid, line in one_liners.WORKFLOWS.items():
        assert line in moc, sid
    note = vault.read_note(index, "Module 9 - Essential services template")["content"]
    first = next(l for l in note.split("---", 2)[2].splitlines() if l.strip())
    assert one_liners.WORKFLOWS["essential"] in first, first


def test_session_view_lists_what_was_fetched() -> None:
    from api.console_server import _session_view
    from core.session import SessionRecord

    record = SessionRecord("s-test")
    record.workflows_run = ["fetch_weather_grid", "where_am_i"]
    view = _session_view(record)
    assert view["fetched"] == ["fetch_weather", "where_am_i"], view["fetched"]


def test_nothing_of_the_machine_is_sent() -> None:
    from core import redact

    repo = Path(__file__).resolve().parents[1]
    payload = {"events": [{"detail": f"data_availability -> {repo / 'sessions' / 's-1' / 'inputs'}"},
                          {"detail": "wrote sessions/s-1/rasters/a.tif"},
                          {"detail": f"home {Path.home()}"}],
               "reply": "**207** hospitals in Bangkok, 24-30 Sep 2026",
               "figure": "<image - run-bangkok-2026-where_am_i_map>"}
    out = redact.deep(payload)
    text = str(out)
    assert str(repo) not in text and str(Path.home()) not in text
    assert "sessions/s-1" not in text and Path.home().name not in text
    assert out["reply"] == payload["reply"] and out["figure"] == payload["figure"]


def test_a_numeric_event_id_is_a_string_to_the_tool() -> None:
    from core.monty_graph import _call

    seen = {}

    def fn(bbox, output_dir="outputs", event_id=""):
        seen.update(event_id=event_id)
        return {"path": str(Path(output_dir) / event_id)}

    out = _call({"function": "t", "params": {"bbox": [0, 0, 1, 1], "event_id": 42},
                 "workflow": SimpleNamespace(fn=fn), "error": None})
    assert out["status"] == "success", out
    assert seen["event_id"] == "42"


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
