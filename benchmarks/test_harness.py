#!/usr/bin/env python3
"""W0.9/W0.10: fixtures for the parts of the harness that measured everything
except themselves.

Five things are pinned here, all of which the README listed as "still
unverified" until this file existed:

1. **The metric definitions** are checked against hand-computed values, so
   `chars_per_hit`, nDCG and friends cannot drift without a red suite.
2. **The verdict** (`run_bench.verdict`) is checked with fixture inputs: the
   pre-registered rule rows must decide from the numbers, unmeasured inputs must
   read NOT MEASURED instead of scoring a phantom FAIL (D4), the informational
   row must stay out of the denominator (D2), and the significance row must
   require BOTH flat baselines (D6).
3. **Replay determinism**: the same scenario against the same arm twice must
   produce identical records apart from latency, which the report already
   disclaims.
4. **The budget invariant** holds across every offline arm, not just the
   engine: `result.chars <= budget` on every query at several budgets.
5. **The report renderer and the D15 path guards**: the renderer produces the
   report from a payload, and a scoped run cannot write the committed
   `RESULTS.md`/`results/raw.json` — including via a *relative* spelling of the
   results directory, the exact regression D15 was about.

The LoCoMo conversion is validated when its (git-ignored) data cache is
present, and its absence is itself checked to be a clean state so CI, which has
no cache, still runs a real assertion.

    python3 benchmarks/test_harness.py     # harness ok: N checks
"""

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.chdir(ROOT)  # the D15 relative-path checks are only meaningful from the root

import common  # noqa: E402
import engines  # noqa: E402
import metrics  # noqa: E402
import public_locomo  # noqa: E402
import run_bench  # noqa: E402

CHECKS = 0


def check(condition, name):
    global CHECKS
    assert condition, name
    CHECKS += 1


def approx(value, expected, tol=1e-9):
    return value is not None and abs(value - expected) <= tol


