"""The plain-language guardrail's safety net: internal names out of the answer.

The rule lives in Argus' prompt and in the vault (Heart/Guardrails): the answer
is read by a flood analyst, a member of the public or a city mayor, so it
names the specialists by role and a workflow by what it does, and it carries
no backend vocabulary. A prompt is a request, so this module makes the parts
of the rule that can be checked mechanically true after the fact:

    Monty               -> the Python agent
    Norwood             -> the remote sensing expert
    Entro               -> the uncertainty agent
    Chanakya, Citadel   -> the flood analyst agent
    fetch_weather       -> the event weather workflow   (every registered name)
    payload(s)          -> data
    monty_result        -> the results

Whole words only, never inside a figure placeholder, and "Virginia Norwood" --
the Landsat engineer the agent is named after -- is left alone. Every
replacement is counted and the caller logs it: a rule enforced silently is
one nobody can see was needed, and a scrubbed answer means the prompt failed.

The rest of the rule -- no error messages, no "call", no file paths -- needs
judgment, and stays with the prompt.
"""

from __future__ import annotations

import re

ROLES = {
    "Monty": "the Python agent",
    "Norwood": "the remote sensing expert",
    "Entro": "the uncertainty agent",
    "Chanakya": "the flood analyst agent",
    "Citadel": "the flood analyst agent",
}

# What each workflow does, in a reader's words. A workflow missing here falls
# back to its name with underscores as spaces, never to the raw identifier.
WORKFLOWS = {
    "fetch_place_context": "place context",
    "fetch_weather": "event weather",
    # Renamed and retired names, kept so an old transcript or a model's habit
    # is still translated.
    "fetch_weather_grid": "event weather",
    "fetch_weather_data": "point weather",
    "fetch_lulc_data": "land cover",
    "fetch_worldcover": "land cover (WorldCover 2021)",
    "fetch_world_bank_indicators": "national GDP and population",
    "fetch_inform_risk": "national risk index",
    "get_pop_data": "population",
    "search_previous_disasters": "previous disasters",
    "get_flood_extent": "flood extent",
    "get_flood_map": "flood map",
    "assess_road_access": "road and hospital access",
    "check_slope_tree_roads": "slope-and-tree road check",
    "fetch_terrain_data": "terrain",
    "fetch_permanent_water": "permanent water",
    "data_availability": "flood product availability",
    "rank_flood_products": "flood product ranking",
    "sensitivity_analysis": "sensitivity analysis",
    "accuracy_metrics": "agreement metrics",
    "where_am_i": "area overview",
}

WORDS = {
    r"monty_result": "the results",
    r"payloads": "data",
    r"payload": "data",
}

_PLACEHOLDER = re.compile(r"<image\s*-\s*[^>]*>")


def workflow_name(name: str) -> str:
    """A workflow's plain name: 'fetch_weather' -> 'event weather'."""
    return WORKFLOWS.get(name) or name.removeprefix("fetch_").replace("_", " ")


def _registered() -> list[str]:
    try:
        from . import workflows
        return list(workflows.names(include_inactive=True))
    except Exception:                                          # noqa: BLE001
        return []


def _starts_sentence(text: str, at: int) -> bool:
    before = text[:at].rstrip(" *_`")
    return not before or before[-1] in ".!?:\n#-|"


def _scrub_text(text: str, counts: dict[str, int]) -> str:
    def swap(pattern: str, plain: str, flags: int = 0) -> None:
        nonlocal text

        def repl(m: re.Match) -> str:
            counts[m.group(0).strip("`")] = counts.get(m.group(0).strip("`"), 0) + 1
            out = plain
            if _starts_sentence(m.string, m.start()):
                out = out[0].upper() + out[1:]
            return out

        text = re.sub(pattern, repl, text, flags=flags)

    names = sorted(set(_registered()) | set(WORKFLOWS), key=len, reverse=True)
    for name in names:
        plain = f"the {workflow_name(name)} workflow"
        # "the `fetch_x` workflow" -> one "the ... workflow", not two.
        swap(rf"(?:\bthe\s+)?`?\b{re.escape(name)}\b`?(?:\s+workflow)?", plain,
             re.IGNORECASE)
    for word, plain in ROLES.items():
        # Possessive kept: "Monty's result" -> "the Python agent's result".
        # "the Norwood agent" and "Agent Norwood" both become one role name.
        swap(rf"(?<!Virginia )(?:\b[Tt]he\s+)?(?:\bAgent\s+)?\b{word}\b"
             rf"(?:\s+agent\b)?", plain)
    for word, plain in WORDS.items():
        swap(rf"`?\b{word}\b`?", plain, re.IGNORECASE)
    return text


def scrub(answer: str) -> tuple[str, dict[str, int]]:
    """The answer with internal names replaced, and what was replaced how often.

    Figure placeholders are cut out first and put back untouched: their names
    are addresses the console resolves, not prose.
    """
    counts: dict[str, int] = {}
    if not answer:
        return answer, counts
    parts, last, out = [], 0, []
    for m in _PLACEHOLDER.finditer(answer):
        parts.append((answer[last:m.start()], m.group(0)))
        last = m.end()
    parts.append((answer[last:], ""))
    for prose, keep in parts:
        out.append(_scrub_text(prose, counts) + keep)
    return "".join(out), counts
