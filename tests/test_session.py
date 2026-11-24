"""The session record, and the rules that settle an AOI and a time frame.

    python tests/test_session.py            # rules only, no network
    python tests/test_session.py --live     # also hit Nominatim and GDACS

The resolver takes its model turn as a callable, so every rule below is tested
against a scripted extraction rather than a live one: which candidate wins,
when the turn stops to ask, and what carries forward to the next message are
decisions in Python, and this is what holds them still.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import places, resolve, session                     # noqa: E402
from core.session import (Demographics, Place, SessionRecord,  # noqa: E402
                          Window, approx_tokens)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def reachable(name: str, result: dict) -> bool:
    """A live check that tells an outage apart from a defect.

    A public register that is down is news, not a failing test -- nothing in
    this repo changed. It is reported, loudly, and not counted as a pass.
    """
    if result["ok"]:
        check(name, True)
        return True
    results.append((SKIP, name, result["problem"]))
    print(f"[{SKIP}] {name} -- unreachable right now: {result['problem']}")
    return False


def scripted(**fields):
    """An extraction that always returns the same reading of the message."""
    payload = {"place": "", "approx_start": "", "approx_end": "",
               "change": "none", "chosen_place_index": None,
               "chosen_event_index": None, **fields}
    return lambda situation, message: json.dumps(payload)


def lookups(candidates=None, events=None):
    """Stand-ins for the gazetteer and the disaster register.

    Every rules test uses these. Calling the real ones makes the test a
    statement about what OpenStreetMap and GDACS hold today: the near-miss
    check below passed while GDACS was unreachable and failed the moment it
    came back, which says nothing at all about the rule it pins.
    """
    def geocode(query, limit=5):
        return {"ok": bool(candidates), "candidates": list(candidates or []),
                "source": places.OSM_SOURCE,
                "problem": "" if candidates else f"no place matched {query!r}"}

    def find_event(country, start, end, kinds=""):
        return {"ok": bool(events), "events": list(events or []),
                "source": places.GDACS_SOURCE,
                "problem": "" if events else "no record"}

    return geocode, find_event


def filled(**over) -> SessionRecord:
    record = SessionRecord(session_id="test")
    record.place = Place(display="Tirupati, Andhra Pradesh, India",
                         bbox=(79.35, 13.58, 79.50, 13.70), country="India",
                         admin="Andhra Pradesh", source="osm",
                         retrieved="2026-09-15")
    record.window = Window(start="2021-11-14", end="2021-11-18",
                           event="Flood in India (flood, Orange)",
                           source=places.GDACS_SOURCE, retrieved="2026-09-15")
    record.confirmed = True
    for key, value in over.items():
        setattr(record, key, value)
    return record


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------
def test_budget() -> None:
    record = filled()
    record.demographics = Demographics(population=374260, density_per_km2=5400,
                                       source="Census of India", vintage="2011")
    record.flows_run = ["citadel:human_health_risk"]
    record.digest = "Citadel ran the sewage flow. " * 4

    header, detail = record.header(), record.detail()
    check("header carries the AOI and the window",
          "Tirupati" in header and "2021-11-14" in header)
    check("header carries the bounding box", "79.3500" in header)
    check("header stays under its budget",
          approx_tokens(header) <= session.HEADER_BUDGET,
          f"{approx_tokens(header)} tokens")
    check("detail stays under the 1000-token budget",
          approx_tokens(detail) <= session.TOKEN_BUDGET,
          f"{approx_tokens(detail)} tokens")
    check("demographics are in the detail, not the header",
          "374,260" in detail and "374,260" not in header)
    check("the vintage travels with the population", "2011" in detail)


def test_trim() -> None:
    record = filled(digest="x" * 8000)
    record.trim()
    check("an oversized digest is trimmed back under budget",
          approx_tokens(record.detail()) <= session.TOKEN_BUDGET,
          f"{approx_tokens(record.detail())} tokens")
    check("the trim is marked rather than silent",
          "[digest trimmed to fit]" in record.digest)


def test_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session.SESSIONS_DIR = Path(tmp)
        original = filled(digest="Sewage risk is high in the northern wards.")
        session.save(original)
        back = session.load("test")
    check("a saved record loads back with its bounding box",
          back.place.bbox == original.place.bbox, str(back.place.bbox))
    check("a saved record loads back with its digest",
          back.digest == original.digest)
    check("a bbox survives JSON as a tuple, not a list",
          isinstance(back.place.bbox, tuple))


def test_unknown_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session.SESSIONS_DIR = Path(tmp)
        fresh = session.load("never-seen")
        (Path(tmp) / "broken.json").write_text("{not json", encoding="utf-8")
        salvaged = session.load("broken")
    check("an unknown session id yields an empty record",
          fresh.place.display == "" and fresh.turns == 0)
    check("a corrupt file yields an empty record rather than an exception",
          salvaged.session_id == "broken")


def test_path_confinement() -> None:
    # One folder per session, directly under SESSIONS_DIR, whatever the id.
    path = session._path("../../etc/passwd")
    check("a session id cannot name a path of its own",
          path.parent.parent == session.SESSIONS_DIR and path.name == "session.json",
          str(path))


# ---------------------------------------------------------------------------
# Ambiguity: which candidate wins, and when to ask instead
# ---------------------------------------------------------------------------
def test_rivals() -> None:
    boston = [
        {"display": "Boston, Massachusetts, United States", "importance": 0.773,
         "country": "United States", "admin": "Massachusetts"},
        {"display": "Boston, Lincolnshire, United Kingdom", "importance": 0.536,
         "country": "United Kingdom", "admin": "England"},
    ]
    tirupati = [
        {"display": "Tirupati (Urban), Andhra Pradesh", "importance": 0.493,
         "country": "India", "admin": "Andhra Pradesh"},
        {"display": "Tirupati, Nethaji Road, Andhra Pradesh", "importance": 0.437,
         "country": "India", "admin": "Andhra Pradesh"},
        {"display": "Tirupati, Andhra Pradesh, India", "importance": 0.415,
         "country": "India", "admin": "Andhra Pradesh"},
    ]
    faint = [
        {"display": "Big Place", "importance": 0.90,
         "country": "A", "admin": "X"},
        {"display": "Tiny hamlet of the same name", "importance": 0.10,
         "country": "B", "admin": "Y"},
    ]
    check("two countries with comparable rank is an ambiguity",
          len(resolve._rivals(boston)) == 2)
    check("one city at three granularities is not",
          resolve._rivals(tirupati) == [])
    check("a faint namesake elsewhere does not force a question",
          resolve._rivals(faint) == [])
    check("no candidates is not an ambiguity", resolve._rivals([]) == [])


def test_event_tiers() -> None:
    events = [
        {"name": "Flood in India", "start": "2021-11-14", "end": "2021-11-18"},
        {"name": "Flood in India", "start": "2021-10-12", "end": "2021-10-19"},
    ]
    november, nov_inside = resolve._candidates(
        events, date(2021, 11, 1), date(2021, 11, 30))
    october, oct_inside = resolve._candidates(
        events, date(2021, 10, 15), date(2021, 10, 16))
    september, sep_inside = resolve._candidates(
        events, date(2021, 9, 20), date(2021, 9, 25))
    check("a window picks the event inside it, not the one merely near it",
          len(november) == 1 and november[0]["start"] == "2021-11-14" and nov_inside,
          f"{len(november)} candidate(s)")
    check("a window inside an event still matches it",
          len(october) == 1 and october[0]["start"] == "2021-10-12" and oct_inside)
    check("a window with no match falls back to nearby events, flagged as such",
          len(september) == 1 and september[0]["start"] == "2021-10-12"
          and sep_inside is False,
          f"{len(september)} candidate(s), inside={sep_inside}")


def test_spanning_event() -> None:
    # An event that began before the window and was still running through it.
    monsoon = [{"name": "Flood in India", "start": "2021-07-24",
                "end": "2021-10-01"}]
    found, inside = resolve._candidates(monsoon, date(2021, 9, 1),
                                        date(2021, 9, 30))
    check("an event spanning the window is matched on overlap, not onset",
          len(found) == 1 and inside)


def test_user_dates_are_the_window() -> None:
    """The disaster register is off. The dates the user gave are the window.

    This replaces a near-miss check that pinned GDACS behaviour: asked for
    Boston in January 2024 the register offered a February wildfire, and the
    resolver stopped to ask rather than taking it. That whole tier is gone --
    `places.GDACS_ENABLED` is False -- because the thesis supplies its own
    window and a register was not corroborating it but competing with it.
    """
    record = SessionRecord(session_id="dates")
    record.place = Place(display="Boston, Massachusetts, United States",
                         country="United States", bbox=(-71.2, 42.2, -70.8, 42.4))
    boston = [{"display": "Boston, Massachusetts, United States",
               "bbox": (-71.2, 42.2, -70.8, 42.4), "country": "United States",
               "admin": "Massachusetts", "importance": 0.77}]
    geocode, find_event = lookups(boston)
    outcome = resolve.resolve(
        record, "the January 2024 storms",
        scripted(place="Boston", change="new", approx_start="2024-01-01",
                 approx_end="2024-01-31"),
        geocode, find_event)

    check("the stated dates become the window, with no question asked",
          not outcome["asked"] and record.window.start == "2024-01-01"
          and record.window.end == "2024-01-31",
          f"asked={outcome['asked']} {record.window.start}..{record.window.end}")
    check("the window is sourced to the user, not to a register",
          record.window.source == "user-stated", record.window.source)
    check("a window and an AOI together count as settled",
          record.confirmed is True)


def test_the_register_is_not_consulted() -> None:
    """Switched off means not called, not called-and-ignored."""
    calls = {"n": 0}

    def find_event(country, start, end, kinds=""):
        calls["n"] += 1
        return {"ok": True, "events": [{"name": "Some flood", "kind": "flood",
                                        "start": "2024-01-05",
                                        "end": "2024-01-09", "glide": "",
                                        "severity": "Red"}],
                "source": places.GDACS_SOURCE, "problem": ""}

    record = SessionRecord(session_id="off")
    record.place = Place(display="Boston", country="United States",
                         bbox=(-71.2, 42.2, -70.8, 42.4))
    geocode, _ = lookups()
    resolve.resolve(record, "January 2024",
                    scripted(change="new", approx_start="2024-01-01",
                             approx_end="2024-01-31"),
                    geocode, find_event)
    check("the register is never called", calls["n"] == 0, f"{calls['n']} call(s)")
    check("and an event it would have offered does not reach the window",
          "Some flood" not in record.window.event, record.window.event)

    # The provider itself reports disabled rather than empty. The two lead to
    # different behaviour and must not look alike.
    direct = places.find_event("United States", "2024-01-01", "2024-01-31")
    check("places.find_event reports itself disabled",
          direct.get("disabled") is True and not direct["events"],
          direct.get("problem", "")[:60])


def test_a_stated_bbox_beats_the_gazetteer() -> None:
    """Coordinates in the message are the AOI. The gazetteer is not asked.

    The Kelani run asked for bbox 79.867..80.217 and got Kelaniya the town,
    79.877..79.957 -- about a fifteenth of the area -- because a place name was
    the only way in and the gazetteer is what answers those.
    """
    record = SessionRecord(session_id="bbox")
    called = {"n": 0}

    def geocode(query, limit=5):
        called["n"] += 1
        return {"ok": True, "source": places.OSM_SOURCE, "problem": "",
                "candidates": [{"display": "Kelaniya, Sri Lanka",
                                "bbox": [79.8773, 6.9101, 79.9573, 6.9901],
                                "lat": 6.95, "lon": 79.92, "country": "Sri Lanka",
                                "admin": "Western", "kind": "town",
                                "importance": 0.5}]}

    _, find_event = lookups()
    outcome = resolve.resolve(
        record,
        "Run the weather grid for Kelaniya, Sri Lanka with bbox "
        "[79.867, 6.783, 80.217, 7.083] over 2025-11-24 to 2025-12-01",
        scripted(place="Kelaniya, Sri Lanka", change="new",
                 approx_start="2025-11-24", approx_end="2025-12-01"),
        geocode, find_event)

    check("the stated bbox is the one that lands in the record",
          record.place.bbox == (79.867, 6.783, 80.217, 7.083),
          str(record.place.bbox))
    check("the gazetteer is not consulted at all",
          called["n"] == 0, f"{called['n']} call(s)")
    check("and it is sourced to the user",
          record.place.source == "user-stated", record.place.source)
    check("the turn proceeds rather than asking anything",
          not outcome["asked"])


def test_a_malformed_bbox_is_refused_not_repaired() -> None:
    """Four numbers in the wrong order are rejected, never reordered."""
    for bad in ("bbox [80.217, 6.783, 79.867, 7.083]",     # east < west
                "bbox [79.867, 7.083, 80.217, 6.783]",     # north < south
                "bbox [200.0, 6.783, 210.0, 7.083]"):      # off the planet
        check(f"refused: {bad[5:30]}",
              resolve._explicit_bbox(bad) is None)
    check("a well-formed one is read",
          resolve._explicit_bbox("bbox: 79.867, 6.783, 80.217, 7.083")
          == (79.867, 6.783, 80.217, 7.083))
    check("and so is the bare-space spelling",
          resolve._explicit_bbox("bbox 79.867 6.783 80.217 7.083")
          == (79.867, 6.783, 80.217, 7.083))


# ---------------------------------------------------------------------------
# The resolver: what carries forward, and when the turn stops to ask
# ---------------------------------------------------------------------------
def test_carry_forward() -> None:
    record = filled()
    geocode, find_event = lookups()
    outcome = resolve.resolve(record, "now check the power outages",
                              scripted(change="none"), geocode, find_event)
    check("a follow-up question inherits the AOI instead of re-deriving it",
          not outcome["asked"] and record.place.display.startswith("Tirupati"))
    check("carrying forward is recorded in the log",
          any("session-carry" in line for line in outcome["log"]))
    check("nothing is announced when nothing changed",
          outcome["announce"] is False)


def test_no_aoi_at_all() -> None:
    """No place named and none held: the turn goes on (since 2026-10-10).

    It used to stop with "Before I can run anything I need to know where",
    which blocked every question that needs no AOI. The header now carries
    "NOT SET" instead, so no agent invents one.
    """
    record = SessionRecord(session_id="empty")
    geocode, find_event = lookups()
    outcome = resolve.resolve(record, "what is the sewage risk",
                              scripted(change="none"), geocode, find_event)
    check("a first message with no place does not stop the turn",
          not outcome["asked"] and outcome["answer"] == "", outcome["answer"][:60])
    check("the old where-question is gone",
          "need to know where" not in outcome["answer"])
    check("the record stays unconfirmed", record.confirmed is False)
    header = record.header()
    check("the header says the AOI and the window are NOT SET",
          "AOI: NOT SET" in header and "Time frame: NOT SET" in header, header)
    check("the header stays under its budget with nothing set",
          approx_tokens(header) <= session.HEADER_BUDGET,
          f"{approx_tokens(header)} tokens")


def test_missing_window_continues() -> None:
    """A place with no date: the AOI is settled and the turn goes on."""
    record = SessionRecord(session_id="w")
    record.place = Place(display="Boston, Massachusetts, United States",
                         country="United States", bbox=(-71.2, 42.2, -70.8, 42.4))
    geocode, find_event = lookups()
    outcome = resolve.resolve(record, "sewage risk in Boston",
                              scripted(place="Boston", change="same"),
                              geocode, find_event)
    check("a settled AOI with no window does not stop the turn",
          not outcome["asked"], outcome["answer"][:60])
    check("the window is logged as missing",
          any("window-missing" in line for line in outcome["log"]))
    check("the AOI is still announced", outcome["announce"] is True)
    check("the header keeps the AOI and marks the window NOT SET",
          "Boston" in record.header() and "Time frame: NOT SET" in record.header())


def test_about_event_is_read() -> None:
    """`about_event` passes through, and only a literal true counts."""
    geocode, find_event = lookups()
    outcome = resolve.resolve(filled(), "was this flood mapped by Copernicus?",
                              scripted(change="none", about_event=True),
                              geocode, find_event)
    check("a message about the event is flagged", outcome["about_event"] is True)
    outcome = resolve.resolve(filled(), "what is the sewage risk there",
                              scripted(change="none", about_event=False),
                              geocode, find_event)
    check("an analysis question is not", outcome["about_event"] is False)
    outcome = resolve.resolve(filled(), "what happened",
                              scripted(change="none", about_event="yes"),
                              geocode, find_event)
    check("a non-boolean reading is not taken as true",
          outcome["about_event"] is False)
    outcome = resolve.resolve(SessionRecord(session_id="e"), "what happened?",
                              scripted(change="none", about_event=True),
                              geocode, find_event)
    check("the flag survives a turn with no AOI", outcome["about_event"] is True)


def test_choice_is_remembered() -> None:
    record = SessionRecord(session_id="pick")
    record.pending = {
        "places": ["Boston, Massachusetts", "Boston, Lincolnshire"],
        "raw_places": [
            {"display": "Boston, Massachusetts, United States",
             "bbox": [-71.19, 42.22, -70.80, 42.39], "country": "United States",
             "admin": "Massachusetts"},
            {"display": "Boston, Lincolnshire, United Kingdom",
             "bbox": [-0.06, 52.95, 0.00, 53.00], "country": "United Kingdom",
             "admin": "England"},
        ],
    }
    geocode, find_event = lookups()
    outcome = resolve.resolve(record, "the Lincolnshire one",
                              scripted(change="same", chosen_place_index=1),
                              geocode, find_event)
    check("answering by description lands on the option it answered",
          record.place.display.startswith("Boston, Lincolnshire"),
          record.place.display)
    check("answering the AOI question goes straight on -- a missing window "
          "is no longer asked for",
          not outcome["asked"], outcome["answer"][:60])
    check("the pending question is cleared", record.pending == {})
    check("the chosen AOI is the one the session holds",
          "Boston, Lincolnshire" in record.header())


def _answering_an_event_question():
    """A record mid-conversation: AOI settled, two events offered, one picked.

    This is the exact state the live run was in at 23:53 when it began asking
    the same question forever.
    """
    record = SessionRecord(session_id="evt")
    record.place = Place(display="Shillong, East Khasi Hills, Meghalaya, India",
                         bbox=(91.72, 25.41, 92.04, 25.73), country="India",
                         admin="Meghalaya", source=places.OSM_SOURCE,
                         retrieved="2026-09-18")
    record.pending = {
        "events": ["Flood in India, Pakistan -- flood, 2023-06-14 to 2023-06-16",
                   "Tropical Cyclone BIPARJOY-23 -- tropical cyclone, "
                   "2023-06-06 to 2023-06-15"],
        "raw_events": [
            {"name": "Flood in India, Pakistan", "kind": "flood",
             "start": "2023-06-14", "end": "2023-06-16",
             "glide": "FL-2023-000104-IND", "severity": "Orange",
             "country": "India, Pakistan"},
            {"name": "Tropical Cyclone BIPARJOY-23", "kind": "tropical cyclone",
             "start": "2023-06-06", "end": "2023-06-15", "glide": "",
             "severity": "Orange", "country": "India, Pakistan"},
        ],
    }
    return record


def test_answering_an_event_question_ends_it() -> None:
    """Picking an event settles the window. It must not re-ask.

    The reply is the event's own title, so it carries dates and the reading
    comes back `change="new"` -- which sent the turn straight back into the
    search that produced the question, to find the same candidates and put the
    same question again.
    """
    record = _answering_an_event_question()
    asked_again = {"n": 0}

    def find_event(country, start, end, kinds=""):
        asked_again["n"] += 1
        return {"ok": True, "events": list(record.pending["raw_events"]),
                "source": places.GDACS_SOURCE, "problem": ""}

    geocode, _ = lookups()
    outcome = resolve.resolve(
        record,
        "Tropical Cyclone BIPARJOY-23 -- tropical cyclone, 2023-06-06 to 2023-06-15",
        scripted(change="new", place="India, Pakistan",
                 approx_start="2023-06-06", approx_end="2023-06-15",
                 chosen_event_index=1),
        geocode, find_event)

    check("picking an event settles the window",
          record.window.start == "2023-06-06"
          and "BIPARJOY" in record.window.event,
          record.window.line())
    check("the register is not searched again on the turn that answered it",
          asked_again["n"] == 0, f"searched {asked_again['n']}x")
    check("the turn does not end by asking the same question again",
          not outcome["asked"], outcome["answer"][:70])


def test_event_title_does_not_clobber_the_aoi() -> None:
    """The place read out of an event title must not replace the AOI.

    "Flood in Bangladesh, India" contains a place name, and the extraction
    duly returns one. Re-geocoding it threw away a bounding box that had
    nothing to do with the event -- the geometry of a city does not depend on
    which disaster is being tagged to it.
    """
    record = _answering_an_event_question()
    before = record.place.display
    regeocoded = {"n": 0}

    def geocode(query, limit=5):
        regeocoded["n"] += 1
        return {"ok": True, "source": places.OSM_SOURCE, "problem": "",
                "candidates": [
                    {"display": "Bangladesh", "bbox": [88.0, 20.7, 92.7, 26.6],
                     "lat": 23.7, "lon": 90.4, "country": "Bangladesh",
                     "admin": "", "kind": "country", "importance": 0.9},
                    {"display": "India", "bbox": [68.1, 6.7, 97.4, 35.5],
                     "lat": 22.4, "lon": 78.7, "country": "India",
                     "admin": "", "kind": "country", "importance": 0.88},
                ]}

    _, find_event = lookups()
    resolve.resolve(
        record,
        "Flood in Bangladesh, India -- flood, 2019-11-09 to 2019-11-12",
        scripted(change="new", place="Bangladesh, India",
                 approx_start="2019-11-09", approx_end="2019-11-12",
                 chosen_event_index=0),
        geocode, find_event)

    check("the gazetteer is not called on a turn that answered an event question",
          regeocoded["n"] == 0, f"geocoded {regeocoded['n']}x")
    check("the AOI survives the event being tagged to it",
          record.place.display == before, record.place.display)
    check("and so does its bounding box",
          record.place.bbox == (91.72, 25.41, 92.04, 25.73),
          str(record.place.bbox))


def test_stale_question_not_reanswered() -> None:
    """A record with nothing pending ignores a stray index."""
    record = filled()
    geocode, find_event = lookups()
    resolve.resolve(record, "and the power outages?",
                    scripted(change="none", chosen_place_index=1),
                    geocode, find_event)
    check("an index with no question behind it changes nothing",
          record.place.display.startswith("Tirupati"))


def test_resolver_survives_a_bad_reading() -> None:
    record = filled()
    geocode, find_event = lookups()
    outcome = resolve.resolve(record, "anything",
                              lambda situation, message: "not json at all",
                              geocode, find_event)
    check("an unparseable extraction carries the session forward",
          not outcome["asked"] and record.place.display.startswith("Tirupati"))


def test_unreadable_reading_is_retried_only_when_it_matters() -> None:
    """An unreadable extraction is not the same as "no place was named".

    With an AOI held it lands on `change = none` and the session carries
    forward, which is right anyway. With none held it lands on "no place
    named" -- and a message that plainly said "Boston" came back as "I need to
    know where", which a user sees and cannot explain. So it is read again,
    once, and only in that case.
    """
    calls: list[str] = []

    def unreadable(situation, message):
        calls.append(message)
        return "not json at all"

    geocode, find_event = lookups()

    empty = SessionRecord(session_id="retry-empty")
    outcome = resolve.resolve(empty, "sewage risk in Boston", unreadable,
                              geocode, find_event)
    check("an unreadable reading with no AOI held is read again",
          len(calls) == 2, f"{len(calls)} extraction call(s)")
    check("and the retry is in the log, not silent",
          any("resolve-retry" in line for line in outcome["log"]))

    calls.clear()
    held = filled()
    resolve.resolve(held, "and the power outages there?", unreadable,
                    geocode, find_event)
    check("an unreadable reading with an AOI held is not retried",
          len(calls) == 1, f"{len(calls)} extraction call(s)")
    check("because carrying the session forward is already the right answer",
          held.place.display.startswith("Tirupati"))


def test_a_pronoun_does_not_move_the_aoi() -> None:
    """The reading the prompt now spells out, and what it has to produce.

    All three benchmarked models got this wrong in one of two ways: marking a
    pronoun follow-up as `new`, or copying the session's own AOI into `place`.
    Either one re-runs a lookup that was settled turns ago.
    """
    geocode, find_event = lookups()
    record = filled()
    outcome = resolve.resolve(record, "and what about the power outages there?",
                              scripted(change="none", place=""),
                              geocode, find_event)
    check("a pronoun follow-up carries the AOI forward untouched",
          not outcome["asked"] and record.place.display.startswith("Tirupati"))
    check("and costs no lookup at all",
          any("session-carry" in line for line in outcome["log"]))


def test_naming_the_held_aoi_again_is_not_new() -> None:
    """A suggestion accepted from the chip names the AOI in Argus' words.

    User bug 2026-11-17: "Briefing on what the Kandy area looked like before
    the flood" was read as place "Kandy area", change new; the gazetteer gave
    a road in Kegalle District and the session's whole thesis path was reset.
    """
    def kandy() -> SessionRecord:
        record = filled()
        record.place = Place(name="Kandy, Sri Lanka",
                             display="Kandy, Central Province, Sri Lanka",
                             bbox=(80.5772, 7.2545, 80.6312, 7.3),
                             country="Sri Lanka", admin="Central Province",
                             source="osm", retrieved="2026-11-17")
        record.window = Window(start="2025-11-26", end="2025-12-04",
                               event="Kandy, Sri Lanka", source="user-stated",
                               retrieved="2026-11-17")
        return record

    road = [{"display": "Colombo-Kandy Road, Ambepussa, Kegalle District, Sri Lanka",
             "bbox": [80.2114, 7.2307, 80.3321, 7.2518], "importance": 0.4,
             "country": "Sri Lanka", "admin": "Sabaragamuwa Province"}]
    asked: list[str] = []
    geocode, find_event = lookups(road)

    def counting(query, limit=5):
        asked.append(query)
        return geocode(query, limit)

    for name in ("Kandy area", "the Kandy AOI", "Kandy, Sri Lanka", "around Kandy"):
        record = kandy()
        resolve.resolve(record, f"Briefing on what the {name} looked like before the flood",
                        scripted(change="new", place=name), counting, find_event)
        check(f"{name!r} keeps the session AOI and window",
              record.place.bbox == (80.5772, 7.2545, 80.6312, 7.3)
              and record.window.start == "2025-11-26")
    check("and the gazetteer is never asked", not asked)

    record = kandy()
    outcome = resolve.resolve(record, "Kandy in June 2024",
                              scripted(change="new", place="Kandy",
                                       approx_start="2024-06-01", approx_end="2024-06-10"),
                              counting, find_event)
    check("the same place with new dates asks before moving the window",
          outcome["asked"] and record.window.start == "2025-11-26"
          and record.pending["switch"]["window"]["start"] == "2024-06-01"
          and record.pending["switch"]["place"] is None and not asked)

    record = kandy()
    resolve.resolve(record, "now do Colombo",
                    scripted(change="new", place="Colombo"), counting, find_event)
    check("a genuinely different place is still looked up", asked == ["Colombo"])

    record = kandy()
    record.place.display = "Colombo-Kandy Road, Ambepussa, Sri Lanka"
    record.place.name = "Colombo-Kandy Road"
    asked.clear()
    resolve.resolve(record, "now do Colombo",
                    scripted(change="new", place="Colombo"), counting, find_event)
    check("a word inside a road name is not the held place", asked == ["Colombo"])


def _kandy() -> SessionRecord:
    record = filled()
    record.place = Place(name="Kandy, Sri Lanka", display="Kandy, Central Province, Sri Lanka",
                         bbox=(80.5772, 7.2545, 80.6312, 7.3), country="Sri Lanka",
                         admin="Central Province", source="osm", retrieved="2026-11-17")
    record.window = Window(start="2025-11-26", end="2025-12-04", event="Kandy, Sri Lanka",
                           source="user-stated", retrieved="2026-11-17")
    return record


COLOMBO = [{"display": "Colombo, Western Province, Sri Lanka",
            "bbox": [79.82, 6.86, 79.92, 6.98], "importance": 0.7,
            "country": "Sri Lanka", "admin": "Western Province"}]
KANDY_BOX = (80.5772, 7.2545, 80.6312, 7.3)


def test_the_aoi_is_locked_until_the_user_says_yes() -> None:
    """A held AOI and window change only on the user's explicit yes.

    User rule 2026-11-17: the place must not change "because of anything"
    until the user explicitly asks. A change resets the thesis path.
    """
    geocode, find_event = lookups(COLOMBO)

    record = _kandy()
    outcome = resolve.resolve(record, "Now look at Colombo",
                              scripted(change="new", place="Colombo"), geocode, find_event)
    check("a new place is asked about, not taken",
          outcome["asked"] and record.place.bbox == KANDY_BOX
          and "yes" in outcome["answer"] and "Colombo" in outcome["answer"])
    check("and the proposal waits on the record",
          record.pending["switch"]["place"]["display"].startswith("Colombo"))

    never = lambda situation, message: (_ for _ in ()).throw(AssertionError("read"))
    outcome = resolve.resolve(record, "yes", never, geocode, find_event)
    check("yes switches -- without a model reading the reply",
          not outcome["asked"] and record.place.bbox == (79.82, 6.86, 79.92, 6.98)
          and record.window.start == "2025-11-26")
    check("and hands back the request that prompted the question",
          outcome["task"] == "Now look at Colombo")
    check("and the question is cleared", not record.pending)

    record = _kandy()
    resolve.resolve(record, "Now look at Colombo",
                    scripted(change="new", place="Colombo"), geocode, find_event)
    outcome = resolve.resolve(record, "no, keep Kandy", never, geocode, find_event)
    check("no keeps the AOI and ends the turn saying so",
          outcome["asked"] and record.place.bbox == KANDY_BOX
          and "Nothing was changed" in outcome["answer"] and not record.pending)

    record = _kandy()
    resolve.resolve(record, "Now look at Colombo",
                    scripted(change="new", place="Colombo"), geocode, find_event)
    outcome = resolve.resolve(record, "what was the peak rainfall?",
                              scripted(change="none"), geocode, find_event)
    check("an unrelated reply drops the question and keeps the AOI",
          not outcome["asked"] and record.place.bbox == KANDY_BOX and not record.pending)


def test_a_place_the_user_did_not_write_is_ignored() -> None:
    calls: list[str] = []
    geocode, find_event = lookups(COLOMBO)

    def counting(query, limit=5):
        calls.append(query)
        return geocode(query, limit)

    record = _kandy()
    outcome = resolve.resolve(record, "Write the pre-flood brief from the land cover",
                              scripted(change="new", place="Colombo"), counting, find_event)
    check("a place read but not written changes nothing and asks nothing",
          not outcome["asked"] and record.place.bbox == KANDY_BOX and not calls)
    check("and the log says why",
          any("aoi-not-in-message" in line for line in outcome["log"]))

    record = _kandy()
    outcome = resolve.resolve(record, "What did the area look like before the flood?",
                              scripted(change="new", approx_start="2025-10-01",
                                       approx_end="2025-10-31"), counting, find_event)
    check("dates read from a message with none in it are ignored",
          not outcome["asked"] and record.window.start == "2025-11-26")


def test_a_stated_bbox_on_a_held_aoi_is_asked() -> None:
    geocode, find_event = lookups()
    record = _kandy()
    outcome = resolve.resolve(record, "use bbox 80.0, 7.0, 80.2, 7.2",
                              scripted(change="new"), geocode, find_event)
    check("coordinates for a different box are proposed, not applied",
          outcome["asked"] and record.place.bbox == KANDY_BOX
          and tuple(record.pending["switch"]["place"]["bbox"]) == (80.0, 7.0, 80.2, 7.2))
    outcome = resolve.resolve(record, "Yes", scripted(), geocode, find_event)
    check("and applied on yes", record.place.bbox == (80.0, 7.0, 80.2, 7.2))


def test_a_first_aoi_needs_no_confirmation() -> None:
    geocode, find_event = lookups(COLOMBO)
    record = SessionRecord(session_id="fresh")
    outcome = resolve.resolve(record, "Floods in Colombo, 26 Nov to 4 Dec 2025",
                              scripted(change="new", place="Colombo",
                                       approx_start="2025-11-26", approx_end="2025-12-04"),
                              geocode, find_event)
    check("an empty session takes its first AOI and window straight away",
          not outcome["asked"] and record.place.bbox == (79.82, 6.86, 79.92, 6.98)
          and record.window.start == "2025-11-26")


# ---------------------------------------------------------------------------
# Live lookups
# ---------------------------------------------------------------------------
def test_live() -> None:
    found = places.geocode_place("Boston")
    if reachable("Nominatim answers with candidates", found):
        check("a candidate carries a bounding box",
              found["candidates"][0]["bbox"] is not None)
        check("Boston is reported as ambiguous",
              len(resolve._rivals(found["candidates"])) > 1,
              f"{len(resolve._rivals(found['candidates']))} distinct places")

    events = places.find_event("India", "2021-10-01", "2021-12-31")
    if reachable("GDACS answers with events", events):
        event = events["events"][0]
        check("an event carries a start and an end",
              bool(event["start"] and event["end"]),
              f"{event['start']} to {event['end']}")


def main() -> int:
    live = "--live" in sys.argv
    test_budget()
    test_trim()
    test_round_trip()
    test_unknown_session()
    test_path_confinement()
    test_rivals()
    test_event_tiers()
    test_spanning_event()
    test_user_dates_are_the_window()
    test_the_register_is_not_consulted()
    test_a_stated_bbox_beats_the_gazetteer()
    test_a_malformed_bbox_is_refused_not_repaired()
    test_carry_forward()
    test_no_aoi_at_all()
    test_missing_window_continues()
    test_about_event_is_read()
    test_choice_is_remembered()
    test_answering_an_event_question_ends_it()
    test_event_title_does_not_clobber_the_aoi()
    test_stale_question_not_reanswered()
    test_resolver_survives_a_bad_reading()
    test_unreadable_reading_is_retried_only_when_it_matters()
    test_a_pronoun_does_not_move_the_aoi()
    test_naming_the_held_aoi_again_is_not_new()
    test_the_aoi_is_locked_until_the_user_says_yes()
    test_a_place_the_user_did_not_write_is_ignored()
    test_a_stated_bbox_on_a_held_aoi_is_asked()
    test_a_first_aoi_needs_no_confirmation()
    if live:
        test_live()
    else:
        print("\n(skipping the live lookups; pass --live to run them)")

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