# --------------------------------------------------------------- 1. metrics
def metric_fixtures():
    rec = {"evidence_ids": ["e1", "e2"], "used_ids": ["x", "e1", "y", "e2"],
           "ranked_ids": ["x", "e1", "y", "e2"]}
    check(metrics.hit(rec) == 1.0, "hit: evidence present -> 1.0")
    check(metrics.hit({"evidence_ids": ["e1"], "used_ids": ["x"]}) == 0.0, "hit: miss -> 0.0")
    check(metrics.hit({"evidence_ids": ["e1"], "used_ids": []}) == 0.0, "hit: empty context -> 0.0")

    # Reciprocal rank: the first evidence id sits at position 2 -> 1/2.
    check(approx(metrics.reciprocal_rank(rec), 0.5), "rr: position 2 -> 1/2")
    check(metrics.reciprocal_rank({"evidence_ids": ["e1"], "ranked_ids": ["x", "y"]}) == 0.0,
          "rr: evidence absent -> 0.0")
    # D8: the ranked order and the packed order are scored separately. Here the
    # arm ranked e1 second but the allocator put it first; both orders must move.
    two = {"evidence_ids": ["e1"], "used_ids": ["e1"], "ranked_ids": ["x", "e1"]}
    check(approx(metrics.reciprocal_rank(two), 0.5) and
          approx(metrics.reciprocal_rank(two, "packed"), 1.0),
          "rr: ranked vs packed score separately (D8)")

    check(approx(metrics.evidence_precision(rec), 0.5), "precision: 2 of 4 -> 0.5")
    check(metrics.evidence_precision({"used_ids": []}) is None, "precision: empty context -> None")

    # nDCG pinned to its closed form AND to the literal, so the formula cannot
    # drift silently: rel order [0, 1, 0, 1] over [x, e1, y, e2].
    #   dcg   = 1/log2(3) + 1/log2(5) = 0.6309297536 + 0.4306765581 = 1.0616063117
    #   ideal = 1/log2(2) + 1/log2(3) = 1.6309297536
    closed = (1.0 / math.log2(3) + 1.0 / math.log2(5)) / (1.0 / math.log2(2) + 1.0 / math.log2(3))
    value = metrics.ndcg(rec)
    check(approx(value, closed, 1e-12), "ndcg matches its closed form")
    check(approx(value, 0.6509217, 1e-6), "ndcg pinned to 0.6509217")
    check(metrics.ndcg(rec, k=1) == 0.0, "ndcg: k truncation drops the evidence")
    check(metrics.ndcg({"evidence_ids": [], "ranked_ids": ["x"]}) is None,
          "ndcg: no evidence -> None")

    check(approx(metrics.stale_leak({"stale_ids": ["s1", "s2"], "used_ids": ["s2", "x"]}), 0.5),
          "stale_leak: 1 of 2 -> 0.5")
    check(metrics.stale_leak({"stale_ids": [], "used_ids": ["x"]}) is None,
          "stale_leak: no stale ids -> None")

    check(approx(metrics._percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5), "percentile: median of 4")
    check(approx(metrics._percentile([7.0], 0.95), 7.0), "percentile: single sample")
    check(metrics._percentile([], 0.5) == 0.0, "percentile: empty -> 0.0")

    # summarize() on two hand-built records:
    #   r1 hits at 100 chars of 200, judged 1.0   r2 misses at 300 chars, judged 0.0
    #   hit_rate 0.5, task_success 0.5, mean_chars 200,
    #   chars_per_hit = mean(chars)/mean(hits) = 200/0.5 = 400,
    #   budget_utilisation = mean(0.5, 1.5) = 1.0
    r1 = {"evidence_ids": ["e1"], "used_ids": ["e1"], "ranked_ids": ["e1"],
          "chars": 100, "budget": 200, "judge_state": "judged", "judge_score": 1.0,
          "latency_ms": 2.0, "cold": False}
    r2 = {"evidence_ids": ["e2"], "used_ids": ["z"], "ranked_ids": ["z"],
          "chars": 300, "budget": 200, "judge_state": "judged", "judge_score": 0.0,
          "latency_ms": 4.0, "cold": True}
    summary = metrics.summarize([r1, r2])
    check(approx(summary["hit_rate"], 0.5), "summarize: hit_rate 0.5")
    check(approx(summary["task_success"], 0.5), "summarize: task_success 0.5")
    check(approx(summary["chars_per_hit"], 400.0), "summarize: chars_per_hit 400")
    check(approx(summary["budget_utilisation"], 1.0), "summarize: budget_utilisation 1.0")
    check(approx(summary["cold_ms"], 4.0) and approx(summary["mean_latency_ms"], 2.0),
          "summarize: cold excluded from warm latency")

    # An unjudged record must not average in as a zero (D5/D16).
    r3 = dict(r2, judge_state="skipped", judge_score=None, evidence_ids=["e3"],
              used_ids=["e3"], ranked_ids=["e3"], chars=100)
    summary = metrics.summarize([r1, r3])
    check(approx(summary["task_success"], 1.0), "summarize: skipped record leaves task_success")
    check(summary["judge_skipped"] == 1.0, "summarize: skipped count reported")

    miss = {"evidence_ids": ["e1"], "used_ids": ["z"], "ranked_ids": ["z"],
            "chars": 50, "budget": 100, "judge_state": "judged", "judge_score": 0.0}
    summary = metrics.summarize([miss])
    check(summary["chars_per_hit"] == float("inf"), "summarize: no hits -> inf, never 0")


# --------------------------------------------------------------- 2. verdict
def _row(chars_per_hit, hit_rate):
    """A full summary row: verdict() reads two keys, render_report reads more."""
    return {"chars_per_hit": chars_per_hit, "hit_rate": hit_rate, "ndcg@10": hit_rate / 2.0,
            "ndcg@10_packed": hit_rate / 2.0, "mrr": hit_rate, "mrr_packed": hit_rate,
            "task_success": hit_rate / 2.0, "mean_chars": 1000.0,
            "evidence_precision": hit_rate / 4.0, "stale_leak_rate": 0.0}


def _summaries(**overrides):
    base = {
        run_bench.HERO_ARM: _row(1000.0, 0.90),
        run_bench.PURE_ARM: _row(1900.0, 0.60),
        "M1 flat/FIFO": _row(1500.0, 0.50),
        "M2 recency/LRU": _row(2000.0, 0.40),
        "M3 semantic/BM25": _row(1300.0, 0.88),
        "M11 myelinated +lexical ranking": _row(900.0, 0.92),
    }
    base.update(overrides)
    return base


