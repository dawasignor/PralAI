"""The workflow catalogue Argos routes on.

Two halves, with two different owners, and the split is the point:

* **Judgment** comes from the knowledge vault -- `Hand/Workflows/Monty data
  workflows.md`. What each workflow is for, what geometry it takes, when it is
  the wrong tool. That is curated prose, and the vault is where the project
  keeps curated prose.
* **Signatures** come from the registry in `core/workflows/`. Parameter names
  are executable truth, and a name copied into a note drifts the moment the
  function changes -- producing a signature error that reads like a model
  mistake rather than a stale document.

`tests/test_workflow_catalogue.py` keeps the two consistent by name.

Argos holds no tools on purpose, so it cannot fetch the note itself. The graph
fetches it and puts it in the routing request instead, which keeps the vault as
the single source without turning the orchestrator into a researcher.

A vault that cannot be read is **not** fatal: the signature list alone is
enough to route with, so the catalogue degrades to it and says so.
"""

from __future__ import annotations

import re

from . import vault, workflows

CATALOGUE_NOTE = "Monty data workflows"


def _note_body() -> tuple[str, str]:
    """The catalogue note's prose, or ("", reason) if it cannot be read."""
    try:
        note = vault.read_note(vault.shared_index(), CATALOGUE_NOTE)
    except vault.VaultError as exc:
        return "", str(exc)
    except OSError as exc:                                  # pragma: no cover
        return "", f"{type(exc).__name__}: {exc}"

    _, body = vault.split_frontmatter(note["content"])

    # The trailing `Related:` line is navigation for a human reader in Obsidian.
    # It is noise in a prompt, and the links do not resolve to anything Argos
    # can open.
    body = body.split("\nRelated:")[0]

    # Demote the note's headings one level so they nest under the block's own
    # heading instead of competing with it. This is the only transformation
    # applied -- the words are the vault's, unchanged. Verbatim matters most
    # for the notes that carry citations; this one carries none.
    body = re.sub(r"^(#{1,5}) ", r"#\1 ", body, flags=re.MULTILINE)
    return body.strip().strip("-").strip(), ""


def for_routing() -> tuple[str, str]:
    """The catalogue block for a routing request, and any problem to log.

    Returns ("", reason) for the block only if the registry itself is empty --
    which would mean nothing is runnable at all.
    """
    signatures = workflows.catalogue()
    if not signatures:                                      # pragma: no cover
        return "", "no workflows are registered"

    body, problem = _note_body()

    parts = ["## Workflows Monty can run"]
    if body:
        parts.append(body)
    else:
        parts.append(
            "(The catalogue note could not be read, so what follows is the "
            "registry alone -- names and parameters, without the guidance on "
            "when each one is the wrong tool.)"
        )
    parts.append(
        "**Exact signatures. These parameter names are the only truth; "
        "anything else is a guess:**\n" + signatures
    )
    return "\n\n".join(parts) + "\n\n", problem
