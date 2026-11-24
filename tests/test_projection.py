"""The field entitlement, and whether it still matches the schema.

    python tests/test_projection.py

Offline. No model, no network.

The projection decides what a consumer is shown of a cached payload. The
dangerous direction is serving too little: a missing field does not raise --
the agent writes the brief without it and the gap is invisible in the output.
So these checks are mostly about under-service, not over-service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.projection import PROJECTIONS, project, withheld    # noqa: E402

FAILURES: list[str] = []
SCHEMA = "norwood/pre-flood-aoi-brief"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "docs" / \
    "norwood_preflood_brief.schema.json"


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def _from_lines(node, out: list[str]) -> list[str]:
    """Every `from:` declaration in the schema, wherever it sits."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "from" and isinstance(value, str):
                out.append(value)
            else:
                _from_lines(value, out)
    elif isinstance(node, list):
        for value in node:
            _from_lines(value, out)
    return out


def check_schema_sources_are_served() -> None:
    """Every payload key a schema `from:` line names must be served.

    This is the drift guard. The `from:` lines are prose, so they are not
    parsed into paths -- but the payload keys they mention are extracted and
    checked against what the projection lets through. A schema that starts
    naming `payload.foo` while the projection withholds `foo` fails here
    rather than producing a brief with a quietly missing section.
    """
    if not SCHEMA_FILE.exists():
        fail(f"schema file missing at {SCHEMA_FILE}")
        return

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    lines = _from_lines(schema, [])
    print(f"  schema declares {len(lines)} `from:` line(s)")

    # The payload keys those lines mention, e.g. "payload.selection.attempts"
    # -> "selection". Only `payload.` paths: "class fractions" names no key.
    named: set[str] = set()
    for line in lines:
        for token in line.replace(",", " ").split():
            if token.startswith("payload."):
                named.add(token.split(".")[1].strip("*"))
    print(f"  payload keys named: {sorted(named) or 'none'}")

    served = set()
    for allowed in (PROJECTIONS.get(SCHEMA) or {}).values():
        served.update(allowed)

    missing = sorted(named - served)
    if missing:
        fail(f"the schema sources {missing} and the projection withholds them")
    else:
        print("  ok  every key the schema names is served")


def check_projection_is_a_narrowing_not_a_rename() -> None:
    """Projecting keeps values identical; it only removes whole keys."""
    payload = {"aoi": {"bbox": [1, 2, 3, 4]}, "grid": {"shape": [10, 10]},
               "secret_raster_index": {"path": "x.tif"}}
    out = project(payload, "terrain", SCHEMA)

    if out.get("aoi") is not payload["aoi"]:
        fail("projection copied or rewrote a value instead of passing it through")
    else:
        print("  ok  values pass through untouched")
    if "secret_raster_index" in out:
        fail("a key outside the entitlement was served")
    else:
        print("  ok  an undeclared key is withheld")
    if withheld(payload, "terrain", SCHEMA) != ["secret_raster_index"]:
        fail(f"withheld() disagrees: {withheld(payload, 'terrain', SCHEMA)}")


def check_unknown_inputs_are_served_whole() -> None:
    """Withholding by default would be a silent starvation bug.

    A workflow added tomorrow has no entry here. Serving it whole is the safe
    failure: the consumer sees too much, which is visible and correctable.
    Serving nothing would be invisible.
    """
    payload = {"anything": 1, "at_all": 2}
    if project(payload, "brand_new_workflow", SCHEMA) != payload:
        fail("an unprojected payload was narrowed anyway")
    else:
        print("  ok  an unknown payload is served whole")
    if project(payload, "lulc", "some/other-schema") != payload:
        fail("an unknown schema narrowed a payload")
    else:
        print("  ok  an unknown schema narrows nothing")


def check_the_narrowing_is_reported() -> None:
    """Whatever is dropped has to be nameable, so the cut is auditable."""
    payload = {"aoi": 1, "grid": 2, "junk": 3, "more_junk": 4}
    dropped = withheld(payload, "terrain", SCHEMA)
    print(f"  withheld from terrain: {dropped}")
    if dropped != ["junk", "more_junk"]:
        fail(f"withheld() should name both extras, got {dropped}")


def main() -> int:
    hr("1. the schema's own sources are all served")
    check_schema_sources_are_served()
    hr("2. projecting narrows, it does not rewrite")
    check_projection_is_a_narrowing_not_a_rename()
    hr("3. anything unmapped is served whole")
    check_unknown_inputs_are_served_whole()
    hr("4. the narrowing is reportable")
    check_the_narrowing_is_reported()

    print()
    if FAILURES:
        print(f"{'=' * 68}\n{len(FAILURES)} CHECK(S) FAILED\n{'=' * 68}")
        for message in FAILURES:
            print(f"  - {message}")
        return 1
    print(f"{'=' * 68}\nALL CHECKS PASSED\n{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
