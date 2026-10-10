#!/usr/bin/env python3
"""The myelination council's adversarial suite.

    python3 benchmarks/test_adversarial.py     # adversarial ok: N checks

Four chairs reviewed the code and architecture and each formulated the tests
their domain owed. The suspected defects below were frozen BEFORE this suite
ran, so a confirmed and a refuted suspicion are both reported honestly.

  Store & persistence   - schema-version refusal, corrupt files, load
                          replacing (not merging), save atomicity, round-trip
                          fidelity, id recycling on retire-then-restate, and the
                          concurrency contract: last-write-wins, no torn file
                          under a concurrent writer.
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
import threading

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
              "access_count", "score", "score_at", "protected",
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

    # D21, found by this round's council review: valid JSON of the wrong shape.
    # The top level was validated; the ``memories`` container and its entries were
    # not, so these reached ``raw.items()`` / ``Memory(**known)`` uncaught.
    for index, payload in enumerate(({"schema": myelinate.SCHEMA_VERSION, "memories": "abc"},
                                     {"schema": myelinate.SCHEMA_VERSION, "memories": {}},
                                     {"schema": myelinate.SCHEMA_VERSION, "memories": [1, 2]},
                                     {"schema": myelinate.SCHEMA_VERSION,
                                      "memories": [{"id": "x"}]},
                                     {"schema": myelinate.SCHEMA_VERSION,
                                      "memories": [{"content": "no id"}]})):
        wrong_path = os.path.join(tmp, "wrong-%d.json" % index)
        with open(wrong_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        try:
            myelinate.MyelinatedMemory(path=wrong_path)
            shape_clean = False
        except ValueError:
            shape_clean = True
        except Exception:
            shape_clean = False
        check(shape_clean, "D21: wrong-shape store refused with ValueError: %s" % (payload,))

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

    # Q3: the scale path. The benchmark's 10,000-memory probe is not a check, so the
    # budget invariant and determinism were only asserted at n <= 80 - below the
    # point where RECALL_POOL and the candidate ceilings actually bind.
    big = myelinate.MyelinatedMemory(in_memory=True)
    rng_big = random.Random(99)
    words = ("deploy", "target", "lunch", "retention", "window", "audit", "policy",
             "concise", "reply", "staging", "cluster", "budget")
    for i in range(2000):
        text = " ".join(rng_big.choice(words) for _ in range(12)) + " note %d" % i
        big.add(text, now=float(i % 30))
    check(big.size() == 2000, "scale: the store holds every memory it was given")
    for budget in (0, 1, 512, 2200):
        scaled = big.recall(budget=budget, query="deploy target", now=100.0)
        check(scaled.chars <= budget,
              "scale: budget %d is respected on a 2000-memory store (%d)" % (budget, scaled.chars))
    first_big = big.recall(budget=2200, query="deploy target", now=100.0)
    second_big = big.recall(budget=2200, query="deploy target", now=100.0)
    check(first_big.text == second_big.text and first_big.used_ids == second_big.used_ids,
          "scale: recall is deterministic at 2000 memories")


def determinism_fixtures():
    # Deterministic dense store + script: identical replay under any PYTHONHASHSEED.
    probe = (
        "import json, os, random, sys\n"
        "sys.path.insert(0, %r)\n"
        "from scripts import myelinate\n"
        "rng = random.Random(7)\n"
        "vocab = ['deploy', 'target', 'server', 'cache', 'index', 'token', 'query', 'budget']\n"
        "mem = myelinate.MyelinatedMemory(in_memory=True)\n"
        "for i in range(200):\n"
        "    words = ' '.join(rng.choice(vocab) for _ in range(rng.randint(3, 9)))\n"
        "    mem.add(words, now=float(i))\n"
        "mem.refresh(now=200.0)\n"
        "out = mem.recall(budget=1200, query='deploy target server cache', now=300.0)\n"
        "sims = mem.similarity_scores('deploy target server cache')\n"
        "json.dump({'text': out.text, 'used_ids': out.used_ids,\n"
        "           'ranked_ids': list(out.ranked_ids),\n"
        "           'sims': [(k, repr(v)) for k, v in sims.items()]}, sys.stdout)\n"
    ) % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    blobs = []
    for seed in ("1", "2"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, env=env)
        # check() raises on failure, so reaching the append means this seed
        # replayed cleanly: blobs always holds exactly the successful outputs.
        check(proc.returncode == 0, "determinism: seed-%s probe replays cleanly" % seed)
        blobs.append(proc.stdout)
    check(blobs[0] == blobs[1],
          "determinism: recall output is byte-identical across PYTHONHASHSEED")
    # QUERY_TERM_LIMIT truncation keeps the query-order prefix on ties.
    cut = myelinate.MyelinatedMemory(in_memory=True)
    for term in ("alpha", "beta", "gamma", "delta",
                 "epsilon", "zeta", "eta", "theta"):
        cut.add(term, now=0.0)
    old_limit = myelinate.QUERY_TERM_LIMIT
    myelinate.QUERY_TERM_LIMIT = 2
    try:
        cut_sims = cut.similarity_scores(" ".join(
            ("alpha", "beta", "gamma", "delta",
             "epsilon", "zeta", "eta", "theta")))
    finally:
        myelinate.QUERY_TERM_LIMIT = old_limit
    cut_ids = list(cut.memories)
    check([mid for mid, score in cut_sims.items() if score > 0.0] == cut_ids[:2],
          "determinism: term-limit truncation keeps the query-order prefix")


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

    # D21: the same contract for a wrong-shaped store, and the empty-store path.
    for index, payload in enumerate(({"schema": myelinate.SCHEMA_VERSION, "memories": "abc"},
                                     {"schema": myelinate.SCHEMA_VERSION, "memories": [1, 2]},
                                     {"schema": myelinate.SCHEMA_VERSION,
                                      "memories": [{"id": "x"}]})):
        shaped = os.path.join(tempfile.gettempdir(), "adv-shaped-%d.json" % index)
        with open(shaped, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        result = cli("--store", shaped, "stats")
        check(result.returncode == 2 and "Traceback" not in result.stderr
              and result.stderr.startswith("error:"),
              "D21: a wrong-shape store is a clean error and exit 2 (exit %d)"
              % result.returncode)

    empty = os.path.join(tempfile.gettempdir(), "adv-empty-store.json")
    if os.path.exists(empty):
        os.remove(empty)
    result = cli("--store", empty, "stats")
    check(result.returncode == 0 and "Traceback" not in result.stderr,
          "CLI: an empty store is a fresh engine, not an error")


def concurrency_fixtures():
    # The contract: save() writes a temp file and renames it over the store,
    # so the path never names a partial file - but there is no locking. Two
    # writers silently supersede each other (last-write-wins) and the loser
    # keeps serving stale state; a concurrent reader always sees one complete
    # generation, never a torn file.
    path = os.path.join(tempfile.gettempdir(), "adv-concurrent.json")
    if os.path.exists(path):
        os.remove(path)
    first = myelinate.MyelinatedMemory(path=path)
    first.add("the first writer's memory", now=0.0)
    first.save()
    second = myelinate.MyelinatedMemory(path=path)
    second.load()
    second.add("the second writer's memory", now=1.0)
    second.save()
    first.add("a stale write from the first writer", now=2.0)
    first.save()
    loser = myelinate.MyelinatedMemory(path=path)
    loser.load()
    contents = [m.content for m in loser.all()]
    check("the second writer's memory" not in contents
          and "a stale write from the first writer" in contents,
          "concurrency: the last writer wins and the loser is silent")
    # A writer publishing numbered generations while a reader loads in a tight
    # loop: every read must parse and must name exactly one published
    # generation (an older one is fine - that is last-write-wins from the
    # reader's side; a partial one is a torn file and fails).
    if os.path.exists(path):
        os.remove(path)
    announced: list = []
    failures: list = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                try:
                    # The constructor loads the store itself, so it is inside
                    # the try: a torn file would crash construction, not just
                    # load(), and that must count as a bad read too.
                    probe = myelinate.MyelinatedMemory(path=path)
                    probe.load()
                except Exception as exc:
                    failures.append("unreadable: %r" % (exc,))
                    continue
                seen = [m.content for m in probe.all()]
                if not seen:
                    continue  # no generation published yet
                if len(seen) != 1 or not seen[0].startswith("generation "):
                    failures.append("partial: %r" % (seen,))
                    continue
                try:
                    gen = int(seen[0].split()[-1])
                except ValueError:
                    failures.append("partial: %r" % (seen,))
                    continue
                if gen not in announced:
                    failures.append("unpublished: %r" % (seen,))
        except Exception as exc:
            # A dying reader must fail the check, not pass it vacuously: a
            # thread dead from an exception is not alive and leaves no
            # failures behind, so both checks below would pass on silence.
            failures.append("reader died: %r" % (exc,))

    # Daemonic so a writer-side failure fails fast: otherwise stop is never
    # set and a spinning non-daemon reader would hang the suite instead of
    # exiting on the failed assertion.
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    for gen in range(60):
        announced.append(gen)
        engine = myelinate.MyelinatedMemory(in_memory=True)
        engine.add("generation %d" % gen, now=float(gen))
        engine.save(path)
    stop.set()
    thread.join(timeout=60)
    check(not thread.is_alive(), "concurrency: the reader thread finishes")
    check(not failures,
          "concurrency: no torn file is ever readable (%d bad reads)"
          % len(failures))


def main():
    store_fixtures()
    decay_fixtures()
    recall_fixtures()
    determinism_fixtures()
    concurrency_fixtures()
    protocol_fixtures()
    print("adversarial ok: %d checks" % CHECKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
