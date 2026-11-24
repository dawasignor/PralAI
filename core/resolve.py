"""Settling the AOI and the time frame before any analysis runs.

A message like "what is the sewage risk in Boston" carries two things nobody
has checked: which Boston, and when. The rest of the graph treats both as
given, so this is where they stop being a guess.

One pass, three steps:

  1. **Read the message.** One model turn extracts the place and the rough
     window, and says whether they are new, unchanged, or absent -- "now check
     the power outages" names neither, and must inherit both rather than
     wiping them.
  2. **Look them up.** `core.places` resolves the name against OpenStreetMap.
     The user's dates are the window; no register is consulted for it.
  3. **Decide, or ask.** One clear match is accepted and announced in the
     answer. An ambiguous name ends the turn with a question, because a run
     over the wrong Boston costs more than a round trip. A MISSING place or
     window does not: the turn goes on with it marked "not set".

It also reads whether the message asks about the event itself (`about_event`).
The graph then lists what the disaster registers hold for it -- names and
links only (core/event_records.py).

The model is used for language and nothing else. Which candidate wins, and
whether the result is good enough to proceed on, is decided in Python -- those
are rules, and rules belong somewhere they can be tested.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, timedelta

from . import pinned, places
from .logbook import log
from .session import Place, SessionRecord, Window

# How far outside the window the user described an event may sit and still be
# the event they meant. "Around November" reaches back into late October; it
# does not reach into August.
NEAR_DAYS = 45

# What counts as a rival, as a share of the top match's Nominatim importance.
#
# Rank alone is the wrong test. "Tirupati" returns three entries that are all
# the same city at different granularities, and choosing between those matters
# to nobody. "Boston" returns Massachusetts at 0.77 and Lincolnshire at 0.54 --
# a clear winner by rank, two different countries by geography, and an analysis
# run over the loser is wrong in a way no reader can see. So ambiguity is
# measured by *place*: a rival is a candidate in a different country or region
# that scores at least this much of the leader.
RIVAL_SHARE = 0.6

# The fields a reading has to carry at least one of. Anything else parsed out
# of the reply is some other contract's answer, not this one's.
KEYS = {"place", "approx_start", "approx_end", "change",
        "chosen_place_index", "chosen_event_index"}

# Four decimal numbers after the word "bbox", in any of the shapes a person
# actually writes them: `bbox [a, b, c, d]`, `bbox: a,b,c,d`, `bbox a b c d`.
#
# Parsed in Python and not asked of the model, for the reason the rest of this
# module exists: a bounding box is the one input where a plausible-looking
# wrong answer is invisible downstream, and an extraction is a guess where a
# regex is a reading. If the user wrote coordinates, those coordinates are the
# AOI -- the gazetteer is not consulted and cannot overrule them.
_NUM = r"([-+]?\d+(?:\.\d+)?)"

# The positional form: the word, then four numbers in west/south/east/north
# order. "bbox" or "bounding box" -- the second is how anyone who is not
# reading a function signature writes it, and requiring the abbreviation sent a
# correctly specified AOI to the gazetteer instead.
_BBOX = re.compile(
    r"(?:bbox|bounding\s+box)\D{0,4}"
    + _NUM + r"[,\s]+" + _NUM + r"[,\s]+" + _NUM + r"[,\s]+" + _NUM,
    re.IGNORECASE)

# The labelled form: each edge named, in any order, anywhere in the message --
# "N 7.083 / S 6.783 / W 79.867 / E 80.217", or "west 79.867, south 6.783...".
# Safer than the positional one rather than looser: the labels say which number
# is which, so there is nothing to infer from position and a stray number
# elsewhere in the sentence cannot be swept in.
#
# A bare letter needs the word boundary on BOTH sides. Without it "Kelani
# basin, E 80.217" is fine but "Colombo N 7.083" would also let the "n" of an
# ordinary word open a match.
_EDGE = {
    "west": re.compile(r"\b(?:w|west|lon_?min|min_?lon)\b\s*[:=]?\s*" + _NUM,
                       re.IGNORECASE),
    "south": re.compile(r"\b(?:s|south|lat_?min|min_?lat)\b\s*[:=]?\s*" + _NUM,
                        re.IGNORECASE),
    "east": re.compile(r"\b(?:e|east|lon_?max|max_?lon)\b\s*[:=]?\s*" + _NUM,
                       re.IGNORECASE),
    "north": re.compile(r"\b(?:n|north|lat_?max|max_?lat)\b\s*[:=]?\s*" + _NUM,
                        re.IGNORECASE),
}


def _explicit_bbox(text: str) -> tuple[float, float, float, float] | None:
    """A bounding box the user stated outright, or None.

    Returns (west, south, east, north). A box that is not west<east and
    south<north, or that leaves the planet, is rejected rather than repaired --
    "I think you meant these four numbers in a different order" is exactly the
    kind of help that produces an analysis over the wrong place.
    """
    text = text or ""
    match = _BBOX.search(text)
    if match:
        corners = [float(v) for v in match.groups()]
    else:
        # All four edges must be labelled. Three labelled and one inferred is
        # the kind of repair that produces an analysis over the wrong place,
        # which is the whole reason this function refuses to reorder as well.
        found = {}
        for edge, pattern in _EDGE.items():
            hit = pattern.search(text)
            if hit is None:
                return None
            found[edge] = float(hit.group(1))
        corners = [found["west"], found["south"], found["east"], found["north"]]

    west, south, east, north = corners
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    return west, south, east, north


def _parse(text: str) -> dict:
    blob = text.strip()
    if blob.startswith("```"):
        blob = blob.split("```")[1]
        blob = blob[4:] if blob.startswith("json") else blob
    start, end = blob.find("{"), blob.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(blob[start : end + 1], strict=False)
    except json.JSONDecodeError:
        return {}
    # A routing decision is valid JSON and completely useless here. Returning
    # it as a reading would mean "no place named" rather than "I could not
    # read this", and the two lead to different, visible behaviour.
    if not isinstance(parsed, dict) or not KEYS & set(parsed):
        return {}
    return parsed


def _iso(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _widen(start: date | None, end: date | None) -> tuple[date, date] | None:
    """The user's window, opened up by NEAR_DAYS at each end."""
    if not (start or end):
        return None
    return ((start or end) - timedelta(days=NEAR_DAYS),
            (end or start) + timedelta(days=NEAR_DAYS))


