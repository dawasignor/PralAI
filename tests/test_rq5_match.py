"""RQ5 match on synthetic runs: API facts and LLM text, checked apart.

    python tests/test_rq5_match.py      # no model, no network, no cost

    1. identical   -- identical runs: every fact and text matches 100 %
    2. fact        -- one changed fact value is found, and only it
    3. not known   -- NOT KNOWN vs NOT KNOWN matches; vs a value it does not
    4. figures     -- quoted figures traced to the same run's facts, at the
                      text's decimals and as a percentage; an invented one is listed
    5. text        -- identical / similarity / word overlap against hand values
    6. volatile    -- the render date is not compared, and is listed
    7. one run     -- run-pair figures are n/a, nothing crashes
    8. written     -- match.md, match.json, both CSVs and the figures exist
    9. sidecar     -- render() keeps the model's slot text in llm_text
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import rq5_match  # noqa: E402

results: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def fact(key, value, status="ok", source="weather_grid", text=""):
    return {"key": key, "value": value, "unit": "", "source": source, "status": status,
            "reason": "" if status == "ok" else "no data", "basis": "measured",
            "needs": "", "run": [], "text": text}


BASE = {"facts": [fact("storm_total_mm", 379.24), fact("peak_hour_mm", 18.5),
                  fact("observed_share", 0.14), fact("flooded_km2", None, "not_known"),
                  fact("last_updated", "2026-11-11", source="render time (UTC)"),
                  fact("product", "gfm", source="rank_flood_products")],
        "llm_text": {"cause": {"written": "Rain of 379.2 mm fell; 14 % of the land was seen.",
                               "placed": ""}}}


def make_batch(root: Path, sidecars: list[dict], deliverables: list[str] | None = None) -> Path:
    batch = root / "b"
    runs = []
    for i, side in enumerate(sidecars, 1):
        rid = f"b-r{i:02d}"
        folder = batch / "runs" / rid
        (folder / "facts").mkdir(parents=True)
        (folder / "facts" / "essential_services_hev.facts.json").write_text(json.dumps(side), encoding="utf-8")
        if deliverables:
            (folder / "deliverables").mkdir()
            (folder / "deliverables" / "land_cover_brief.json").write_text(
                json.dumps({"name": "land_cover_brief", "agent": "norwood", "text": deliverables[i - 1]}),
                encoding="utf-8")
        (folder / "run.json").write_text(json.dumps({"run_id": rid, "source_failures": [],
                                                     "tool_failures": []}), encoding="utf-8")
        runs.append({"run_id": rid})
    (batch / "batch.json").write_text(json.dumps({"batch_id": "b", "n": len(runs), "runs": runs,
                                                  "cache": "off", "agents": True}), encoding="utf-8")
    return batch


def by(rows, key, value):
    return next(r for r in rows if r[key] == value)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 1. identical
        res = rq5_match.match(make_batch(tmp / "1", [BASE] * 3, ["Same brief."] * 3))
        f = [x for x in res["api_facts"] if not x["volatile"]]
        t = res["llm_texts"]
        check("identical: every fact 100 %", all(x["match_pct"] == 100.0 for x in f))
        check("identical: every text 100 %", all(x["identical_pct"] == 100.0 and x["similarity_pct"] == 100.0
                                                 and x["word_overlap_pct"] == 100.0 for x in t),
              str([(x["unit"], x["identical_pct"]) for x in t]))
        check("identical: deliverable read as LLM text", any(x["unit"] == "deliverable: land_cover_brief" for x in t))

        # 2. one changed fact
        other = copy.deepcopy(BASE)
        other["facts"][1]["value"] = 18.6
        res = rq5_match.match(make_batch(tmp / "2", [BASE, BASE, other]))
        f = res["api_facts"]
        check("fact: changed value found", abs(by(f, "fact", "peak_hour_mm")["match_pct"] - 33.3) < 0.1,
              str(by(f, "fact", "peak_hour_mm")["match_pct"]))
        check("fact: only it", all(x["match_pct"] == 100.0 for x in f
                                   if x["fact"] != "peak_hour_mm" and not x["volatile"]))

        # 3. NOT KNOWN
        nk = copy.deepcopy(BASE)
        nk["facts"][3] = fact("flooded_km2", 2.5)
        res = rq5_match.match(make_batch(tmp / "3", [BASE, BASE, nk]))
        row = by(res["api_facts"], "fact", "flooded_km2")
        check("not known: NK=NK matches, NK vs value does not",
              abs(row["match_pct"] - 33.3) < 0.1 and row["not_known"] == 2 and row["known"] == 1, str(row))

        # 4. figures
        bad = copy.deepcopy(BASE)
        bad["llm_text"]["cause"]["written"] = "Rain of 379.2 mm fell; 14 % seen; 412 homes flooded."
        res = rq5_match.match(make_batch(tmp / "4", [BASE, bad]))
        row = by(res["llm_texts"], "unit", "essential_services_hev: cause")
        # run 1: 2/2 traced; run 2: 2/3 -> mean 83.3
        check("figures: 379.2 and 14 % traced, 412 not", abs(row["fact_match_pct"] - 83.3) < 0.1
              and row["values_not_in_facts"] == ["412"], str(row))
        check("figures: figure sets compared", abs(row["value_agreement_pct"] - 66.7) < 0.1,
              str(row["value_agreement_pct"]))
        check("figures: thousands comma read", rq5_match.numbers_in("273,819 people, 2.5 km2") == ["273819", "2.5"])

        # 4b. a deliverable's figure traced to a tool payload of the same run
        batch = make_batch(tmp / "4b", [BASE, BASE], ["Best scene 0.6076; 412 homes.",
                                                      "Best scene 0.6076; 412 homes."])
        for rid in ("b-r01", "b-r02"):
            (batch / "runs" / rid / "inputs").mkdir()
            (batch / "runs" / rid / "inputs" / "fetch_lulc_data.json").write_text(
                json.dumps({"payload": {"selection": {"best": 0.60763}}}), encoding="utf-8")
        row = by(rq5_match.match(batch)["llm_texts"], "unit", "deliverable: land_cover_brief")
        check("payload: 0.6076 found in run data, 412 not; no own-facts score for a deliverable",
              row["any_data_match_pct"] == 50.0 and row["fact_match_pct"] is None
              and row["values_not_in_facts"] == ["412"], str(row))

        # 5. text against hand values
        a, b = copy.deepcopy(BASE), copy.deepcopy(BASE)
        a["llm_text"]["cause"]["written"] = "heavy rain fell"
        b["llm_text"]["cause"]["written"] = "heavy rain  fell today"
        res = rq5_match.match(make_batch(tmp / "5", [a, b]))
        row = by(res["llm_texts"], "unit", "essential_services_hev: cause")
        from difflib import SequenceMatcher
        want = round(100 * SequenceMatcher(None, "heavy rain fell", "heavy rain fell today",
                                           autojunk=False).ratio(), 1)
        check("text: identical 0, word overlap 3/4, similarity = difflib",
              row["identical_pct"] == 0.0 and row["word_overlap_pct"] == 75.0 and row["similarity_pct"] == want,
              str(row))

        # 6. volatile
        late = copy.deepcopy(BASE)
        late["facts"][4]["value"] = "2026-11-12"
        res = rq5_match.match(make_batch(tmp / "6", [BASE, late]))
        over = res["overview"][0]
        check("volatile: render date not in the overview", over["mean_pct"] == 100.0, str(over))
        md = rq5_match.markdown(res, [])
        check("volatile: listed as not compared", "Not compared (differ by construction): last_updated" in md)

        # 6b. a figure path carries its run id; that is not a difference
        pa, pb = copy.deepcopy(BASE), copy.deepcopy(BASE)
        pa["facts"].append(fact("rain_hyetograph", "runs/b-r01/figures/hyetograph.png"))
        pb["facts"].append(fact("rain_hyetograph", "runs/b-r02/figures/hyetograph.png"))
        res = rq5_match.match(make_batch(tmp / "6b", [pa, pb]))
        check("paths: run id masked", by(res["api_facts"], "fact", "rain_hyetograph")["match_pct"] == 100.0)

        # 7. one run
        res = rq5_match.match(make_batch(tmp / "7", [BASE]))
        check("one run: pairwise n/a", all(x["match_pct"] is None for x in res["api_facts"])
              and "One run only" in rq5_match.markdown(res, []))

        # 7b. thesis path only: a Module 11/12 sidecar is not read; a fact in
        # both the flood_analyst sidecar and a module's is counted once
        batch = make_batch(tmp / "7b", [BASE, BASE])
        for rid in ("b-r01", "b-r02"):
            facts = batch / "runs" / rid / "facts"
            (facts / "public_report.facts.json").write_text(json.dumps(BASE), encoding="utf-8")
            (facts / "flood_analyst.facts.json").write_text(json.dumps({"facts": BASE["facts"]}),
                                                            encoding="utf-8")
        res = rq5_match.match(batch)
        labels = {f["label"] for f in res["api_facts"]}
        keys = [f["fact"] for f in res["api_facts"]]
        check("thesis path: reports left out, shared facts once",
              "public_report" not in labels and len(keys) == len(set(keys))
              and not any(t["unit"].startswith("public_report") for t in res["llm_texts"]),
              f"{labels} {len(keys)}")
        check("headline: four measures", [o["measure"] for o in res["overview"]] == [
            "Fact match", "Fact match (values traced to the facts it was given)",
            "Fact agreement (same values quoted in every run)", "Text match (character similarity)"])

        # 7b2. an outage's knock-on is set aside; "" equals None; a result
        # failure (BelowValidFraction) is not an outage
        a, b = copy.deepcopy(BASE), copy.deepcopy(BASE)
        a["facts"] += [fact("ranked", "gfm", source="rank_flood_products"),
                       fact("blank", None), fact("s2_note", "fails at 0.7", source="fetch_lulc_data")]
        b["facts"] += [fact("ranked", None, "not_known", source=""),
                       fact("blank", ""), fact("s2_note", "fails at 0.7", source="fetch_lulc_data")]
        b["facts"][-3]["run"] = ["rank_flood_products"]
        batch = make_batch(tmp / "7b2", [a, b])
        (batch / "runs" / "b-r02" / "pipelines").mkdir()
        (batch / "runs" / "b-r02" / "pipelines" / "flood_analyst.json").write_text(json.dumps(
            {"steps": [{"kind": "tool", "name": "rank_flood_products", "status": "skipped",
                        "detail": "not stored: weather"}]}), encoding="utf-8")
        for rid in ("b-r01", "b-r02"):
            rj = batch / "runs" / rid / "run.json"
            rec = json.loads(rj.read_text(encoding="utf-8"))
            rec["tool_failures"] = [{"pipeline": "flood_analyst", "tool": "fetch_lulc_data",
                                     "detail": "runtime_error: BelowValidFraction: no scene"}]
            rj.write_text(json.dumps(rec), encoding="utf-8")
        f = rq5_match.api_facts(rq5_match.load(batch)["runs"])
        check("outage knock-on set aside, blank = none, result failure compared",
              by(f, "fact", "ranked")["set_aside_for_source_failure"] == ["b-r02"]
              and by(f, "fact", "blank")["match_pct"] == 100.0
              and by(f, "fact", "s2_note")["match_pct"] == 100.0
              and not by(f, "fact", "s2_note")["set_aside_for_source_failure"],
              str([(x["fact"], x["match_pct"], x["set_aside_for_source_failure"]) for x in f
                   if x["fact"] in ("ranked", "blank", "s2_note")]))

        # 7c. time and cost: mean over the runs; an unreported cost is not 0
        batch = make_batch(tmp / "7c", [BASE, BASE, BASE])
        for rid, secs, cost in (("b-r01", 600.0, 0.05), ("b-r02", 660.0, 0.07), ("b-r03", 630.0, None)):
            rj = batch / "runs" / rid / "run.json"
            rec = json.loads(rj.read_text(encoding="utf-8"))
            rec.update(duration_s=secs, cost_usd=cost, agent_calls=4)
            rj.write_text(json.dumps(rec), encoding="utf-8")
        res = rq5_match.match(batch)
        ct = res["cost_time"]
        md = rq5_match.markdown(res, [])
        check("time and cost: means, unreported cost left out",
              ct["time"]["mean"] == 630.0 and ct["time"]["n"] == 3 and abs(ct["cost"]["mean"] - 0.06) < 1e-9
              and ct["cost"]["n"] == 2 and "not known" in md and "Average time per run: 10 min 30 s" in md
              and "Average cost per run: US$ 0.0600" in md and "2 of 3 runs with a reported cost" in md,
              str(ct["cost"]))

        # 8. written
        batch = make_batch(tmp / "8", [BASE, other, bad], ["Brief one.", "Brief two.", "Brief one."])
        path = rq5_match.write(batch)
        out = batch / "match"
        names = sorted(p.name for p in (out / "figures").glob("*.png"))
        check("written: md, json, csv, figures",
              path.exists() and (out / "match.json").exists() and (out / "api_facts.csv").exists()
              and (out / "llm_text.csv").exists()
              and names == ["match_api_facts.png", "match_llm_text.png", "match_overview.png"], str(names))

    # 9. render keeps the slot text
    from core import report_render
    src = (REPO / "core" / "report_render.py").read_text(encoding="utf-8")
    check("sidecar: render writes llm_text", '"llm_text"' in src and "_slots_task" in src)
    _ = report_render  # imported to prove the module still loads

    failed = [n for s, n in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
