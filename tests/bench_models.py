"""Which model to point this repo at, decided by measurement.

    python tests/bench_models.py --list
    python tests/bench_models.py openai/gpt-oss-120b qwen/qwen3.8-flash
    python tests/bench_models.py --slot1        # re-test whatever .env holds

**This spends money.** Three short calls per model, a few thousand tokens each
-- well under a cent for anything on the shortlist -- but it is real spend, so
it runs only when you ask and it prints the bill at the end.

What it measures is not "is this model clever". It is the three things this
repo has actually been broken by:

  1. **Contract adherence.** Argos is told to reply with one JSON object and
     the graph reads specific keys off it. A model that answers with prose
     around the JSON, or invents its own key names, produces
     `bad_payload: no tasks supplied` -- a turn that fails while nothing is
     wrong. This is the failure mode that cost the most time here.
  2. **Overlay obedience.** The resolver prompt asks for a different JSON shape
     than the routing prompt, on the same agent. A model that reaches for the
     contract it saw first returns a routing decision instead of a reading, and
     the AOI comes back empty.
  3. **Verbatim parameters.** `fetch_lulc_data` takes `snake_case` names and
     the workflow catalogue lists indices in capitals. A model that
     "helpfully" reformats either one sends a task that cannot run.

Scoring is deterministic -- no model judges another.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_model_slot                          # noqa: E402
from core.llm import LLMClient                                  # noqa: E402

SHORTLIST = [
    "openai/gpt-oss-120b",
    "deepseek/deepseek-v4-flash-0731",
    "qwen/qwen3-235b-a22b-2507",
    "qwen/qwen3.8-flash",
    "openai/gpt-5-mini",
    "google/gemini-2.5-flash",
    "anthropic/claude-haiku-4.5",
]

ROUTING_SYSTEM = (
    "You are Argos, the orchestrator of a five-agent system. You do not do the "
    "technical work. You decide who does it next. Your specialists are norwood "
    "(earth observation), monty (runs registered Python workflows), entro "
    "(uncertainty) and citadel (planning recommendations)."
)

ROUTING_TASK = """\
## Active context
### Session
- AOI: Derna, Libya | bbox(W,S,E,N) 22.5568, 32.7401, 22.6843, 32.7915
- Time frame: 2023-09-08 to 2023-09-14 | Flood in Libya (flood, Red)

## Task
Give me the lay of the land before the flood.

## Workflows monty can run
- fetch_lulc_data(bbox, start_date, lookback_days, indices, event_id) -- \
Sentinel-2 land-cover indices. `indices` takes any of NDVI, NDBI, BUI, NDMI, \
NMDI, MNDWI in any case; omit it for all six.
- fetch_terrain_data(bbox, event_id) -- Copernicus DEM elevation and slope.

## Budget
6 delegation(s) remaining.

Decide the next move. Reply with ONLY a JSON object:
{"next": "norwood|monty|entro|citadel|finish", \
"brief": "what that agent must do, one paragraph", \
"event_id": 42, \
"tasks": [{"function": "fetch_lulc_data", "params": {}}], \
"digest": "what this session has established so far, 2-3 sentences", \
"answer": "the final answer -- only when next == finish"}

`event_id` and `tasks` are required when next == "monty" and ignored \
otherwise, and both sit at the TOP level of the object -- not inside a wrapper.
"""

RESOLVER_TASK = """\
## Session
today: 2026-09-15
session AOI: Derna, Libya
session time frame: 2023-09-08 to 2023-09-14

## Message
and what about the power outages there?

## Reply with ONLY this JSON object
{"place": "the place named in THIS message, or \\"\\"", \
"approx_start": "YYYY-MM-DD or \\"\\"", "approx_end": "YYYY-MM-DD or \\"\\"", \
"change": "new|same|none", "chosen_place_index": null, \
"chosen_event_index": null}

You are NOT routing this message and NOT answering it. Do not reply with \
{"next": ...} -- that contract does not apply to this turn.
"""

DEBUG_TASK = """\
## What you sent Monty
tasks = [{"function": "fetch_lulc_data", "params": {"bbox": [22.5568, \
32.7401, 22.6843, 32.7915], "indices": ["ndvi"], "min_valid_fraction": 0.9}}]

## What happened
error_type: runtime_error
message: no scene cleared min_valid_fraction=0.9 over the AOI in 90 days

Diagnose and fix. Reply with ONLY a JSON object:
{"diagnosis": "one sentence -- what is actually wrong", \
"fix": "params|function|drop|abandon", \
"patched_tasks": [{"function": "name", "params": {}}], \
"brief": "what Monty should do on this attempt"}
"""


def parse(text: str) -> dict:
    """The graph's own forgiving parse, so a model is judged the way it is used."""
    blob = (text or "").strip()
    if blob.startswith("```"):
        blob = blob.split("```")[1]
        blob = blob[4:] if blob.startswith("json") else blob
    a, b = blob.find("{"), blob.rfind("}")
    if a == -1 or b <= a:
        return {}
    for strict in (True, False):
        try:
            return json.loads(blob[a:b + 1], strict=strict)
        except json.JSONDecodeError:
            continue
    return {}