def _overlaps(event: dict, span: tuple[date, date] | None) -> bool:
    """Does this event run over any part of `span`?

    Overlap rather than onset. A flood that began in late October and ran
    through November is the November flood, and testing only its start date
    would throw it away in favour of nothing.
    """
    began, ended = _iso(event.get("start", "")), _iso(event.get("end", ""))
    if not span or not began:
        return False
    return began <= span[1] and (ended or began) >= span[0]


def _candidates(events: list[dict], start: date | None,
                end: date | None) -> tuple[list[dict], bool]:
    """The events worth offering, and whether they are inside the stated window.

    Two tiers, and the first non-empty one wins. An event inside the window
    beats one merely near it, so "November 2021" over India resolves to the
    flood of 14-18 November rather than asking the user to choose between that
    and one in October -- a question with an obvious answer is not worth
    asking.

    The returned flag is what separates the tiers, and it matters: an event
    inside the window can be taken silently, and one merely near it cannot.
    Asked for January storms in the United States, the register offers a
    February wildfire -- the only nearby record, and not remotely the thing
    that was asked about.
    """
    if not (start or end):
        return [e for e in events if _overlaps(e, _widen(start, end))], False
    exact = [e for e in events if _overlaps(e, (start or end, end or start))]
    if exact:
        return exact, True
    return [e for e in events if _overlaps(e, _widen(start, end))], False


