"""Steps, uncertainty by step, the run register, Entro's reader, and tables.

    python tests/test_uncertainty.py        # no model, no network, no cost

Held still here:

    1. steps       -- every adapter reads its steps off payload fields; a field
                      that is absent makes no item; a tool's own block wins
    2. register    -- Monty stores run.steps with the result and writes
                      runs/<run_id>/steps.jsonl and uncertainty.json, and emits
                      one tool-step event per step
    3. crisp       -- the register Entro reads is one line per step and per item
    4. reader      -- read_uncertainty prints the stored register and a table
    5. tables      -- data rendered three ways; values copied, never computed;
                      the PNG has a white backdrop; only indexed ids resolve
    6. endpoint    -- GET /api/table/<id> serves the data and the PNG
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (cache_policy, experiment, logbook, tables, tools,  # noqa: E402
                  uncertainty, workflows)
from core.experiment import Experiment                              # noqa: E402
from core.monty_graph import run_monty                              # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def weather(model_id: str | None = "ecmwf_ifs") -> dict:
    src = {"model": "ecmwf_ifs -- IFS", "native_res_km": 9, "retrieved_at": "2026-10-25T00:00:00Z"}
    if model_id:
        src["model_id"] = model_id
    return {"meta": {"source": src, "window_utc": ["2025-11-24T00:00Z", "2025-12-01T23:00Z"]},
            "grid": {"n_points": 4, "spacing_deg": 0.1},
            "qc": {"warnings": ["9 km model wind and cloud (ecmwf_ifs) are used to gate products"],
                   "gaps": {"window_complete": True, "withheld": []}}}


def test_adapters() -> None:
    steps, block = uncertainty.capture("fetch_weather", weather())
    check("weather: request, aggregate and completeness steps, numbered",
          [s["step"] for s in steps] == ["request", "aggregate", "completeness"]
          and [s["n"] for s in steps] == [1, 2, 3])
    check("the model is named from the request", "ecmwf_ifs (9 km)" in block["input"][0]["statement"])
    _, legacy = uncertainty.capture("fetch_weather", weather(None))
    check("a payload stored before the model was named says its label is not reliable",
          any(i["key"] == "model_label" for i in legacy["input"]))
    lulc = {"selection": {"scenes_found": 9, "scenes_probed": 9, "lookback_days": 90,
                          "min_valid_fraction": 0.7},
            "scene": {"datetime": "2025-10-31T05:00Z", "days_before_start": 24,
                      "aoi_valid_fraction": 0.7416, "is_mosaic": True, "single_datatake": True},
            "indices": {"ndvi": {"stats": {"coverage": {"saturated_fraction": 0.0}}}}}
    steps, block = uncertainty.capture("fetch_lulc_data", lulc)
    vf = next(i for i in block["tool"] if i["key"] == "valid_fraction")
    check("land cover: the valid fraction is copied, not recomputed",
          vf["value"] == 0.7416 and "74.2%" in vf["statement"])
    steps, _ = uncertainty.capture("fetch_terrain_data", {"slope": {"classes": {}}})
    check("an absent field makes a vague step, not an invented number",
          all(i.get("value") in (None, 0) or i["key"] == "surface_model"
              for s in steps for i in s["uncertainty"]))
    ranking = {"gate": {"cloud_gate_pct": 100.0, "can_fire": False}, "method": {"mode": "WEIGHT"},
               "criteria": [{"base_weight": 1 / 12}] * 12,
               "ranking": [{"alternative": "GFM", "rank": 1, "closeness": 0.64}]}
    _, block = uncertainty.capture("rank_flood_products", ranking)
    keys = {i["key"] for i in block["decision"]}
    check("ranking: equal weights, the gate and 'a rank is not usability' are decisions",
          {"equal_weights", "cloud_gate", "rank_not_usability"} <= keys, str(keys))

    own = {"value": 1, "uncertainty": {"input": [{"statement": "mine", "basis": "measured"}],
                                       "tool": [], "pipeline": [], "decision": []}}
    steps, block = uncertainty.capture("some_new_tool", own)
    check("a tool with no adapter gets one step from its own block",
          len(steps) == 1 and steps[0]["uncertainty"][0]["statement"] == "mine")
    _, block = uncertainty.capture("fetch_weather", {**weather(), "uncertainty": {
        "input": [{"statement": "the tool's own", "basis": "measured"}],
        "tool": [], "pipeline": [], "decision": []}})
    check("a level the tool filled is kept; empty levels are filled from the steps",
          block["input"] == [{"statement": "the tool's own", "basis": "measured"}]
          and block["tool"])
    steps, _ = uncertainty.capture("fetch_lulc_data", {"selection": "not a dict"})
    check("a payload the adapter cannot read costs the steps, never the call",
          steps[0]["step"] == "steps not derived")


def test_register(tmp: Path) -> None:
    saved = cache_policy.RUNS_DIR
    cache_policy.RUNS_DIR = tmp / "runs"
    sink: list[dict] = []
    try:
        with cache_policy.use("on", "u-run"), logbook.collect_events(sink):
            r = run_monty({"event_id": 1, "tasks": [{"function": "rank_flood_products", "params": {
                "cloud_pct": 91.67, "wind_ms": 4.13, "veg_pct": 61.26, "steep_pct": 9.85,
                "urban_pct": 0.19}}]})["results"]["result_1"]
    finally:
        cache_policy.RUNS_DIR = saved
    out = r["output"]
    check("the result carries its steps and a filled decision block",
          len(out["run"]["steps"]) >= 3 and out["uncertainty"]["decision"])
    folder = tmp / "runs" / "u-run"
    lines = (folder / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    reg = json.loads((folder / "uncertainty.json").read_text(encoding="utf-8"))
    check("steps.jsonl holds one line per step, uncertainty.json one entry per call",
          len(lines) == len(out["run"]["steps"]) and reg[0]["tool"] == "rank_flood_products")
    check("one tool-step event per step",
          sum(1 for e in sink if e["event"] == "tool-step") == len(out["run"]["steps"]))
    text = uncertainty.crisp([("rank_flood_products", out)])
    check("the crisp register is a header, one line per step, tagged items",
          text.startswith("## rank_flood_products") and "\n1 indices -- " in text
          and "  DEC  " in text, text[:200])


def test_reader(tmp: Path) -> None:
    store = Experiment("reader-test", root=tmp / "experiments")
    store.create()
    saved = tables.INDEX
    tables.INDEX = tmp / "outputs" / "tables_index.json"
    experiment.set_current(None)
    try:
        none = tools.read_uncertainty()
        experiment.set_current(store)
        empty = tools.read_uncertainty()
        steps, block = uncertainty.capture("fetch_weather", weather())
        store.cache_input("weather_grid", {**weather(), "uncertainty": block,
                                           "run": {"steps": steps, "cache": "on", "run_id": "r1"}})
        text = tools.read_uncertainty()
        missing = tools.read_uncertainty("lulc")
        tid = tables.PLACEHOLDER.search(text).group(1) if tables.PLACEHOLDER.search(text) else ""
        data = json.loads(tables.resolve(tid).read_text(encoding="utf-8")) if tid else {}
    finally:
        experiment.set_current(None)
        tables.INDEX = saved
    check("with no store, the reader says so instead of reconstructing",
          "no uncertainty register" in none)
    check("with nothing stored, it says there is no register", "Nothing is stored" in empty)
    check("it prints the stored steps crisply", "## weather_grid (cache on, run r1)" in text
          and "1 request --" in text)
    check("it names the register's table placeholder, which resolves",
          bool(tid) and data.get("title") == "Uncertainty by step" and data["rows"])
    check("a result that is not stored is reported absent", "No stored result named 'lulc'" in missing)


def test_tables(tmp: Path) -> None:
    payload = {"ranking": [{"rank": 2, "alternative": "B", "sensor": "SAR", "closeness": 0.5,
                            "d_plus": 0.1, "d_minus": 0.1},
                           {"rank": 1, "alternative": "A", "sensor": "SAR", "closeness": 0.6421,
                            "d_plus": 0.09, "d_minus": 0.16}]}
    t = tables._ranking(payload)[0]
    check("a builder copies values and only orders them",
          t.rows[0][:2] == [1, "A"] and t.rows[0][3] == 0.6421)
    md = t.to_markdown()
    check("markdown right-aligns numeric columns", md.splitlines()[1].startswith("|---:|---|---|---:|"))
    saved = tables.INDEX
    tables.INDEX = tmp / "outputs" / "tables_index.json"
    try:
        entry = tables.save(t, tmp / "experiments" / "t-store")
        png = Path(entry["png"])
        from PIL import Image
        corner = Image.open(png).convert("RGB").getpixel((2, 2))
        check("the PNG is written on a white backdrop", png.is_file() and corner == (255, 255, 255),
              str(corner))
        check("the id resolves to the data", json.loads(tables.resolve(entry["id"]).read_text(
            encoding="utf-8"))["title"] == "MCDM ranking (TOPSIS)")
        for bad in ("t-zz", "../etc", "t-000000000000"):
            try:
                tables.resolve(bad)
                check(f"{bad!r} is refused", False)
            except KeyError:
                pass
        check("a malformed or unknown id is refused", True)
        index = json.loads(tables.INDEX.read_text(encoding="utf-8"))
        outside = tmp.parent / "elsewhere.json"
        outside.write_text("{}", encoding="utf-8")
        index["t-aaaaaaaaaaaa"] = {"json": str(outside), "png": ""}
        tables.INDEX.write_text(json.dumps(index), encoding="utf-8")
        try:
            tables.resolve("t-aaaaaaaaaaaa")
            check("an indexed file outside the allowed folders is refused", False)
        except KeyError:
            check("an indexed file outside the allowed folders is refused", True)
        outside.unlink()
        test_endpoint(entry)
    finally:
        tables.INDEX = saved
    out = tables.for_results({"result_1": {"task": "rank_flood_products", "status": "success",
                                           "output": {**payload, "run": {"steps": []}}}})
    check("a batch yields its tables plus its uncertainty register",
          [x.key for x in out] == ["mcdm_ranking", "uncertainty_this_run"])


def test_endpoint(entry: dict) -> None:
    import api.console_server as api

    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/table/"
    try:
        with urllib.request.urlopen(base + entry["id"]) as reply:
            body = json.loads(reply.read())
            check("GET /api/table/<id> serves the table data",
                  reply.status == 200 and body["columns"][0] == "Rank")
        with urllib.request.urlopen(base + entry["id"] + ".png") as reply:
            check("and <id>.png its white-backdrop image",
                  reply.headers["Content-Type"] == "image/png" and reply.read()[:4] == b"\x89PNG")
        try:
            urllib.request.urlopen(base + "t-ffffffffffff")
            check("an unknown table is a 404", False)
        except urllib.error.HTTPError as exc:
            check("an unknown table is a 404", exc.code == 404)
    finally:
        server.shutdown()


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            saved_root, experiment.ROOT = experiment.ROOT, tmp / "experiments"
            try:
                test_adapters()
                test_register(tmp)
                test_reader(tmp)
                test_tables(tmp)
            finally:
                experiment.ROOT = saved_root
    finally:
        logbook._log_path = saved
    check("read_uncertainty is on Entro's tool list",
          "read_uncertainty" in (Path(__file__).resolve().parents[1] / "agents" / "entro"
                                 / "agents.md").read_text(encoding="utf-8"))
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