def _pair(arm, diff, significant=True, p=0.001):
    """A full paired-comparison row: render_report reads the CI columns too."""
    return {"arm": arm, "diff": diff, "significant": significant, "p_adjusted": p,
            "p_value": p, "arm_mean": 0.6, "base_mean": 0.6 - diff,
            "mean_a": 0.6, "mean_b": 0.6 - diff, "mean_diff": diff,
            "ci_low": diff - 0.05, "ci_high": diff + 0.05, "n": 20, "delta": 1.0}


def verdict_fixtures():
    by_source = {
        run_bench.HERO_ARM: {
            run_bench.TIER_LABELS["staleness"]: {"stale_leak_rate": 0.0},
            run_bench.TIER_LABELS["staleness-decay"]: {"stale_leak_rate": 0.0},
        },
        "M1 flat/FIFO": {run_bench.TIER_LABELS["staleness"]: {"stale_leak_rate": 0.0}},
    }
    scale = [{"arm": run_bench.HERO_ARM, "recall_p95_ms": 10.0},
             {"arm": "M3 semantic/BM25", "recall_p95_ms": 30.0}]
    pairs = [_pair("M1 flat/FIFO", 0.1), _pair("M2 recency/LRU", 0.2)]

    result = run_bench.verdict(_summaries(), by_source, scale, pairs)
    statuses = [row[1] for row in result["criteria"]]
    check(statuses[:5] == ["PASS"] * 5, "verdict: all five scored rows pass on clean numbers")
    check(statuses[5] == "INFORMATIONAL", "verdict: scale row is informational")
    check(statuses[6] == "PASS", "verdict: both flat arms significant -> PASS")
    check(result["passed"] == 6 and result["measured"] == 6, "verdict: 6 of 6 scored")
    check(result["informational"] == 1 and result["not_measured"] == 0, "verdict: 1 informational")
    check(result["criteria_version"] == run_bench.CRITERIA_VERSION, "verdict: version pinned")
    check(result["hero"] == run_bench.HERO_ARM, "verdict: hero is pre-registered, not the winner")
    check(result["best_engine"] == "M11 myelinated +lexical ranking",
          "verdict: best engine derived from ARM_ORDER, not hardcoded")
    check(result["best_engine_needs_remediation"] is False, "verdict: remediation flag consistent")

    # D4: inputs this run never produced read NOT MEASURED, never a phantom FAIL.
    result = run_bench.verdict(_summaries(), {}, [], [])
    statuses = [row[1] for row in result["criteria"]]
    check(statuses == ["PASS", "PASS", "PASS", "NOT MEASURED", "NOT MEASURED",
                       "NOT MEASURED", "NOT MEASURED"],
          "verdict: unmeasured inputs -> NOT MEASURED (D4)")
    check(result["measured"] == 3 and result["not_measured"] == 4, "verdict: NOT MEASURED is out of the denominator")

    # D6: ONE significant flat baseline is not enough.
    half = [_pair("M1 flat/FIFO", 0.1), _pair("M2 recency/LRU", 0.01, significant=False, p=0.5)]
    result = run_bench.verdict(_summaries(), by_source, scale, half)
    check(result["criteria"][6][1] == "FAIL", "verdict: one flat arm is not BOTH (D6)")

    # D2: a criterion whose sign flips stays informational even when it "wins".
    scale_lose = [{"arm": run_bench.HERO_ARM, "recall_p95_ms": 30.0},
                  {"arm": "M3 semantic/BM25", "recall_p95_ms": 10.0}]
    result = run_bench.verdict(_summaries(), by_source, scale_lose, pairs)
    check(result["criteria"][5][1] == "INFORMATIONAL", "verdict: scale row never scores (D2)")
    check(result["measured"] == 6, "verdict: informational row excluded from the denominator")


