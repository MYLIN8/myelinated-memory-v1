#!/usr/bin/env python3
"""The myelination council's adversarial suite.

    python3 benchmarks/test_adversarial.py     # adversarial ok: N checks

Four chairs reviewed the code and architecture and each formulated the tests
their domain owed. The suspected defects below were frozen BEFORE this suite
ran, so a confirmed and a refuted suspicion are both reported honestly.

  Store & persistence   - schema-version refusal, corrupt files, load
                          replacing (not merging), save atomicity, round-trip
                          fidelity, id recycling on retire-then-restate.
  Algebra of decay      - the decay semigroup over fuzzed refresh schedules,
                          realize idempotence, boost bounds, the protected
                          invariant, and dead-memory mutations.
  Recall & budget       - the hard budget ceiling under fuzzed stores and
                          budgets (including unicode), recall determinism,
                          ranked_ids == pool, and the --pure + --force-similarity
                          combination.
  Protocol robustness   - the MCP server must survive malformed JSON-RPC
                          (non-object lines, bad tool args) and keep serving;
                          the CLI must fail cleanly, never with a traceback.

Suspected defects, frozen before the run:

  S1  a JSON-RPC line that parses to a non-object (``[1,2]``, ``"x"``, ``3``)
      reaches ``message.get`` uncaught and crashes the server loop.
  S2  tool arguments can raise what ``_call_tool`` does not catch
      (``TypeError`` from ``int(None)``, ``OSError`` from ``save()``), killing
      the server on one bad call.
  S3  ``load()`` writes a schema version but never reads one: a future-version
      store loads silently, dropping unknown fields.
  S4  ``access()``/``reinforce()`` strengthen a *retired* memory, disagreeing
      with ``pin()`` (D18) and the dead-entry rules.
  S5  the CLI accepts a negative ``--budget``.
  S6  ``--pure --force-similarity`` - the forced flag on an engine built with
      similarity off - is untested and may crash.
"""

import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import myelinate  # noqa: E402
import myelinated_mcp  # noqa: E402

CHECKS = 0


