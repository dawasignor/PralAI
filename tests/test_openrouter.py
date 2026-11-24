#!/usr/bin/env python
"""Is the OpenRouter configuration actually working?

Three checks, in increasing depth:

    1. config    -- .env resolves to a provider, model and key
    2. live      -- one real request reaches OpenRouter and comes back
    3. round trip -- Argos -> Monty -> a registered workflow, and the result
                    comes back as result_1

Run it:

    python tests/test_openrouter.py              # needs MODEL_1_API_KEY
    python tests/test_openrouter.py --offline    # no key: Argos' turns are canned

Offline mode skips checks 1-2 and substitutes a fixed pair of Argos replies, so
it exercises the graph, Monty and the workflow registry but proves nothing
about OpenRouter. Only the live run does that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

TASK = ("For event 42, get the hourly rainfall over the bounding box "
        "[-3.25, 55.90, -3.10, 56.00] (Edinburgh) from 2026-08-18 to 2026-08-20.")
# This test checks the round trip -- Argos -> Monty -> workflow -> back -- not
# the meteorology, so it asserts the SHAPE of the series rather than pinned
# values. Pinning them made it fail whenever the numbers legitimately moved
# (the switch to local-day buckets did exactly that), and it would fail again
# on any Open-Meteo archive revision. The values themselves are cross-validated
# in tests/test_weather_grid.py --live, which is where that belongs.
EXPECTED_HOURS = 72                    # 18-20 Aug, UTC, inclusive

# Argos' turns for --offline. Written by hand to match what the routing prompt
# asks for; they are NOT model output and prove nothing about the provider.
CANNED_ARGOS = [
    json.dumps({
        "next": "monty",
        "brief": "Fetch daily rainfall for the event window.",
        "event_id": 42,
        "tasks": [{"function": "fetch_weather",
                   "params": {"bbox": [-3.25, 55.90, -3.10, 56.00],
                              "start_date": "2026-08-18",
                              "end_date": "2026-08-20",
                              "write_figure": False}}],
    }),
    json.dumps({
        "next": "finish",
        "answer": "Hourly rainfall for 18-20 Aug 2026 came back for the "
                  "Edinburgh box.",
    }),
]


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


# ---------------------------------------------------------------------------
# 1. config
# ---------------------------------------------------------------------------
def check_config():
    from core.config import get_model_slot

    slot = get_model_slot(1)
    print(f"  provider   : {slot.provider}")
    print(f"  model      : {slot.model}")
    print(f"  base_url   : {slot.base_url or slot.default_base_url}")
    print(f"  api_key    : {'set (' + slot.api_key[:10] + '...)' if slot.api_key else 'MISSING'}")
    print(f"  max_tokens : {slot.max_tokens}   effort: {slot.effort}")
    print(f"  reasoning  : {slot.reasoning}")

    if not slot.api_key:
        raise SystemExit(
            "\n  FAIL: MODEL_1_API_KEY is empty.\n"
            "  Put your OpenRouter key in .env, or run with --offline to test\n"
            "  the graph without a provider."
        )
    print("\n  OK: config resolves.")
    return slot


# ---------------------------------------------------------------------------
# 2. live call
# ---------------------------------------------------------------------------
def check_live_call(slot):
    from core.llm import LLMClient

    client = LLMClient(slot=1, max_tokens=64)
    response = client.complete(
        system="You are a connectivity check. Answer with one word.",
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
    )
    print(f"  reply      : {response.text!r}")
    print(f"  stop_reason: {response.stop_reason}")
    print(f"  usage      : {response.usage}")

    if not response.text:
        raise SystemExit("\n  FAIL: reached the provider but got empty content.")
    print(f"\n  OK: {slot.provider} answered using {slot.model}.")


# ---------------------------------------------------------------------------
# 3. the round trip
# ---------------------------------------------------------------------------
def check_round_trip(offline: bool):
    journals = {}
    if offline:
        _install_canned_argos()
        # Canned turns are not the agent's experience: they must not reach its
        # journal, which the agents read back as context (BUG-006). The note
        # used to say "model=slot 1 | openrouter:..." although no model ran.
        from core import memory

        memory.append_entry = lambda *a, **k: None
        journals = {p: p.read_bytes() for p in Path("agents").glob("*/memory.md")}

    from core.graph import run_task

    state = run_task(TASK)
    changed = [str(p) for p, before in journals.items() if p.read_bytes() != before]
    if changed:
        raise SystemExit(f"\n  FAIL: the offline round trip wrote to {changed}")

    print("\n  --- exchanges ---")
    for line in state.get("exchange_log", []):
        print(f"    {line}")

    result = state.get("monty_result") or {}
    attempts = state.get("monty_attempts", 0)
    print(f"\n  attempts    : {attempts}")
    print(f"  status      : {result.get('status')}")
    print(f"  request sent: {json.dumps(state.get('monty_request') or {})}")
    print(f"\n  --- Argos' answer ---\n    {state.get('answer', '').strip()}")

    if result.get("status") != "success":
        raise SystemExit(
            f"\n  FAIL: Monty did not succeed after {attempts} attempt(s).\n"
            f"  {state.get('answer', '')}"
        )

    first = (result.get("results") or {}).get("result_1") or {}
    if first.get("task") != "fetch_weather":
        raise SystemExit(f"\n  FAIL: expected fetch_weather, got {first.get('task')}")
    rain = ((((first.get("output") or {}).get("hourly") or {}).get("vars") or {})
            .get("precipitation") or {}).get("mean")
    if not rain:
        raise SystemExit(f"\n  FAIL: no hourly precipitation in {str(first)[:300]}")
    if len(rain) != EXPECTED_HOURS:
        raise SystemExit(f"\n  FAIL: expected {EXPECTED_HOURS} hours, "
                         f"got {len(rain)}")
    if not all(v is None or (isinstance(v, (int, float)) and v >= 0) for v in rain):
        raise SystemExit(f"\n  FAIL: hourly rain must be non-negative or a "
                         f"reported gap, got {rain}")

    total = (first["output"].get("event_summary") or {}).get("storm_total_mm")
    print(f"\n  OK: result_1 hourly precipitation, {len(rain)} hours, all "
          f"non-negative; storm total {total}")
    if attempts > 1:
        print(f"  (self-healed: {attempts - 1} retry/retries were needed)")


def _install_canned_argos():
    """Replace the model with the canned replies above. Offline only."""
    import core.llm as llm_mod
    from core.llm import LLMClient, LLMResponse

    replies = iter(CANNED_ARGOS)
    llm_mod._build_client = lambda slot: None          # no provider, no key

    def canned(self, *, system, messages, tools=None, max_tokens=None, stream=None):
        try:
            return LLMResponse(text=next(replies), stop_reason="end_turn")
        except StopIteration:
            raise SystemExit(
                "  FAIL: the graph asked for more Argos turns than are canned. "
                "That usually means Monty failed and the debugger loop engaged."
            )

    LLMClient.complete = canned


# ---------------------------------------------------------------------------
def main() -> int:
    offline = "--offline" in sys.argv

    if offline:
        hr("MODE: offline -- Argos' turns are canned, OpenRouter is NOT exercised")
    else:
        hr("1. CONFIG")
        slot = check_config()
        hr("2. LIVE CALL")
        check_live_call(slot)

    hr("3. ROUND TRIP  (Argos -> Monty -> fetch_weather)")
    print(f"  task: {TASK}")
    check_round_trip(offline)

    hr("RESULT")
    if offline:
        print("  Graph, Monty and the workflow registry work.")
        print("  OpenRouter is UNVERIFIED -- add MODEL_1_API_KEY and rerun "
              "without --offline.")
    else:
        print("  OpenRouter configuration is working end to end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
