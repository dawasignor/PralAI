"""RQ5 match: facts from the APIs and the model's interpretation, checked apart.

Reads one batch folder written by `scripts/run_n.py` (the same folder
`core/compare.py` reads) and writes `match/` beside it:

    match/match.md          the tables, for the thesis
    match/match.json        every number behind them
    match/api_facts.csv     one row per fact
    match/llm_text.csv      one row per model-written text
    match/figures/*.png     the overview and the two per-item charts

**Two layers, never mixed.**

* **API facts** are the `facts` of each sidecar (`runs/<id>/facts/*.facts.json`).
  Code computes every one of them from what the tools fetched; no model writes
  a fact. A fact MATCHES between two runs when its status and its value are
  identical (canonical JSON, no tolerance). A NOT KNOWN fact matches another
  NOT KNOWN; NOT KNOWN against a value is a mismatch. An observation whose
  source tool failed in that run is set aside, as in `core/compare.py`.
* **LLM interpretation** is every text an agent wrote: the report slots
  (`llm_text` in each sidecar, the reply as parsed) and the stored
  deliverables (`runs/<id>/deliverables/*.json`). Each is checked two ways:

  - *fact match*: each value quoted in the text is looked up in the facts of
    the SAME run. A value matches when a fact value equals it at the text's
    own decimals (379.2 for 379.24) or as a percentage of a fraction
    (14 for 0.14). Score = matched values / quoted values. Plus how far the
    runs quote the same values (Jaccard of the value sets, pairwise).
  - *text match*: identical text (whitespace collapsed), character similarity
    (`difflib.SequenceMatcher`, autojunk off) and word overlap (Jaccard of the
    lower-cased word sets), each averaged over all run pairs.

Everything is deterministic: no model, no network, no randomness. A match
percentage needs two runs; with one run the pairwise results are "n/a".

**Not compared, on purpose:** `VOLATILE` facts (the render date), which differ
by construction. They are listed, not hidden.
"""

from __future__ import annotations

import csv
import json
import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any

from . import compare

VOLATILE = {"last_updated"}            # the render date: differs by construction
# RQ5 reports the thesis path (Modules 1-10, the Flood analyst pipeline) as one
# run, not the reader reports built on it (user decision 2026-11-11).
NOT_THESIS_PATH = {"public_report", "executive_summary"}
# The sidecar every thesis-path fact is first looked for in; a key repeated in
# a module's own sidecar is the same computation and is counted once.
FIRST = "flood_analyst"
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])\d+(?:\.\d+)?")
WORD = re.compile(r"[a-z0-9]+(?:['.][a-z0-9]+)*")


# -- reading ---------------------------------------------------------------------
def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load(batch_dir: Path) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    batch = _read(batch_dir / "batch.json")
    runs = []
    for entry in batch.get("runs") or []:
        folder = batch_dir / "runs" / entry["run_id"]
        run = _read(folder / "run.json") if (folder / "run.json").exists() else dict(entry)
        run["facts"], run["texts"] = {}, {}
        for side in sorted((folder / "facts").glob("*.facts.json")):
            label = side.name.removesuffix(".facts.json")
            if label in NOT_THESIS_PATH:
                continue
            data = _read(side)
            run["facts"][label] = {f["key"]: f for f in data.get("facts") or []}
            for slot, t in (data.get("llm_text") or {}).items():
                run["texts"][f"{label}: {slot}"] = {"text": t.get("written", ""), "label": label}
        for d in sorted((folder / "deliverables").glob("*.json")):
            data = _read(d)
            run["texts"][f"deliverable: {data.get('name', d.stem)}"] = {
                "text": data.get("text", ""), "label": None, "agent": data.get("agent", "")}
        run["payload_numbers"] = set()
        for p in sorted((folder / "inputs").rglob("*.json")):
            try:
                rec = _read(p) or {}
                # A stored result keeps its data under `payload`; a failure
                # record (inputs/_failures/) is the data itself.
                _fact_numbers(rec.get("payload", rec) if isinstance(rec, dict) else rec,
                              run["payload_numbers"])
            except (OSError, json.JSONDecodeError):
                continue
        run["has_inputs"] = (folder / "inputs").is_dir()
        run["conditions"] = _conditions(folder / "metrics.jsonl")
        # Steps skipped because an input was "not stored": the knock-on of an
        # earlier failure in this run (see unavailable_tools).
        run["cascade_tools"] = sorted({
            s.get("name", "") for p in sorted((folder / "pipelines").glob("*.json"))
            for s in (_read(p).get("steps") or [])
            if s.get("status") == "skipped" and "not stored" in str(s.get("detail", ""))})
        runs.append(run)
    batch["runs"] = runs
    return batch