def _situation(record: SessionRecord) -> str:
    """What the session already holds, for the extraction turn to read."""
    lines = [f"today: {date.today().isoformat()}"]
    if record.place.display:
        lines.append(f"session AOI: {record.place.display}")
    if line := record.window.line():
        lines.append(f"session time frame: {line}")
    if not record.place.display:
        lines.append("session AOI: none yet")

    pending = record.pending or {}
    for key, label in (("places", "place"), ("events", "event")):
        options = pending.get(key) or []
        if options:
            lines.append(f"awaiting a {label} choice:")
            lines.extend(f"  [{i}] {opt}" for i, opt in enumerate(options))
    return "\n".join(lines)


# Words that make a place name longer without naming a different place: "the
# Kandy area" and "around Kandy" are Kandy.
_GENERIC = {"the", "a", "an", "of", "in", "around", "near", "area", "areas",
            "aoi", "region", "surroundings", "surrounding", "vicinity", "city",
            "town", "target", "this", "that", "same", "whole", "entire"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - _GENERIC


def _names_held_place(record: SessionRecord, place_name: str) -> bool:
    """True when `place_name` is just the session's own AOI named again.

    The extraction is told to leave `place` empty for a follow-up and often
    does not: it copies the AOI into `place` and marks it `new` (see
    tests/test_session.py, the pronoun test). A user who accepts a suggested
    next step sends Argus' own wording -- "Briefing on what the Kandy area
    looked like before the flood" -- and that was read as a NEW place. The
    gazetteer was asked for "Kandy area", answered with a road in Kegalle
    District 30 km away, and the session's results and thesis path were
    cleared (user bug, 2026-11-17). Naming the place you are already in is
    not a change of place, so Python decides that here rather than trusting
    the reading.

    Compared on words, against the name the AOI was settled from, the first
    part of its display name and the window's event label -- each widened by
    the country and region, so "Kandy, Sri Lanka" matches too. Only the FIRST
    part of the display: "Colombo-Kandy Road, ..." must not make "Colombo"
    read as the place already held.
    """
    asked = _words(place_name)
    if not asked or not record.place.bbox:
        return False
    widen = _words(record.place.country) | _words(record.place.admin)
    held = [record.place.name, record.place.display.split(",")[0],
            re.sub(r"\(.*?\)", "", record.window.event or "")]
    # Every word of the held name is in the message's name, and nothing else
    # is but the country or region. One word of a longer name is not enough:
    # "Colombo" is not the "Colombo-Kandy Road".
    for name in held:
        core = _words(name) - widen
        if core and core <= asked <= (core | widen):
            return True
    return False


# -- the AOI lock --------------------------------------------------------
#
# Once a session holds an AOI and a window they change only when the USER
# asks, and says yes when asked to confirm (user rule, 2026-11-17). A change
# clears the thesis path and moves every stored result aside, so a wrong one
# costs the whole session -- and it happened from a suggested next step whose
# wording named the place. Four guards, all here and all in Python:
#
#   1. a place the message does not contain is not named (the reading copied
#      it from the session, or invented it);
#   2. dates the message does not contain are not stated;
#   3. the session's own place named again is no change (`_names_held_place`);
#   4. anything left is a PROPOSAL: the turn ends with a yes/no question and
#      nothing moves until the user answers yes.

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
           "oct", "nov", "dec")
_YES = re.compile(r"^\W*(yes|y|yeah|yep|ok|okay|sure|switch|confirm(ed)?|"
                  r"go ahead|do it|please do|change it)\b", re.IGNORECASE)
_NO = re.compile(r"^\W*(no|n|nope|keep|stay|don'?t|do not|cancel|never ?mind)\b",
                 re.IGNORECASE)


def _in_message(place_name: str, task: str) -> bool:
    """Every distinctive word of the place is in the user's own text."""
    asked = _words(place_name)
    return bool(asked) and asked <= _words(task)


def _has_date(task: str) -> bool:
    """The message writes a date: a digit, or a month name."""
    text = (task or "").lower()
    return bool(re.search(r"\d", text)) or any(
        re.search(rf"\b{m}[a-z]*\b", text) for m in _MONTHS)