def score_routing(reply: dict) -> list[tuple[str, bool, str]]:
    nxt = reply.get("next")
    tasks = reply.get("tasks")
    checks = [
        ("returned parseable JSON", bool(reply), ""),
        ("chose a real next hop",
         nxt in ("norwood", "monty", "entro", "citadel", "finish"), str(nxt)),
        ("wrote a digest", bool(str(reply.get("digest") or "").strip()), ""),
    ]
    if nxt == "monty":
        names = [t.get("function") for t in (tasks or []) if isinstance(t, dict)]
        checks += [
            ("put `tasks` at the top level, not in a wrapper",
             isinstance(tasks, list) and bool(tasks), str(list(reply))),
            ("named a registered function verbatim",
             all(n in ("fetch_lulc_data", "fetch_terrain_data") for n in names)
             and bool(names), str(names)),
            ("kept event_id at the top level",
             reply.get("event_id") is not None, repr(reply.get("event_id"))),
        ]
    return checks


def score_resolver(reply: dict) -> list[tuple[str, bool, str]]:
    change = str(reply.get("change") or "").lower()
    return [
        ("returned parseable JSON", bool(reply), ""),
        ("obeyed the overlay, not the routing contract",
         "next" not in reply, str(list(reply))),
        ("read a follow-up as inheriting the session",
         change == "none", change or "(missing)"),
        ("did not invent a place",
         not str(reply.get("place") or "").strip(), repr(reply.get("place"))),
    ]


def score_debug(reply: dict) -> list[tuple[str, bool, str]]:
    patched = reply.get("patched_tasks") or []
    params = patched[0].get("params", {}) if patched and isinstance(patched[0], dict) else {}
    return [
        ("returned parseable JSON", bool(reply), ""),
        ("chose a fix from the vocabulary",
         reply.get("fix") in ("params", "function", "drop", "abandon"),
         str(reply.get("fix"))),
        ("diagnosed in one sentence",
         0 < len(str(reply.get("diagnosis") or "")) < 400, ""),
        ("loosened the threshold that actually failed",
         isinstance(params.get("min_valid_fraction"), (int, float))
         and params["min_valid_fraction"] < 0.9,
         str(params.get("min_valid_fraction"))),
        ("kept snake_case parameter names",
         all("_" in k or k in ("bbox", "indices") for k in params),
         str(list(params))),
    ]


CASES = [
    ("routing", ROUTING_SYSTEM, ROUTING_TASK, score_routing),
    ("resolver", ROUTING_SYSTEM, RESOLVER_TASK, score_resolver),
    ("debugger", ROUTING_SYSTEM, DEBUG_TASK, score_debug),
]


def bench(model: str, verbose: bool) -> dict:
    slot = get_model_slot(1)
    client = LLMClient(slot=1, max_tokens=2000, effort=slot.effort)
    # ModelSlot is a frozen dataclass, so `client.model = ...` would set an
    # attribute nothing reads and quietly benchmark .env's model every time.
    # The transport is built from provider and base_url, neither of which the
    # model id touches, so swapping only that field is safe.
    client.slot = dataclasses.replace(client.slot, model=model)
    assert client.slot.model == model
    passed = failed = 0
    seconds = 0.0
    notes: list[str] = []

    for name, system, task, scorer in CASES:
        began = time.time()
        try:
            response = client.complete(system=system,
                                       messages=[{"role": "user", "content": task}],
                                       tools=None)
            text = response.text or ""
        except Exception as exc:                                # noqa: BLE001
            notes.append(f"{name}: call failed -- {type(exc).__name__}: {exc}")
            failed += 1
            continue
        seconds += time.time() - began

        for label, ok, detail in scorer(parse(text)):
            passed += ok
            failed += not ok
            if verbose or not ok:
                mark = "ok  " if ok else "MISS"
                notes.append(f"  [{mark}] {name}: {label}"
                             + (f" -- {detail}" if detail and not ok else ""))

    return {"model": model, "passed": passed, "failed": failed,
            "seconds": seconds, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="*", help="OpenRouter model ids to compare")
    ap.add_argument("--list", action="store_true", help="print the shortlist")
    ap.add_argument("--slot1", action="store_true",
                    help="test whatever LLM_MODEL_1 currently holds")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("Shortlist (all tool-capable, >=128k context):")
        for m in SHORTLIST:
            print("  " + m)
        print("\nPass any of these as arguments, or your own ids.")
        return 0

    models = list(args.models)
    if args.slot1 or not models:
        models.append(get_model_slot(1).model)
    models = list(dict.fromkeys(models))

    print(f"Three contract cases per model. {len(models)} model(s).")
    print("This spends real money -- a fraction of a cent each.\n")

    results = [bench(m, args.verbose) for m in models]
    for r in results:
        total = r["passed"] + r["failed"]
        print(f"{r['model']:<40} {r['passed']:>2}/{total} checks  "
              f"{r['seconds']:>5.1f}s")
        for note in r["notes"]:
            print(note if note.startswith("  ") else f"    {note}")

    print("\nA model that misses a contract check will produce turns that fail "
          "while nothing is wrong.\nWeigh that against its price per run "
          "before choosing on price alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
