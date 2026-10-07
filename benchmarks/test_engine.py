"""Engine regression checks (plain Python, no pytest - run it as a script).

    python3 benchmarks/test_engine.py

The review in `docs/TESTING.md` found that the one file holding every real
defect in this project had no automated check at all, so a wrong arithmetic path
could produce plausible numbers for two rounds without anything going red. This
is the smallest suite that closes the highest-risk part of that gap: decay,
tiers and rendering, the budget guarantee on both recall paths, pinning,
supersession, duplicate collapsing, and persistence.

Two conventions, both deliberate:

* Real assertions fail the run (non-zero exit). They describe behaviour that is
  supposed to be true today.
* Behaviour that is currently WRONG but tracked as a defect is registered with
  `known_defect()`. That prints `KNOWN DEFECT ... (tracked by W0.x)` instead of
  failing, and switches to `RESOLVED` on the day the fix lands - so the suite is
  green before and after the fix, and a defect can never be silently forgotten.
  Registering a defect asserts the defect's *signature*, not the wrong value:
  if the code changes for another reason, the registry notices.

Stdlib only, like everything else here.
"""

from __future__ import annotations

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
    GIST_MAX_CHARS,
    INITIAL_SCORE,
    PROTECTED_SCORE,
    SUMMARY_MAX_CHARS,
    MyelinatedMemory,
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
def test_duplicate_collapsing_and_distinct_content() -> None:
    engine = _engine()
    engine.add("The deploy target for the web service is the staging cluster in eu-west.", now=T0)
    engine.add("The deploy target for the web service is the staging cluster in eu-west!", now=T0)
    engine.refresh(now=T0 + DAY)
    ok(len(engine.memories) == 1, "near-identical memories must collapse to one entry")
    engine.add("Lunch is at noon and the kitchen is on the second floor.", now=T0 + DAY)
    engine.refresh(now=T0 + 2 * DAY)
    ok(len(engine.memories) == 2, "unrelated content must not collapse")


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


# A pair at exactly DUPLICATE_JACCARD (0.900): the registrations for D13/D14 use it, because
# the contradiction case ("the value changed") lands in the same region of similarity as a
# pure restatement, which is where collapsing stops being a feature.
_UPDATE_A = ("The deploy target for the web service in the eu-west region is the staging cluster "
             "owned by the platform team for nightly builds")
_UPDATE_B = ("The deploy target for the web service in the eu-west region is the production cluster "
             "owned by the platform team for nightly builds")


# ----------------------------------------------------------- known defects
def test_known_defects() -> None:
    """Assert each tracked defect's signature; never fail the run."""
    # W0.1 - decay is applied again on every refresh, so the exponent compounds.
    engine = _engine()
    memory_id = engine.add("The deploy target for the web service is the staging cluster.", now=T0)
    for day in range(1, 8):
        engine.refresh(now=T0 + day * DAY)
    rate = CATEGORY_DECAY_PER_DAY["general"]
    observed = engine.memories[memory_id].score
    documented = INITIAL_SCORE * math.exp(-rate * 7)
    compounded = INITIAL_SCORE * math.exp(-rate * 7 * 8 / 2)
    known_defect("W0.1", "decay compounds (rate x d(d+1)/2 instead of rate x d)",
                 abs(observed - compounded) < 1e-6 and abs(observed - documented) > 1e-3)

    # W0.7 - retiring a memory leaves the cached similarity index (and its IDF) stale.
    engine = _engine()
    first = engine.add("The cache layer runs Redis 6 in the staging cluster.", now=T0)
    engine.add("Lunch is served at noon in the second floor kitchen today.", now=T0)
    engine.add("The retention window for audit logs is ninety days by policy.", now=T0)
    before = engine.similarity_scores("cache layer staging cluster")
    engine.retire(first)
    dirty = engine._sim_dirty
    same_without_rebuild = engine.similarity_scores("cache layer staging cluster") == before
    engine._sim_dirty = True
    after_rebuild = engine.similarity_scores("cache layer staging cluster")
    known_defect("W0.7", "retire() does not invalidate the similarity index",
                 (not dirty) and same_without_rebuild
                 and (first not in after_rebuild or after_rebuild != before))

    # D13 - an update that is >= 0.9 similar to a RETIRED memory is absorbed by that entry, so the
    # correction is never stored, the entry stays retired, and recall returns nothing at all.
    engine = _engine()
    old = engine.add(_UPDATE_A, now=T0)
    engine.retire(old)
    new = engine.add(_UPDATE_B, now=T0 + DAY)
    absorbed = (new == old) and engine.memories[new].retired
    absorbed = absorbed and _UPDATE_B not in engine.memories[new].content
    nothing = engine.recall(budget=2200, query="where is the deploy target",
                            now=T0 + 2 * DAY).used_ids == []
    known_defect("D13", "an update >=0.9 similar to a retired memory is absorbed by it and lost",
                 absorbed and nothing)

    # D14 - the same threshold against a LIVE memory keeps the older wording and drops the new one,
    # with no signal to the caller beyond "the id you got back is the old id".
    engine = _engine()
    original = engine.add(_UPDATE_A, now=T0)
    updated = engine.add(_UPDATE_B, now=T0 + DAY)
    known_defect("D14", "an update >=0.9 similar to a live memory discards the newer wording",
                 updated == original and _UPDATE_B not in engine.memories[updated].content)


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
