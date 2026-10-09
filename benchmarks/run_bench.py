#!/usr/bin/env python3
"""End-to-end benchmark for the Myelinated Memory engine.

    python3 benchmarks/run_bench.py                      # offline, oracle judge
    python3 benchmarks/run_bench.py --judge openai       # LLM-judged end to end
    python3 benchmarks/run_bench.py --tier staleness     # only the staleness suites

What it does
------------
1. Loads scenarios from the curated suite, the seeded synthetic generator, the
   two staleness suites and the public LoCoMo benchmark.
2. Replays each scenario against every memory arm with the same virtual clock
   and the same character budget.
3. Judges every answer *inside the timeline*, so an arm that consumes the
   utility-credit signal can learn within the scenario instead of after it.
4. Reports retrieval, context, staleness, latency and task-success metrics,
   with paired significance tests against the recommended configuration.
5. Writes ``benchmarks/results/raw.json`` and ``benchmarks/RESULTS.md``.

The pre-registered decision rule lives in ``verdict()``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pickle
import platform
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import judge as judge_mod
import metrics
import stats
from common import DEFAULT_BUDGET, Scenario, day, replay
from engines import ARM_ORDER, build_arms

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(ROOT, "results")
SCALE_MEMORIES = 10000
SCALE_DAYS = 60
# The scale probe times only a handful of queries, so a single pass makes the
# reported p95 the maximum of that handful and lets two identical runs disagree.
# The timed recall pass is therefore repeated and the reported percentiles are
# the median of the per-pass values (defect D2 reaching the decision rule).
SCALE_TIMED_PASSES = 3
# How often an in-flight scale arm is checkpointed (defect D22). Resilience,
# not measurement: checkpoint writes happen outside the ingest timing, so this
# cadence cannot move a number.
CHECKPOINT_SECONDS = 20.0

# The pre-registered hero: the round-4 recommended configuration, with every
# remediation applied *at the time it was registered*. It is fixed and is never
# swapped for whichever arm wins (BM-004); re-registering it is a decision-rule
# change that needs a criteria-version bump, not an edit. Note that since round 5
# the engine's shipped default is M11's configuration (PRIOR_WEIGHT is 0.0 while
# M10 pins LEGACY_PRIOR_WEIGHT), so hero and shipped arm differ by that one
# constant and the report prints both rather than quietly equating them.
HERO_ARM = "M10 myelinated +reinforcement"
# The specification's behaviour, kept for attribution.
PURE_ARM = "M6 myelinated (pure)"
SEMANTIC_ARMS = ("M3 semantic/BM25", "M4 semantic/TF-IDF", "M5 semantic/dense-embeddings")
FLAT_ARMS = ("M1 flat/FIFO", "M2 recency/LRU")
# Allocator controls, NOT baselines. They share BM25's ranking and vary only how
# the budget is filled, so they separate the engine's allocator from the
# engine's store (docs/ROUND4-DESIGN.md 4). They must never be mistaken for
# "the best baseline" the engine is measured against.
CONTROL_ARMS = ("M3t BM25 +truncated text", "M3p BM25 +engine ladder",
                "M3k BM25 +engine packer")
# The decision-rule text is versioned, and the version is written into the
# report, so a criterion that changed after a run is visible instead of
# silently rewritten (BM-004).
CRITERIA_VERSION = 3

# Tier labels reported separately. BM-003: a supersession win must never be
# reported as a decay win, so the two staleness suites never share a label.
TIER_LABELS = {
    "curated": "curated",
    "synthetic": "synthetic",
    "staleness": "staleness (supersession)",
    "staleness-decay": "staleness (decay only)",
    "staleness-update": "staleness (update)",
    "locomo": "locomo (public)",
}


# --------------------------------------------------------------------- tiers
def load_scenarios(tiers: Sequence[str], locomo_limit: int, locomo_queries: int,
                   seed: int) -> List[Scenario]:
    scenarios: List[Scenario] = []

    def label(group: List[Scenario], tier: str) -> None:
        for scenario in group:
            scenario.source = TIER_LABELS[tier]
        scenarios.extend(group)

    if "curated" in tiers:
        import scenarios as curated_mod

        label(curated_mod.curated(), "curated")
    if "synthetic" in tiers:
        import synthetic

        label(synthetic.generate(seed=seed), "synthetic")
    if "staleness" in tiers:
        import synthetic

        label(synthetic.staleness_suite(seed=seed), "staleness")
        label(synthetic.decay_only_staleness_suite(seed=seed), "staleness-decay")
        label(synthetic.update_supersession_suite(seed=seed), "staleness-update")
    if "locomo" in tiers:
        import public_locomo

        if public_locomo.available():
            label(public_locomo.load_locomo(limit=locomo_limit,
                                            max_queries_per_conversation=locomo_queries), "locomo")
        else:
            print("[warn] LoCoMo cache missing; skipping the public tier "
                  "(run benchmarks/public_locomo.py to download it)", file=sys.stderr)
    return scenarios


# ------------------------------------------------------------------- running
def evaluate(scenarios: Sequence[Scenario], arms: Sequence, judge, budget: int,
             judge_limit: int) -> Tuple[Dict[str, List[Dict]], Dict[str, Dict[str, int]]]:
    """Replay every scenario against every arm, judging inside the timeline.

    Every judged record carries an explicit ``judge_state``. A query the run
    never judged (a cost cap, or a call that failed) is *not* a wrong answer and
    must not be averaged in as one (defects D5/D16).
    """
    by_arm: Dict[str, List[Dict]] = {}
    ledgers: Dict[str, Dict[str, int]] = {}
    source_of = {scenario.id: scenario.source for scenario in scenarios}

    for arm in arms:
        records: List[Dict] = []
        counted = {"judged": 0, "skipped": 0, "errored": 0}

        def on_result(a, query, result, record, _counted=counted) -> None:
            if judge_limit and _counted["judged"] >= judge_limit:
                record["judge_state"] = "skipped"
                _counted["skipped"] += 1
                return
            try:
                model_answer = judge.answer(query.query, result.text)
                score = judge.grade(query.query, query.answer, model_answer,
                                    context=result.text,
                                    check=getattr(query, "answer_check", "contains"))
                record["model_answer"] = model_answer
                record["judge_score"] = float(score)
                record["judge_state"] = "judged"
                _counted["judged"] += 1
                # Utility-credit reinforcement (R2): reward the memories that
                # were in the context when the answer came out right. This uses
                # the judged outcome, never the ground-truth evidence, so it is
                # available to every arm rather than being oracle leakage.
                if score >= 1.0 and a.supports_reinforcement:
                    a.reinforce(result.used_ids, now=day(query.session))
            except judge_mod.JudgeError as exc:
                record["judge_error"] = str(exc)
                record["judge_state"] = "errored"
                _counted["errored"] += 1

        for scenario in scenarios:
            records.extend(replay(scenario, arm, budget=budget, on_result=on_result))
        for record in records:
            record["source"] = source_of.get(record["scenario"], "unknown")
        by_arm[arm.name] = records
        print("  %-34s %3d queries judged=%d skipped=%d errored=%d"
              % (arm.name, len(records), counted["judged"], counted["skipped"],
                 counted["errored"]), file=sys.stderr)
        ledgers[arm.name] = dict(counted)
    return by_arm, ledgers


def pairwise(by_arm: Dict[str, List[Dict]], baseline: str, key: str = "judge_score") -> List[Dict]:
    """Paired comparisons of every arm against ``baseline``, Holm-corrected."""
    base = by_arm.get(baseline, [])
    rows: List[Dict] = []
    pvalues: Dict[str, float] = {}
    for name, records in by_arm.items():
        if name == baseline:
            continue
        left, right = metrics.align(base, records, key)
        if len(left) < 2:
            continue
        summary = stats.paired_summary(left, right)
        pvalues[name] = summary["p_value"]
        rows.append({
            "arm": name,
            "metric": key,
            "n": summary["n"],
            "baseline_mean": summary["mean_a"],
            "arm_mean": summary["mean_b"],
            "diff": summary["mean_diff"],
            "ci_low": summary["ci_low"],
            "ci_high": summary["ci_high"],
            "win_rate": _win_rate(left, right),
            "delta": summary["delta"],
            "p_value": summary["p_value"],
        })
    corrected = stats.holm(pvalues) if pvalues else {}
    for row in rows:
        entry = corrected.get(row["arm"], {"adjusted": row["p_value"], "reject": False})
        row["p_adjusted"] = entry["adjusted"]
        row["significant"] = bool(entry["reject"])
    return rows


def _win_rate(baseline: Sequence[float], other: Sequence[float]) -> float:
    wins = sum(1 for a, b in zip(baseline, other) if a > b)
    losses = sum(1 for a, b in zip(baseline, other) if a < b)
    ties = len(baseline) - wins - losses
    return (wins + 0.5 * ties) / len(baseline) if baseline else 0.0


# --------------------------------------------------------------------- scale
def _write_checkpoint(path: str, arm, events_done: int, ingest_s: float,
                      last_day, corpus: Dict) -> None:
    """Persist an in-flight arm (defect D22). One pickled blob, written
    atomically, so a command cut off at any moment loses at most the chunk of
    events since the previous checkpoint. Pickle because a JSON store round
    trip would lose the engine's live consolidation set (``_touched``), which
    decides what the next ``refresh()`` merges - a resumed arm must be the same
    arm, not merely a close one. This is harness scratch state written and read
    only by the same kind of run; it is never a store interchange format.
    """
    payload = {
        "arm": arm.name,
        "corpus": dict(corpus),
        "events_done": events_done,
        "ingest_s": ingest_s,
        "refresh_ms": list(arm.refresh_ms),
        "last_day": last_day,
        "state": arm.snapshot_state(),
    }
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        pickle.dump(payload, handle)
    os.replace(tmp, path)


def _read_checkpoint(path: str, arm, corpus: Dict) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        payload = pickle.load(handle)
    if payload.get("arm") != arm.name or payload.get("corpus") != corpus:
        # A checkpoint from a different run is refused, never replayed: splicing
        # two runs into one artifact is the quiet incomparability the D15 output
        # guard exists to prevent.
        raise ValueError(
            "in-flight checkpoint %s belongs to arm %r on corpus %r, not %r on %r; "
            "delete it or replay with the original flags"
            % (path, payload.get("arm"), payload.get("corpus"), arm.name, corpus))
    return payload


def scale_rows(arms: Sequence, memories: int, days: int, seed: int,
               done: Optional[Dict[str, Dict]] = None,
               on_row: Optional[Callable[[Dict], None]] = None,
               checkpoint_path: Optional[str] = None,
               pass_budget_s: Optional[float] = None,
               checkpoint_seconds: float = CHECKPOINT_SECONDS) -> List[Dict]:
    """Latency at scale, one row per arm.

    ``done`` maps an arm name to a row already measured by an earlier bounded
    pass and is reused verbatim; ``on_row`` is called after each new row so a
    caller can persist it and resume after a command timeout (defect D20). Each
    row is an independent measurement of one arm, so resuming loses nothing, and
    the arms are measured in the same order with the same corpus either way.

    ``checkpoint_path`` extends that contract to the case D20 left open: an arm
    whose row outlasts the command cap on its own. The ingest state of an
    in-flight snapshot-capable arm is saved every ``checkpoint_seconds`` of work
    and resumed by the next call, so a timeout costs only the chunk in flight
    (defect D22). ``pass_budget_s`` lets a pass stop cleanly at the next
    checkpoint instead of waiting to be cut off; the arm's row is then simply
    absent from the returned rows until a later pass completes it. Checkpoint
    writes happen outside the ingest timing and a resumed arm applies the same
    events in the same order, so neither mechanism can move a number.
    """
    import synthetic

    events, queries = synthetic.scalability_corpus(seed=seed, n_memories=memories, days=days)
    corpus = {"seed": seed, "memories": memories, "days": days}
    rows: List[Dict] = []
    for arm in arms:
        if done and arm.name in done:
            rows.append(done[arm.name])
            print("  %-34s (already measured by an earlier pass; reused)" % arm.name,
                  file=sys.stderr)
            continue
        arm.reset()
        # Ingest seconds banked by earlier passes of this arm, and how many of
        # the corpus's events those passes already applied (defect D22).
        banked = 0.0
        skip = 0
        last_day = None
        if checkpoint_path and arm.supports_snapshot():
            checkpoint = _read_checkpoint(checkpoint_path, arm, corpus)
            if checkpoint is not None:
                arm.restore_state(checkpoint["state"])
                arm.refresh_ms = list(checkpoint["refresh_ms"])
                banked = float(checkpoint["ingest_s"])
                skip = int(checkpoint["events_done"])
                last_day = checkpoint["last_day"]
                print("  %-34s (resuming mid-ingest at event %d/%d)"
                      % (arm.name, skip, len(events)), file=sys.stderr)
        span_start = time.perf_counter()
        spent = 0.0  # ingest seconds spent by THIS call, against pass_budget_s
        stopped = False
        for index, event in enumerate(events):
            if index < skip:
                continue
            if last_day is None or event.day != last_day:
                arm.refresh(event.timestamp())
                last_day = event.day
            if event.op == "add":
                arm.add(event.content, memory_id=event.id or None, category=event.category,
                        protected=event.protected, now=event.timestamp())
            elif event.op == "retire":
                arm.retire(event.id, event.timestamp())
            else:
                arm.access(event.id, event.timestamp())
            if checkpoint_path and arm.supports_snapshot():
                span = time.perf_counter() - span_start
                if (span >= checkpoint_seconds
                        or (pass_budget_s is not None and spent + span >= pass_budget_s)):
                    banked += span
                    spent += span
                    _write_checkpoint(checkpoint_path, arm, index + 1, banked, last_day, corpus)
                    span_start = time.perf_counter()
                    if pass_budget_s is not None and spent >= pass_budget_s:
                        stopped = True
                        break
        if stopped:
            print("  %-34s (pass budget reached at event %d; row deferred to a later pass)"
                  % (arm.name, index + 1), file=sys.stderr)
            return rows
        ingest = banked + (time.perf_counter() - span_start)
        if checkpoint_path and arm.supports_snapshot():
            # Ingest complete: bank it before the timed passes below, so a pass
            # cut off there costs the passes only, never the ingest again.
            _write_checkpoint(checkpoint_path, arm, len(events), ingest, last_day, corpus)

        def pct(samples: Sequence[float], q: float) -> float:
            if not samples:
                return 0.0
            return samples[min(len(samples) - 1, int(q * len(samples)))]

        def median(values: Sequence[float]) -> float:
            return stats.percentile(values, 0.5)

        # Repeat the timed pass. Every arm does the same number of passes on the
        # same queries, so the comparison stays fair, and taking the MEDIAN of
        # the per-pass values keeps one unlucky pass from deciding a criterion.
        per_pass_p95: List[float] = []
        per_pass_p50: List[float] = []
        per_pass_s: List[float] = []
        for _ in range(SCALE_TIMED_PASSES):
            arm.recall_ms.clear()
            start = time.perf_counter()
            for query in queries:
                arm.recall(query.query, budget=DEFAULT_BUDGET, now=day(query.session))
            per_pass_s.append(time.perf_counter() - start)
            samples = sorted(arm.recall_ms)
            per_pass_p50.append(pct(samples, 0.50))
            per_pass_p95.append(pct(samples, 0.95))

        rows.append({
            "arm": arm.name,
            "stored": arm.size(),
            "memories": len([e for e in events if e.op == "add"]),
            "ingest_s": round(ingest, 3),
            "refresh_total_s": round(sum(arm.refresh_ms) / 1000.0, 3),
            "recall_p50_ms": round(median(per_pass_p50), 3),
            "recall_p95_ms": round(median(per_pass_p95), 3),
            "recall_total_s": round(median(per_pass_s), 3),
            # Kept in the raw evidence so a reader can see the spread the median
            # was taken over rather than trusting a single figure.
            "recall_p95_passes_ms": [round(v, 3) for v in per_pass_p95],
            "timed_passes": SCALE_TIMED_PASSES,
        })
        print("  %-34s stored=%d ingest=%.2fs refresh=%.2fs p95=%.1fms (median of %d passes: %s)"
              % (arm.name, arm.size(), ingest, sum(arm.refresh_ms) / 1000.0,
                 median(per_pass_p95), SCALE_TIMED_PASSES,
                 ", ".join("%.1f" % v for v in per_pass_p95)),
              file=sys.stderr)
        if on_row is not None:
            on_row(rows[-1])
        if checkpoint_path and arm.supports_snapshot() and os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
    return rows


# --------------------------------------------------------------- decision rule
def verdict(summaries: Dict[str, Dict[str, float]],
            by_source: Dict[str, Dict[str, Dict[str, float]]],
            scale: Sequence[Dict],
            pairs: List[Dict]) -> Dict:
    """Pre-registered criteria. Evaluated on the numbers, not chosen after.

    A criterion whose inputs this run never produced is reported as
    ``NOT MEASURED`` and left out of the denominator, instead of being scored a
    phantom FAIL (defect D4). Each criterion decides that for itself from the
    inputs it actually holds (``crit_stale_measured``, ``crit_decay_measured``,
    ``crit_sig_measured``, ``crit_latency_measured``). There is deliberately no
    separate "what did this run measure" switch: one existed here as an unused
    parameter that the call site dutifully passed, which reads like a control
    that is not wired to anything.
    """
    mine = summaries.get(HERO_ARM, {})
    pure = summaries.get(PURE_ARM, {})
    flat = summaries.get("M1 flat/FIFO", {})

    best_semantic = None
    for name in SEMANTIC_ARMS:
        summary = summaries.get(name)
        if summary and (best_semantic is None or summary["hit_rate"] > best_semantic[1]["hit_rate"]):
            best_semantic = (name, summary)

    crit_budget = mine.get("chars_per_hit", float("inf")) <= flat.get("chars_per_hit", float("inf"))

    # The hero is fixed in advance and is NOT swapped for whichever arm wins, so
    # the paired tests stay pre-registered. The best-performing engine
    # configuration is reported alongside it rather than instead of it.
    # Derived from ARM_ORDER rather than hand-written, so a newly added engine
    # arm is eligible for "best measured engine configuration" the moment it runs.
    # A frozen list would let the report name an engine combination the suite no
    # longer contains, and would hide a better engine row from this criterion
    # while the table above it showed that row (round 5).
    ladder = [name for name in ARM_ORDER if "myelinated" in name]
    ranked = [name for name in ladder if name in summaries]
    best_engine = max(ranked, key=lambda name: summaries[name]["hit_rate"]) if ranked else None

    semantic_gap = 0.0
    crit_quality = True
    if best_semantic:
        semantic_gap = best_semantic[1]["hit_rate"] - mine.get("hit_rate", 0.0)
        crit_quality = semantic_gap <= 0.05

    best_gap = None
    if best_semantic and best_engine:
        best_gap = best_semantic[1]["hit_rate"] - summaries[best_engine]["hit_rate"]

    scale_by_arm = {row["arm"]: row for row in scale}
    hero_scale = scale_by_arm.get(HERO_ARM, {})
    bm25_scale = scale_by_arm.get("M3 semantic/BM25", {})
    crit_latency_measured = bool(hero_scale)
    crit_latency = crit_latency_measured and (
        hero_scale.get("recall_p95_ms", float("inf")) < bm25_scale.get("recall_p95_ms", float("inf")))

    stale_retire = by_source.get(HERO_ARM, {}).get(TIER_LABELS["staleness"], {})
    stale_flat = by_source.get("M1 flat/FIFO", {}).get(TIER_LABELS["staleness"], {})
    crit_stale_measured = bool(stale_retire)
    crit_stale = crit_stale_measured and stale_retire.get("stale_leak_rate", 1.0) < 0.20

    stale_decay = by_source.get(HERO_ARM, {}).get(TIER_LABELS["staleness-decay"], {})
    decay_leak = stale_decay.get("stale_leak_rate")
    crit_decay_measured = decay_leak is not None

    # BOTH flat baselines, not whichever one flatters the hero (defect D6): the
    # criterion used to pass when either arm lost, while the other could beat it.
    flat_rows = [row for row in pairs if row["arm"] in FLAT_ARMS]
    crit_sig_measured = bool(flat_rows)
    crit_sig = crit_sig_measured and all(
        row["significant"] and row["diff"] > 0 for row in flat_rows)
    sig_detail = "; ".join("%s %+.3f %s" % (row["arm"], row["diff"],
                                             stats.format_p(row["p_adjusted"]))
                           for row in flat_rows) or "no paired comparison with a flat baseline"

    def criterion(text: str, ok: bool, detail: str, was_measured: bool = True,
                  counted: bool = True) -> Tuple[str, str, str, bool]:
        """A rule row: (text, status, detail, counted).

        ``counted=False`` marks a criterion whose inputs exist but cannot
        support a verdict. That is a distinct outcome from NOT MEASURED, and the
        values are still printed - what changes is only that the row is kept out
        of the pass/fail denominator.
        """
        if not was_measured:
            return (text, "NOT MEASURED", "this run did not measure the inputs for it", False)
        if not counted:
            return (text, "INFORMATIONAL", detail, False)
        return (text, "PASS" if ok else "FAIL", detail, True)

    criteria = [
        criterion("Budget efficiency: no more characters per evidence hit than flat/FIFO",
                  bool(crit_budget),
                  "%.0f vs flat %.0f" % (mine.get("chars_per_hit", 0.0), flat.get("chars_per_hit", 0.0))),
        criterion("Query-conditioned retrieval (pre-registered %s): within 5 hit-rate points of the best baseline" % HERO_ARM,
                  bool(crit_quality),
                  "%s leads by %+.3f" % (best_semantic[0] if best_semantic else "n/a", -semantic_gap)),
        criterion("Best measured engine configuration reaches within 5 hit-rate points of the best baseline",
                  bool(best_gap is not None and best_gap <= 0.05),
                  "%s at %.3f, %s leads by %+.3f" % (
                      best_engine or "n/a", summaries.get(best_engine, {}).get("hit_rate", 0.0),
                      best_semantic[0] if best_semantic else "n/a", -(best_gap or 0.0))),
        criterion("Staleness with supersession: under 20% of superseded facts still surface",
                  bool(crit_stale),
                  "leak %.3f vs flat %.3f" % (stale_retire.get("stale_leak_rate", 1.0),
                                              stale_flat.get("stale_leak_rate", 1.0)),
                  crit_stale_measured),
        criterion("Decay alone: superseded facts fade without an explicit retire signal",
                  bool(decay_leak is not None and decay_leak < 0.20),
                  "leak %s" % ("n/a" if decay_leak is None else "%.3f" % decay_leak),
                  crit_decay_measured),
        # Reported, never scored. Two back-to-back runs of this exact commit on
        # this host measured 121.9ms vs 101.3ms (hero loses) and 108.1ms vs
        # 126.4ms (hero wins) on the same seed, with per-pass spreads of 5x. A
        # criterion whose sign is not reproducible cannot decide a verdict; the
        # retrieval criteria, which do reproduce, still can (defect D2).
        criterion("Scale: faster recall p95 than BM25 at %d memories "
                  "(informational - single-host wall clock, not scored)" % SCALE_MEMORIES,
                  bool(crit_latency),
                  "%.1fms vs %.1fms; the sign of this flips between identical runs "
                  "(median of %d passes, spread in raw.json)" % (
                      hero_scale.get("recall_p95_ms", 0.0),
                      bm25_scale.get("recall_p95_ms", 0.0), SCALE_TIMED_PASSES),
                  crit_latency_measured, counted=False),
        criterion("Significantly better end-to-end score than BOTH flat baselines (Holm-adjusted p<0.05)",
                  bool(crit_sig), sig_detail, crit_sig_measured),
    ]
    return {
        "criteria": criteria,
        "passed": sum(1 for _, status, _, _ in criteria if status == "PASS"),
        "measured": sum(1 for _, status, _, _ in criteria if status in ("PASS", "FAIL")),
        "informational": sum(1 for _, status, _, _ in criteria if status == "INFORMATIONAL"),
        "not_measured": sum(1 for _, status, _, _ in criteria if status == "NOT MEASURED"),
        "total": len(criteria),
        "criteria_version": CRITERIA_VERSION,
        "hero": HERO_ARM,
        "best_engine": best_engine,
        "pure": pure,
        "best_engine_needs_remediation": not (crit_budget and best_gap is not None and best_gap <= 0.05),
    }


# --------------------------------------------------------------------- report
def render_report(payload: Dict) -> str:
    summaries: Dict[str, Dict[str, float]] = payload["summaries"]
    order = payload["arm_order"]
    lines: List[str] = []
    add = lines.append

    add("# Myelinated Memory - Benchmark Results")
    add("")
    add("Generated by `benchmarks/run_bench.py` on %s." % payload["generated"])
    add("")
    add("> Every number in this file is produced by the command below and stored in")
    add("> `%s`. Nothing here is estimated by hand." % payload.get(
        "raw_path", "benchmarks/results/raw.json"))
    add("")
    add("```bash")
    add(payload["command"])
    add("```")
    add("")
    add("## Setup")
    add("")
    add("| | |")
    add("| :--- | :--- |")
    add("| Python | %s |" % payload["python"])
    add("| Judge | %s |" % payload["judge"])
    add("| Budget | %d characters per query |" % payload["budget"])
    add("| Seeds | %s |" % payload["seeds"])
    add("| Tiers | %s |" % ", ".join(sorted(set(TIER_LABELS.get(t, t) for t in payload["tiers"]))))
    add("| Scenarios | %d |" % payload["scenario_count"])
    add("| Queries | %d |" % payload["query_count"])
    ledger = payload.get("judge_ledger", {})
    add("| Queries judged / skipped / errored | %d / %d / %d |" % (
        ledger.get("judged", 0), ledger.get("skipped", 0), ledger.get("errored", 0)))
    add("| Decision rule | version %s (frozen before the run) |" % payload.get("criteria_version", 1))
    add("")
    if not payload["judge_is_llm"]:
        add("**The judge in this run is not a language model.** It is a deterministic")
        add("evidence-containment oracle: it answers with the single most relevant retrieved")
        add("line and scores the gold answer's presence in it. That makes the run free,")
        add("offline and reproducible, but it rewards *surfacing* the evidence rather than")
        add("*reasoning* over it. Set a key and run `--judge gemini`, `--judge openai` or")
        add("`--judge nvidia` for true task-success numbers. `task_success` counts only")
        add("queries the judge actually returned; skipped and errored queries are counted in")
        add("the setup table instead.")
        add("")

    add("## Retrieval and context, per arm")
    add("")
    columns = ["arm", "task_success", "judged_queries", "hit_rate", "ndcg@10",
               "ndcg@10_packed", "evidence_precision", "leak_supersession", "leak_decay_only",
               "chars_per_hit", "p50_latency_ms", "p95_latency_ms", "cold_ms"]
    rows = []
    for name in order:
        if name not in summaries:
            continue
        arm_sources = payload.get("by_source", {}).get(name, {})
        rows.append(dict(
            summaries[name], arm=name,
            leak_supersession=arm_sources.get(TIER_LABELS["staleness"], {}).get("stale_leak_rate"),
            leak_decay_only=arm_sources.get(TIER_LABELS["staleness-decay"], {}).get("stale_leak_rate"),
        ))
    add(metrics.format_table(rows, columns))
    add("")
    add("*`chars_per_hit` is characters spent per query that actually reached evidence - lower is")
    add("better. It is a ratio of two means, so a hit-rate gain can hide a cost rise; read it next")
    add("to `hit_rate`. `ndcg@10` and `mrr` are scored on each arm's RANK order, while")
    add("`ndcg@10_packed` is the same measure on the order the allocator packed in - they differ")
    add("only for arms that declare a rank order, which is the point (defect D8). The two leak")
    add("columns are the two staleness suites, reported separately and never merged (BM-003).")
    add("Latency percentiles exclude each scenario's first (cold) recall, which pays any lazy")
    add("index build; `cold_ms` is the mean of those samples and `-` means none were taken.*")
    add("")

    controls = [name for name in CONTROL_ARMS if name in summaries]
    if controls:
        add("## Allocator controls: is the win the store or the packer?")
        add("")
        add("M3t, M3p and M3k share BM25's ranking and differ only in how the budget is filled,")
        add("so they separate the engine's allocator from the engine's store (round-4 design 4).")
        add("They are controls, not baselines: none of them is the arm the engine is judged against.")
        add("")
        control_rows = []
        # Engine rows are derived from ARM_ORDER like every other engine list here.
        # The hand-written list this replaces named "M9 recency +knapsack", which is
        # not an arm: `summaries.get()` returned None and the row silently vanished,
        # while the real M9 was missing from the one table it belongs in.
        engine_rows = [name for name in ARM_ORDER
                       if "myelinated" in name and name in summaries]
        for name in ("M3 semantic/BM25",) + tuple(controls) + tuple(engine_rows):
            summary = summaries.get(name)
            if not summary:
                continue
            control_rows.append({"arm": name, "task_success": summary["task_success"],
                                 "hit_rate": summary["hit_rate"], "ndcg@10": summary["ndcg@10"],
                                 "chars_per_hit": summary["chars_per_hit"],
                                 "budget_fill": summary["mean_chars"]})
        add(metrics.format_table(control_rows, ["arm", "task_success", "hit_rate", "ndcg@10",
                                                "chars_per_hit", "budget_fill"]))
        add("")

    add("## Ablation ladder")
    add("")
    add("The engine arms are the same code with one capability added at a time, so each")
    add("row is attributable to a single change:")
    add("")
    ladder = [(PURE_ARM, "-"), ("M7 myelinated +similarity", "R1 query similarity"),
              ("M8 myelinated +knapsack", "R4/R7 value-per-char packing"),
              ("M9 myelinated +supersession", "R5 retire/supersede"),
              (HERO_ARM, "R2 utility reinforcement")]
    rows = []
    for name, change in ladder:
        summary = summaries.get(name)
        if not summary:
            continue
        rows.append({
            "arm": name, "change": change,
            "hit_rate": summary["hit_rate"], "ndcg@10": summary["ndcg@10"],
            "chars_per_hit": summary["chars_per_hit"],
            "task_success": summary["task_success"],
        })
    add(metrics.format_table(rows, ["arm", "change", "task_success", "hit_rate",
                                    "ndcg@10", "chars_per_hit"]))
    add("")

    if payload.get("by_source"):
        add("## Hit rate by tier")
        add("")
        sources = [TIER_LABELS[t] for t in ("curated", "synthetic", "staleness",
                                            "staleness-decay", "locomo")]
        sources = [s for s in sources if any(s in arm for arm in payload["by_source"].values())]
        arms_for_source = [a for a in order if a in payload["by_source"]]
        rows = []
        for source in sources:
            row = {"tier": source}
            for arm in arms_for_source:
                row[arm] = payload["by_source"][arm].get(source, {}).get("hit_rate")
            rows.append(row)
        add(metrics.format_table(rows, ["tier"] + arms_for_source))
        add("")

    add("## Staleness: supersession vs decay alone")
    add("")
    add("Board ruling BM-003: these are two different claims and are never merged. The")
    add("supersession suite emits an explicit retire signal (every arm is offered it);")
    add("the decay-only suite is the SAME scenarios with no retire signal at all, so it")
    add("measures whether retrieval-strength decay alone retires a stale fact.")
    add("")
    rows = []
    for name in order:
        arm_sources = payload.get("by_source", {}).get(name, {})
        retire = arm_sources.get(TIER_LABELS["staleness"], {})
        decay = arm_sources.get(TIER_LABELS["staleness-decay"], {})
        if not retire and not decay:
            continue
        rows.append({
            "arm": name,
            "supports_retire": "yes" if name in payload.get("retirement_arms", []) else "no",
            "leak_with_supersession": retire.get("stale_leak_rate"),
            "leak_decay_only": decay.get("stale_leak_rate"),
            "hit_rate_decay_only": decay.get("hit_rate"),
        })
    add(metrics.format_table(rows, ["arm", "supports_retire", "leak_with_supersession",
                                    "leak_decay_only", "hit_rate_decay_only"]))
    add("")

    add("## End-to-end task success")
    add("")
    add("| arm | mean score | vs %s | 95%% CI | Holm-adjusted p |" % HERO_ARM)
    add("| :--- | ---: | ---: | :--- | ---: |")
    for row in payload["pairs"]:
        add("| %s | %.3f | %+.3f | [%+.3f, %+.3f] | %s |" % (
            row["arm"], row["arm_mean"], row["diff"], row["ci_low"], row["ci_high"],
            stats.format_p(row["p_adjusted"])))
    add("")
    add("Positive `diff` means the arm beat %s on the same queries." % HERO_ARM)
    add("")

    if payload.get("by_kind"):
        add("## Where it wins and loses, by question type")
        add("")
        kinds = sorted(payload["by_kind"].get(HERO_ARM, {}).keys())
        arms_for_table = [a for a in order if a in payload["by_kind"]]
        rows = []
        for kind in kinds:
            row = {"kind": kind}
            for arm in arms_for_table:
                row[arm] = payload["by_kind"][arm].get(kind, {}).get("hit_rate")
            rows.append(row)
        add(metrics.format_table(rows, ["kind"] + arms_for_table))
        add("")

    if payload["scale"]["rows"]:
        add("## Scale: %d memories over %d virtual days" % (
            payload["scale"]["memories"], payload["scale"]["days"]))
        add("")
        add(metrics.format_table(payload["scale"]["rows"],
                                 ["arm", "stored", "ingest_s", "refresh_total_s",
                                  "recall_p50_ms", "recall_p95_ms"]))
    else:
        # A skipped scale tier used to render as "Scale: 0 memories over 0 virtual
        # days", which reads like the measurement of an empty corpus.
        add("## Scale: not measured in this run")
        add("")
        add("This run passed `--skip-scale`, so the latency criterion below is reported as")
        add("NOT MEASURED rather than scored on an empty table (defects D4/D15).")
    add("")

    add("## Decision rule")
    add("")
    add("The rule below was fixed before the run:")
    add("")
    add("> The recommended configuration is *proven better* only if it wins the")
    add("> budget-efficiency and query-conditioned-retrieval criteria simultaneously.")
    add("> Anything else is a loss that needs the remediation list in")
    add("> `docs/REMEDIATION.md`.")
    add("")
    for text, status, detail, _counted in payload["verdict"]["criteria"]:
        add("- **%s** - %s - %s" % (status, text, detail))
    add("")
    # Named `rule`, not `verdict`: the local used to shadow the module-level
    # `verdict()` function inside the one function most likely to want to call it.
    rule = payload["verdict"]
    measured_n = rule.get("measured", rule["total"])
    info_n = rule.get("informational", 0)
    unmeasured_n = rule.get("not_measured", rule["total"] - measured_n - info_n)
    add("**Verdict: %d of %d scored criteria passed** - %d row%s informational (printed, not"
        " scored) and %d not measured in this run." % (
            rule["passed"], measured_n, info_n, "" if info_n == 1 else "s", unmeasured_n))
    add("")
    add("The pre-registered hero is `%s` and is not swapped for whichever arm wins." % HERO_ARM)
    add("The best measured engine configuration in this run is `%s`%s." % (
        payload["verdict"].get("best_engine") or "n/a",
        "" if payload["verdict"].get("best_engine") == HERO_ARM
        else " - a different arm, reported alongside rather than instead"))
    add("")

    # If an allocator control outranks every engine arm, say so here. It is the
    # single most load-bearing reading of this report (round-4 design 4): a
    # control leading means the mechanism's budget win came from its packer, and
    # a reader who only sees the arm table would otherwise take the engine's
    # hit-rate row for a store success.
    best_engine_name = payload["verdict"].get("best_engine") or HERO_ARM
    # Same reason as in verdict(): this comparison decides whether a control is
    # announced as leading *every* engine arm, so it has to cover every engine
    # arm in the run rather than a list frozen at round 4.
    engine_names = [name for name in order if "myelinated" in name]
    leading_control = None
    for name in CONTROL_ARMS:
        if name not in summaries:
            continue
        engine_best = max((summaries[n]["hit_rate"] for n in engine_names if n in summaries),
                          default=0.0)
        if summaries[name]["hit_rate"] > engine_best:
            leading_control = name
    if leading_control:
        control = summaries[leading_control]
        engine = summaries.get(best_engine_name, {})
        add("**Attribution, and it matters:** `%s` is an allocator *control*, not an engine -"
            % leading_control)
        add("it ranks with BM25 and only packs the budget differently - and it leads every engine")
        add("arm on hit rate (%.3f against %s's %.3f) while spending %.0f characters per hit"
            % (control["hit_rate"], best_engine_name, engine.get("hit_rate", 0.0),
               control["chars_per_hit"]))
        add("against %.0f. So the engine's budget win is attributable to its **allocator**, not"
            % engine.get("chars_per_hit", 0.0))
        add("its **store**: the strength, decay and tier machinery is not what earned it. It is")
        add("reported as a finding rather than a win, and the control is not swapped in as the")
        add("hero (BM-004).")
        add("")
    add("**%s**" % ("Remediation continues against the best configuration."
                    if payload["verdict"]["best_engine_needs_remediation"]
                    else "The best configuration closes the stated gap on this workload."))
    add("")
    add("## Method notes")
    add("")
    for note in payload["notes"]:
        add("- %s" % note)
    add("")
    return "\n".join(lines)


CONTEXT_FIELDS = ("context", "model_answer")


def trim_records(by_arm: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Drop the bulky rendered context; keep the scored, checkable fields."""
    return {
        name: [{k: v for k, v in record.items() if k not in CONTEXT_FIELDS}
               for record in records]
        for name, records in by_arm.items()
    }


