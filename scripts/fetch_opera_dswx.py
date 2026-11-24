"""Download OPERA DSWx water layers (B01_WTR) for one AOI and window.

Run by the user, not by an agent: the files sit behind an Earthdata login.

    python scripts/fetch_opera_dswx.py --bbox 79.88 8.70 80.22 9.10 \
        --start 2025-11-20T18:30:00Z --end 2025-12-02T18:29:59Z \
        --out experiments/mannar-2025/validation/opera

**Credentials are never read, printed or stored by this script.** Requests
picks them up itself from one of:

- `~/.netrc` (Windows: `%USERPROFILE%\\_netrc` or `.netrc`) with a line
  `machine urs.earthdata.nasa.gov login <user> password <password>`;
- the environment variable `EARTHDATA_TOKEN`, an Earthdata user token, sent
  as a Bearer header;
- `EARTHDATA_TOKEN` in the repo's `.env`, loaded at run time the way the app
  loads its own keys (core/config.py).

The search (NASA CMR) is public. Only the downloads need the login.

Each (product, tile, acquisition) is downloaded once: when a granule was
reprocessed, only the newest production is kept, and the others are listed
as superseded in the manifest rather than silently dropped.

Writes `<out>/manifest.json`: one entry per file with its acquisition time,
which is what `accuracy_metrics` (`source: files`) and the labelled sample
need to place a file in the window.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The search, the Earthdata session and the download are shared with the
# get_flood_map tool (BUG-029): core/workflows/opera.py.
from core.workflows.opera import (SHORT_NAMES as PRODUCTS, NAME, LoginRefused,  # noqa: E402
                                  download, newest_per_acquisition, search, session, wtr_url)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--start", required=True, help="UTC, e.g. 2025-11-20T18:30:00Z")
    ap.add_argument("--end", required=True, help="UTC, e.g. 2025-12-02T18:29:59Z")
    ap.add_argument("--out", required=True)
    ap.add_argument("--products", default="dswx_hls,dswx_s1")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    s, credential = session()
    print(f"Earthdata login: {credential}")
    manifest = {"bbox": args.bbox, "window_utc": [args.start, args.end],
                "source": "NASA CMR (search) and PO.DAAC (files)", "products": {}}
    for key in args.products.split(","):
        short = PRODUCTS[key]
        kept, superseded = newest_per_acquisition(search(short, args.bbox, args.start, args.end))
        files = []
        for it in sorted(kept, key=lambda i: i["umm"]["GranuleUR"]):
            umm = it["umm"]
            url = wtr_url(umm)
            if not url:
                print(f"  {umm['GranuleUR']}: no B01_WTR link, skipped (listed in manifest)")
                files.append({"granule": umm["GranuleUR"], "status": "no_wtr_link"})
                continue
            (out / key).mkdir(exist_ok=True)
            target = out / key / url.rsplit("/", 1)[-1]
            try:
                status = download(s, url, target)
            except LoginRefused as exc:
                raise SystemExit(str(exc))
            acq = umm["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"]
            files.append({"granule": umm["GranuleUR"], "path": f"{key}/{target.name}",
                          "datetime": acq, "tile": NAME.search(umm["GranuleUR"])["tile"],
                          "status": status})
            print(f"  {key:9} {acq[:16]}  {target.name}  {status}")
        manifest["products"][key] = {"collection": short, "files": files,
                                     "superseded": superseded}
        print(f"{key}: {len(files)} file(s), {len(superseded)} superseded")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"manifest: {out / 'manifest.json'}")


if __name__ == "__main__":
    sys.exit(main())
