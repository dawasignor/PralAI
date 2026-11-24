"""The validation hop: Monty's accuracy_metrics result goes to Entro in the same run.

    pytest tests/test_interpretation_hop.py
    python tests/test_interpretation_hop.py

No model, no network. Held still: the Entro card declares the claim; the claim
is pending only after a SUCCESSFUL accuracy_metrics task and only until the
flow has run; and the flow is fed this run's result -- with raster and file
paths stripped -- even when no experiment is named (the console sends none).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import graph                                          # noqa: E402
from core.spec import load_flow                                  # noqa: E402

RESULT = {"table": [{"product": "GFM (Sentinel-1)", "f1": 0.465}],
          "outputs": {"csv": "x.csv"},
          "products": [{"id": "gfm", "raster": "secret.tif"}]}


def state(status: str = "success", flows_run=None) -> dict:
    return {"task": "how well does GFM agree with UNOSAT?",
            "findings": [{"agent": "Monty", "summary": "1/1", "detail": ""}],
            "flows_run": flows_run or [],
            "monty_result": {"results": {"result_1": {
                "task": "accuracy_metrics", "status": status, "output": RESULT}}}}


def test_card_declares_the_claim() -> None:
    flow = load_flow("entro", "accuracy_interpretation")
    assert flow.after_workflow == "accuracy_metrics"
    assert flow.consumes == ["accuracy_metrics"]
    assert flow.knowledge_note == "Agreement metrics for binary flood maps"


def test_pending_after_success_only() -> None:
    assert graph._pending_interpretation(state()) == ("entro", "accuracy_interpretation")
    assert graph._pending_interpretation(state(status="error")) is None


def test_not_pending_once_run() -> None:
    done = state(flows_run=["entro:accuracy_interpretation"])
    assert graph._pending_interpretation(done) is None


def test_fed_from_this_run_without_an_experiment() -> None:
    fed, lines = graph._flow_inputs(state(), "accuracy_interpretation", "entro")
    assert "### accuracy_metrics" in fed and "computed by Monty in this run" in fed
    assert "0.465" in fed
    assert "secret.tif" not in fed            # raster paths are stripped
    assert any("served ['accuracy_metrics']" in line for line in lines)


def test_absent_result_is_reported_absent() -> None:
    empty = {"task": "x", "monty_result": {}}
    fed, _ = graph._flow_inputs(empty, "accuracy_interpretation", "entro")
    assert "ABSENT" in fed


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {test.__name__} {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
