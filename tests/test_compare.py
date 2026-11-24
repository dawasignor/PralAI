"""RQ5 comparison and runner, on synthetic runs.

    python tests/test_compare.py      # no model, no network, no cost

    1. identical   -- identical runs give zero spread, agreement 1, rho 1, W 1
    2. detected    -- one changed numeric fact is found, attributed, and only it
    3. chips       -- agreement rate and modal share against hand counts
    4. failures    -- an observation whose tool had a source failure is set
                      aside, not counted as a disagreement; NOT KNOWN is not a value
    5. rankings    -- Spearman and Kendall's W against closed forms, ties included
    6. summary     -- summary.md, compare.json and the figures are written
    7. runner      -- strictly sequential, one run id per run, a crashing
                      pipeline is recorded and the batch goes on, cost summed
                      from the metrics (and "not known" when unreported), an
                      existing batch is refused
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from core import cache_policy, compare, logbook, pipelines  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
PRODUCTS = ["gfm", "dswx_hls", "dswx_s1", "mcdwd", "vcdwd"]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def fact(key, value, source="get_flood_extent (GFM)", status="ok", reason=""):
    return {"key": key, "value": value, "unit": "", "source": source, "status": status,
            "reason": reason, "text": ""}


def base_facts() -> list[dict]:
    return [fact("flooded_km2", 42.43), fact("people_in_aoi", 310564, "get_pop_data (2024)"),
            fact("status_roads", "AFFECTED", "rule: ..."), fact("flood_product", "GFM (Sentinel-1)",
                                                                "rank_flood_products (best-ranked)"),
            fact("inform_risk", 3.3, "INFORM Risk Mid 2025 (published 2025-08-28, national)")]


def make_batch(root: Path, runs: list[dict]) -> Path:
    """runs: [{"facts": [...], "ranking": [...], "source_failures": [...]}]"""
    batch = root / "batch"
    entries = []
    for i, spec in enumerate(runs, start=1):
        rid = f"b-r{i:02d}"
        folder = batch / "runs" / rid
        (folder / "facts").mkdir(parents=True)
        (folder / "facts" / "public_report.facts.json").write_text(
            json.dumps({"facts": spec["facts"]}), encoding="utf-8")
        ranking = spec.get("ranking") or [{"id": p, "rank": k} for k, p in enumerate(PRODUCTS, start=1)]
        run = {"run_id": rid, "pipelines": {"flood_bot": {"status": "ok"}}, "duration_s": 10.0,
               "cost_usd": 0.01, "input_tokens": 100, "output_tokens": 50, "ranking": ranking,
               "source_failures": spec.get("source_failures") or [], "tool_failures": []}
        (folder / "run.json").write_text(json.dumps(run), encoding="utf-8")
        entries.append({"run_id": rid})
    (batch / "batch.json").write_text(json.dumps({"batch_id": "b", "experiment": "x",
                                                  "pipelines": ["flood_bot"], "n": len(runs),
                                                  "cache": "off", "agents": False, "runs": entries}),
                                      encoding="utf-8")
    return batch


def test_identical(root: Path) -> None:
    res = compare.compare(make_batch(root, [{"facts": base_facts()} for _ in range(4)]))
    f = res["facts"]["public_report"]
    check("identical runs: every fact identical, zero spread",
          all(v["identical"] for v in f.values()) and f["flooded_km2"]["sd"] == 0
          and f["flooded_km2"]["range"] == 0 and f["flooded_km2"]["cv"] == 0)
    check("identical runs: agreement 1 and modal share 1",
          f["status_roads"]["agreement_rate"] == 1 and f["status_roads"]["modal_share"] == 1)
    check("identical runs: Spearman 1 and Kendall's W 1",
          abs(res["ranking"]["spearman_min"] - 1) < 1e-12 and abs(res["ranking"]["kendall_w"] - 1) < 1e-12)


def test_detected(root: Path) -> None:
    runs = [{"facts": base_facts()} for _ in range(4)]
    runs[2]["facts"][0] = fact("flooded_km2", 46.43)
    runs[3]["facts"][2] = fact("status_roads", "NOT AFFECTED", "rule: ...")
    res = compare.compare(make_batch(root, runs))
    f = res["facts"]["public_report"]
    changed = sorted(k for k, v in f.items() if not v["identical"])
    check("only the changed facts are flagged", changed == ["flooded_km2", "status_roads"], str(changed))
    v = f["flooded_km2"]
    mean = (42.43 * 3 + 46.43) / 4
    sd = (((42.43 - mean) ** 2 * 3 + (46.43 - mean) ** 2) / 3) ** 0.5
    check("its mean, SD, range and max deviation are exact",
          abs(v["mean"] - mean) < 1e-9 and abs(v["sd"] - sd) < 1e-9 and abs(v["range"] - 4.0) < 1e-9
          and abs(v["max_abs_dev"] - (46.43 - mean)) < 1e-9, json.dumps({k: v[k] for k in ("mean", "sd")}))
    c = f["status_roads"]
    check("chip agreement: 3 of 4 alike -> agreement 0.5 (3 of 6 pairs), modal share 0.75",
          c["agreement_rate"] == 0.5 and c["modal_share"] == 0.75 and c["modal"] == "AFFECTED")


def test_failures(root: Path) -> None:
    runs = [{"facts": base_facts()} for _ in range(4)]
    runs[1]["facts"][1] = fact("people_in_aoi", 120000, "get_pop_data (2024)")
    runs[1]["source_failures"] = [{"input": "population", "tool": "population",
                                   "failures": [{"domain": "example.org", "status": 502}]}]
    runs[3]["facts"][4] = fact("inform_risk", None, "", status="not_known", reason="INFORM is not stored")
    res = compare.compare(make_batch(root, runs))
    f = res["facts"]["public_report"]
    p = f["people_in_aoi"]
    check("a value from a run whose tool had a source failure is set aside",
          p["set_aside_for_source_failure"] == ["b-r02"] and p["identical"] and p["sd"] == 0, str(p))
    check("... and still counts as known", p["known"] == 4)
    i = f["inform_risk"]
    check("NOT KNOWN is not a value: 3 of 4 known, the reason kept",
          i["known"] == 3 and i["not_known"] == 1 and i["identical"]
          and i["not_known_reasons"] == ["INFORM is not stored"])
    check("a source string naming no tool is mapped by its publisher",
          "fetch_inform_risk" in compare.tools_of("INFORM Risk Mid 2025 (national)", set())
          and {"get_pop_data", "population"} <= compare.tools_of("get_pop_data (2024)",
                                                                     {"get_pop_data"}))


def test_rankings() -> None:
    base = {p: float(k) for k, p in enumerate(PRODUCTS, start=1)}
    swap = dict(base, gfm=2.0, dswx_hls=1.0)
    check("Spearman: one adjacent swap in 5 -> 0.9 (1 - 6*2/120)",
          abs(compare.spearman(base, swap) - 0.9) < 1e-12)
    w = compare.kendall_w([base, base, base, swap])
    check("Kendall's W: three alike and one swap -> 0.9625 (12*154 / (16*120))", abs(w - 0.9625) < 1e-12, str(w))
    tied = {"a": 1.0, "b": 1.0, "c": 3.0}
    check("ties: identical tied rankings give W 1 and rho 1",
          abs(compare.kendall_w([tied, tied]) - 1) < 1e-12 and abs(compare.spearman(tied, tied) - 1) < 1e-12)
    check("one ranking alone gives no W", compare.kendall_w([base]) is None)


def test_summary(root: Path) -> None:
    runs = [{"facts": base_facts()} for _ in range(3)]
    runs[1]["facts"][0] = fact("flooded_km2", 44.0)
    runs[2]["ranking"] = [{"id": p, "rank": k} for k, p in enumerate(reversed(PRODUCTS), start=1)]
    batch = make_batch(root, runs)
    path = compare.write_summary(batch)
    text = path.read_text(encoding="utf-8")
    figs = sorted(p.name for p in (batch / "figures").glob("*.png"))
    check("summary.md, compare.json and three figures are written",
          path.exists() and (batch / "compare.json").exists()
          and figs == ["chip_agreement.png", "numeric_spread.png", "rank_frequency.png"], str(figs))
    check("the summary names the changed fact, the ranking and the uncovered text layer",
          "| flooded_km2 | 3/3 |" in text and "Kendall's W" in text and "text layer is not compared" in text)
    check("the summary sums the cost of the runs", "Total cost: US$ 0.03" in text)


def test_runner(root: Path) -> None:
    import run_n

    store = root / "store"
    calls: list[tuple[str, str]] = []

    class Fake:
        def __init__(self, name, crash_on=""):
            self.name, self.crash_on = name, crash_on

        def run(self, experiment, *, cache, run_id, writer):
            calls.append((self.name, run_id))
            if run_id.endswith(self.crash_on) and self.crash_on:
                raise RuntimeError("boom")
            store.mkdir(parents=True, exist_ok=True)
            n = int(run_id[-2:])
            (store / "11_public_report.facts.json").write_text(json.dumps(
                {"facts": [fact("flooded_km2", 40.0 + n)]}), encoding="utf-8")
            (store / "11_public_report.md").write_text(f"report {n}", encoding="utf-8")
            folder = cache_policy.RUNS_DIR / run_id
            folder.mkdir(parents=True, exist_ok=True)
            cost = None if n == 3 else 0.02
            with (folder / "metrics.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "agent", "input_tokens": 10, "output_tokens": 5,
                                     "cost_usd": cost}) + "\n")
                fh.write(json.dumps({"kind": "pipeline", "duration_s": 1.5}) + "\n")
            return {"store": str(store), "status": "ok", "duration_s": 1.5,
                    "steps": [{"kind": "tool", "name": "get_pop_data", "status": "failed",
                               "detail": "x"}]}

    fakes = {"fa": Fake("fa"), "bot": Fake("bot", crash_on="r02")}
    saved = (pipelines.get, run_n.RESULTS)
    pipelines.get = lambda name: fakes[name]
    run_n.RESULTS = root / "results"
    try:
        rc = run_n.main(["--experiment", "x", "--pipelines", "fa,bot", "--n", "3", "--sleep", "0",
                         "--no-cache", "--no-agents", "--batch-id", "t1"])
        batch = json.loads((root / "results" / "t1" / "batch.json").read_text(encoding="utf-8"))
        check("the batch runs strictly in order, one run id per run, both pipelines each",
              rc == 0 and calls == [("fa", "t1-r01"), ("bot", "t1-r01"), ("fa", "t1-r02"),
                                    ("bot", "t1-r02"), ("fa", "t1-r03"), ("bot", "t1-r03")], str(calls))
        r2 = batch["runs"][1]
        check("a crashing pipeline is recorded and the batch goes on",
              r2["pipelines"]["bot"]["status"] == "crashed" and len(batch["runs"]) == 3)
        run1 = json.loads((root / "results" / "t1" / "runs" / "t1-r01" / "run.json").read_text(encoding="utf-8"))
        check("per-run cost and tokens come from the metrics", run1["cost_usd"] == 0.04
              and run1["input_tokens"] == 20 and run1["tool_failures"][0]["tool"] == "get_pop_data")
        run3 = json.loads((root / "results" / "t1" / "runs" / "t1-r03" / "run.json").read_text(encoding="utf-8"))
        check("a cost a call did not report is not known, never 0", run3["cost_usd"] is None)
        snap = root / "results" / "t1" / "runs" / "t1-r02" / "facts" / "public_report.facts.json"
        check("each run's own sidecar and text are snapshotted",
              json.loads(snap.read_text(encoding="utf-8"))["facts"][0]["value"] == 42.0
              and (snap.parent.parent / "text" / "11_public_report.md").exists())
        summary = (root / "results" / "t1" / "summary.md").read_text(encoding="utf-8")
        check("the comparison is written at the end, cost not known overall",
              "flooded_km2" in summary and "Total cost: not known" in summary)
        try:
            run_n.main(["--experiment", "x", "--pipelines", "fa", "--n", "1", "--sleep", "0",
                        "--batch-id", "t1"])
            check("an existing batch id is refused", False)
        except SystemExit as exc:
            check("an existing batch id is refused", exc.code == 2)
    finally:
        pipelines.get, run_n.RESULTS = saved


def main() -> None:
    saved = (logbook._log_path, cache_policy.RUNS_DIR)
    logbook._log_path = lambda: None
    try:
        for test in (test_identical, test_detected, test_failures, test_summary, test_runner):
            with tempfile.TemporaryDirectory() as raw:
                cache_policy.RUNS_DIR = Path(raw) / "runs"
                test(Path(raw))
        test_rankings()
    finally:
        logbook._log_path, cache_policy.RUNS_DIR = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
