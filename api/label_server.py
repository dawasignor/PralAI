"""The labelling page for the stratified sample. Local, stdlib HTTP, no auth.

    python -m api.label_server experiments/mannar-2025/validation/config.yaml pilot
    # then open http://127.0.0.1:8790 (--port N to run a second one beside it)

Design: docs/sample_reference.md. One point at a time, every in-window image
that covers it, each in true and false colour, wide and close, beside the
pre-event scene. The labeller marks each event image; the server appends one
row per image to `labels.csv`.

**Blind by construction.** The page is sent a point's id, position and images
-- never its stratum, never a product's or the reference's value there.
`product_values.csv` is not read by anything in this file.

**Labels are validated here, not trusted from the page.** Classes, reasons and
confidence come from fixed lists; an image must be one offered for that point.
A rejected label is an HTTP 400 with the reason, and nothing is written.

    GET  /                          the page
    GET  /api/session               experiment, kind, counts, class lists
    GET  /api/points?labeller=L1    point ids in draw order, and which are done
    GET  /api/point/<id>            the point's images (renders its chips)
    GET  /api/chip/<image>/<id>/<name>.png
    POST /api/label                 one point's labels
"""

from __future__ import annotations

import csv
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.workflows.validation import chips  # noqa: E402

HOST, PORT = "127.0.0.1", 8790
CLASSES = ("flood", "not_flood", "preexisting_water", "unobservable")
REASONS = ("cloud", "cloud_shadow", "canopy", "built_up", "no_image")
LABELLER = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
FIELDS = ["point_id", "obs_id", "labeller", "round", "labelled_at_utc", "source",
          "image_id", "image_datetime_utc", "class", "unobservable_reason",
          "confidence", "pre_event_image_id", "blind", "elapsed_s", "notes"]
PREFETCH = 6


