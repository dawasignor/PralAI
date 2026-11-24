"""The plain-language guardrail's safety net.

    pytest tests/test_plain_language.py
    python tests/test_plain_language.py     # no model, no network, no cost

The answer is read by a flood analyst, the public or a mayor: specialists by
role, workflows by what they do, no backend words. Held still here: agent
names become roles, once and capitalised at a sentence start; a workflow
identifier becomes "the ... workflow" without doubling "the" or "workflow";
"payload" becomes "data"; a figure placeholder is never touched; Virginia
Norwood is a person, not an agent; every replacement is counted.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.plain_language import scrub, workflow_name           # noqa: E402


def test_agents_become_roles() -> None:
    text, counts = scrub("Monty fetched the rain. Then the Norwood agent read "
                         "the scene, Entro scored it and Agent Citadel advised.")
    assert text == ("The Python agent fetched the rain. Then the remote sensing "
                    "expert read the scene, the uncertainty agent scored it and "
                    "the flood analyst agent advised."), text
    assert counts["Monty"] == 1 and sum(counts.values()) == 4, counts


def test_workflow_identifier_becomes_a_name() -> None:
    text, _ = scrub("The `fetch_weather_grid` workflow ran; next, run "
                    "fetch_lulc_data.")
    assert text == ("The event weather workflow ran; next, run the land cover "
                    "workflow."), text
    assert workflow_name("rank_flood_products") == "flood product ranking"


def test_backend_words() -> None:
    text, _ = scrub("The payload holds 391 mm; see monty_result.")
    assert text == "The data holds 391 mm; see the results.", text


def test_placeholders_and_people_untouched() -> None:
    answer = ("Virginia Norwood designed the Landsat scanner.\n"
              "<image - run-kelani-2025-hyetograph>\n"
              "Chanakya's view follows.")
    text, counts = scrub(answer)
    assert "Virginia Norwood designed" in text
    assert "<image - run-kelani-2025-hyetograph>" in text
    assert "The flood analyst agent's view follows." in text, text
    assert set(counts) == {"Chanakya"}, counts


def test_clean_answer_is_unchanged() -> None:
    answer = "The storm total was 391.0 mm over the AOI, mostly on 27-28 Nov."
    assert scrub(answer) == (answer, {})


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