def _when(start: str, end: str) -> str:
    def one(iso: str) -> str:
        d = _iso(iso)
        return f"{d.day} {d.strftime('%b %Y')}" if d else iso
    return f"{one(start)} to {one(end)}" if end and end != start else one(start)


def _propose(record: SessionRecord, place: Place | None, window: Window | None,
             task: str) -> dict:
    """End the turn asking whether to switch. Nothing on the record moves."""
    record.pending = {"switch": {
        "place": asdict(place) if place else None,
        "window": asdict(window) if window else None,
        "message": task,
    }}
    now_place = record.place.display or "no area yet"
    now_when = (_when(record.window.start, record.window.end)
                if record.window.start else "no time frame yet")
    changes = []
    if place:
        changes.append(f"- Area: **{now_place}** -> **{place.display}**")
    if window:
        changes.append(f"- Time frame: **{now_when}** -> "
                       f"**{_when(window.start, window.end)}**")
    keep = record.place.display.split(",")[0] or "the current area"
    return {
        "answer": (
            "Your message would change what this session is about:\n\n"
            + "\n".join(changes)
            + "\n\nSwitching starts the thesis path again for the new "
            "area and time frame. What has been fetched so far is kept "
            "aside, not deleted.\n\nReply **yes** to switch, or **no** to "
            f"stay on {keep}."),
        "asked": True,
    }


def _rebuild(data: dict | None, cls):
    if not isinstance(data, dict):
        return None
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    if isinstance(known.get("bbox"), list):
        known["bbox"] = tuple(known["bbox"])
    return cls(**known)


def _answer_switch(record: SessionRecord, task: str, lines: list[str]) -> dict | None:
    """The user's reply to a switch question, or None when there was none.

    Yes applies the proposal and hands back the message that prompted it, so
    the turn runs THAT request on the new AOI instead of the word "yes". No
    keeps everything and ends the turn. Anything else drops the question --
    the AOI stays -- and the message is read as a turn of its own.
    """
    switch = (record.pending or {}).get("switch")
    if not switch:
        return None
    record.pending = {}
    if _YES.match(task or ""):
        place, window = _rebuild(switch.get("place"), Place), _rebuild(switch.get("window"), Window)
        if place:
            record.place = place
        if window:
            record.window = window
        record.confirmed = bool(record.place.bbox and record.window.start)
        lines.append(log("argos", None, "aoi-switch",
                         f"user confirmed: {record.place.line()} | {record.window.line()}"))
        return {"answer": "", "asked": False, "log": lines, "announce": True,
                "about_event": False, "task": switch.get("message") or task}
    if _NO.match(task or ""):
        lines.append(log("argos", None, "aoi-kept", "user declined the switch"))
        return {"answer": f"Staying on **{record.place.display}**"
                          + (f", {_when(record.window.start, record.window.end)}"
                             if record.window.start else "")
                          + ". Nothing was changed.",
                "asked": True, "log": lines, "announce": False, "about_event": False}
    lines.append(log("argos", None, "aoi-kept",
                     "switch question not answered; AOI unchanged"))
    return None


def _rivals(candidates: list[dict]) -> list[dict]:
    """One candidate per distinct place, when the name covers more than one.

    Returns `[]` when the name is unambiguous -- every serious match is the
    same place, however many entries the gazetteer holds for it. Otherwise it
    returns the leader plus each genuine rival, best entry first, which is
    exactly the list the user is asked to choose from.
    """
    if not candidates:
        return []
    floor = candidates[0]["importance"] * RIVAL_SHARE
    seen: dict[tuple[str, str], dict] = {}
    for candidate in candidates:
        if candidate["importance"] < floor:
            continue
        # Same country and same region means the same place shown at a
        # different granularity -- a city, its centre, its municipality.
        seen.setdefault((candidate["country"], candidate["admin"]), candidate)
    return list(seen.values()) if len(seen) > 1 else []


