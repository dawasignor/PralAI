"""where_am_i: framing, zoom, admin units across countries, crossings, influences.

No network: Overpass is replaced by canned replies. Runs under pytest or as a
plain script.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workflows import osm_context as oc  # noqa: E402


def _canned(reply):
    real = oc._overpass
    oc._overpass = lambda q, timeout=90: reply
    return real


def test_registered() -> None:
    from core import workflows

    assert "where_am_i" in workflows.names()
    params = workflows.get("where_am_i").fn.__code__.co_varnames
    assert {"bbox", "aoi_fraction", "influence_km"} <= set(params)


def test_frame_puts_the_box_in_the_middle_fraction() -> None:
    box = [80.0, 7.0, 80.3, 7.2]
    ext = oc.map_extent(box, 0.75)
    assert abs((box[2] - box[0]) / (ext[2] - ext[0]) - 0.75) < 1e-9
    assert abs((box[3] - box[1]) / (ext[3] - ext[1]) - 0.75) < 1e-9


def test_zoom_meets_width_and_tile_budget() -> None:
    ext = oc.map_extent([79.94, 6.905, 80.09, 6.978], 0.75)
    z = oc.choose_zoom(ext)
    x0, _ = oc._px(ext[0], ext[3], z)
    x1, _ = oc._px(ext[2], ext[1], z)
    assert x1 - x0 >= oc.TARGET_WIDTH_PX or z == 16
    big = oc.map_extent([99.0, 13.0, 102.0, 16.0], 0.75)
    zb = oc.choose_zoom(big)
    x0, y0 = oc._px(big[0], big[3], zb)
    x1, y1 = oc._px(big[2], big[1], zb)
    tiles = (int(x1 // 256) - int(x0 // 256) + 1) * (int(y1 // 256) - int(y0 // 256) + 1)
    assert tiles <= oc.MAX_TILES or zb == 8


def _sep(name):
    return {"type": "sep", "tags": {"name": name}}


def test_admin_levels_are_read_per_country() -> None:
    # Sri Lanka: province 4, district 5.
    lk = {"elements": [
        _sep("contains"),
        {"type": "relation", "id": 1, "tags": {"admin_level": "2", "name": "Sri Lanka"}},
        {"type": "relation", "id": 2, "tags": {"admin_level": "4", "name": "Western Province"}},
        {"type": "relation", "id": 3, "tags": {"admin_level": "5", "name": "Gampaha District"}},
        _sep("crosses"),
        {"type": "relation", "id": 4, "tags": {"admin_level": "5", "name": "Colombo District"}},
        _sep("places"),
        {"type": "node", "id": 10, "lon": 79.98, "lat": 6.93, "tags": {"place": "city", "name": "Kaduwela"}},
        _sep("membership"),
        {"type": "relation", "id": 4}, {"type": "node", "id": 10},
        {"type": "relation", "id": 2}, {"type": "node", "id": 10},
    ]}
    real = _canned(lk)
    try:
        a = oc.admin_and_places([79.94, 6.905, 80.09, 6.978])
    finally:
        oc._overpass = real
    assert a["country"] == "Sri Lanka"
    assert a["first_order"] == {"level": 4, "units": ["Western Province"]}
    assert a["second_order"] == {"level": 5, "units": ["Colombo District", "Gampaha District"]}
    assert a["major_places"][0]["second_order"] == "Colombo District"

    # Thailand: province 4, district 6, municipality 7 (not second order).
    th = {"elements": [
        _sep("contains"),
        {"type": "relation", "id": 1, "tags": {"admin_level": "2", "name": "Thailand"}},
        {"type": "relation", "id": 2, "tags": {"admin_level": "4", "name": "Pathum Thani Province"}},
        {"type": "relation", "id": 3, "tags": {"admin_level": "6", "name": "Thanyaburi District"}},
        _sep("crosses"), _sep("places"), _sep("membership"),
    ]}
    real = _canned(th)
    try:
        a = oc.admin_and_places([100.6, 13.98, 100.7, 14.05])
    finally:
        oc._overpass = real
    assert a["second_order"]["level"] == 6 and a["second_order"]["units"] == ["Thanyaburi District"]


def test_crossings_are_exact_line_intersections() -> None:
    assert oc._cross((0, 0, 2, 2), (0, 2, 2, 0))
    assert not oc._cross((0, 0, 1, 0), (0, 1, 1, 1))
    river = [{"geometry": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}]}]
    crosses = {"geometry": [{"lon": 0.5, "lat": -0.1}, {"lon": 0.5, "lat": 0.1}], "tags": {}}
    near = {"geometry": [{"lon": 0.5, "lat": 0.0005}, {"lon": 0.6, "lat": 0.0005}], "tags": {}}
    assert len(oc._crossings(river, [crosses, near])) == 1


def test_influences_parse_every_category_and_never_claim_upstream() -> None:
    reply = {"elements": [_sep("hydropower_and_dams"),
                          {"type": "way", "id": 1, "center": {"lon": 80.5, "lat": 6.9},
                           "tags": {"waterway": "dam", "name": "Laxapana Dam"}},
                          _sep("_rivers"), _sep("_bridges"),
                          _sep("flood_protection")]}
    real = _canned(reply)
    try:
        inf = oc.influences([79.94, 6.905, 80.09, 6.978], 60.0)
    finally:
        oc._overpass = real
    assert set(inf["categories"]) == set(oc.INFLUENCES)
    dam = inf["categories"]["hydropower_and_dams"]["items"][0]
    assert dam["inside_aoi"] is False and dam["bearing"] == "E" and dam["distance_km"] > 40
    assert "not" in inf["note"].lower() and "upstream" in inf["note"].lower()
    assert inf["categories"]["river_gauges"]["count"] == 0      # none mapped, reported as such


def test_every_count_has_a_definition_and_the_requested_rows() -> None:
    for key in ("buildings", "hospitals", "schools", "commercial_buildings",
                "food_grocery_shops", "electric_substations"):
        assert key in oc.COUNTS and oc.COUNTS[key][2]


BOX = [79.94, 6.905, 80.09, 6.978]


class _Isolated:
    """A fake Overpass, an empty answer store, and no real waiting."""

    def __init__(self, fake):
        import tempfile

        self.fake, self.tmp = fake, tempfile.TemporaryDirectory()

    def __enter__(self):
        self.saved = (oc._overpass, oc.COUNT_CACHE)
        oc._overpass, oc.COUNT_CACHE = self.fake, Path(self.tmp.name)
        return self

    def __exit__(self, *exc):
        oc._overpass, oc.COUNT_CACHE = self.saved
        self.tmp.cleanup()


def _reply(query, total="3"):
    """A canned answer for every category named in the query."""
    keys = [k for k in oc.COUNTS if f'name="{k}"' in query]
    return {"elements": [e for k in keys
                         for e in (_sep(k), {"type": "count", "tags": {"total": total}})]}


def test_counts_go_one_at_a_time_facilities_first_and_fail_soft() -> None:
    q = oc._count_query(BOX, oc.COUNT_GROUPS["facilities"])
    assert q.count("out count") == len(oc.COUNT_GROUPS["facilities"])
    assert sorted(k for g in oc.COUNT_GROUPS.values() for k in g) == sorted(oc.COUNTS)

    sent: list[str] = []

    def fake(query, timeout=90, attempts=None):
        sent.append(query)
        if 'name="buildings"' in query or 'name="houses"' in query:
            raise RuntimeError("502")
        return _reply(query)

    with _Isolated(fake):
        c = oc.counts(BOX)
    # Facilities first; buildings group refused, then retried one category each.
    assert 'name="hospitals"' in sent[0]
    assert len(sent) == 2 + len(oc.COUNT_GROUPS["buildings"]), len(sent)
    assert c["requests"] == len(sent)
    assert c["counts"]["schools"] == 3
    # The per-category retry recovered what the server would answer ...
    assert c["counts"]["apartments"] == 3 and c["counts"]["commercial_buildings"] == 3
    # ... and what it would not is missing, never 0.
    assert set(c["missing"]) == {"buildings", "houses"}
    assert "buildings" not in c["counts"] and "houses" not in c["counts"]
    assert set(c["retrieved"]) == set(c["counts"])


def test_stored_answers_are_reused_and_only_gaps_asked() -> None:
    sent: list[str] = []
    refuse = {"on": True}

    def fake(query, timeout=90, attempts=None):
        sent.append(query)
        if refuse["on"] and 'name="schools"' in query:
            raise RuntimeError("429")
        return _reply(query)

    with _Isolated(fake):
        first = oc.counts(BOX, groups=("facilities",))
        assert "schools" in first["missing"] and first["from_store"] == []
        sent.clear()
        refuse["on"] = False
        second = oc.counts(BOX, groups=("facilities",))
        # Only the gap went to the server; everything else came from the store.
        assert len(sent) == 1 and 'name="schools"' in sent[0] and "out count" in sent[0]
        assert sent[0].count("out count") == 1
        assert second["counts"]["schools"] == 3 and not second["missing"]
        assert "schools" not in second["from_store"]
        assert set(second["from_store"]) == set(oc.COUNT_GROUPS["facilities"]) - {"schools"}
        # A reused count keeps the time it was first retrieved.
        assert second["retrieved"]["hospitals"] == first["retrieved"]["hospitals"]

        # A failure is never stored: a gap stays "not measured".
        assert not list(oc.COUNT_CACHE.glob("buildings_*.json"))

        # An uncached run (RQ5) reads nothing from the store.
        from core import cache_policy

        sent.clear()
        with cache_policy.use(cache_policy.OFF):
            oc.counts(BOX, groups=("facilities",))
        assert len(sent) == 1 and sent[0].count("out count") == len(oc.COUNT_GROUPS["facilities"])


def test_a_stored_list_cut_shorter_than_now_asked_is_fetched_again() -> None:
    sent: list[str] = []

    def fake(query, timeout=90, attempts=None):
        sent.append(query)
        out = _reply(query, total="900")
        for k in oc.COUNT_GROUPS["facilities"]:
            if f'name="{k}"' in query:
                out["elements"] += [{"type": "node", "id": i, "lat": 6.95, "lon": 80.0,
                                     "tags": {"amenity": "school", "name": f"s{i}"}}
                                    for i in range(3)]
        return out

    with _Isolated(fake):
        oc.counts(BOX, locate_max=400, groups=("facilities",))
        sent.clear()
        oc.counts(BOX, locate_max=5000, groups=("facilities",))
        assert len(sent) == 1, "900 exist and only 400 were listed: ask again"
        sent.clear()
        oc.counts(BOX, locate_max=400, groups=("facilities",))
        assert not sent, "a longer stored list serves a shorter request"


def test_the_retry_waits_as_the_server_says() -> None:
    saved = oc._slot_wait
    try:
        oc._slot_wait = lambda url: 23.0
        assert oc._pause_before(1, 429, "u", 0.0) == 24.0          # the server's wait
        oc._slot_wait = lambda url: 0.0
        p = oc._pause_before(1, 502, "u", 0.0)
        assert 12.0 <= p <= 18.0                                   # a free slot is no reason to rush
        oc._slot_wait = lambda url: None
        assert 36.0 <= oc._pause_before(2, 504, "u", 0.0) <= 54.0  # backoff grows
        oc._slot_wait = lambda url: 500.0
        assert oc._pause_before(1, 429, "u", 0.0) == oc.MAX_PAUSE_S
        assert oc._pause_before(1, 429, "u", oc.MAX_WAIT_S - 5) == 5.0
    finally:
        oc._slot_wait = saved


def test_the_status_page_is_read() -> None:
    import io

    pages = {
        "free": "Connected as: 1\nRate limit: 2\n2 slots available now.\n",
        "busy": ("Connected as: 1\nRate limit: 2\nSlot available after: "
                 "2026-11-17T10:00:40Z, in 40 seconds.\nSlot available after: "
                 "2026-11-17T10:00:12Z, in 12 seconds.\n"),
        "junk": "<html>gateway</html>",
    }
    real = oc.urllib.request.urlopen
    try:
        for name, want in (("free", 0.0), ("busy", 12.0), ("junk", None)):
            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            oc.urllib.request.urlopen = lambda req, timeout=0, _t=pages[name]: R(_t.encode())
            assert oc._slot_wait("https://x/api/interpreter") == want, name

        def down(req, timeout=0):
            raise OSError("down")
        oc.urllib.request.urlopen = down
        assert oc._slot_wait("https://x/api/interpreter") is None
    finally:
        oc.urllib.request.urlopen = real


def test_hospitals_are_actual_hospitals_or_general_clinics() -> None:
    keep = oc.hospital_or_general_clinic
    assert keep({"amenity": "hospital", "name": "Western General Hospital"})
    assert keep({"amenity": "clinic", "healthcare": "clinic", "healthcare:speciality": "emergency",
                 "name": "A&E"})
    assert keep({"amenity": "clinic", "healthcare": "clinic", "name": "Gracemount Medical Centre"})
    assert keep({"amenity": "clinic", "healthcare": "hospital", "name": "Lothian Birth Centre"})
    assert not keep({"amenity": "clinic", "healthcare": "clinic", "name": "The Salt Cave"})
    assert not keep({"amenity": "clinic", "healthcare": "clinic",
                     "healthcare:speciality": "chiropractic", "name": "City Chiropractic"})
    assert not keep({"amenity": "clinic", "healthcare": "alternative",
                     "healthcare:speciality": "chiropractic", "name": "Bruntsfield Clinic"})
    assert not keep({"amenity": "doctors", "name": "Stockbridge Surgery"})


def test_road_access_uses_the_same_hospital_set() -> None:
    """BUG-037: one selector, one filter, one inside-the-AOI rule, one entry per id."""
    from core.workflows import impact

    els = [{"type": "node", "id": 1, "lat": 55.95, "lon": -3.2,
            "tags": {"amenity": "hospital", "name": "Royal Infirmary"}},
           {"type": "node", "id": 1, "lat": 55.95, "lon": -3.2,
            "tags": {"amenity": "hospital", "name": "Royal Infirmary"}},
           {"type": "node", "id": 2, "lat": 55.95, "lon": -3.2,
            "tags": {"amenity": "clinic", "healthcare": "clinic", "name": "The Salt Cave"}},
           {"type": "way", "id": 3, "center": {"lat": 56.5, "lon": -3.2},
            "tags": {"amenity": "hospital", "name": "Outside"}}]
    reply = {"elements": [{"type": "count", "id": 0, "tags": {"total": "0"}},
                          {"type": "sep", "id": 0, "tags": {"name": "hospitals"}}, *els]}
    real = _canned(reply)
    try:
        _, hospitals = impact._roads_and_hospitals([-3.25, 55.90, -3.10, 56.00])
    finally:
        oc._overpass = real
    assert [h["osm"] for h in hospitals] == ["node/1"], hospitals
    assert [i["osm"] for i in oc._located(els, [-3.25, 55.90, -3.10, 56.00],
                                          oc.KEEP["hospitals"])] == ["node/1"]


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"[FAIL] {test.__name__} {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
