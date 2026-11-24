"""Small HTTP helpers for the context tools: JSON with a retry, and a cached
download for the static files (census tables, attribute tables, releases).

Every request is reported through `observability.source_call` -- domain only,
never the query string -- so a source outage reaches the run's
`source_failures` and is never mistaken for a different answer.

**The static-file cache is the same kind of thing as the OSM tile cache**: a
published release does not change under a fixed URL often, and a census table
is 1.5 MB. It lives in `data/cache/<subdir>/` (gitignored) with a sidecar
recording the URL, the retrieval time and the SHA-256 of the bytes, so a
figure computed from it can say which file it came from. An uncached run
(core/cache_policy.py) re-downloads and says so.

**A caller may accept the copy on disk when the download fails**
(`copy_on_failure`, user decision 2026-10-26, made for the census, which was
retired on 2026-10-30; no caller uses it now). The meta then
says `copy_fallback` with the download error, and the failed request has
already been recorded as a source failure -- so a figure read from the copy
is never presented as re-measured.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import certifi

from .. import cache_policy, observability

CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"
USER_AGENT = "pralai-flood-research/0.1 (research; contact via repo)"
TIMEOUT = 90
# Verify against certifi's bundle, not the Windows store. The store on this
# machine holds two "USERTrust RSA Certification Authority" certificates --
# the self-signed root and a copy cross-signed by "AAA Certificate Services",
# which is not installed. OpenSSL picks one by name; picking the cross copy
# fails with "unable to get issuer certificate", so Copernicus EMS (and any
# chain through USERTrust) failed in some runs and not others.
TLS = ssl.create_default_context(cafile=certifi.where())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Hosts that drop a connection opened too soon after the previous one: seconds
# to wait after a call to that host ENDS before the next one starts. Measured
# 2026-11-11 on INFORM, 8-20 sequential calls each: back to back 4/8 dropped
# (RemoteDisconnected), 1 s 1/8, 1.5 s 3/20, 2 s 0/8, 2.5 s 0/20. 3 s keeps a
# margin. Calls to such a host are also serialised (one at a time, across
# threads), so two tools can never hit it together.
SPACING_S = {"drmkc.jrc.ec.europa.eu": 3.0}
_HOST_LOCKS: dict[str, threading.Lock] = {h: threading.Lock() for h in SPACING_S}
_LAST_END: dict[str, float] = {}


def _paced(url: str):
    """A context manager that holds the host's turn, or does nothing."""
    host = urllib.parse.urlsplit(url).hostname or ""
    lock = _HOST_LOCKS.get(host)

    class _Turn:
        def __enter__(self):
            if lock is None:
                return self
            lock.acquire()
            wait = _LAST_END.get(host, 0.0) + SPACING_S[host] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            return self

        def __exit__(self, *exc):
            if lock is not None:
                _LAST_END[host] = time.monotonic()
                lock.release()
            return False

    return _Turn()


def get_bytes(url: str, *, retries: int = 1, pause: float = 3.0) -> bytes:
    """GET with one retry on a dropped connection or a 5xx. A 4xx is final.
    A host in SPACING_S is called one at a time, spaced (see there)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    failed: list[dict[str, Any]] = []      # this request's failed attempts, settled below
    for attempt in range(retries + 1):
        with _paced(url):
            t0 = time.perf_counter()           # after the wait: the call's own time
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT,
                                            context=TLS) as response:
                    body = response.read()
                observability.source_call(url, duration_s=time.perf_counter() - t0)
                # The data arrived: earlier drops of this request were retries.
                observability.settle_attempts(failed, recovered=True)
                return body
            except urllib.error.HTTPError as exc:
                failed.append(observability.source_call(
                    url, ok=False, status=exc.code, reason=str(exc.reason),
                    duration_s=time.perf_counter() - t0, record=False))
                last = exc
                if exc.code < 500:
                    break
            except Exception as exc:                           # noqa: BLE001
                failed.append(observability.source_call(
                    url, ok=False, reason=type(exc).__name__,
                    duration_s=time.perf_counter() - t0, record=False))
                last = exc
        if attempt < retries:
            time.sleep(pause)
    observability.settle_attempts(failed, recovered=False)
    raise last                                                 # type: ignore[misc]


def get_json(url: str, **kwargs: Any) -> Any:
    return json.loads(get_bytes(url, **kwargs).decode("utf-8", "replace"))


def cached_file(url: str, subdir: str, name: str, *,
                copy_on_failure: bool = False) -> tuple[Path, dict[str, Any]]:
    """`(path, meta)` for a static file, downloaded once and kept.

    `meta` is the sidecar: url, retrieved_at, sha256, bytes, and `cached`
    (True when this call did not download). Under an uncached run the file is
    downloaded again and replaced; the sidecar then says so. An empty `url`
    means the address itself could not be looked up.

    With `copy_on_failure`, a failed download returns the copy already on disk
    (if there is one), marked `copy_fallback` with the error. Without a copy
    the download error is raised as before.
    """
    folder = CACHE_ROOT / subdir
    target = folder / name
    sidecar = folder / f"{name}.meta.json"
    if target.exists() and sidecar.exists() and not cache_policy.is_off():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        return target, {**meta, "cached": True}
    try:
        if not url:
            raise ValueError(f"no download address for {name}")
        body = get_bytes(url)
    except Exception as exc:                                   # noqa: BLE001
        if copy_on_failure and target.exists() and sidecar.exists():
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            return target, {**meta, "cached": True, "copy_fallback": True,
                            "download_error": f"{type(exc).__name__}: {exc}"[:200]}
        raise
    folder.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(body)
    partial.replace(target)
    meta = {"url": url, "retrieved_at": _now(),
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            "uncached_run": cache_policy.is_off()}
    sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return target, {**meta, "cached": False}


def item(key: str, statement: str, value: Any, unit: str, basis: str,
         vintage: str = "") -> dict[str, Any]:
    """One entry of a tool's uncertainty block (the contract shape, 4.2)."""
    return {"key": key, "statement": statement, "value": value, "unit": unit,
            "basis": basis, "vintage": vintage, "vault_note": ""}


def source(id: str, title: str, publisher: str, url: str, vintage: str,
           licence: str = "", role: str = "major", retrieved_at: str = "") -> dict[str, Any]:
    return {"id": id, "title": title, "publisher": publisher, "url": url,
            "vintage": vintage, "retrieved_at": retrieved_at or _now(),
            "licence": licence, "role": role}


def blocks(input_items: list | None = None,
           tool_items: list | None = None) -> dict[str, list]:
    return {"input": list(input_items or []), "tool": list(tool_items or []),
            "pipeline": [], "decision": []}