def _conditions(path: Path) -> dict[str, Any]:
    """What the model calls of one run ran on: prompt hashes and cache reads."""
    if not path.exists():
        return {"agent_calls": 0, "prompt_sha": {}, "cache_read": None,
                "providers": [], "temperature": [], "run_token": None}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    agents = [r for r in rows if r.get("kind") == "agent"]
    shas: dict[str, list[str]] = {}
    for r in agents:
        if r.get("prompt_sha"):
            shas.setdefault(r.get("agent", "?"), [])
            if r["prompt_sha"] not in shas[r.get("agent", "?")]:
                shas[r.get("agent", "?")].append(r["prompt_sha"])
    reads = [r.get("cache_read") for r in agents]
    providers = sorted({p for r in agents for p in (r.get("providers") or [])})
    settings = [r.get("settings") for r in agents if r.get("settings")]
    temps = sorted({str(s.get("temperature")) for s in settings})
    tokens = {bool(s.get("run_token")) for s in settings}
    return {"agent_calls": len(agents), "prompt_sha": shas,
            "cache_read": sum(reads) if reads and None not in reads else None,
            "providers": providers, "temperature": temps,
            "run_token": (tokens.pop() if len(tokens) == 1 else ("mixed" if tokens else None))}


# -- helpers ---------------------------------------------------------------------
def _pairs(values: list[Any], same) -> float | None:
    pairs = list(combinations(values, 2))
    return (sum(same(a, b) for a, b in pairs) / len(pairs)) if pairs else None


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(x: float | None) -> float | None:
    return None if x is None else round(100 * x, 1)


# A list number ("1. Drinking water", "2) Access") is numbering, not a value.
ENUMERATION = re.compile(r"(?:(?<=^)|(?<=[\s(]))\d{1,2}[.)](?=\s+[A-Z(*_])", re.M)


def numbers_in(text: str) -> list[str]:
    """Values (numbers) quoted in a text, as written (thousands commas
    removed); list numbering is not a value."""
    text = ENUMERATION.sub(" ", text or "")
    return [m.group(0).replace(",", "") for m in NUMBER.finditer(text)]


