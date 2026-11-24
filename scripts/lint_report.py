#!/usr/bin/env python
"""Lint a report or a report template for the wording rules.

    python scripts/lint_report.py <file.md> [more.md ...] [--json] [--strict]

Mechanical checks only -- what a script can see without judging style:

    filler       generic transitions and filler ("it is important to note", ...)
    openers      a sentence opening used three or more times; two sections
                 opening with the same words
    numbers      a template's fixed text holds a number (it should come from a
                 placeholder); a rendered report holds a number that no fact
                 in its `.facts.json` sidecar carries -- an untraceable number
    spelling     US spellings, where the project writes UK English
    dates        more than one date format in one document

**A template is linted on its wording only** (D-22): the fenced block of a
vault template note, with its placeholders removed. A rendered report is linted
on its prose, with tables, images and placeholders removed, and its numbers
checked against the sidecar beside it.

Exit 0 always, unless `--strict` and an issue was found. `--json` prints the
machine-readable result, which the RQ5 runner keeps as the text layer of a run.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

FILLER = ("it is important to note", "it is worth noting", "it should be noted",
          "in today's world", "needless to say", "in conclusion", "in summary",
          "to sum up", "overall,", "at the end of the day", "it goes without saying",
          "plays a crucial role", "a testament to", "delve into")

# US -> UK. Words only; "meter" is left out (a placeholder kind).
US_SPELLINGS = {"color": "colour", "colors": "colours", "center": "centre", "centers": "centres",
                "behavior": "behaviour", "analyze": "analyse", "analyzed": "analysed",
                "analyzing": "analysing", "organization": "organisation", "labeled": "labelled",
                "modeling": "modelling", "modeled": "modelled", "favor": "favour",
                "neighbor": "neighbour", "neighborhood": "neighbourhood", "gray": "grey",
                "liter": "litre", "catalog": "catalogue", "defense": "defence",
                "program": "programme", "traveled": "travelled", "fertilizer": "fertiliser",
                "utilize": "use", "prioritize": "prioritise", "summarize": "summarise"}

# A fixed number a template may carry, with the decision that allows it.
ALLOWED_TEMPLATE_NUMBERS = {"10 cm": "design decision D-16 (the advisory)"}

PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
COMMENT = re.compile(r"<!--.*?-->", re.S)
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LONG_DATE = re.compile(r"\b\d{1,2} (?:January|February|March|April|May|June|July|August|"
                       r"September|October|November|December) \d{4}\b")
SHORT_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")


def _split_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def template_block(text: str) -> str | None:
    m = re.search(r"## Template\s*\n```markdown\n(.*?)\n```", text, re.S)
    return m.group(1) if m else None


def prose(text: str) -> list[tuple[int, str]]:
    """(line number, text) of the prose: no tables, images, placeholders,
    code fences or comments."""
    text = COMMENT.sub("", text)
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("|"):
            continue
        line = IMAGE.sub("", PLACEHOLDER.sub("", line))
        if line.strip():
            out.append((n, line))
    return out


# The renderer's own markers -- a gap said aloud, an agent slot still to
# write -- repeat by design and are not wording to lint.
MARKERS = re.compile(r"^(?:\*?[^*]*: )?(?:Not known yet|not known yet|_\(to be written)")


def _sentences(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    out = []
    for n, line in lines:
        body = re.sub(r"^\s*(?:#+|[-*>]|\d+\.)\s*", "", line)
        for s in re.split(r"(?<=[.!?])\s+", body):
            s = s.strip()
            if len(s.split()) >= 3 and not MARKERS.match(s.lstrip("*")):
                out.append((n, s))
    return out


def _numbers_in_facts(sidecar: dict) -> list[float]:
    blob = json.dumps(sidecar, default=str)
    out = []
    for m in NUMBER.findall(blob):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _traceable(num: str, known: list[float]) -> bool:
    """Is a shown number a fact's value at the precision it is shown with?"""
    shown = num.rstrip(",")
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    value = float(shown.replace(",", ""))
    digits = len(shown.replace(",", "").replace(".", "").lstrip("-0")) or 1
    # As stored at the shown precision, as a share shown in % (BUG-059), or
    # at the significant figures it is shown with.
    return any(round(k, decimals) == value or round(k * 100, decimals) == value
               or any(float(f"{k:.{d}g}") == value for d in range(1, digits + 1))
               for k in known)


