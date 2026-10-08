#!/usr/bin/env python3
"""R11: pool recall@k — where the residual ranking loss lives.

    python3 benchmarks/pool_probe.py

For every query in the run, capture ``engine.candidate_pool(query)`` — the ids
the ranker is handed, before scoring and packing — and ask whether the query's
gold evidence is among them. The probe shares one funnel with ``recall()``, so
the pool it measures cannot drift from the pool recall used (the whole point of
that API). It is read-only, offline, and writes no report artifact.

The pre-registered criterion is R11 in ``docs/PLAN.md``, frozen before this
tool produced a number:

    gap = pool-hit rate - hit rate   (same replay, same queries)
    gap <= 0.05  -> the residual loss is candidate generation
    gap >= 0.15  -> the residual loss is ordering
    between      -> mixed; both numbers are reported per tier

``pool hit`` and ``hit`` use the same any-evidence semantics: at least one of
the query's ``evidence_ids`` is in the set in question (the pool, or the packed
context).
"""

import os
import sys
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import common  # noqa: E402
import engines  # noqa: E402
import metrics  # noqa: E402
import run_bench  # noqa: E402

BUDGET = 2200
TIERS = ["curated", "synthetic", "staleness", "locomo"]
ARMS = ["M11 myelinated +lexical ranking", "M8 myelinated +knapsack"]


def probe_arm(arm, scenarios) -> List[Dict]:
    """Replay every scenario, recording the candidate pool at each query."""
    rows: List[Dict] = []
    for scenario in scenarios:
        pools: Dict[str, set] = {}

        def on_result(arm, query, recall, record, pools=pools):
            # Read-only: the ids recall() was handed, at the moment it was asked.
            pools[query.id] = set(arm.engine.candidate_pool(query.query))

        records = common.replay(scenario, arm, budget=BUDGET, on_result=on_result)
        for query, record in zip(scenario.queries, records):
            gold = set(query.evidence_ids)
            pool = pools.get(query.id, set())
            rows.append({
                "source": scenario.source,
                "query": query.id,
                "pool_hit": float(bool(gold & pool)),
                "hit": metrics.hit(record),
                "gold_in_pool": len(gold & pool),
                "gold": len(gold),
                "pool_size": len(pool),
            })
    return rows


def summarise(rows: List[Dict]) -> List[Tuple[str, float, float, float, int]]:
    out = []
    for source in sorted({r["source"] for r in rows}):
        group = [r for r in rows if r["source"] == source]
        pool_hit = sum(r["pool_hit"] for r in group) / len(group)
        hit = sum(r["hit"] for r in group) / len(group)
        out.append((source, pool_hit, hit, pool_hit - hit, len(group)))
    pool_hit = sum(r["pool_hit"] for r in rows) / len(rows)
    hit = sum(r["hit"] for r in rows) / len(rows)
    out.append(("ALL", pool_hit, hit, pool_hit - hit, len(rows)))
    return out


def main() -> int:
    scenarios = run_bench.load_scenarios(TIERS, 3, 20, seed=0)
    for arm_name in ARMS:
        arm = next((a for a in engines.build_arms() if a.name == arm_name), None)
        if arm is None:
            print("arm not found: %s" % arm_name, file=sys.stderr)
            return 2
        rows = probe_arm(arm, scenarios)
        print("\n%s  (budget %d, seed 0)" % (arm_name, BUDGET))
        print("%-28s %10s %8s %8s %7s" % ("tier", "pool hit", "hit", "gap", "queries"))
        for source, pool_hit, hit, gap, n in summarise(rows):
            print("%-28s %10.3f %8.3f %+8.3f %7d" % (source, pool_hit, hit, gap, n))
        overall = summarise(rows)[-1]
        gap = overall[3]
        if gap <= 0.05:
            verdict = ("gap <= 0.05: the residual loss is CANDIDATE GENERATION - "
                       "the ranker was never handed the gold memory")
        elif gap >= 0.15:
            verdict = ("gap >= 0.15: the residual loss is ORDERING - the pool had "
                       "the gold and the ranking or packing dropped it")
        else:
            verdict = ("0.05 < gap < 0.15: MIXED - read the per-tier rows, "
                       "nothing collapses into one word")
        print("R11: %s" % verdict)
        sizes = [r["pool_size"] for r in rows]
        print("pool size: mean %.1f, max %d" % (sum(sizes) / len(sizes), max(sizes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