def build_notes(include_network: bool) -> List[str]:
    notes = [
        "All arms receive the identical event stream, virtual clock and character "
        "budget; only the memory policy differs.",
        "Judging happens inside the timeline, so an arm that consumes the "
        "utility-credit signal can learn within the scenario rather than after it.",
        "Decay runs on a virtual clock, so no wall-clock waiting is involved and "
        "the results are replayable.",
        "Every arm is offered the supersession signal and every baseline honours it "
        "by deleting the retired memory. Supersession is therefore not an engine "
        "advantage; the informative number is the decay-only suite.",
        "Near-duplicate collapsing uses a MinHash sketch index. Measured recall is "
        "97.5% on pairs at Jaccard 0.96 with zero false merges on a negative "
        "control; recall degrades on pathological corpora with a tiny shared "
        "vocabulary, where every index key is shared by hundreds of memories.",
        "raw.json keeps each query's used/evidence/stale ids, scores and latencies; "
        "the rendered context is omitted because it is large and reproducible.",
        "The LoCoMo tier is a multi-session transcript of roughly 1,450 turns scored "
        "against the same 2,200-character budget, and its turns are labelled "
        "ephemeral, so almost every memory decays to the archived tier before the "
        "question is asked. Every arm scores near zero task success there; the tier "
        "is kept because it is a public benchmark and its hit-rate column is the "
        "informative part. No workload parameter was tuned to favour the engine.",
        "The archived tier renders a content gist rather than the specification's "
        "\"ID stub\", because id stubs filled the budget with text no answering "
        "model can read (remediation R7).",
        "Latency percentiles cover warm recalls only. The first recall of a scenario "
        "pays any lazy index build and is reported as its own mean (cold_ms), because "
        "folding one cold sample into a five-sample p95 measured startup, not query "
        "cost (defect D2).",
        "The scale latency figures are the median of %d timed passes over the same "
        "queries, not one pass: with five queries a single pass made \"p95\" the "
        "maximum of five samples, and two identical runs disagreed about the scale "
        "criterion. The per-pass values stay in raw.json. Wall-clock numbers remain "
        "noisier than the retrieval metrics." % SCALE_TIMED_PASSES,
        "M3t/M3p/M3k are allocator controls, not baselines: they share BM25's ranking "
        "and vary only how the budget is filled, so they attribute the engine's "
        "budget win to its allocator rather than its store (round-4 design 4).",
        "The engine arms are not one configuration. Since round 5 the shipped default "
        "is PRIOR_WEIGHT = 0.0, which is M11's ordering; M6-M10 and M12 pin the "
        "pre-round-5 LEGACY_PRIOR_WEIGHT = 0.35 so the round-4 rows stay reproducible, "
        "and M12 additionally lifts the candidate ceilings (it reproduced M10 exactly, "
        "which is how those ceilings were measured to be immaterial on these tiers). "
        "The pre-registered hero is M10, so the hero row and M11's row are the "
        "pre-registered configuration and the shipped one, one constant apart.",
        "Supersession fires on an inferred update as well as on an explicit retire: a "
        "memory at Jaccard >= 0.90 whose value words changed (different token set) is "
        "stored and the superseded entry is retired, with no signal from the caller. "
        "That is a capability change, reported as one (BM-002), and it is why the "
        "decay-only leak column can now move.",
        "The decision rule is version 3, and every change in it is a repair of the "
        "same class of bug: an unmeasured criterion reports NOT MEASURED instead of a "
        "phantom FAIL, the scale latency criterion reports INFORMATIONAL instead of a "
        "coin flip (its sign is not reproducible between identical runs), the "
        "significance criterion now requires BOTH flat baselines rather than whichever "
        "one flattered the hero, and the merged stale-leak column is gone. The rule is "
        "still frozen before the run and its version is printed above.",
    ]
    if not include_network:
        notes.append(
            "The dense-embedding arm (M5) was not run: it needs OPENAI_API_KEY or "
            "NVIDIA_CLOUD_KEY. TF-IDF cosine stands in as the vector-space semantic "
            "baseline.")
    return notes