def lint(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    block = template_block(raw)
    mode = "template" if block is not None else "report"
    body = block if block is not None else _split_frontmatter(raw)
    lines = prose(body)
    issues: list[dict] = []

    def add(check: str, line: int, text: str, detail: str) -> None:
        issues.append({"check": check, "line": line, "text": text.strip()[:140], "detail": detail})

    for n, line in lines:
        low = line.lower()
        for phrase in FILLER:
            if phrase in low:
                add("filler", n, line, f"filler: {phrase!r}")
        for us, uk in US_SPELLINGS.items():
            if re.search(rf"\b{us}\b", low):
                add("spelling", n, line, f"US spelling {us!r}; write {uk!r}")

    sentences = _sentences(lines)
    openers = Counter(" ".join(s.lower().split()[:2]) for _, s in sentences)
    for opener, count in openers.items():
        if count >= 3:
            add("openers", 0, opener, f"{count} sentences open with {opener!r}")
    firsts: dict[str, str] = {}
    section = None
    for n, line in lines:
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section and section not in firsts.values():
            words = " ".join(re.sub(r"^\W+", "", line).lower().split()[:3])
            if len(words.split()) == 3 and words in firsts:
                add("openers", n, line, f"sections {firsts[words]!r} and {section!r} open alike")
            firsts.setdefault(words, section)
            section = None

    if mode == "template":
        for n, line in lines:
            if line.startswith("#"):
                continue
            stripped = line
            for allowed in ALLOWED_TEMPLATE_NUMBERS:
                stripped = stripped.replace(allowed, "")
            for num in NUMBER.findall(stripped):
                add("numbers", n, line, f"fixed number {num!r} in template wording; "
                                        "a number belongs in a placeholder")
    else:
        sidecar_path = path.with_suffix(".facts.json")
        if sidecar_path.exists():
            known = _numbers_in_facts(json.loads(sidecar_path.read_text(encoding="utf-8")))
            for n, line in lines:
                if line.startswith("#"):
                    continue
                text = ISO_DATE.sub("", LONG_DATE.sub("", line))
                # "/ 10" is a meter's scale, from the template, not a fact.
                text = re.sub(r"/ \d+", "", text)
                for num in NUMBER.findall(text):
                    if not _traceable(num, known):
                        add("numbers", n, line, f"number {num!r} is in no fact of the sidecar")
        else:
            add("numbers", 0, "", "no .facts.json sidecar beside the report; numbers not checked")

    formats = {name for name, rx in (("ISO yyyy-mm-dd", ISO_DATE), ("d Month yyyy", LONG_DATE),
                                      ("d/m/y", SHORT_DATE))
               if any(rx.search(line) for _, line in lines)}
    if len(formats) > 1:
        add("dates", 0, "", f"mixed date formats: {', '.join(sorted(formats))}")

    by_check = Counter(i["check"] for i in issues)
    return {"file": str(path), "mode": mode, "sentences": len(sentences),
            "issues": issues, "counts": dict(by_check), "ok": not issues}


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    results = [lint(f) for f in files]
    if "--json" in argv:
        print(json.dumps(results, indent=1))
    else:
        for r in results:
            print(f"{r['file']} ({r['mode']}): {'clean' if r['ok'] else r['counts']}")
            for i in r["issues"]:
                where = f"line {i['line']}: " if i["line"] else ""
                print(f"  [{i['check']}] {where}{i['detail']}" + (f" -- {i['text']}" if i["text"] else ""))
    return 1 if "--strict" in argv and any(not r["ok"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