class Store:
    """The frozen sample, its images, and the label file."""

    def __init__(self, config_path: str, kind: str):
        import yaml

        from core.workflows.validation.config import load

        self.cfg = load(config_path)
        self.kind = kind
        self.dir = Path(self.cfg["_dir"]) / "sample" / kind
        manifest = self.dir / "manifest.yaml"
        if not manifest.exists():
            raise SystemExit(f"{manifest} not found: freeze the sample first "
                             "(python -m core.workflows.validation.sample <config> <kind>)")
        self.manifest = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        with open(self.dir / "sample_points.csv", encoding="utf-8") as f:
            rows = sorted(csv.DictReader(f), key=lambda r: int(r["draw_order"]))
        # Only what the page may see. The stratum stays on disk.
        self.points = [{"point_id": r["point_id"], "lon": float(r["lon"]),
                        "lat": float(r["lat"]), "draw_order": int(r["draw_order"])}
                       for r in rows]
        self.by_id = {p["point_id"]: p for p in self.points}
        self.cache = self.dir / "chips"
        self.labels_path = self.dir / "labels.csv"
        self.lock = threading.Lock()
        self.index = self._images()
        self.point_images = {p["point_id"]: chips.images_for_point(
            self.index["images"], p["lon"], p["lat"]) for p in self.points}
        self.pool = ThreadPoolExecutor(max_workers=4)

    def _images(self) -> dict[str, Any]:
        path = self.dir / "images.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        from core.experiment import Experiment

        lulc = Experiment(self.cfg["event"]).cached_input("lulc")
        named = (self.cfg.get("sample") or {}).get("pre_event_ids") or []
        if lulc:
            scene = lulc["payload"]["scene"]
            pre = [m["id"] for m in scene.get("members", [])] or [scene["id"]]
            pre_source = "the scene fetch_lulc_data selected"
        elif named:
            # The land-cover output failed (or was not run); the config names the
            # pre-event scene to SHOW. A picture for labellers, not a statistic --
            # the failed output stays failed.
            pre, pre_source = list(named), "sample.pre_event_ids in the config (no lulc result)"
        else:
            raise SystemExit("no pre-event scene: the experiment has no cached lulc input "
                             "and the config names no sample.pre_event_ids")
        first, last = (datetime.fromisoformat(d) for d in self.cfg["temporal"]["window"])
        # The window is in local days; Sri Lanka is UTC+5:30. Stored in the
        # file so the conversion is visible, not implied.
        offset = timedelta(hours=5, minutes=30)
        start = (first - offset).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (last + timedelta(days=1) - offset - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        index = chips.find_images(self.cfg["aoi"]["bbox"], start, end, pre)
        index["utc_offset_of_window"] = "+05:30"
        index["pre_event_source"] = pre_source
        path.write_text(json.dumps(index, indent=1), encoding="utf-8")
        return index

    # -- reading ------------------------------------------------------------
    def done(self, labeller: str) -> set[str]:
        if not self.labels_path.exists():
            return set()
        with open(self.labels_path, encoding="utf-8") as f:
            return {r["point_id"] for r in csv.DictReader(f)
                    if r["labeller"] == labeller and r["round"] == "primary"}

    def point_view(self, point_id: str) -> dict[str, Any]:
        p = self.by_id[point_id]
        images = []
        for img in self.point_images[point_id]:
            images.append({"key": img["key"], "source": img["source"],
                           "image_id": img["image_id"], "role": img["role"],
                           "datetime": img["datetime"], "platform": img["platform"],
                           "cloud": img["cloud"], "tier": img["tier"]})
        return {"point_id": point_id, "lon": p["lon"], "lat": p["lat"],
                "draw_order": p["draw_order"], "images": images}

    def render_point(self, point_id: str) -> None:
        p = self.by_id[point_id]
        for img in self.point_images[point_id]:
            try:
                chips.ensure_chips(self.cache, img, p)
            except Exception as exc:                          # noqa: BLE001
                print(f"[label] chip failed {img['key']} {point_id}: {exc}", flush=True)

    def prefetch_after(self, point_id: str) -> None:
        order = [p["point_id"] for p in self.points]
        i = order.index(point_id)
        for nxt in order[i + 1:i + 1 + PREFETCH]:
            self.pool.submit(self.render_point, nxt)

    # -- writing --------------------------------------------------------------
    def write(self, body: dict[str, Any]) -> int:
        labeller = str(body.get("labeller", ""))
        if not LABELLER.match(labeller):
            raise ValueError("labeller must be a short pseudonym: letters, digits, - or _")
        rnd = body.get("round", "primary")
        if rnd not in ("primary", "qa"):
            raise ValueError("round must be primary or qa")
        point_id = str(body.get("point_id", ""))
        if point_id not in self.by_id:
            raise ValueError(f"unknown point {point_id!r}")
        offered = {i["image_id"]: i for i in self.point_images[point_id]}
        pre = [i["image_id"] for i in self.point_images[point_id] if i["role"] == "pre_event"]
        events = {k for k, v in offered.items() if v["role"] == "event"}
        rows = body.get("rows") or []
        if {r.get("image_id") for r in rows} != events:
            raise ValueError("every event image of the point needs exactly one label")
        elapsed = body.get("elapsed_s")
        elapsed = round(float(elapsed), 1) if isinstance(elapsed, (int, float)) else ""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out = []
        for n, r in enumerate(rows, 1):
            cls, reason = r.get("class"), r.get("unobservable_reason") or ""
            if cls not in CLASSES:
                raise ValueError(f"class must be one of {CLASSES}")
            if (cls == "unobservable") != bool(reason) or (reason and reason not in REASONS):
                raise ValueError(f"unobservable needs a reason from {REASONS}; other "
                                 "classes take none")
            conf = r.get("confidence")
            if conf not in (1, 2, 3):
                raise ValueError("confidence must be 1, 2 or 3")
            notes = re.sub(r"\s+", " ", str(r.get("notes") or ""))[:300]
            img = offered[r["image_id"]]
            out.append({"point_id": point_id, "obs_id": f"{point_id}-{n}",
                        "labeller": labeller, "round": rnd, "labelled_at_utc": now,
                        "source": img["source"] + (f" ({img['tier']})" if img["tier"] else ""),
                        "image_id": img["image_id"], "image_datetime_utc": img["datetime"],
                        "class": cls, "unobservable_reason": reason, "confidence": conf,
                        "pre_event_image_id": pre[0] if pre else "",
                        "blind": "true", "elapsed_s": elapsed, "notes": notes})
        with self.lock:
            if rnd == "primary" and point_id in self.done(labeller):
                raise ValueError(f"{point_id} already has a primary label from {labeller}")
            new = not self.labels_path.exists()
            with open(self.labels_path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerows(out)
        return len(out)


STORE: Store | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:     # quieter console
        if "/api/chip/" not in str(args[0] if args else ""):
            sys.stderr.write("[label] " + fmt % args + "\n")

    def do_HEAD(self) -> None:
        self.send_response(200 if urlparse(self.path).path in ("/", "/index.html") else 404)
        self.end_headers()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Only chips are cached: they never change once rendered. The page and
        # the API must not be, or a fixed page keeps being served from cache.
        self.send_header("Cache-Control", "max-age=86400" if ctype == "image/png" else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        s = STORE
        url = urlparse(self.path)
        path = unquote(url.path)            # image keys carry ':' -> '%3A' in the page
        if path in ("/", "/index.html"):
            page = (Path(__file__).parent / "label_page.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/api/session":
            return self._json(200, {
                "experiment": s.cfg.get("event"), "kind": s.kind, "points": len(s.points),
                "label_key_version": s.manifest.get("label_key_version"),
                "peak_date": s.cfg["temporal"].get("peak_date"),
                "window": s.cfg["temporal"]["window"], "classes": CLASSES,
                "reasons": REASONS})
        if path == "/api/points":
            labeller = (parse_qs(url.query).get("labeller") or [""])[0]
            if not LABELLER.match(labeller):
                # Refused here, not only at save: a name that cannot be saved
                # must not get as far as a labelled point.
                return self._json(400, {"error": "labeller must be a short pseudonym: "
                                                 "1-16 letters, digits, - or _, no spaces"})
            done = s.done(labeller)
            return self._json(200, [{"point_id": p["point_id"], "draw_order": p["draw_order"],
                                     "done": p["point_id"] in done} for p in s.points])
        m = re.fullmatch(r"/api/point/([A-Z]\d{4})", path)
        if m and m[1] in s.by_id:
            # Chips render on request (one lock per image and point), so the
            # page shows each as soon as it is ready; the next points render
            # in the background meanwhile.
            s.prefetch_after(m[1])
            return self._json(200, s.point_view(m[1]))
        m = re.fullmatch(r"/api/chip/([\w.:-]+)/([A-Z]\d{4})/(\w+)\.png", path)
        if m and m[2] in s.by_id and m[3] in chips.CHIP_NAMES:
            img = next((i for i in s.point_images[m[2]] if i["key"] == m[1]), None)
            if img is None:
                return self._json(404, {"error": "image not offered for this point"})
            target = chips.chip_path(s.cache, img, m[2], m[3])
            if not target.exists():
                chips.ensure_chips(s.cache, img, s.by_id[m[2]])
            return self._send(200, target.read_bytes(), "image/png")
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/label":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            n = STORE.write(body)
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(200, {"written": n})


def main() -> None:
    global STORE
    if len(sys.argv) < 3:
        raise SystemExit("usage: python -m api.label_server <config.yaml> pilot|main "
                         "[--port N] [--prerender]")
    port = PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    STORE = Store(sys.argv[1], sys.argv[2])
    n_img = sum(len(v) for v in STORE.point_images.values())
    print(f"[label] {STORE.cfg.get('event')} {STORE.kind}: {len(STORE.points)} points, "
          f"{n_img} point-images, {len(STORE.index.get('excluded', []))} images excluded "
          "(tile cloud 100%)", flush=True)
    # Render ahead, in draw order, so the first points are ready first.
    for p in STORE.points:
        STORE.pool.submit(STORE.render_point, p["point_id"])
    if "--prerender" in sys.argv:
        STORE.pool.shutdown(wait=True)
        print("[label] all chips rendered", flush=True)
        return
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"[label] http://{HOST}:{port}  (local only, no auth)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