# --------------------------------------------------- 3. replay determinism
def replay_determinism():
    arm = next(a for a in engines.build_arms() if a.name == "M11 myelinated +lexical ranking")
    scenario = _probe_scenario()
    first = common.replay(scenario, arm, budget=400)
    second = common.replay(scenario, arm, budget=400)
    check(len(first) == len(second) == len(scenario.queries), "replay: one record per query")

    def fingerprint(records):
        stripped = []
        for record in records:
            row = {k: v for k, v in record.items() if k not in ("latency_ms", "cold_ms")}
            stripped.append(row)
        return json.dumps(stripped, sort_keys=True, default=str)

    check(fingerprint(first) == fingerprint(second),
          "replay: identical records apart from wall-clock latency")


# --------------------------------------------- 4. budget invariant, all arms
def budget_invariant():
    scenario = _probe_scenario()
    arms = engines.build_arms()
    # Derived, not hardcoded: a new arm must be visible here the moment it is
    # added to ARM_ORDER (the same rule the engines self-check applies).
    offline = [n for n in engines.ARM_ORDER if n != engines.DenseEmbeddingArm.name]
    check([a.name for a in arms] == offline, "build_arms() matches ARM_ORDER")
    for budget in (50, 500, 2200):
        for arm in arms:
            for record in common.replay(scenario, arm, budget=budget):
                check(record["chars"] <= budget,
                      "%s exceeds budget %d (%d)" % (arm.name, budget, record["chars"]))


def r12_wiring():
    """The R12 pair must differ in exactly one switch."""
    arms = {a.name: a for a in engines.build_arms()}
    m9 = arms["M9 myelinated +supersession"]
    m13 = arms["M13 myelinated +supersession (auto-update off)"]
    check(m9.use_auto_supersede and not m13.use_auto_supersede,
          "R12: M13 is M9 with auto-update off")
    check(m13.use_similarity == m9.use_similarity and m13.use_knapsack == m9.use_knapsack
          and m13.stale_retirement == m9.stale_retirement
          and m13.reinforcement == m9.reinforcement
          and m13.prior_weight == m9.prior_weight,
          "R12: the pair differs in no other knob")
    check(m13.engine.auto_supersede is False and m9.engine.auto_supersede is True,
          "R12: the switch reaches the engine")


# ------------------------------------------------- 5. LoCoMo conversion gate
def locomo_conversion():
    if not public_locomo.available():
        # CI has no git-ignored cache: absence must still be a clean state, and
        # the loader must fail with a message, not a traceback.
        check(not public_locomo.available(), "locomo: absent cache reports itself")
        try:
            public_locomo.load_locomo()
            raised = False
        except RuntimeError:
            raised = True
        check(raised, "locomo: missing cache raises RuntimeError, not a crash")
        return
    scenarios = public_locomo.load_locomo(limit=3, max_queries_per_conversation=20)
    problems = public_locomo.validate(scenarios)
    check(problems == [], "locomo: conversion validates clean, got %r" % problems[:3])
    ids = {e.id for s in scenarios for e in s.events}
    check(all(q.evidence_ids and set(q.evidence_ids) <= ids
              for s in scenarios for q in s.queries),
          "locomo: every query's evidence exists in the event stream")