def _fact_numbers(value: Any, out: set[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        out.update(float(n) for n in numbers_in(value))
    elif isinstance(value, dict):
        for v in value.values():
            _fact_numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _fact_numbers(v, out)


def fact_pool(facts: dict[str, dict]) -> set[float]:
    pool: set[float] = set()
    for f in facts.values():
        if f.get("status") != "not_known":
            _fact_numbers(f.get("value"), pool)
            _fact_numbers(f.get("text"), pool)
    return pool


class Pool:
    """Numbers a quoted value may be traced to, indexed by rounding, so a payload
    of many thousand numbers is searched once per decimal place, not per value."""

    def __init__(self, numbers: set[float]):
        self.numbers = numbers
        self._at: dict[int, set[float]] = {}

    def has(self, written: str) -> bool:
        """`written` is a value as the text gives it; it matches a value equal
        to it at the text's own decimals, or a fraction given as a percentage."""
        d = len(written.split(".")[1]) if "." in written else 0
        if d not in self._at:
            self._at[d] = ({round(f, d) for f in self.numbers}
                           | {round(f * 100, d) for f in self.numbers})
        return float(written) in self._at[d]


def figure_matches(written: str, pool: set[float]) -> bool:
    return Pool(pool).has(written)


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _words(text: str) -> set[str]:
    return set(WORD.findall((text or "").lower()))


def _jaccard(a: set, b: set) -> float:
    return (len(a & b) / len(a | b)) if (a or b) else 1.0


# -- the two layers --------------------------------------------------------------
# A tool "failure" that IS the measurement, not an outage: the tool answered,
# and the answer was "the data does not allow it". Never set aside for these.
RESULT_FAILURES = ("BelowValidFraction", "NoUsableMosaic")


def unavailable_tools(run: dict) -> set[str]:
    """The tools whose data this run could not get, by outage, plus every tool
    skipped because an input they need was "not stored" -- the knock-on of an
    outage (rq5-batch-1 run 1: Open-Meteo 503 -> no weather -> ranking, flood
    map, population, hazard map skipped). A failure that is a result
    (RESULT_FAILURES) is not an outage."""
    out = {f.get("tool", "") or f.get("input", "") for f in run.get("source_failures") or []}
    out |= {f.get("tool", "") for f in run.get("tool_failures") or []
            if not any(r in str(f.get("detail", "")) for r in RESULT_FAILURES)}
    out |= set(run.get("cascade_tools") or [])
    return compare._with_cache_names({t for t in out if t})


def _value(f: dict, run_id: str) -> tuple:
    if f.get("status") == "not_known":
        return ("not_known",)
    v = f.get("value")
    # "" and None both say "nothing to show" (an empty block): one value.
    canon = "" if v in (None, "") else compare._canon(v)
    # A figure fact is a path under runs/<run_id>/: the run id is where it
    # was written, not what it says, so it is masked.
    return (f.get("status"), canon.replace(run_id, "<run>"))


def api_facts(runs: list[dict]) -> list[dict[str, Any]]:
    known_tools = compare._tool_names()
    labels = sorted({lb for r in runs for lb in r["facts"]}, key=lambda lb: (lb != FIRST, lb))
    lost = {r["run_id"]: unavailable_tools(r) for r in runs}
    rows, seen = [], set()
    for label in labels:
        keys = sorted({k for r in runs for k in r["facts"].get(label, {})} - seen)
        seen |= set(keys)
        for key in keys:
            obs, aside, absent = [], [], 0
            for r in runs:
                f = r["facts"].get(label, {}).get(key)
                if f is None:
                    absent += 1
                    continue
                # The tools a fact rests on: those its source names, and for a
                # NOT KNOWN fact the tools it says still have to run.
                rests_on = compare.tools_of(f.get("source", ""), known_tools)
                rests_on |= compare._with_cache_names({t for t in f.get("run") or [] if t})
                if rests_on & lost[r["run_id"]]:
                    aside.append(r["run_id"])
                    continue
                obs.append(_value(f, r["run_id"]))
            values = [o[1] for o in obs if o[0] != "not_known"]
            kind = ("numeric" if values and all(_is_num(v) for v in values) else
                    "categorical" if values else "not known")
            modal = max((obs.count(o) for o in set(obs)), default=0)
            rows.append({"label": label, "fact": key, "kind": kind,
                         "volatile": key in VOLATILE, "runs": len(runs),
                         "compared": len(obs), "known": len(values),
                         "not_known": len(obs) - len(values), "absent": absent,
                         "set_aside_for_source_failure": aside,
                         "distinct": len(set(obs)),
                         "match_pct": _pct(_pairs(obs, lambda a, b: a == b)),
                         "modal_share_pct": _pct(modal / len(obs)) if obs else None})
    return rows


def _is_num(canon: str) -> bool:
    try:
        float(canon)
        return True
    except (TypeError, ValueError):
        return False


def llm_texts(runs: list[dict]) -> list[dict[str, Any]]:
    units = sorted({u for r in runs for u in r["texts"]})
    rows = []
    pools: dict[str, Pool] = {}
    for r in runs:
        every = set(r["payload_numbers"])
        every |= fact_pool({f"{lb}.{k}": f for lb, fs in r["facts"].items() for k, f in fs.items()})
        pools[r["run_id"]] = Pool(every)
    for unit in units:
        texts, own, anywhere, quoted, ungrounded, value_sets = [], [], [], 0, set(), []
        missing, inputs_seen = 0, True
        for r in runs:
            t = r["texts"].get(unit)
            if not t or not t["text"].strip():
                missing += 1
                continue
            text = t["text"]
            texts.append(text)
            nums = numbers_in(text)
            quoted += len(nums)
            # Own facts: only the sidecar the slot was written from -- the
            # prompt says "use ONLY the facts below". A deliverable has none.
            if t["label"]:
                pool = Pool(fact_pool(r["facts"].get(t["label"], {})))
                own.append(sum(pool.has(n) for n in nums) / len(nums) if nums else None)
            # Any run data: every sidecar of the run and every tool payload.
            hits = [n for n in nums if pools[r["run_id"]].has(n)]
            ungrounded.update(n for n in nums if n not in hits)
            anywhere.append(len(hits) / len(nums) if nums else None)
            inputs_seen = inputs_seen and r["has_inputs"]
            value_sets.append({float(n) for n in nums})
        rows.append({
            "unit": unit, "runs": len(runs), "written": len(texts), "missing": missing,
            "mean_words": round(_mean([len(t.split()) for t in texts]) or 0, 1),
            "values_quoted": quoted,
            "fact_match_pct": _pct(_mean(own)),
            "any_data_match_pct": _pct(_mean(anywhere)),
            "payloads_checked": inputs_seen,
            "values_not_in_facts": sorted(ungrounded, key=float)[:20],
            # A text that quotes no value in any run has nothing to agree on: n/a.
            "value_agreement_pct": (_pct(_pairs(value_sets, lambda a, b: _jaccard(a, b)))
                                     if any(value_sets) else None),
            "identical_pct": _pct(_pairs([_norm(t) for t in texts], lambda a, b: a == b)),
            "similarity_pct": _pct(_pairs([_norm(t) for t in texts],
                                          lambda a, b: SequenceMatcher(None, a, b,
                                                                       autojunk=False).ratio())),
            "word_overlap_pct": _pct(_pairs([_words(t) for t in texts], _jaccard)),
        })
    return rows


def overview(facts: list[dict], texts: list[dict]) -> list[dict[str, Any]]:
    cmp = [f for f in facts if not f["volatile"] and f["match_pct"] is not None]

    def row(layer, measure, items, key):
        vals = [i[key] for i in items if i[key] is not None]
        return {"layer": layer, "measure": measure, "items": len(vals),
                "mean_pct": round(sum(vals) / len(vals), 1) if vals else None,
                "min_pct": min(vals) if vals else None,
                "at_100": sum(1 for v in vals if v == 100.0)}

    # The headline: four measures. Identical text, word overlap and the
    # lenient "anywhere in the run's data" check stay in match.json and the
    # CSVs; they add little to the headline (identical text is near 0 at
    # temperature 1 by design; word overlap tracks similarity).
    return [row("API facts", "Fact match", cmp, "match_pct"),
            row("LLM interpretation", "Fact match (values traced to the facts it was given)",
                texts, "fact_match_pct"),
            row("LLM interpretation", "Fact agreement (same values quoted in every run)",
                texts, "value_agreement_pct"),
            row("LLM interpretation", "Text match (character similarity)",
                texts, "similarity_pct")]


def cost_time(runs: list[dict]) -> dict[str, Any]:
    """Each run's wall time (s) and model cost (US$, as OpenRouter reported it),
    and their mean, SD, min and max. A run whose cost was not reported in full
    is "not known" -- never 0 -- and is left out of the cost statistics, which
    say how many runs they cover."""
    rows = [{"run_id": r["run_id"], "duration_s": r.get("duration_s"),
             "cost_usd": r.get("cost_usd"), "agent_calls": r.get("agent_calls"),
             "input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens")}
            for r in runs]

    def stats(key: str) -> dict[str, Any]:
        vals = [float(x[key]) for x in rows if isinstance(x.get(key), (int, float))]
        if not vals:
            return {"n": 0}
        m = sum(vals) / len(vals)
        sd = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0.0
        return {"n": len(vals), "mean": m, "sd": sd, "min": min(vals), "max": max(vals),
                "total": sum(vals)}

    return {"rows": rows, "time": stats("duration_s"), "cost": stats("cost_usd")}


def match(batch_dir: Path) -> dict[str, Any]:
    batch = load(batch_dir)
    runs = batch["runs"]
    facts, texts = api_facts(runs), llm_texts(runs)
    return {"batch": {k: v for k, v in batch.items() if k != "runs"},
            "runs": [{"run_id": r["run_id"], "conditions": r["conditions"],
                      "sidecars": sorted(r["facts"]), "texts": sorted(r["texts"]),
                      "pipelines": r.get("pipelines")} for r in runs],
            "overview": overview(facts, texts), "api_facts": facts, "llm_texts": texts,
            "cost_time": cost_time(runs)}


# -- writing ---------------------------------------------------------------------
def _f(v: Any) -> str:
    return "n/a" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def markdown(res: dict[str, Any], figures: list[Path]) -> str:
    b, runs = res["batch"], res["runs"]
    n = len(runs)
    L = [f"# RQ5 match: batch {b.get('batch_id')}", "",
         f"- experiment: {b.get('experiment')}; runs: {n} of {b.get('n')}; "
         f"cache {b.get('cache')}; agents {'on' if b.get('agents') else 'off'}; code {b.get('code')}",
         "- facts come from the APIs through code; the LLM interpretation is every "
         "agent-written text. The two are matched separately.", ""]
    if n < 2:
        L += ["**One run only: the run-pair results are n/a.** A match needs two runs.", ""]

    C = ["", "## Run conditions", "",
          "Identical prompts in every run are a precondition for reading a text "
          "difference as model variation. A non-zero cache read means a provider "
          "served part of a prompt from its cache.", "",
          "| Run | Agent calls | Temperature | Run token | Served by | Prompt hashes per agent | "
          "Cached prompt tokens |", "|---|---:|---|---|---|---|---:|"]
    for r in runs:
        c = r["conditions"]
        shas = "; ".join(f"{a} {','.join(s[:8] for s in v)}" for a, v in sorted(c["prompt_sha"].items()))
        token = {True: "yes", False: "no", "mixed": "mixed", None: "not recorded"}[c.get("run_token")]
        C.append(f"| {r['run_id']} | {c['agent_calls']} | {', '.join(c.get('temperature') or []) or 'not recorded'} | "
                 f"{token} | {', '.join(c.get('providers') or []) or 'not recorded'} | "
                 f"{shas or 'not recorded'} | "
                 f"{_f(c['cache_read']) if c['cache_read'] is not None else 'not recorded'} |")
    ms = b.get("model_settings") or {}
    if ms:
        C += ["", f"Batch settings: model {ms.get('model')}, temperature {ms.get('temperature')}, "
                  f"reasoning {'on' if ms.get('reasoning') else 'off'}, provider routing "
                  f"{json.dumps(ms.get('provider_routing')) if ms.get('provider_routing') else 'OpenRouter chooses'}."]
    served = {p for r in runs for p in (r["conditions"].get("providers") or [])}
    if len(served) > 1:
        C += ["", f"**More than one provider served this batch ({', '.join(sorted(served))}).** "
                  "Differences in text may come from different servers, not only from sampling."]
    cached = [r["run_id"] for r in runs if (r["conditions"].get("cache_read") or 0) > 0]
    if cached:
        C += ["", f"Cached prompt tokens in: {', '.join(cached)}. With the run token on, these "
                  "come from calls inside the same run, never from another run."]

    L += ["", "## The result", "",
          "The thesis path (Modules 1-10) run again and again; how often the runs agree.", "",
          "| Layer | Measure | Match % | Items | Lowest % | Items at 100 % |",
          "|---|---|---:|---:|---:|---:|"]
    L += [f"| {o['layer']} | {o['measure']} | **{_f(o['mean_pct'])}** | {o['items']} | "
          f"{_f(o['min_pct'])} | {o['at_100']} |" for o in res["overview"]]
    L += ["", "- **Fact match (API facts)**: for each fact the thesis path reports, the share of "
              "run pairs whose status and value are identical; then the mean over the facts.",
          "- **Fact match (LLM)**: for each agent-written text, the share of the values (numbers) it "
          "quotes that are found in the facts it was given; mean over the runs, then over the texts.",
          "- **Fact agreement (LLM)**: for each text, how far two runs quote the same set of "
          "values (Jaccard: shared / all), over the run pairs; then the mean over the texts.",
          "- **Text match (LLM)**: for each text, the character similarity of two runs' wording "
          "(0 = nothing alike, 100 = identical), over the run pairs; then the mean over the texts."]

    ct = res.get("cost_time") or {}
    tm, cs = ct.get("time") or {}, ct.get("cost") or {}

    def _mmss(s: float | None) -> str:
        return "n/a" if s is None else f"{int(s // 60)} min {int(round(s % 60)):02d} s"

    def _usd(v: float | None) -> str:
        return "not known" if v is None else f"US$ {v:.4f}"

    L += ["", "## Time and cost per run", "",
          "| Run | Time | Cost | Agent calls | Tokens in / out |", "|---|---:|---:|---:|---:|"]
    L += [f"| {x['run_id']} | {_mmss(x['duration_s'])} | {_usd(x['cost_usd'])} | "
          f"{x['agent_calls'] if x['agent_calls'] is not None else 'n/a'} | "
          f"{x['input_tokens'] if x['input_tokens'] is not None else 'n/a'} / "
          f"{x['output_tokens'] if x['output_tokens'] is not None else 'n/a'} |"
          for x in ct.get("rows") or []]
    if tm.get("n"):
        L += ["", f"- **Average time per run: {_mmss(tm['mean'])}** (SD {_mmss(tm['sd'])}; "
                  f"range {_mmss(tm['min'])} to {_mmss(tm['max'])}; {tm['n']} runs). Wall time of "
                  "the pipeline, without the sleep between runs."]
    if cs.get("n"):
        L += [f"- **Average cost per run: {_usd(cs['mean'])}** (SD {_usd(cs['sd'])}; range "
              f"{_usd(cs['min'])} to {_usd(cs['max'])}; total {_usd(cs['total'])}; "
              f"{cs['n']} of {len(ct.get('rows') or [])} runs with a reported cost). Model calls "
              "only, as OpenRouter reported them; the data sources are free."]
    else:
        L += ["- Average cost per run: not known -- no run reported its model cost in full."]

    cmp = [f for f in res["api_facts"] if not f["volatile"] and f["match_pct"] is not None]
    differ = [f for f in cmp if f["match_pct"] < 100]
    known = [f for f in cmp if f["kind"] != "not known"]
    L += ["", "## API facts", "",
          f"{len(cmp) - len(differ)} of {len(cmp)} facts matched in every run pair "
          f"({len(cmp) - len(known)} of them NOT KNOWN in every run, which agrees trivially). "
          + (f"**Over the {len(known)} facts with a value in at least one run, the fact match is "
             f"{_f(sum(f['match_pct'] for f in known) / len(known))} %.**" if known else "")]
    if differ:
        L += ["", "The facts that differed:", "",
              "| Fact | Module | Match % | Distinct answers | Known in |",
              "|---|---|---:|---:|---:|"]
        L += [f"| {f['fact']} | {f['label'].replace('_', ' ')} | {_f(f['match_pct'])} | "
              f"{f['distinct']} | {f['known']}/{f['compared']} |" for f in differ]
    aside = [f for f in res["api_facts"] if f["set_aside_for_source_failure"]]
    if aside:
        L += ["", "Set aside for a source failure (not counted):"]
        L += [f"- {f['fact']}: {', '.join(f['set_aside_for_source_failure'])}" for f in aside]
    vol = sorted({f["fact"] for f in res["api_facts"] if f["volatile"]})
    if vol:
        L += ["", f"Not compared (differ by construction): {', '.join(vol)}."]

    L += ["", "## LLM interpretation", "",
          "| Text | Written | Values quoted | Fact match % | Fact agreement % | Text match % |",
          "|---|---:|---:|---:|---:|---:|"]
    for t in res["llm_texts"]:
        L.append(f"| {t['unit']} | {t['written']}/{t['runs']} | {t['values_quoted']} | "
                 f"{_f(t['fact_match_pct'])} | {_f(t['value_agreement_pct'])} | "
                 f"{_f(t['similarity_pct'])} |")
    bad = [t for t in res["llm_texts"] if t["values_not_in_facts"]]
    if bad:
        L += ["", "Values found nowhere in the run's data -- check each by hand: a rounding "
                  "(\"about 380\"), a difference (\"86 %\" = 100 - 14) or part of a phrase "
                  "(\"30 days\") is not an invented value:"]
        L += [f"- {t['unit']}: {', '.join(t['values_not_in_facts'])}" for t in bad]
    L += ["", "More per text (identical text, word overlap, values anywhere in the run's data) "
              "is in `llm_text.csv` and `match.json`."]

    L += C
    L += ["", "## Method and limits", "",
          "- Deterministic: no model, no network, no randomness; the same batch always gives "
          "the same numbers.",
          "- API facts: no tolerance; a chart's file path is compared without its run id.",
          "- LLM fact match: a quoted value equals a fact value at the text's own decimals, or a "
          "fraction given as a percentage. A deliverable (a brief, a hand-over) has no facts of "
          "its own and is checked against the whole run's data. A value written in words (\"ten\") is not seen.",
          "- Text match: difflib's ratio on the text with whitespace collapsed.",
          "- A run that did not write a text is counted under Written, not as a mismatch.",
          "- A source failure sets the fact aside; the text written from it is still compared."]
    if figures:
        L += ["", "## Figures", ""] + [f"![{p.stem}](figures/{p.name})" for p in figures]
    return "\n".join(L) + "\n"


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (";".join(map(str, v)) if isinstance(v, list) else v) for k, v in r.items()})


def write(batch_dir: Path) -> Path:
    from .charts_rq5 import draw_match

    batch_dir = Path(batch_dir)
    out = batch_dir / "match"
    out.mkdir(parents=True, exist_ok=True)
    res = match(batch_dir)
    (out / "match.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    _csv(out / "api_facts.csv", res["api_facts"])
    _csv(out / "llm_text.csv", res["llm_texts"])
    figures = draw_match(res, out / "figures")
    path = out / "match.md"
    path.write_text(markdown(res, figures), encoding="utf-8")
    return path
