"""The vault catalogue and the Python registry must agree.

    python tests/test_workflow_catalogue.py

The vault is where agents read about workflows; `core/workflows/` is what can
actually be executed. Nothing stops those two drifting except this check, and
the drift is not harmless in either direction:

* a workflow in the vault but not the registry makes Argos route to a function
  that does not exist -- `unknown_function`, a wasted delegation, and a
  confusing log;
* a workflow in the registry but not the vault is invisible to any agent that
  asks the knowledge repo what it can do.

Signatures are deliberately NOT compared, because the note deliberately does
not carry them. Parameter names live in the registry alone.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import vault, workflows                           # noqa: E402

CATALOGUE_NOTE = "Monty data workflows"
FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def main() -> int:
    print(f"{'=' * 68}\nvault catalogue vs the registry\n{'=' * 68}")

    try:
        index = vault.build_index()
        note = vault.read_note(index, CATALOGUE_NOTE)
    except vault.VaultError as exc:
        print(f"  SKIPPED: {exc}")
        return 0

    body = note["content"]
    # The note separates what exists from what is planned with that heading, and
    # the distinction is the whole point: a planned workflow must NOT be
    # routable, so it must not be registered either.
    live_part, _, planned_part = body.partition("## Planned")

    # Match where a workflow is *catalogued* -- a table row, or a bullet under
    # Planned -- not merely mentioned. The live section discusses
    # fetch_lulc_data in prose while explaining the planned one, and a looser
    # pattern reads that as a listing.
    #
    # The name pattern is NOT `fetch_...`. It was, and that silently hid the
    # first workflow that does not fetch anything: `rank_flood_products` is a
    # decision step over payloads the others produced, it was correctly
    # catalogued, and this check reported it missing because of a naming
    # convention the registry has never enforced. A test that only sees names
    # it expects is not checking agreement.
    listed = set(re.findall(r"^\|\s*`([a-z][a-z0-9_]+)`", live_part, re.MULTILINE))
    planned = set(re.findall(r"^-\s*`([a-z][a-z0-9_]+)`", planned_part, re.MULTILINE))
    # Switched-off tools are still registered and keep their row, which must
    # say NOT AVAILABLE so the catalogue never offers them (2026-11-01).
    registered = set(workflows.names(include_inactive=True))
    for name in sorted(workflows.INACTIVE):
        row = re.search(rf"^\|\s*`{name}`.*$", live_part, re.MULTILINE)
        if row and "NOT AVAILABLE" not in row.group(0):
            fail(f"{name} is switched off but its catalogue row does not say NOT AVAILABLE")

    print(f"  registry : {sorted(registered)}")
    print(f"  vault    : {sorted(listed)}")
    print(f"  planned  : {sorted(planned)}")

    for name in sorted(listed - registered):
        fail(f"{name} is in the vault catalogue but not registered -- Argos "
             f"would route to a function that does not exist")
    for name in sorted(registered - listed):
        fail(f"{name} is registered but missing from the vault catalogue -- "
             f"invisible to an agent asking what it can do")
    for name in sorted(planned & registered):
        fail(f"{name} is listed as planned but is already registered -- the "
             f"note understates what can be run")

    # The note must not carry parameter names: they drift, and a stale one
    # produces a signature error that looks like a model mistake.
    for name in sorted(registered):
        if re.search(rf"`{name}\(", body):
            fail(f"{name} appears with a signature in the vault note; "
                 f"signatures belong to the registry only")

    if not FAILURES:
        print(f"  {len(registered)} workflows, catalogue agrees with the registry")
        print(f"  note carries no signatures, as intended")

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