def _place_from(candidate: dict) -> Place:
    return Place(
        name=candidate.get("query", ""),
        display=candidate.get("display", ""),
        bbox=tuple(candidate["bbox"]) if candidate.get("bbox") else None,
        country=candidate.get("country", ""),
        admin=candidate.get("admin", ""),
        source=places.OSM_SOURCE,
        retrieved=date.today().isoformat(),
    )


def _window_from(event: dict, fallback_end: str = "") -> Window:
    # The register's own name for an event is often just "Flood in India".
    # The hazard type and the alert level are what make it identifiable in a
    # header the user is asked to confirm.
    name = event.get("name", "")
    tags = [t for t in (event.get("kind"), event.get("severity")) if t]
    if tags:
        name = f"{name} ({', '.join(tags)})"
    return Window(
        start=event.get("start", ""),
        end=event.get("end") or fallback_end or "",
        event=name,
        glide=event.get("glide", ""),
        source=places.GDACS_SOURCE,
        retrieved=date.today().isoformat(),
    )


def _ask(record: SessionRecord, question: str, place_opts: list[str],
         event_opts: list[str]) -> dict:
    """End the turn with a question, and remember what was offered."""
    record.pending = {"places": place_opts, "events": event_opts}
    record.confirmed = False
    return {"answer": question, "asked": True}