# --------------------------------- 6. renderer smoke + D15 output-path guards
def _payload():
    summaries = _summaries()
    by_source = {
        run_bench.HERO_ARM: {
            run_bench.TIER_LABELS["staleness"]: {"stale_leak_rate": 0.0, "hit_rate": 1.0},
        },
    }
    pairs = [_pair("M1 flat/FIFO", 0.1), _pair("M2 recency/LRU", 0.2)]
    scale_rows = [{"arm": run_bench.HERO_ARM, "stored": 10, "ingest_s": 0.1,
                   "refresh_total_s": 0.0, "recall_p50_ms": 1.0, "recall_p95_ms": 10.0,
                   "recall_p99_ms": 11.0, "cold_ms": 2.0, "passes": 3},
                  {"arm": "M3 semantic/BM25", "stored": 10, "ingest_s": 0.1,
                   "refresh_total_s": 0.0, "recall_p50_ms": 1.0, "recall_p95_ms": 30.0,
                   "recall_p99_ms": 31.0, "cold_ms": 2.0, "passes": 3}]
    return {
        "generated": "2026-01-01T00:00:00", "command": "python3 benchmarks/run_bench.py",
        "phases": None, "python": "3.11.0", "judge": "offline oracle", "judge_mode": "oracle",
        "judge_is_llm": False, "judge_calls": 0,
        "judge_ledger": {"judged": 8, "skipped": 0, "errored": 0}, "judge_counters": {},
        "criteria_version": run_bench.CRITERIA_VERSION, "budget": 2200, "seeds": [0],
        "tiers": ["curated", "synthetic", "staleness", "locomo"], "scenario_count": 4,
        "query_count": 8, "arm_order": list(summaries), "retirement_arms": [],
        "summaries": summaries,
        "by_kind": {}, "by_source": by_source, "pairs": pairs,
        "scale": {"rows": scale_rows, "memories": 10, "passes": 3, "days": 60},
        "verdict": run_bench.verdict(summaries, by_source, scale_rows, pairs),
        "notes": run_bench.build_notes(False), "records_by_arm": {},
        "raw_path": "benchmarks/results/raw.json",
    }


def renderer_and_paths():
    report = run_bench.render_report(_payload())
    check("Myelinated Memory - Benchmark Results" in report, "renderer: header present")
    check("python3 benchmarks/run_bench.py" in report, "renderer: reproducing command present")
    for name in ("M1 flat/FIFO", "M3 semantic/BM25", run_bench.HERO_ARM):
        check(name in report, "renderer: arm row present: %s" % name)
    check("INFORMATIONAL" in report, "renderer: informational row labelled")

    def args(tier="all", skip_scale=False, out=run_bench.RESULTS_DIR, report_path=None):
        return argparse.Namespace(tier=tier, skip_scale=skip_scale, out=out, report=report_path)

    # The full default run owns the committed artifacts; nothing else may.
    check(run_bench.report_path_for(args()).endswith("RESULTS.md"),
          "D15: only the full default run writes RESULTS.md")
    check(run_bench.raw_path_for(args()).endswith(os.path.join("results", "raw.json")),
          "D15: only the full default run writes results/raw.json")
    check(run_bench.report_path_for(args(tier="locomo", skip_scale=True)).endswith("RESULTS-locomo.md"),
          "D15: a scoped run writes its own report")
    check(run_bench.raw_path_for(args(tier="locomo", skip_scale=True)).endswith("raw-locomo.json"),
          "D15: a scoped run writes its own raw file")
    # The actual D15 regression: a relative spelling of the committed directory
    # used to count as "somewhere else" and let a scoped run overwrite raw.json.
    relative = args(tier="locomo", skip_scale=True, out=os.path.join("benchmarks", "results"))
    check(run_bench.raw_path_for(relative) != run_bench.raw_path_for(args()),
          "D15: a relative spelling of the results dir cannot overwrite raw.json")
    # A genuinely different scratch directory is still honoured as typed.
    scratch = args(tier="curated", skip_scale=True, out="/tmp/myelinated-scratch")
    check(run_bench.raw_path_for(scratch) == os.path.join("/tmp/myelinated-scratch", "raw.json"),
          "D15: an explicit scratch --out is honoured")
    check(run_bench.report_path_for(args(tier="curated", report_path="x/OUT.md")).endswith("OUT.md"),
          "D15: an explicit --report path is honoured")


