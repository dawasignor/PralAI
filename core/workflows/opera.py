"""NASA OPERA DSWx water layers (B01_WTR): search, Earthdata login, download.

Shared by `scripts/fetch_opera_dswx.py` (the validation download) and the
`get_flood_map` tool (BUG-029), so the two fetch OPERA the same way. The
search (NASA CMR) is public; the files sit behind an Earthdata login.

**Credentials are never read into a result, printed or stored.** The token is
`EARTHDATA_TOKEN`, from the environment or the repo's `.env` (loaded by
core/config.py, as the app's other keys are), sent as
`Authorization: Bearer <token>` -- EDL's user-token header
(urs.earthdata.nasa.gov/documentation/for_users/user_token). Without one,
requests falls back to `~/.netrc` (machine urs.earthdata.nasa.gov).

Each (tile, acquisition) is kept once: when a granule was reprocessed, only
the newest production is used and the others are listed as superseded.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests

CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
SHORT_NAMES = {
    "dswx_hls": "OPERA_L3_DSWX-HLS_V1",
    "dswx_s1": "OPERA_L3_DSWX-S1_V1",
}
# OPERA_L3_DSWx-HLS_T44PLQ_20251125T050141Z_20251127T162423Z_S2C_30_v1.1
NAME = re.compile(r"_T(?P<tile>\w{5})_(?P<acq>\d{8}T\d{6})Z_(?P<prod>\d{8}T\d{6})Z_")


class LoginRefused(RuntimeError):
    """Earthdata answered 401 or 403: the login was missing or not accepted."""


def search(short_name: str, bbox: list[float], start: str, end: str) -> list[dict]:
    items, page = [], 1
    while True:
        r = requests.get(CMR, params={
            "short_name": short_name, "bounding_box": ",".join(map(str, bbox)),
            "temporal": f"{start},{end}", "page_size": 200, "page_num": page},
            headers={"Accept": "application/json"}, timeout=60)
        r.raise_for_status()
        batch = r.json().get("items", [])
        items += batch
        if len(batch) < 200:
            return items
        page += 1


def wtr_url(umm: dict) -> str | None:
    for u in umm.get("RelatedUrls", []):
        url = u.get("URL", "")
        if u.get("Type") == "GET DATA" and url.startswith("https") and "_B01_WTR" in url:
            return url
    return None


def newest_per_acquisition(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep the newest production of each (tile, acquisition); list the rest."""
    best: dict[tuple[str, str], tuple[str, dict]] = {}
    for it in items:
        gid = it["umm"]["GranuleUR"]
        m = NAME.search(gid)
        if not m:
            raise ValueError(f"unexpected granule name, cannot place it: {gid}")
        key = (m["tile"], m["acq"])
        if key not in best or m["prod"] > best[key][0]:
            best[key] = (m["prod"], it)
    kept = [v[1] for v in best.values()]
    kept_ids = {it["umm"]["GranuleUR"] for it in kept}
    superseded = sorted(it["umm"]["GranuleUR"] for it in items
                        if it["umm"]["GranuleUR"] not in kept_ids)
    return kept, superseded


class EarthdataSession(requests.Session):
    """Keeps `Authorization: Bearer <token>` across Earthdata redirects.

    PO.DAAC answers a file request with redirects through other Earthdata
    hosts before the final pre-signed S3 link. `requests` drops
    `Authorization` whenever the host changes, so the token was lost on the
    first hop and the download came back 401. The header is kept for
    *.earthdata.nasa.gov and dropped for every other host -- the S3 link is
    already signed and refuses a second auth method.
    """

    AUTH_HOSTS = ("earthdata.nasa.gov",)

    def rebuild_auth(self, prepared_request, response):
        token = self.headers.get("Authorization")
        if not token:
            # No token: requests' own handling, which applies ~/.netrc per host.
            return super().rebuild_auth(prepared_request, response)
        host = requests.utils.urlparse(prepared_request.url).hostname or ""
        if any(host == h or host.endswith("." + h) for h in self.AUTH_HOSTS):
            prepared_request.headers["Authorization"] = token
        else:
            prepared_request.headers.pop("Authorization", None)


def session() -> tuple[requests.Session, str]:
    """A session carrying the Earthdata token, if one is configured, and
    where the credential comes from -- never what it is."""
    from .. import config  # noqa: F401  (loads the repo's .env into the environment)

    s = EarthdataSession()
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
        return s, "EARTHDATA_TOKEN"
    return s, "~/.netrc (no EARTHDATA_TOKEN set)"


def download(s: requests.Session, url: str, target: Path) -> str:
    if target.exists() and target.stat().st_size > 0:
        return "present"
    r = s.get(url, timeout=300, stream=True)
    if r.status_code in (401, 403):
        raise LoginRefused(
            f"HTTP {r.status_code} for {target.name}: the Earthdata login was not "
            "accepted. Set EARTHDATA_TOKEN (or ~/.netrc, machine "
            "urs.earthdata.nasa.gov) and run again. Nothing was written for this file.")
    r.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.replace(target)
    return "downloaded"


def fetch(key: str, bbox: list[float], start: str, end: str, folder: Path,
          s: requests.Session | None = None) -> dict[str, Any]:
    """Search one product, download its B01_WTR layers into `folder/<key>/`.
    Returns {"files": [{path, datetime, granule, tile, status}], "superseded",
    "no_wtr_link", "credential"}. Raises LoginRefused on 401/403."""
    credential = ""
    if s is None:
        s, credential = session()
    kept, superseded = newest_per_acquisition(search(SHORT_NAMES[key], bbox, start, end))
    files, no_link = [], []
    for it in sorted(kept, key=lambda i: i["umm"]["GranuleUR"]):
        umm = it["umm"]
        url = wtr_url(umm)
        if not url:
            no_link.append(umm["GranuleUR"])
            continue
        target = folder / key / url.rsplit("/", 1)[-1]
        status = download(s, url, target)
        files.append({"granule": umm["GranuleUR"], "path": str(target),
                      "datetime": umm["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"],
                      "tile": NAME.search(umm["GranuleUR"])["tile"], "status": status})
    return {"files": files, "superseded": superseded, "no_wtr_link": no_link,
            "credential": credential}