def resolve(record: SessionRecord, task: str, extract,
            geocode=None, find_event=None) -> dict:
    """Settle the AOI and window for this turn.

    `extract(situation, message)` runs one model turn and returns its text. It
    is passed in rather than built here for two reasons: the instructions that
    go with it belong in `agents/argos/agents.md` like every other prompt in
    this repo, and the rules below are then testable without a model behind
    them.

    `geocode` and `find_event` default to the real lookups and exist for the
    same reason. Without them a test of these rules is a test of what the
    gazetteer and the disaster register happen to hold today: the near-miss
    case below passed while GDACS was unreachable and failed the moment it came
    back, which says nothing about the rule it was meant to pin.

    Returns `{"answer": str, "asked": bool, "log": [str], "announce": bool,
    "about_event": bool}`. `asked` means the turn is over: the answer is a
    question, and no analysis should run until it comes back. `about_event`
    means the message asks about the event itself, so the graph looks the
    event up in the disaster registers (core/event_records.py).

    **A missing place or window does not stop the turn** (since 2026-10-10).
    It used to: every first message without a place got "Before I can run
    anything I need to know where", which blocked questions that need no AOI
    at all. Now the turn continues with the constant marked "not set" in the
    session header, and an agent that needs it says so in its answer. Only a
    genuine ambiguity -- two Bostons, a name the gazetteer cannot find -- is
    still asked about, because there a wrong guess is invisible downstream.
    """
    geocode = geocode or places.geocode_place
    find_event = find_event or places.find_event

    lines: list[str] = []
    # A reply to "switch the AOI?" is settled before any model reads it: yes
    # and no are not language worth a model call, and a reading could turn
    # "yes, Colombo" into yet another proposal.
    switched = _answer_switch(record, task, lines)
    if switched is not None:
        return switched
    situation = _situation(record)
    parsed = _parse(extract(situation, task))
    if not parsed:
        # Told apart from an empty reading on purpose: "the model did not
        # answer the question I asked" and "this message names no place" lead
        # to the same next step and are not the same problem.
        lines.append(log("argos", None, "resolve-unreadable",
                         "the reading did not come back in the agreed shape"))
        # Read it again, once, but only when there is nothing to fall back on.
        #
        # With an AOI already held, an unreadable reading lands on `change =
        # none` and the session carries forward, which is the right answer
        # anyway. With no AOI, it lands on "no place named" -- and a message
        # that plainly said "Boston" comes back as "I need to know where",
        # which is what a user sees and cannot explain.
        if not record.place.display:
            parsed = _parse(extract(situation, task))
            lines.append(log("argos", None, "resolve-retry",
                             "read it again" + ("" if parsed else ", still unreadable")))
    change = (parsed.get("change") or "none").lower()
    about_event = parsed.get("about_event") is True
    place_name = (parsed.get("place") or "").strip()
    start, end = _iso(parsed.get("approx_start")), _iso(parsed.get("approx_end"))

    # The user answering a question we asked. Resolve against what was offered
    # rather than starting the lookup again, so "the Massachusetts one" lands
    # on the option it was a reply to.
    #
    # Answering one question does not end the turn: a session needs both an
    # AOI and a window, and picking the right Boston leaves the second half
    # still open. Both branches fall through to the blocks below, which then
    # settle whatever is still missing -- or ask about it.
    pending = record.pending or {}
    answered = False
    picked = parsed.get("chosen_place_index")
    if pending.get("places") and isinstance(picked, int):
        chosen = (pending.get("raw_places") or [])[picked : picked + 1]
        if chosen:
            record.place = _place_from(chosen[0])
            record.pending = {}
            answered = True
            lines.append(log("argos", None, "aoi-confirmed", record.place.display))

    picked = parsed.get("chosen_event_index")
    settled_window = False
    if pending.get("events") and isinstance(picked, int):
        chosen = (pending.get("raw_events") or [])[picked : picked + 1]
        if chosen:
            record.window = _window_from(chosen[0], parsed.get("approx_end", ""))
            record.pending = {}
            answered = settled_window = True
            lines.append(log("argos", None, "window-confirmed",
                             record.window.line()))

    # -- the AOI lock (see the note above `_in_message`) -------------------
    locked_place = bool(record.place.bbox)
    locked_window = bool(record.window.start)
    stated = _explicit_bbox(task)
    if not answered:
        # Guard 1: a place the user did not write is not a place they named.
        if (locked_place and place_name and not stated
                and not _in_message(place_name, task)):
            lines.append(log("argos", None, "aoi-not-in-message",
                             f"{place_name!r} is not in the message; ignored"))
            place_name = ""
        # Guard 2: nor are dates the message does not write.
        if locked_window and start and not settled_window and not _has_date(task):
            lines.append(log("argos", None, "window-not-in-message",
                             "dates read but none written; ignored"))
            start = end = None

    # Guard 3: the session's own AOI named again is not a new place.
    same_place = (bool(place_name) and not answered and not stated
                  and _names_held_place(record, place_name))
    if same_place:
        lines.append(log("argos", None, "aoi-same",
                         f"{place_name!r} is the session AOI; not looked up again"))

    # Guard 4: a real change to what the session holds is asked, never made.
    new_place = locked_place and not answered and (
        (stated is not None and tuple(stated) != tuple(record.place.bbox))
        or (change == "new" and bool(place_name) and not same_place and not stated))
    new_dates = (start.isoformat(), (end or start).isoformat()) if start else None
    new_window = (locked_window and not settled_window and new_dates is not None
                  and new_dates != (record.window.start, record.window.end))
    if (new_place or new_window) and pinned.is_pinned(record):
        # The pinned session's AOI and window are hardcoded (core/pinned.py,
        # user decision 2026-11-21): a change is not even offered.
        lines.append(log("argos", None, "aoi-pinned",
                         "the message would change the pinned AOI or window; refused"))
        return {"answer": pinned.refusal(), "asked": True, "log": lines,
                "announce": False, "about_event": about_event}
    if new_place or new_window:
        proposed = None
        if new_place and stated:
            proposed = Place(name=place_name or "user-stated AOI",
                             display=(f"{place_name} (user-stated bbox)" if place_name
                                      else "user-stated bbox"),
                             bbox=stated, country=record.place.country,
                             admin=record.place.admin, source="user-stated",
                             retrieved=date.today().isoformat())
        elif new_place:
            found = geocode(place_name)
            lines.append(log("argos", None, "geocode",
                             f"{place_name}: {len(found['candidates'])} candidate(s)"
                             + (f" -- {found['problem']}" if found["problem"] else "")))
            candidates = found["candidates"]
            if not candidates:
                return {"answer": f"I could not find **{place_name}** in the "
                                  "OpenStreetMap gazetteer, so this session stays on "
                                  f"**{record.place.display}**. To change the area, "
                                  "give a fuller name -- a district and a country -- "
                                  "or coordinates.",
                        "asked": True, "log": lines, "announce": False,
                        "about_event": about_event}
            rivals = _rivals(candidates)
            if rivals:
                # Picking one IS the user's explicit choice, so the pick is
                # applied directly (the `answered` branch above).
                shown = rivals[:5]
                for candidate in shown:
                    candidate["query"] = place_name
                options = [c["display"] for c in shown]
                listing = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(options))
                record.pending = {"places": options, "events": [], "raw_places": shown}
                lines.append(log("argos", None, "aoi-ambiguous",
                                 f"{len(options)} candidates for {place_name}"))
                return {"answer": f"**{place_name}** matches more than one place. "
                                  f"Which one should this session switch to?\n\n"
                                  f"{listing}\n\nOr say **stay** to keep "
                                  f"**{record.place.display}**.",
                        "asked": True, "log": lines, "announce": False,
                        "about_event": about_event}
            candidates[0]["query"] = place_name
            proposed = _place_from(candidates[0])
            if proposed.bbox and tuple(proposed.bbox) == tuple(record.place.bbox):
                proposed = None                 # the gazetteer's name for HERE
        window = None
        if new_dates and new_dates != (record.window.start, record.window.end):
            window = Window(start=new_dates[0], end=new_dates[1],
                            event=(place_name if proposed else "") or record.window.event
                            or record.place.name or "",
                            source="user-stated", retrieved=date.today().isoformat())
        if proposed or window:
            lines.append(log("argos", None, "aoi-switch-asked",
                             (f"place -> {proposed.display}; " if proposed else "")
                             + (f"window -> {window.start}..{window.end}" if window else "")))
            return {**_propose(record, proposed, window, task), "log": lines,
                    "announce": False, "about_event": about_event}

    if locked_place and locked_window:
        # Nothing new named and both constants already held: they carry forward
        # untouched. This is the common case after the first turn. A question
        # left unanswered is dropped, so it cannot be answered turns later.
        if not answered:
            record.pending = {}
        lines.append(log("argos", None, "session-carry",
                         f"reusing {record.place.display}"))
        return {"answer": "", "asked": False, "log": lines, "announce": False,
                "about_event": about_event}

    if change == "none" and record.place.display and record.window.start:
        # Nothing new named and both constants already held: they carry forward
        # untouched. This is the common case after the first turn.
        lines.append(log("argos", None, "session-carry",
                         f"reusing {record.place.display}"))
        return {"answer": "", "asked": False, "log": lines, "announce": False,
                "about_event": about_event}

    if not place_name and not record.place.display:
        # Not a question any more: the turn goes on without an AOI, and the
        # session header tells every agent it is not set.
        lines.append(log("argos", None, "aoi-missing",
                         "no place named and none held; continuing without one"))
        record.confirmed = False
        return {"answer": "", "asked": False, "log": lines, "announce": False,
                "about_event": about_event}

    if not place_name:
        place_name = record.place.display

    # -- an AOI the user stated outright -----------------------------------
    #
    # Checked before the gazetteer, and it ends the matter. A run over the
    # Kelani basin asked for bbox 79.867..80.217 and got Kelaniya the town --
    # 79.877..79.957, about a fifteenth of the area -- because the only way in
    # here was a place name and the gazetteer is what answers those. Stated
    # coordinates are not a hint to be improved on.
    stated = _explicit_bbox(task)
    if stated and not record.place.bbox:
        record.place = Place(
            name=place_name or "user-stated AOI",
            display=(f"{place_name} (user-stated bbox)" if place_name
                     else "user-stated bbox"),
            bbox=stated,
            country=record.place.country,
            admin=record.place.admin,
            source="user-stated",
            retrieved=date.today().isoformat(),
        )
        lines.append(log("argos", None, "aoi-stated", record.place.line()))

    # -- the AOI -----------------------------------------------------------
    #
    # `answered` guards this block, and it is the whole bug. Picking a GDACS
    # event means replying with its title -- "Flood in Bangladesh, India --
    # flood, 2019-11-09 to 2019-11-12" -- and the extraction reads a place out
    # of that title, because there is one in it. The reply then looked like a
    # message naming a new AOI, the block re-geocoded "Bangladesh, India", and
    # the Tirupati bounding box resolved a moment earlier was gone. Tagging the
    # event to the register destroyed the geometry that did not depend on it.
    #
    # A turn that answers a question we asked is not a turn that names a new
    # place. Nothing here should run on it.
    # A held AOI never reaches the gazetteer here: a change to it was proposed
    # above, and anything else is not a change.
    if (not answered and not stated and not locked_place
            and (change == "new" or not record.place.display)):
        found = geocode(place_name)
        lines.append(log("argos", None, "geocode",
                         f"{place_name}: {len(found['candidates'])} candidate(s)"
                         + (f" -- {found['problem']}" if found["problem"] else "")))
        candidates = found["candidates"]
        if not candidates:
            return {
                **_ask(record,
                       f"I could not find **{place_name}** in the OpenStreetMap "
                       "gazetteer"
                       + (f" ({found['problem']})." if found["problem"] else ".")
                       + " Try a fuller name -- a district and a country, or "
                         "coordinates.",
                       [], []),
                "log": lines,
                "announce": False,
                "about_event": about_event,
            }

        top = candidates[0]
        rivals = _rivals(candidates)
        if rivals:
            shown = rivals[:5]
            for candidate in shown:
                candidate["query"] = place_name
            options = [c["display"] for c in shown]
            listing = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(options))
            result = _ask(record,
                          f"**{place_name}** matches more than one place. "
                          f"Which one?\n\n{listing}\n\nI will take the bounding "
                          "box from whichever you name and keep it for the rest "
                          "of the session.",
                          options, [])
            record.pending["raw_places"] = shown
            lines.append(log("argos", None, "aoi-ambiguous",
                             f"{len(options)} candidates for {place_name}"))
            return {**result, "log": lines, "announce": False,
                    "about_event": about_event}

        top["query"] = place_name
        record.place = _place_from(top)
        lines.append(log("argos", None, "aoi", record.place.line()))

    # -- the window --------------------------------------------------------
    #
    # The user's dates ARE the window. The disaster register used to be
    # consulted here and is switched off (`places.GDACS_ENABLED`): the thesis
    # supplies its own event window, so a register was not corroborating it,
    # it was competing with it -- and winning. A run that asked for
    # 2025-11-24..12-01 came back anchored to Ditwah's own 11-27..11-29, and a
    # turn was spent asking which of two Sri Lankan entries was meant when the
    # dates had already been given.
    #
    # `settled_window` still guards this: a turn that answered a pending
    # question is not a turn that names a new window.
    if not settled_window and (change == "new" or not record.window.start):
        if start:
            record.window = Window(
                start=start.isoformat(),
                end=(end or start).isoformat(),
                event=parsed.get("place") or record.place.name or "",
                source="user-stated",
                retrieved=date.today().isoformat(),
            )
            lines.append(log("argos", None, "window-stated",
                             record.window.line()))
        else:
            # No dates: the turn goes on with the window marked "not set".
            lines.append(log("argos", None, "window-missing",
                             f"no dates for {record.place.display}; "
                             "continuing without a window"))

    record.pending = {}
    # Both halves came from the user, and there is no longer a register that
    # could disagree with either. `confirmed` now means "settled", not
    # "corroborated by a third party" -- the distinction mattered when GDACS
    # was consulted and does not now.
    record.confirmed = bool(record.place.bbox and record.window.start)
    # Announced when an AOI is held, so the answer opens by naming it -- and,
    # when the window is missing, by saying so.
    return {"answer": "", "asked": False, "log": lines,
            "announce": bool(record.place.display), "about_event": about_event}
