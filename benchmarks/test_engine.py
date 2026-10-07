"""Engine regression checks (plain Python, no pytest - run it as a script).

    python3 benchmarks/test_engine.py

The review in `docs/TESTING.md` found that the one file holding every real
defect in this project had no automated check at all, so a wrong arithmetic path
could produce plausible numbers for two rounds without anything going red. This
is the smallest suite that closes that gap: decay, tiers and rendering, the
budget guarantee on both recall paths, pinning, supersession, duplicate
collapsing, the update path, and persistence.

Two conventions, both deliberate:

* Real assertions fail the run (non-zero exit). They describe behaviour that is
  supposed to be true today.
* Behaviour that is WRONG but tracked as a defect is registered with
  `known_defect()`. That prints `KNOWN DEFECT ... (tracked by D-x)` instead of
  failing, and switches to `RESOLVED` on the day the fix lands - so the suite
  stays green before and after the fix, and a defect can never be silently
  forgotten the way the compounding decay was for two rounds. Registering a
  defect asserts the defect's *signature*, not the wrong value: if the code
  changes for another reason, the registry notices.

**The registry is empty as of round 4**, on purpose: W0.1 (compounding decay),
W0.7 (a retired memory leaving the similarity index stale), D13 (a correction
absorbed by the retired entry it replaced) and D14 (an update losing its newer
wording) are all fixed, and this file asserts the fixed behaviour instead. A new
tracked defect goes back in via `known_defect("D13", "<description>", <bool>)`.

Stdlib only, like everything else here.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (os.path.join(ROOT, "scripts"), HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from myelinate import (  # noqa: E402
    BOOST_ALPHA,
    CATEGORY_DECAY_PER_DAY,
    DUPLICATE_JACCARD,
    GIST_MAX_CHARS,
    INITIAL_SCORE,
    PROTECTED_SCORE,
    SUMMARY_MAX_CHARS,
    MyelinatedMemory,
    _tokens,
    jaccard,
    tier_for,
)

DAY = 86400.0
T0 = 1700000000.0
CHECKS = 0
KNOWN: List[str] = []
RESOLVED: List[str] = []


def ok(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    assert condition, message


def known_defect(tracking: str, description: str, still_present: bool) -> None:
    """Record a tracked defect. Never fails the run; reports if it is gone."""
    if still_present:
        KNOWN.append("%s %s" % (tracking, description))
    else:
        RESOLVED.append("%s %s" % (tracking, description))


def _engine(**kwargs) -> MyelinatedMemory:
    options = dict(in_memory=True, similarity=True, knapsack=True, stale_retirement=True)
    options.update(kwargs)
    return MyelinatedMemory(**options)


# ------------------------------------------------------------------ the score
def test_boost_is_bounded_and_diminishing() -> None:
    engine = _engine()
    memory_id = engine.add("A preference worth keeping about response length.", now=T0)
    first = engine.memories[memory_id].score
    ok(abs(first - INITIAL_SCORE) < 1e-12, "a new memory starts at INITIAL_SCORE")
    engine.access(memory_id, now=T0)
    second = engine.memories[memory_id].score
    ok(second > first, "access must raise the score")
    engine.access(memory_id, now=T0)
    third = engine.memories[memory_id].score
    ok(third > second, "a second access must raise it again")
    ok((third - second) < (second - first), "increments must diminish")
    for _ in range(500):
        engine.access(memory_id, now=T0)
    ok(engine.memories[memory_id].score <= 1.0 + 1e-12, "the score can never exceed 1.0")
    ok(engine.memories[memory_id].score > 0.999, "500 accesses must approach saturation")


def test_decay_is_per_day_and_can_never_compound() -> None:
    """D1: the exponent is `rate x days`, never `rate x d(d+1)/2`."""
    rate = CATEGORY_DECAY_PER_DAY["general"]

    # Realising twice at the same instant is a no-op: that is what makes the old
    # compounding failure impossible rather than merely corrected.
    engine = _engine()
    memory_id = engine.add("The deploy target for the web service is the staging cluster.", now=T0)
    memory = engine.memories[memory_id]
    engine.refresh(now=T0 + DAY)
    once = memory.score
    engine.refresh(now=T0 + DAY)
    ok(abs(memory.score - once) < 1e-15, "a second refresh at the same instant must change nothing")
    ok(abs(memory.score_at - (T0 + DAY)) < 1e-9, "realising must record when the score was computed")

    # The documented per-day rule, for every day out to a month.
    engine = _engine()
    memory_id = engine.add("The deploy target for the web service is the staging cluster.", now=T0)
    memory = engine.memories[memory_id]
    for day in range(1, 31):
        engine.refresh(now=T0 + day * DAY)
        want = INITIAL_SCORE * math.exp(-rate * day)
        ok(abs(memory.score - want) < 1e-9,
           "day %d: %.12f != %.12f (compounding decay?)" % (day, memory.score, want))

    # Many session boundaries inside one day decay nothing, because no time passed.
    engine = _engine()
    memory_id = engine.add("The deploy target for the web service is the staging cluster.", now=T0)
    for _ in range(100):
        engine.refresh(now=T0)
    ok(abs(engine.memories[memory_id].score - INITIAL_SCORE) < 1e-12,
       "refreshing repeatedly at the same instant must not decay a memory")


def test_access_boosts_the_decayed_value() -> None:
    """D1: one use must not undo an arbitrarily long dormancy."""
    rate = CATEGORY_DECAY_PER_DAY["general"]
    engine = _engine()
    memory_id = engine.add("The deploy target for the web service is the staging cluster.", now=T0)
    engine.refresh(now=T0 + 7 * DAY)
    decayed = INITIAL_SCORE * math.exp(-rate * 7)
    ok(abs(engine.memories[memory_id].score - decayed) < 1e-9,
       "seven dormant days must decay to INITIAL_SCORE x exp(-rate x 7)")
    engine.access(memory_id, now=T0 + 7 * DAY)
    ok(abs(engine.memories[memory_id].score - (decayed + BOOST_ALPHA * (1.0 - decayed))) < 1e-9,
       "an access must boost the decayed value, not the pre-dormancy one")


def test_protected_memories_never_decay() -> None:
    engine = _engine()
    memory_id = engine.add("Identity: the user is called Ada.", category="identity",
                           protected=True, now=T0)
    engine.refresh(now=T0 + 10_000 * DAY)
    memory = engine.memories[memory_id]
    ok(memory.score == PROTECTED_SCORE, "a pinned memory keeps its score after 10,000 days")
    ok(tier_for(memory.score, memory.protected) == "active", "a pinned memory is always active")


def test_tier_thresholds() -> None:
    ok(tier_for(0.9) == "active", "0.9 is active")
    ok(tier_for(0.5) == "latent", "exactly 0.5 is not active")
    ok(tier_for(0.3) == "latent", "0.3 is latent")
    ok(tier_for(0.11) == "latent", "just above 0.1 is latent")
    ok(tier_for(0.1) == "archived", "exactly LATENT_THRESHOLD falls through to archived")
    ok(tier_for(0.05) == "archived", "0.05 is archived")
    ok(tier_for(0.0) == "archived", "0.0 is archived")
    ok(tier_for(0.0, True) == "active", "protection overrides the score")


def test_render_respects_the_detail_caps() -> None:
    engine = _engine()
    memory_id = engine.add(
        "The deploy target for the web service is the staging cluster in eu-west. "
        "Production is only promoted after a green canary run.", now=T0)
    memory = engine.memories[memory_id]
    full = engine.render(memory, "full")
    summary = engine.render(memory, "summary")
    gist = engine.render(memory, "gist")
    ok(len(full) > 0 and len(summary) <= SUMMARY_MAX_CHARS, "a summary never exceeds its cap")
    ok(0 < len(gist) <= GIST_MAX_CHARS, "a gist never exceeds its cap")
    ok(gist != "[%s] (archived)" % memory_id and memory_id not in gist,
       "the archived tier renders readable content, not an opaque id stub (R7)")
    ok(len(gist) < len(full), "the ladder must actually downgrade")


# ---------------------------------------------------------------- the budget
def test_budget_is_never_exceeded_on_either_recall_path() -> None:
    for knapsack in (True, False):
        engine = _engine(knapsack=knapsack)
        for index in range(40):
            engine.add("Fact number %d about deployment, caching and retention policy in region %d."
                       % (index, index), category="general", now=T0)
        for budget in (0, 50, 500, 2200):
            for query in (None, "what is the deploy target for the caching layer?"):
                result = engine.recall(budget=budget, now=T0, query=query)
                ok(result.chars <= budget,
                   "knapsack=%s budget=%d query=%s spent %d chars"
                   % (knapsack, budget, bool(query), result.chars))
                ok(result.budget == budget, "the result reports the budget it was given")


def test_retired_memories_are_excluded_from_recall() -> None:
    engine = _engine()
    old = engine.add("The cache layer runs Redis 6 in the staging cluster.", now=T0)
    new = engine.add("The cache layer was upgraded to Redis 7.2 in the staging cluster.",
                     now=T0 + 40 * DAY, supersedes=old)
    ok(engine.memories[old].retired, "supersedes= must retire the old memory")
    ok(engine.memories[old].superseded_by == new, "the replacement must be recorded")
    result = engine.recall(budget=2200, now=T0 + 41 * DAY, query="which cache layer runs in staging?")
    ok(old not in result.used_ids, "a retired memory must never reach the context")
    ok(result.retired_excluded >= 1, "the result must report what it excluded")


# ------------------------------------------------------- store and structure
def test_restatements_collapse_and_distinct_content_does_not() -> None:
    engine = _engine()
    engine.add("The deploy target for the web service is the staging cluster in eu-west.", now=T0)
    engine.add("The deploy target for the web service is the staging cluster in eu-west!", now=T0)
    engine.refresh(now=T0 + DAY)
    ok(len(engine.memories) == 1, "the same token set must still collapse to one entry")

    engine.add("Lunch is at noon and the kitchen is on the second floor.", now=T0 + DAY)
    engine.refresh(now=T0 + 2 * DAY)
    ok(len(engine.memories) == 2, "unrelated content must not collapse")

    # A retired entry is never a merge candidate nor a merge winner (D13), so it
    # survives the consolidation pass instead of being folded into a live memory.
    survivor = [mid for mid, mem in engine.memories.items() if "Lunch" in mem.content][0]
    engine.retire(survivor)
    engine.refresh(now=T0 + 3 * DAY)
    ok(survivor in engine.memories, "a retired memory must survive the consolidation pass")
    ok(engine.memories[survivor].retired, "and must still be retired afterwards")


# A pair at exactly DUPLICATE_JACCARD (0.900): the contradiction case ("the value
# changed") lands in the same region of similarity as a pure restatement, which is
# where absorbing a memory stops being a feature and starts losing information.
_UPDATE_A = ("The deploy target for the web service in the eu-west region is the staging cluster "
             "owned by the platform team for nightly builds")
_UPDATE_B = ("The deploy target for the web service in the eu-west region is the production cluster "
             "owned by the platform team for nightly builds")


def test_an_update_keeps_its_own_wording() -> None:
    """D14: a changed value must be stored, not absorbed by the entry it replaces."""
    similarity = jaccard(_tokens(_UPDATE_A), _tokens(_UPDATE_B))
    ok(abs(similarity - DUPLICATE_JACCARD) < 1e-9,
       "the fixture must sit exactly at the duplicate threshold (got %.4f)" % similarity)

    engine = _engine()
    original = engine.add(_UPDATE_A, now=T0)
    updated = engine.add(_UPDATE_B, now=T0 + DAY)
    ok(updated != original, "an update must not be absorbed by the memory it replaces")
    ok(engine.memories[updated].content == _UPDATE_B, "the new wording must be stored")
    ok(engine.memories[original].retired, "the superseded wording must be retired")
    ok(engine.memories[original].superseded_by == updated, "the replacement must be recorded")

    context = engine.recall(budget=2200, query="where is the deploy target", now=T0 + 2 * DAY)
    ok(updated in context.used_ids, "recall must surface the correction")
    ok(original not in context.used_ids, "recall must never surface the superseded wording")

    engine.refresh(now=T0 + 3 * DAY)
    ok(len(engine.memories) == 2 and engine.memories[updated].content == _UPDATE_B,
       "the consolidation pass must not merge an update back into the old wording")


def test_a_correction_after_a_retire_is_stored() -> None:
    """D13: a retired memory must never absorb the fact that replaces it."""
    engine = _engine()
    old = engine.add(_UPDATE_A, now=T0)
    ok(engine.retire(old), "retire() must report success")
    new = engine.add(_UPDATE_B, now=T0 + DAY)
    ok(new != old, "the correction must be a new entry, not the dead one")
    ok(not engine.memories[new].retired, "the correction must be live")
    ok(engine.memories[new].content == _UPDATE_B, "the correction's text must be stored")

    context = engine.recall(budget=2200, query="where is the deploy target", now=T0 + 2 * DAY)
    ok(context.used_ids == [new], "recall must return exactly the correction")
    ok(context.chars > 0, "recall must not come back empty")

    # Re-asserting the exact retired wording is a new fact, not a collapse into
    # the dead entry (whose id is still taken).
    again = engine.add(_UPDATE_A, now=T0 + 3 * DAY)
    ok(again != old and not engine.memories[again].retired,
       "re-asserting a retired fact must store a new, live entry")


# --------------------------------------------------------------- the index
def test_retire_and_pin_invalidate_the_similarity_index() -> None:
    """D9 / W0.7: any mutation must invalidate the cached TF-IDF state."""
    engine = _engine()
    first = engine.add("The cache layer runs Redis 6 in the staging cluster.", now=T0)
    engine.add("Lunch is served at noon in the second floor kitchen today.", now=T0)
    engine.add("The retention window for audit logs is ninety days by policy.", now=T0)

    # The pre-retire index scores the memory we are about to kill, and scores
    # every live memory (unrelated ones at 0.0). Both facts are load-bearing
    # below, so assert them rather than assuming them.
    before = engine.similarity_scores("cache layer staging cluster")
    ok(first in before and before[first] > 0.5,
       "fixture: the retired memory must be scored before the retire")
    # A query whose tokens still exist in the live store. A query matching ONLY
    # the retired memory has no IDF weights left at all, so it short-circuits to
    # {} - which is correct, but it cannot show that the surviving memories are
    # still enumerated. This one can.
    live_query = "lunch kitchen second floor noon"
    live_before = engine.similarity_scores(live_query)
    ok(len(live_before) == 3, "fixture: the index spans the live store")

    engine.retire(first)
    ok(engine._sim_dirty is True, "retire() must invalidate the similarity index")
    after = engine.similarity_scores("cache layer staging cluster")
    ok(first not in after, "a retired memory must not be scored")
    scored = engine.similarity_scores(live_query)
    ok(first not in scored, "a retired memory must not be scored on any query")
    ok(set(scored) == set(live_before) - {first},
       "retiring must remove exactly that memory from the index")
    ok(max(scored.values()) > 0.5, "the surviving match must still score")

    # Incremental state must equal what a full rebuild from the same live set gives.
    fresh = _engine()
    fresh.add("Lunch is served at noon in the second floor kitchen today.", now=T0)
    fresh.add("The retention window for audit logs is ninety days by policy.", now=T0)
    rebuilt = fresh.similarity_scores(live_query)
    ok(set(scored) == set(rebuilt), "a rebuild scores the same memories")
    ok(all(abs(scored[mid] - rebuilt[mid]) < 1e-9 for mid in rebuilt),
       "incremental similarity scores must equal a full rebuild after a retire")

    live_id = [mid for mid, mem in engine.memories.items() if not mem.retired][0]
    ok(engine.pin(live_id) is True, "a live memory can be pinned")
    ok(engine._sim_dirty is True, "pin() must invalidate the similarity index too")


def test_pin_refuses_a_retired_memory() -> None:
    """D18: pinning a retired memory would hide it at strength 1.0, so refuse."""
    engine = _engine()
    memory_id = engine.add("A fact that will be retired before it is pinned.", now=T0)
    engine.retire(memory_id)
    ok(engine.pin(memory_id) is False, "pin() must refuse a retired memory")
    memory = engine.memories[memory_id]
    ok(memory.retired and not memory.protected, "and must leave it retired and unprotected")


# -------------------------------------------------------------- persistence
def test_prune_reports_the_deficit() -> None:
    """D18: a ceiling the store cannot reach must be reported, not silently missed."""
    engine = _engine()
    for index in range(5):
        engine.add("Prune probe number %d about retention windows and audit policy." % index, now=T0)
    report = engine.refresh(now=T0 + DAY, max_entries=3)
    ok(report["pruned"] == 0, "nothing can be pruned while every memory is still active")
    ok(report["prune_deficit"] == 2, "the shortfall against the ceiling must be reported")
    ok({"decayed", "merged", "clusters", "pruned", "total"} <= set(report),
       "the existing refresh() keys must survive")


def test_persistence_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "store.json")
        engine = MyelinatedMemory(path=path)
        ids = [engine.add("Persistence check %d about retention windows." % i, now=T0 + i * DAY)
               for i in range(3)]
        engine.save()
        ok(os.path.exists(path), "save() must write the store")
        reloaded = MyelinatedMemory(path=path)
        ok(reloaded.load() == 3, "load() must report every memory it read")
        ok(sorted(reloaded.memories) == sorted(ids), "ids must survive the round trip")
        for memory_id in ids:
            ok(abs(reloaded.memories[memory_id].score - engine.memories[memory_id].score) < 1e-9,
               "scores must survive the round trip for %s" % memory_id)
        ok(engine.to_dict()["schema"] == reloaded.to_dict()["schema"], "the schema is written")
        ok(reloaded.to_dict()["schema"] == 3, "the schema version is the current one")

        # A v2 store has no `score_at`; it must migrate to a real decay baseline
        # rather than decaying from the epoch (decay from 0 would archive it).
        payload = engine.to_dict()
        for raw in payload["memories"]:
            raw.pop("score_at", None)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        migrated = MyelinatedMemory(path=path)
        ok(all(mem.score_at > 0 for mem in migrated.memories.values()),
           "a store without score_at must migrate to a non-zero baseline")


def test_store_path_precedence() -> None:
    explicit = "/tmp/explicit-store.json"
    ok(MyelinatedMemory(in_memory=True).path == "", "in_memory must never touch a store path")
    ok(MyelinatedMemory(path=explicit).path == explicit, "an explicit path wins")
    previous = os.environ.get("HERMES_MEMORY_STORE")
    try:
        os.environ["HERMES_MEMORY_STORE"] = "/tmp/env-store.json"
        ok(MyelinatedMemory().path == "/tmp/env-store.json", "the env var is used when no path is given")
        ok(MyelinatedMemory(path=explicit).path == explicit, "the explicit flag still wins")
    finally:
        if previous is None:
            os.environ.pop("HERMES_MEMORY_STORE", None)
        else:
            os.environ["HERMES_MEMORY_STORE"] = previous


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print("engine ok: %d checks" % CHECKS)
    for entry in KNOWN:
        print("  KNOWN DEFECT %s" % entry)
    for entry in RESOLVED:
        print("  RESOLVED %s - remove it from this registry and assert the fix instead" % entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
