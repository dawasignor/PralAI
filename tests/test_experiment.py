"""The thesis experiment cache: slots, inputs, and what purge does.

    python tests/test_experiment.py

Offline. No model, no network, and every check runs in a temporary directory
so it cannot touch a real event.

The behaviour worth pinning is the boring kind: that an output slot cannot be
invented by typo, that a cached input survives being read back exactly, and
that nothing deletes itself. A cache that quietly refreshed would put two
tables in the same chapter on different numbers, which is the failure this
module exists to prevent -- and it would look like nothing at all until the
thesis was being defended.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.experiment import (OUTPUTS, OUTPUTS_V1, OUTPUTS_V2,  # noqa: E402
                             Experiment, events)

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def check_registries() -> None:
    """Registry v2 is the twelve outputs, numbered and in order; v1 is kept."""
    print(f"  slots: v2 {len(OUTPUTS_V2)}, v1 {len(OUTPUTS_V1)}")
    if OUTPUTS is not OUTPUTS_V2:
        fail("the current registry is not version 2")
    if [o.n for o in OUTPUTS_V2] != list(range(1, 13)):
        fail(f"v2 outputs are not numbered 1..12: {[o.n for o in OUTPUTS_V2]}")
    if [o.n for o in OUTPUTS_V1] != list(range(1, 9)):
        fail("the v1 registry changed; older experiments are read with it")
    if OUTPUTS_V2[0].slot != "data_availability":
        fail("Output 1 must be the data availability check (D-33)")
    if any(o.slot == "smaa" for o in OUTPUTS_V2):
        fail("SMAA is ruled out (D-27) but is in registry v2")
    for registry in (OUTPUTS_V1, OUTPUTS_V2):
        names = [o.slot for o in registry]
        if len(set(names)) != len(names):
            fail(f"duplicate slot names: {names}")
        for entry in registry:
            if not entry.filename.startswith(f"{entry.n:02d}_"):
                fail(f"{entry.slot} filename is not order-prefixed: {entry.filename}")
    print(f"  files: {', '.join(o.filename for o in OUTPUTS[:3])}, ...")


def check_version_detection(root: Path) -> None:
    """A new experiment is v2; one holding v1 files is read as v1, unrenamed."""
    new = Experiment("fresh-test", root=root / "versions")
    new.create()
    if new.registry_version != 2 or "registry_version: 2" not in new.memory_path.read_text(encoding="utf-8"):
        fail("a new experiment is not recorded as registry version 2")
    old = Experiment("old-test", root=root / "versions")
    old.dir.mkdir(parents=True, exist_ok=True)
    (old.dir / "event.md").write_text("---\ntype: experiment-memory\n---\n", encoding="utf-8")
    (old.dir / "01_weather_data.md").write_text("old output", encoding="utf-8")
    if old.registry_version != 1:
        fail("an experiment with a v1 output file was not read as version 1")
    old.write("lulc_info", "still writable under its v1 name")
    try:
        old.write("weather", "a v2 slot")
        fail("a v1 experiment accepted a v2 slot")
    except KeyError:
        pass
    print("  new -> v2; with 01_weather_data.md -> v1, v2 slots refused: yes")


def check_write_and_read(root: Path) -> None:
    run = Experiment("derna-test", root=root)
    run.create(place="Derna, Libya", window="2023-09-08 to 2023-09-14")

    run.write("mcdm_pre_ranking", "Decision matrix before the run.",
              inputs_used=["weather", "lulc"], source="test")
    text = run.read("mcdm_pre_ranking")

    print(f"  wrote  : {run.path('mcdm_pre_ranking').name}")
    if "Decision matrix before the run" not in text:
        fail("the body did not survive the round trip")
    if "inputs_used: [weather, lulc]" not in text:
        fail("provenance is missing from the frontmatter")
    else:
        print("  records which inputs it rests on: yes")
    if "n: 6" not in text:
        fail("the output is not numbered in its own frontmatter")

    written = [r for r in run.status() if r["written"]]
    if [r["slot"] for r in written] != ["mcdm_pre_ranking"]:
        fail(f"status should show exactly one written slot, got {written}")
    else:
        print(f"  status : 1 of {len(OUTPUTS)} written")


def check_unknown_slot_is_refused(root: Path) -> None:
    """A typo must not create a ninth output nobody reads."""
    run = Experiment("derna-test", root=root)
    try:
        run.write("mcdm_rankng", "oops")
    except KeyError as exc:
        print(f"  refused: {str(exc)[:58]}")
        return
    fail("an unknown slot name was accepted and written")


def check_input_round_trip(root: Path) -> None:
    """A cached payload comes back byte-identical, not reformatted."""
    run = Experiment("derna-test", root=root)
    series = {"hourly_rain": [0.0, 1.4, 12.7, 3.2], "units": "mm"}
    run.cache_input("weather", series, summary="4 h of rain over Derna",
                    params={"latitude": 32.76, "longitude": 22.64})

    back = run.cached_input("weather")
    if back is None or back["payload"] != series:
        fail(f"the payload did not survive the round trip: {back}")
    else:
        print("  payload round-trips exactly: yes")
    if back["params"]["latitude"] != 32.76:
        fail("the parameters that produced it were not kept")
    else:
        print("  keeps the params that produced it: yes")

    if run.cached_input("lulc") is not None:
        fail("an uncached input did not report as a miss")

    memory = run.memory_path.read_text(encoding="utf-8")
    if "`weather`" not in memory or "4 h of rain" not in memory:
        fail("the memory file does not index the cached input")
    else:
        print("  event.md indexes it: yes")


def check_purge_is_explicit(root: Path) -> None:
    """Outputs go on request; inputs only when asked for by name."""
    run = Experiment("derna-test", root=root)
    run.write("weather", "body")

    removed = run.purge()                       # the default
    print(f"  purge()        removed {len(removed)}: {removed}")
    if not any(f.endswith("_mcdm_pre_ranking.md") for f in removed):
        fail("purge did not remove a written output")
    if any(f.startswith("inputs/") for f in removed):
        fail("the default purge deleted cached inputs")
    if run.cached_input("weather") is None:
        fail("the cached input was destroyed by an outputs-only purge")
    else:
        print("  cached inputs survive an outputs purge: yes")

    removed = run.purge(inputs=True)
    print(f"  purge(inputs=True) removed {len(removed)}: {removed}")
    if not any(f.startswith("inputs/") for f in removed):
        fail("purge(inputs=True) left the inputs in place")
    if run.cached_input("weather") is not None:
        fail("the input survived a purge that asked for it")

    memory = run.memory_path.read_text(encoding="utf-8")
    if "_None yet._" not in memory:
        fail("the memory file still lists an input that was purged")
    else:
        print("  event.md is reindexed after a purge: yes")


def check_purge_can_be_narrowed(_shared: Path) -> None:
    """Re-running one flow must not cost the outputs of another.

    The all-or-nothing purge made "purge the results of this flow" a choice
    between deleting too much and deleting by hand -- and by hand is how a
    stale raster survives a rebuild and gets rendered into a figure nobody
    thinks is stale.
    """
    # Its own root, not the shared one. This check needs a second experiment
    # to prove the narrowing, and check 6 asserts the shared root holds exactly
    # one -- a fixture two checks disagree about is a failure waiting for
    # whichever runs second.
    import tempfile
    with tempfile.TemporaryDirectory() as own:
        _narrowed(Path(own))


def _narrowed(root: Path) -> None:
    run = Experiment("narrow-test", root=root)
    run.write("weather", "weather body")
    run.write("land_use_pre_event", "lulc body")
    run.cache_input("weather", {"n": 1})
    run.cache_input("lulc", {"n": 2})
    run.write_deliverable("pre_flood_aoi_brief", "{}")

    removed = run.purge(outputs=True, inputs=True, slots=["land_use_pre_event"],
                        names=["lulc"], deliverables=["pre_flood_aoi_brief"])
    print(f"  narrowed purge removed {len(removed)}: {sorted(removed)}")

    if run.cached_input("lulc") is not None:
        fail("the named input survived a purge that asked for it")
    if run.cached_input("weather") is None:
        fail("purging the lulc flow destroyed the weather input")
    written = {s["slot"] for s in run.status() if s.get("written")}
    if "land_use_pre_event" in written:
        fail("the named output slot survived")
    if "weather" not in written:
        fail("purging the lulc flow destroyed the weather output")
    if run.deliverable("pre_flood_aoi_brief") is not None:
        fail("the named deliverable survived")
    print("  weather output and input both untouched: yes")

    # Naming something absent is an error, not a no-op: the point of naming
    # what to delete is that you believed it was there, and a silent miss
    # leaves stale output behind under the impression it is gone.
    for kwargs, what in (({"slots": ["no_such_slot"]}, "slot"),
                         ({"inputs": True, "names": ["no_such_input"]}, "input")):
        try:
            run.purge(**kwargs)
            fail(f"a purge naming a missing {what} should raise")
        except ValueError:
            pass
    print("  naming a missing slot or input raises rather than passing: yes")

    # And the guard must not have deleted anything on its way to raising.
    if run.cached_input("weather") is None:
        fail("a refused purge deleted an input before raising")
    print("  a refused purge deletes nothing: yes")


def check_slug_is_confined(root: Path) -> None:
    """A slug is a directory name, so a traversal must be refused."""
    for bad in ("../escape", "Derna Libya", "", "a/b"):
        try:
            Experiment(bad, root=root)
        except ValueError:
            continue
        fail(f"slug {bad!r} was accepted as a directory name")
    print("  ../escape, spaces, empty and a/b all refused")

    listed = events(root=root)
    print(f"  events(): {listed}")
    if listed != ["derna-test"]:
        fail(f"events() should list the one real event, got {listed}")


def check_experiment_reaches_the_state() -> None:
    """`new_state` must actually carry the experiment, not just accept it.

    It accepted the argument and dropped it on the floor: the signature took
    `experiment=`, the returned GraphState never held it, so `monty_node` read
    an empty slug and cached nothing. A live run looked entirely successful --
    the workflow ran, the answer came back -- and wrote no record.
    """
    from core.state import new_state

    state = new_state("task", experiment="kelani-2025")
    got = state.get("experiment")
    print(f"  state['experiment'] = {got!r}")
    if got != "kelani-2025":
        fail(f"new_state dropped the experiment: {got!r}")
    else:
        print("  ok  it survives into the state")

    blank = new_state("task")
    if blank.get("experiment"):
        fail(f"an unnamed run claims an experiment: {blank.get('experiment')!r}")
    else:
        print("  ok  and defaults to empty, so nothing caches by accident")


def main() -> int:
    hr("0. the experiment slug reaches the graph state")
    check_experiment_reaches_the_state()
    hr("1. the output registries (v2 current, v1 kept)")
    check_registries()
    hr("1b. which registry an experiment is on")
    with tempfile.TemporaryDirectory() as own:
        check_version_detection(Path(own))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hr("2. writing and reading an output")
        check_write_and_read(root)
        hr("3. an unknown slot is refused")
        check_unknown_slot_is_refused(root)
        hr("4. cached inputs round-trip")
        check_input_round_trip(root)
        hr("5. purge is explicit, and never touches inputs by default")
        check_purge_is_explicit(root)
        hr("5b. a purge narrowed to one flow")
        check_purge_can_be_narrowed(root)
        hr("6. the event slug is confined")
        check_slug_is_confined(root)

    print()
    if FAILURES:
        print(f"{'=' * 68}\n{len(FAILURES)} CHECK(S) FAILED\n{'=' * 68}")
        for message in FAILURES:
            print(f"  - {message}")
        return 1
    print(f"{'=' * 68}\nALL CHECKS PASSED\n{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