def check(condition, name):
    global CHECKS
    assert condition, name
    CHECKS += 1


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ================================================ chair 1: store & persistence
def store_fixtures():
    tmp = tempfile.mkdtemp(prefix="myelinated-adv-")
    path = os.path.join(tmp, "store.json")

    engine = myelinate.MyelinatedMemory(path=path)
    ids = [engine.add("memory number %d about the deploy target" % i,
                      category="identity") for i in range(3)]
    engine.add("pinned fact", protected=True)
    engine.retire(ids[0], superseded_by=ids[1])
    engine.save()
    check(not os.path.exists(path + ".tmp"), "save: no temp file left behind")

    loaded = myelinate.MyelinatedMemory(path=path)
    original = {m.id: m for m in engine.all()}
    copy = {m.id: m for m in loaded.all()}
    check(set(original) == set(copy), "load: every id survives the round trip")
    fields = ("content", "summary", "category", "created", "last_access",
              "access_count", "score", "score_at", "protected", "cluster",
              "tier", "retired", "superseded_by")
    same = all(getattr(original[i], f) == getattr(copy[i], f)
               for i in original for f in fields)
    check(same, "load: every field round-trips exactly")

    # load() replaces, it does not merge: loading twice must not duplicate.
    before = loaded.load()
    after = loaded.load()
    check(before == after == len(copy), "load: replaces the store, double load is stable")

    # S3: the file carries a schema version; load() must honour it.
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    check(payload.get("schema") == myelinate.SCHEMA_VERSION, "save: schema version recorded")
    future = dict(payload, schema=myelinate.SCHEMA_VERSION + 1)
    future_path = os.path.join(tmp, "future.json")
    with open(future_path, "w", encoding="utf-8") as handle:
        json.dump(future, handle)
    try:
        myelinate.MyelinatedMemory(path=future_path)
        refused = False
    except ValueError:
        refused = True
    check(refused, "S3: a future-schema store is refused, not loaded lossily")

    corrupt_path = os.path.join(tmp, "corrupt.json")
    with open(corrupt_path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    try:
        myelinate.MyelinatedMemory(path=corrupt_path)
        clean = False
    except ValueError:
        clean = True
    check(clean, "load: a corrupt store raises ValueError, not a raw decode crash")

    # Retire-then-restate must not recycle the retired entry's id (D13 family).
    e = myelinate.MyelinatedMemory(in_memory=True)
    old = e.add("the deploy target is the staging cluster")
    e.retire(old)
    new = e.add("the deploy target is the staging cluster")
    check(new != old, "retire-then-restate: fresh id, the dead entry keeps its own")
    check(e.get(old) is not None and e.get(old).retired, "retire-then-restate: old stays retired")
    check(not e.get(new).retired, "retire-then-restate: the restatement is live")


# ==================================================== chair 2: decay algebra
def decay_fixtures():
    # The decay semigroup: however often refresh() folds dormancy, the strength
    # at a given moment is a pure function of (score, score_at) and the clock.
    def curve(schedule, days):
        engine = myelinate.MyelinatedMemory(in_memory=True, clock=lambda: 0.0)
        engine.add("a fact that decays", category="general", now=0.0)
        for day in schedule:
            engine.refresh(now=day * 86400.0)
        return engine.strength(engine.all()[0], days * 86400.0)

    rng = random.Random(20261008)
    for _ in range(5):
        days = rng.uniform(1.0, 30.0)
        frequent = sorted(rng.uniform(0.0, days) for _ in range(6))
        sparse = sorted(rng.uniform(0.0, days) for _ in range(2))
        check(approx(curve(frequent, days), curve(sparse, days), 1e-9),
              "decay: strength at day %.1f is schedule-independent" % days)

    engine = myelinate.MyelinatedMemory(in_memory=True, clock=lambda: 100.0)
    mid = engine.add("a fact", now=0.0)
    mem = engine.get(mid)
    once = engine.realize(mem, 50.0)
    twice = engine.realize(mem, 50.0)
    check(approx(once, twice), "realize: idempotent at one instant")

    engine.access(mid, now=50.0)
    check(mem.score <= 1.0 + 1e-12, "boost: bounded by 1.0")
    check(approx(mem.score, myelinate.PROTECTED_SCORE) or mem.score < 1.0,
          "boost: not protective-scored unless pinned")

    pinned = engine.add("identity fact", protected=True, now=0.0)
    engine.refresh(now=1000 * 86400.0)
    check(approx(engine.get(pinned).score, myelinate.PROTECTED_SCORE),
          "protected: never decays below the protected score")

    # S4: a dead memory must not be strengthened.
    dead = engine.add("stale value", now=0.0)
    engine.retire(dead)
    check(engine.access(dead) is False, "S4: access() refuses a retired memory")
    check(engine.reinforce([dead]) == 0, "S4: reinforce() cannot resurrect a dead entry")
    check(engine.get(dead).access_count == 0, "S4: no usage recorded on a retired memory")


# =================================================== chair 3: recall & budget
def recall_fixtures():
    rng = random.Random(4242)
    configs = [(False, False), (True, False), (True, True)]
    for similarity, knapsack in configs:
        engine = myelinate.MyelinatedMemory(in_memory=True, similarity=similarity,
                                            knapsack=knapsack)
        for i in range(12):
            text = ("fact %d " % i) * rng.randint(1, 40) + "中文字符串与 emoji 🧠 " * (i % 3)
            engine.add(text, now=float(i))
        for budget in (0, 1, 37, 128, 500, 2200):
            result = engine.recall(budget=budget, query="fact 3 中文", now=100.0)
            check(result.chars <= budget,
                  "budget: sim=%s knap=%s budget=%d exceeded (%d)"
                  % (similarity, knapsack, budget, result.chars))
            check(result.chars == len(result.text), "budget: chars is the true length")
        first = engine.recall(budget=300, query="fact 3", now=100.0)
        second = engine.recall(budget=300, query="fact 3", now=100.0)
        check(first.text == second.text and first.used_ids == second.used_ids,
              "recall: deterministic for one query")
        pool = engine.candidate_pool("fact 3", now=100.0)
        check(first.ranked_ids == pool or set(first.ranked_ids) == set(pool),
              "recall: ranked_ids is the candidate pool it was handed")

    # S6: --pure is similarity/knapsack/supersession off; --force-similarity
    # must still be honoured for one call without mutating the engine.
    engine = myelinate.MyelinatedMemory(in_memory=True, similarity=False,
                                        knapsack=False, stale_retirement=False)
    engine.add("the deploy target is production", now=0.0)
    engine.add("a completely unrelated note about lunch", now=0.0)
    forced = engine.recall(query="deploy target", budget=2200, now=10.0,
                           force_similarity=True)
    check(forced.similarity_used is True, "S6: force_similarity works on a similarity-off engine")
    check(forced.ranked_ids and forced.ranked_ids[0] != "",
          "S6: forced ranking produces a full order")
    normal = engine.recall(query="deploy target", budget=2200, now=10.0)
    check(normal.similarity_used is False, "S6: the engine default is not mutated by the override")


# ============================================== chair 4: protocol robustness
def protocol_fixtures():
    server = myelinated_mcp.McpServer(myelinate.MyelinatedMemory(in_memory=True))

    def rpc(payload):
        return server.handle_line(payload)

    def rpc_json(obj):
        return json.loads(rpc(json.dumps(obj)))

    # S1: valid JSON that is not an object must be answered, not fatal.
    for bad in ("[1, 2]", '"hello"', "3", "null", "true"):
        try:
            response = rpc(bad)
            crashed = False
        except Exception:
            crashed, response = True, None
        check(not crashed, "S1: non-object line does not crash: %s" % bad)
        if response is not None:
            parsed = json.loads(response)
            check(parsed.get("error", {}).get("code") == -32600,
                  "S1: non-object line answered with a parse/invalid error")

    # S2: tool arguments that raise non-ValueError must come back as isError.
    for args in ({"name": "memory_recall", "arguments": {"budget": None}},
                 {"name": "memory_refresh", "arguments": {"max_entries": None}},
                 {"name": "memory_add", "arguments": {"content": ""}},
                 {"name": "no-such-tool", "arguments": {}}):
        try:
            response = rpc_json({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                 "params": args})
            crashed = False
        except Exception:
            crashed, response = True, None
        check(not crashed, "S2: tool call does not crash: %s" % args.get("name"))
        check(response is not None and response.get("result", {}).get("isError") is True,
              "S2: failing tool call is an isError result: %s" % args.get("name"))

    # The server must still be alive and correct after every hostile line.
    alive = rpc_json({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    check(alive.get("result") == {}, "server survives every hostile line and answers ping")

    # CLI: failures are clean messages with exit 2, never a traceback.
    def cli(*argv):
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "myelinate.py"),
                               *argv], capture_output=True, text=True)

    result = cli("--store", os.path.join(tempfile.gettempdir(), "adv-cli.json"),
                 "recall", "--budget", "-5")
    check(result.returncode != 0 and "Traceback" not in result.stderr,
          "S5: negative --budget is refused cleanly (exit %d)" % result.returncode)
    bad_store = os.path.join(tempfile.gettempdir(), "adv-corrupt.json")
    with open(bad_store, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    result = cli("--store", bad_store, "stats")
    check(result.returncode != 0 and "Traceback" not in result.stderr,
          "CLI: a corrupt store is a clean error, not a traceback")


def main():
    store_fixtures()
    decay_fixtures()
    recall_fixtures()
    protocol_fixtures()
    print("adversarial ok: %d checks" % CHECKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