# ------------------------------- 6. scale-probe resume (defect D22)
def scale_resume():
    """An interrupted arm's row must equal an uninterrupted one.

    The scale probe can be cut off by a command timeout in the middle of an arm
    whose row alone outlasts the cap (defect D22); the in-flight ingest state is
    checkpointed and a later pass resumes it. Resuming must lose nothing: the
    completed row is the one the same corpus would have produced in one pass
    (timing fields excepted - those are wall clock), and the arm's final store
    is identical, which is what the restored consolidation set decides.
    """
    import tempfile

    def measure(n, days, **kwargs):
        arm = engines.MyelinatedArm("T myelinated", similarity=True, knapsack=True,
                                    stale_retirement=True)
        rows = run_bench.scale_rows([arm], n, days, 0, **kwargs)
        return rows, arm

    rows_a, arm_a = measure(36, 3)
    check(len(rows_a) == 1, "scale resume: the uninterrupted pass yields one row")

    with tempfile.TemporaryDirectory() as tmp:
        checkpoint = os.path.join(tmp, "inflight")
        deferred, _ = measure(36, 3, checkpoint_path=checkpoint, pass_budget_s=0.0,
                              checkpoint_seconds=0.0)
        check(deferred == [], "scale resume: a pass over budget defers the row")
        check(os.path.exists(checkpoint), "scale resume: a deferred arm leaves a checkpoint")
        rows_b, arm_b = measure(36, 3, checkpoint_path=checkpoint)
        check(len(rows_b) == 1, "scale resume: the next pass completes the row")
        check(not os.path.exists(checkpoint),
              "scale resume: a completed row clears its checkpoint")

        # An arm without snapshot support keeps the old atomic contract and
        # never touches a checkpoint file.
        flat_path = os.path.join(tmp, "flat")
        flat_rows = run_bench.scale_rows([engines.FlatFifoArm()], 8, 2, 0,
                                         checkpoint_path=flat_path, pass_budget_s=0.0)
        check(len(flat_rows) == 1,
              "scale resume: an arm without snapshot support measures atomically")
        check(not os.path.exists(flat_path),
              "scale resume: an atomic arm writes no checkpoint")

        # A checkpoint from a different corpus is refused, never replayed.
        measure(36, 3, checkpoint_path=checkpoint, pass_budget_s=0.0, checkpoint_seconds=0.0)
        try:
            measure(8, 2, checkpoint_path=checkpoint)
        except ValueError:
            check(True, "scale resume: a checkpoint from another corpus is refused")
        else:
            check(False, "scale resume: a checkpoint from another corpus is refused")

    timing = {"ingest_s", "refresh_total_s", "recall_p50_ms", "recall_p95_ms",
              "recall_total_s", "recall_p95_passes_ms"}
    stable_a = {k: v for k, v in rows_a[0].items() if k not in timing}
    stable_b = {k: v for k, v in rows_b[0].items() if k not in timing}
    check(stable_a == stable_b, "scale resume: the resumed row equals the single-pass row")
    check(arm_a.engine.to_dict() == arm_b.engine.to_dict(),
          "scale resume: the resumed store is identical, not merely close")
    query = "what did memory number 0 say?"
    recall_a = arm_a.recall(query, budget=500, now=2.0)
    recall_b = arm_b.recall(query, budget=500, now=2.0)

    def deterministic(result):
        return (result.text, result.used_ids, result.ranked_ids, result.chars, result.budget)

    check(deterministic(recall_a) == deterministic(recall_b),
          "scale resume: recall from a resumed arm is identical")


# ------------------------------------------------------------------- fixture
def _probe_scenario():
    """A tiny scenario long enough that the budget binds at every test size."""
    events = []
    for i in range(8):
        events.append(common.Event(op="add", day=0.0, id="m%d" % i, category="general",
                                   content="memory number %d about the %s deployment target "
                                           "and the quarterly budget review" % (i, "staging")))
    events.append(common.Event(op="retire", day=1.0, id="m0"))
    events.append(common.Event(op="add", day=1.0, id="m9", supersedes="m0",
                               content="memory number 0 about the production deployment "
                                       "target and the quarterly budget review"))
    queries = [
        common.Query(id="q1", query="what is the deployment target?", answer="production",
                     evidence_ids=["m9"]),
        common.Query(id="q2", query="tell me about the budget review", answer="quarterly",
                     evidence_ids=["m3", "m5"]),
        common.Query(id="q3", query="what did memory number 0 say?", answer="staging",
                     evidence_ids=["m9"], stale_ids=["m0"]),
    ]
    return common.Scenario(id="probe", kind="lookup", description="harness fixture probe",
                           events=events, queries=queries)


def main():
    metric_fixtures()
    verdict_fixtures()
    replay_determinism()
    budget_invariant()
    r12_wiring()
    locomo_conversion()
    renderer_and_paths()
    scale_resume()
    print("harness ok: %d checks" % CHECKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
