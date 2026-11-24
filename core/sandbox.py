"""Local execution of a fetched code snippet, in a child process.

NOT ON THE GRAPH PATH. Monty runs first-party functions from core/workflows/
in-process, so nothing on that path needs sandboxing. This module exists for
the chat path's run_python tool, which is still a stub, and is where execution
of untrusted or generated code belongs if that need arises. Delete it if
run_python stays a stub.

Snippet contract
----------------
A snippet is a module that defines a callable named `run`. It is invoked with
the input parameters as keyword arguments:

    def run(event_id, **_):
        return event_id * 2

    run(**input_parameters)  ->  the value that becomes execution_result.output

Isolation
---------
`PY_SANDBOX=subprocess` (the default) runs the snippet in a separate
interpreter with a timeout, a scratch working directory, and a stripped
environment. That is **process isolation, not a security boundary** -- it stops
a hang or a crash from taking the graph down, it does not stop hostile code.
Snippets would come from a source you control. For genuinely
untrusted code use a container and set `PY_SANDBOX=container` (not implemented).

Every failure returns a structured error rather than raising, because Argos
reads that structure when it puts on its debugger persona. The `error_type`
values are the vocabulary of that conversation:

    snippet_syntax      the snippet does not compile
    missing_entrypoint  no callable named `run`
    signature_mismatch  parameters do not fit run()'s signature
    runtime_error       run() raised
    unserialisable      run() returned something that is not JSON
    timeout             exceeded PY_SANDBOX_TIMEOUT
    sandbox_disabled    PY_SANDBOX=none
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any

# The child process. Reads {"code", "params", "entrypoint"} on stdin, writes a
# single JSON object on stdout. User prints are captured separately so they
# cannot corrupt that channel.
_RUNNER = r'''
import contextlib, inspect, io, json, sys, traceback

payload = json.loads(sys.stdin.read())
code, params = payload["code"], payload["params"]
entrypoint = payload.get("entrypoint", "run")
captured = io.StringIO()

def fail(kind, message, tb="", **extra):
    print(json.dumps({"status": "error", "error_type": kind, "message": message,
                      "traceback": tb, "stdout": captured.getvalue(), **extra}))
    sys.exit(0)

ns = {}
try:
    with contextlib.redirect_stdout(captured):
        exec(compile(code, "<agent_m_snippet>", "exec"), ns)
except SyntaxError as exc:
    fail("snippet_syntax", f"{type(exc).__name__}: {exc}", traceback.format_exc())
except Exception as exc:
    fail("runtime_error", f"import-time {type(exc).__name__}: {exc}",
         traceback.format_exc())

fn = ns.get(entrypoint)
if not callable(fn):
    fail("missing_entrypoint",
         f"snippet defines no callable named {entrypoint!r}",
         defined=sorted(k for k in ns if not k.startswith("__")))

try:
    signature = str(inspect.signature(fn))
    inspect.signature(fn).bind(**params)
except TypeError as exc:
    fail("signature_mismatch", f"{exc}", signature=signature,
         supplied=sorted(params))

try:
    with contextlib.redirect_stdout(captured):
        result = fn(**params)
except Exception as exc:
    fail("runtime_error", f"{type(exc).__name__}: {exc}", traceback.format_exc())

try:
    json.dumps(result)
except (TypeError, ValueError) as exc:
    fail("unserialisable", f"run() returned {type(result).__name__}: {exc}")

print(json.dumps({"status": "success", "output": result,
                  "stdout": captured.getvalue()}))
'''


def _timeout() -> int:
    return int((os.getenv("PY_SANDBOX_TIMEOUT") or "30").strip())


def _mode() -> str:
    return (os.getenv("PY_SANDBOX") or "subprocess").strip().lower()


def run_snippet(code: str, params: dict[str, Any],
                entrypoint: str = "run") -> dict[str, Any]:
    """Execute `code` and return a JSON-shaped result. Never raises."""
    mode = _mode()
    if mode == "none":
        return {
            "status": "error",
            "error_type": "sandbox_disabled",
            "message": "PY_SANDBOX=none -- execution is switched off",
            "hint": "set PY_SANDBOX=subprocess in agents/monty/.env",
        }
    if mode != "subprocess":
        return {
            "status": "error",
            "error_type": "sandbox_disabled",
            "message": f"PY_SANDBOX={mode!r} is declared but not implemented",
            "hint": "see core/sandbox.py -- only 'subprocess' is wired up",
        }

    payload = json.dumps({"code": code, "params": params, "entrypoint": entrypoint})
    # Minimal environment: no inherited API keys reach the snippet.
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "PYTHONIOENCODING")
    }
    env["PYTHONIOENCODING"] = "utf-8"

    with tempfile.TemporaryDirectory(prefix="monty_") as scratch:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", _RUNNER],
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_timeout(),
                cwd=scratch,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "error_type": "timeout",
                "message": f"snippet exceeded {_timeout()}s",
                "hint": "raise PY_SANDBOX_TIMEOUT or fix the loop",
            }

    raw = (proc.stdout or "").strip().splitlines()
    if not raw:
        return {
            "status": "error",
            "error_type": "runtime_error",
            "message": "runner produced no output",
            "traceback": (proc.stderr or "")[-800:],
        }
    try:
        return json.loads(raw[-1])
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error_type": "runtime_error",
            "message": "runner output was not JSON",
            "traceback": raw[-1][:800],
        }
