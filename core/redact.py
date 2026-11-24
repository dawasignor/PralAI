"""Nothing of the machine reaches the reader (user bug, 2026-11-02).

Everything the console server sends -- the answer, the session log, tables,
the session view -- passes through `deep()`, which replaces what describes
this computer rather than the analysis:

* absolute paths (`E:\\...`, `C:\\Users\\...`, `/c/Users/...`, `/home/...`),
  and the repository's own folders written relative (`sessions/...`,
  `outputs/...`, `experiments/...`, `runs/...`, `data/cache/...`);
* the account name of whoever runs the server, and its home folder.

A path becomes "(local file)": the reader learns that something was saved,
not where. The developer's view is the server's own stdout and
`logs/exchange.log`, which are not sent anywhere.
"""

from __future__ import annotations

import getpass
import re
from pathlib import Path
from typing import Any

LOCAL = "(local file)"
_REPO = Path(__file__).resolve().parents[1]

# The roots whose paths may contain spaces ("D:\My work\..."): matched as
# literal prefixes, longest first, with whatever path follows them.
_ROOTS = sorted({str(p) for p in (_REPO, _REPO.parent, Path.home())}
                | {str(p).replace("\\", "/") for p in (_REPO, _REPO.parent, Path.home())},
                key=len, reverse=True)
_TAIL = r"(?:[\\/][^\s\"'<>|()\]]*)?"
_ROOTED = re.compile("|".join(re.escape(r) + _TAIL for r in _ROOTS), re.I)
# Any other absolute path without spaces: a drive letter, or a POSIX home.
_ABSOLUTE = re.compile(r"(?<![\w/])(?:[A-Za-z]:[\\/]|/(?:c|d|e)/Users/|/home/|/Users/)"
                       r"[^\s\"'<>|()\]]+")
# The repository's own folders, written relative.
_RELATIVE = re.compile(r"(?<![\w./-])(?:sessions|outputs|experiments|runs|data[\\/]cache|logs)"
                       r"[\\/][^\s\"'<>|()\]]*")


def _account() -> list[str]:
    names = {Path.home().name}
    try:
        names.add(getpass.getuser())
    except Exception:                                          # noqa: BLE001
        pass
    return sorted((n for n in names if n and len(n) >= 3), key=len, reverse=True)


_ACCOUNT = [re.compile(rf"\b{re.escape(n)}\b", re.I) for n in _account()]


def text(value: str) -> str:
    """`value` with every local path and the account name replaced."""
    if not value:
        return value
    out = _ROOTED.sub(LOCAL, value)
    out = _ABSOLUTE.sub(LOCAL, out)
    out = _TOKEN_PATH.sub(LOCAL, out)
    out = _RELATIVE.sub(LOCAL, out)
    for pattern in _ACCOUNT:
        out = pattern.sub("user", out)
    return out


def _walk(value: Any, fn) -> Any:
    """`fn` over every string in a JSON-shaped value; keys are kept."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, fn) for v in value)
    return value


def deep(value: Any) -> Any:
    """`text()` over every string in a JSON-shaped value; keys are kept."""
    return _walk(value, text)


# -- the model's view ------------------------------------------------------------
# What goes to the model provider is redacted too, at the one choke point
# (`LLMClient.complete`). It cannot be redacted the reader's way: Argus copies a
# raster path out of one result into the next task (`extent_raster`,
# `flood_raster`), so a path made "(local file)" would break the chain. Paths
# under the repository become `<repo>/...` instead -- the provider learns the
# file's place in the repo, not the machine -- and Monty expands the token back
# when it binds the parameters (`expand`). Anything outside the repository
# (home folder, temp folders, other drives) and the account name go entirely.
REPO_TOKEN = "<repo>"
_REPO_FORMS = sorted({str(_REPO), str(_REPO).replace("\\", "/")}, key=len, reverse=True)
_IN_REPO = re.compile("(?:" + "|".join(re.escape(r) for r in _REPO_FORMS) + ")"
                      r"(?=[\\/\s\"'<>|()\]]|$)", re.I)
_TOKEN_PATH = re.compile(re.escape(REPO_TOKEN) + _TAIL)


def prompt_text(value: str) -> str:
    """`value` as the model may see it: repo paths as `<repo>/...`, nothing else
    of the machine."""
    if not value:
        return value
    out = _IN_REPO.sub(REPO_TOKEN, value)
    out = re.sub(re.escape(REPO_TOKEN) + r"\\", REPO_TOKEN + "/", out)
    out = _ROOTED.sub(LOCAL, out)
    out = _ABSOLUTE.sub(LOCAL, out)
    for pattern in _ACCOUNT:
        out = pattern.sub("user", out)
    return out


def prompt_deep(value: Any) -> Any:
    """`prompt_text()` over every string in a JSON-shaped value."""
    return _walk(value, prompt_text)


def expand(value: str) -> str:
    """A path the model wrote with `<repo>` turned back into a real one."""
    if isinstance(value, str) and value.startswith(REPO_TOKEN):
        rest = value[len(REPO_TOKEN):].lstrip("\/")
        return str(_REPO / rest) if rest else str(_REPO)
    return value