# ----------------------------------------------------------------------- main
def report_path_for(args: argparse.Namespace) -> str:
    """Where this run's report goes.

    The committed ``benchmarks/RESULTS.md`` is only ever replaced by the full
    default run. A scoped run (``--tier``, ``--skip-scale``) writes its own file,
    because a partial report that silently overwrites the published one is a
    data-loss bug, not a convenience (defect D15).
    """
    if args.report:
        # An explicit path is honoured as the caller typed it, relative to the
        # working directory. Joining it to the harness directory turned
        # `--report benchmarks/x.md` into `benchmarks/benchmarks/x.md`.
        return os.path.abspath(args.report)
    # `--out` is compared by absolute path, not as typed: `--out benchmarks/results`
    # is the results directory under another name, and string comparison counted it
    # as somewhere else (see raw_path_for, where that was a real overwrite).
    full = (args.tier == "all" and not args.skip_scale
            and os.path.abspath(args.out) == RESULTS_DIR)
    if full:
        return os.path.join(ROOT, "RESULTS.md")
    label = args.tier if args.tier != "all" else "partial"
    return os.path.join(ROOT, "RESULTS-%s.md" % label)


def raw_path_for(args: argparse.Namespace) -> str:
    """Where this run's raw evidence goes, on the same rule as the report.

    ``results/raw.json`` is the evidence behind the committed report, so a
    scoped run writes ``raw-<tier>.json`` instead of overwriting it (the same
    D15 data-loss bug, one file over). An explicit ``--out`` is honoured as
    written, because that is a scratch directory the caller chose.
    """
    # Compared by absolute path, so that a relative spelling of the committed
    # directory (`--out benchmarks/results`) cannot slip past this guard and let a
    # scoped run overwrite `results/raw.json` - the exact data loss the guard
    # exists to prevent, one path spelling away. An out directory that is genuinely
    # elsewhere is still honoured as written, because that is a scratch directory
    # the caller chose.
    if os.path.abspath(args.out) != RESULTS_DIR:
        return os.path.join(args.out, "raw.json")
    if args.tier == "all" and not args.skip_scale:
        return os.path.join(RESULTS_DIR, "raw.json")
    label = args.tier if args.tier != "all" else "partial"
    return os.path.join(RESULTS_DIR, "raw-%s.json" % label)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Myelinated Memory benchmark")
    parser.add_argument("--tier", default="all",
                        choices=["all", "curated", "synthetic", "staleness", "locomo"])
    parser.add_argument("--judge", default="auto",
                        choices=["auto", "oracle", "openai", "gemini", "nvidia", "llm"],
                        help="auto/oracle = the offline deterministic judge (default); "
                             "llm = first available model judge (gemini, then openai, then "
                             "nvidia); gemini/openai/nvidia = pin one")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--locomo-limit", type=int, default=3)
    parser.add_argument("--locomo-queries", type=int, default=20)
    parser.add_argument("--judge-limit", type=int, default=0,
                        help="cap on judged queries per arm (0 = no cap)")
    parser.add_argument("--network", action="store_true",
                        help="include the dense-embedding arm (needs OPENAI_API_KEY "
                             "or NVIDIA_CLOUD_KEY)")
    parser.add_argument("--skip-scale", action="store_true")
    # Two bounded passes instead of one process, for hosts whose command timeout
    # is shorter than the full run (the scale probe at 10,000 memories alone can
    # outlast a 3-minute limit). Both passes must agree on seed, tiers, budget,
    # judge and arm list, the assembly is recorded in the report, and the default
    # remains a single process - see defect D20.
    parser.add_argument("--phase", default="full", choices=["full", "queries", "scale"],
                        help="full = one process (default); queries = replay the query tiers "
                             "and write --state; scale = resume from --state, measure the scale "
                             "probe and render the report")
    parser.add_argument("--state", default=None,
                        help="scratch file holding the query-tier records between --phase passes")
    parser.add_argument("--scale-memories", type=int, default=SCALE_MEMORIES)
    parser.add_argument("--pass-budget", type=float, default=None,
                        help="seconds of scale-probe ingest work this pass may spend before it "
                             "checkpoints and stops cleanly (for hosts whose command timeout is "
                             "shorter than one arm's row; re-run the same command to resume)")
    parser.add_argument("--out", default=RESULTS_DIR)
    parser.add_argument("--report", default=None,
                        help="override the report path (default: RESULTS.md for a full run, "
                             "RESULTS-<tier>.md otherwise)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.tier == "all":
        tiers = ["curated", "synthetic", "staleness", "locomo"]
    elif args.tier == "staleness":
        tiers = ["staleness"]
    else:
        tiers = [args.tier]

    try:
        judge = judge_mod.make_judge(args.judge)
    except judge_mod.JudgeError as exc:
        # A missing key is a usage error, not a crash: name the variable, exit 2
        # and replay nothing. Reaching this with --judge openai/gemini/llm means
        # no provider key is set; auto/oracle cannot get here at all.
        print("[error] %s" % exc, file=sys.stderr)
        return 2

    scenarios = load_scenarios(tiers, args.locomo_limit, args.locomo_queries, args.seed)
    query_count = sum(len(s.queries) for s in scenarios)
    print("scenarios=%d queries=%d judge=%s" % (len(scenarios), query_count, judge.mode),
          file=sys.stderr)

    arms = build_arms(include_network=args.network)
    identity = {
        "seed": args.seed,
        "tiers": list(tiers),
        "budget": args.budget,
        "judge": judge_mod.describe(judge),
        "arm_order": list(ARM_ORDER),
        "scenario_count": len(scenarios),
        "query_count": query_count,
        "locomo_limit": args.locomo_limit,
        "locomo_queries": args.locomo_queries,
    }
    phases = "one process"
    if args.phase == "queries":
        if not args.state:
            print("[error] --phase queries needs --state PATH", file=sys.stderr)
            return 2
        by_arm, ledgers = evaluate(scenarios, arms, judge, args.budget, args.judge_limit)
        with open(args.state, "w", encoding="utf-8") as handle:
            json.dump({"identity": identity, "by_arm": by_arm, "ledgers": ledgers},
                      handle, default=str)
            handle.write("\n")
        print("phase=queries: %d arms, %d queries, state written to %s. Now re-run with "
              "--phase scale, the same flags and --state %s to measure the scale probe and "
              "write the report." % (len(arms), query_count, args.state, args.state),
              file=sys.stderr)
        return 0
    if args.phase == "scale":
        if not args.state or not os.path.exists(args.state):
            print("[error] --phase scale needs a --state file written by --phase queries",
                  file=sys.stderr)
            return 2
        with open(args.state, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        # Both halves must be the same run: the same seed, tiers, budget, judge and
        # arm list. Otherwise the artifact would be a splice of two different runs
        # wearing one command line, which is the quiet incomparability the D15
        # output guard exists to prevent.
        if state.get("identity") != identity:
            print("[error] the state file was produced by a different run - seed, tiers, "
                  "budget, judge or arm list differ. Replay --phase queries with these flags "
                  "first.", file=sys.stderr)
            return 2
        by_arm, ledgers = state["by_arm"], state["ledgers"]
        phases = "two bounded passes of one configuration: --phase queries (query tiers) " \
                 "then --phase scale (scale probe), same seed, tiers, budget, judge and arms"
    else:
        by_arm, ledgers = evaluate(scenarios, arms, judge, args.budget, args.judge_limit)
    judge_ledger = {
        key: sum(per_arm.get(key, 0) for per_arm in ledgers.values())
        for key in ("judged", "skipped", "errored")
    }

    summaries = {name: metrics.summarize(records) for name, records in by_arm.items()}
    by_kind = {name: metrics.by_kind(records) for name, records in by_arm.items()}
    by_source = {name: metrics.by_field(records, "source") for name, records in by_arm.items()}
    pairs = pairwise(by_arm, HERO_ARM)

    if args.skip_scale:
        scale = {"memories": 0, "days": 0, "rows": []}
    else:
        # A bounded pass can be cut off by a command timeout; every row it did
        # measure is kept next to the state file and reused, so the artifact is
        # still one configuration, one corpus and one arm order (defect D20).
        progress_path = (args.state + ".scale.json") if args.state else None
        checkpoint_path = (args.state + ".scale.inflight") if args.state else None
        measured: Dict[str, Dict] = {}
        if progress_path and os.path.exists(progress_path):
            with open(progress_path, "r", encoding="utf-8") as handle:
                measured = {row["arm"]: row for row in json.load(handle)}

        def _on_row(row: Dict, _path=progress_path, _seen=measured) -> None:
            _seen[row["arm"]] = row
            if _path:
                with open(_path, "w", encoding="utf-8") as handle:
                    json.dump(list(_seen.values()), handle, indent=2, default=str)

        # The arms the scale probe measures, captured once so the note below counts
        # them and not `ARM_ORDER`, which also lists the opt-in network arm.
        scale_arms = build_arms(include_network=False)
        reused = len(measured)
        resumed_mid_arm = bool(checkpoint_path and os.path.exists(checkpoint_path))
        try:
            measured_rows = scale_rows(scale_arms, args.scale_memories,
                                       SCALE_DAYS, args.seed, done=measured, on_row=_on_row,
                                       checkpoint_path=checkpoint_path,
                                       pass_budget_s=args.pass_budget)
        except ValueError as exc:
            # A stale in-flight checkpoint from another run: a clean refusal,
            # never a traceback and never a silent splice of two runs.
            print("[error] %s" % exc, file=sys.stderr)
            return 2
        scale = {"memories": args.scale_memories, "days": SCALE_DAYS, "rows": measured_rows}
        if len(measured_rows) < len(scale_arms):
            # A bounded pass that hit its budget renders nothing: a partial
            # probe must not overwrite the committed artifacts (D15).
            print("scale probe incomplete: %d of %d arms measured; every row so far is kept "
                  "in %s - re-run the same command to resume and render the report."
                  % (len(measured_rows), len(scale_arms), progress_path), file=sys.stderr)
            return 0
        if reused or resumed_mid_arm:
            note = ("; the scale probe was resumed across bounded passes (%d of %d arms "
                    "reused from earlier passes, not re-measured)"
                    % (reused, len(scale_arms)))
            if resumed_mid_arm:
                note += ", and an interrupted arm resumed from its mid-ingest checkpoint"
            phases += note

    payload = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "command": ("python3 benchmarks/run_bench.py " + " ".join(sys.argv[1:])
                    + ("" if args.phase == "scale" else "")),
        "phases": phases,
        "python": platform.python_version(),
        "judge": judge_mod.describe(judge),
        "judge_mode": judge.mode,
        "judge_is_llm": bool(judge.is_llm),
        "judge_calls": getattr(judge, "calls", 0),
        "judge_ledger": judge_ledger,
        "judge_counters": judge.counters() if hasattr(judge, "counters") else {},
        "criteria_version": CRITERIA_VERSION,
        "budget": args.budget,
        "seeds": [args.seed],
        "tiers": tiers,
        "scenario_count": len(scenarios),
        "query_count": query_count,
        "arm_order": ARM_ORDER,
        "retirement_arms": [a.name for a in arms if getattr(a, "supports_retirement", False)],
        "summaries": summaries,
        "by_kind": by_kind,
        "by_source": by_source,
        "pairs": pairs,
        "scale": scale,
        "verdict": verdict(summaries, by_source, scale["rows"], pairs),
        "notes": build_notes(args.network),
        "records_by_arm": trim_records(by_arm),
    }

    os.makedirs(args.out, exist_ok=True)
    raw_path = raw_path_for(args)
    # Repo-relative, so the path printed in the report is one a reader can use
    # from the repository root rather than one relative to benchmarks/.
    payload["raw_path"] = "benchmarks/" + os.path.relpath(raw_path, ROOT)
    with open(raw_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")

    report = render_report(payload)
    report_path = report_path_for(args)
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report)
        handle.write("\n")
    if os.path.basename(report_path) != "RESULTS.md":
        print("note: wrote %s, not the committed RESULTS.md, because this is not the full "
              "default run (defect D15)" % report_path, file=sys.stderr)

    print("\n" + metrics.format_table(
        [dict(summaries[name], arm=name) for name in payload["arm_order"] if name in summaries],
        ["arm", "task_success", "judged_queries", "hit_rate", "ndcg@10", "ndcg@10_packed",
         "chars_per_hit", "p95_latency_ms", "cold_ms"]))
    print("\nverdict: %d of %d scored criteria passed (%d informational, %d not measured)" % (
        payload["verdict"]["passed"], payload["verdict"]["measured"],
        payload["verdict"].get("informational", 0), payload["verdict"].get("not_measured", 0)))
    print("wrote %s" % raw_path)
    print("wrote %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
