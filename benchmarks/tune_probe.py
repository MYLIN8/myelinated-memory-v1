"""Fine-tuning probe: sweep one engine constant across the real suites.

`docs/TESTING.md` found that no engine constant had ever been swept against the
benchmark - every value is hand-set apart from the dedupe sketch caps, which were
measured on a probe corpus. This module fills that gap and nothing else: it
replays the same seeds, tiers and budget as `run_bench.py`, swaps one module
constant per configuration, and prints the resulting curve.

    python3 benchmarks/tune_probe.py                       # PRIOR_WEIGHT, the default sweep
    python3 benchmarks/tune_probe.py --name DETAIL_VALUE ... # not wired for dicts; see --values
    python3 benchmarks/tune_probe.py --values 0,0.1,0.35 --tiers curated,synthetic

It never writes `RESULTS.md` or `results/raw.json`, so a sweep cannot overwrite
the committed evidence. A sweep run on the report's own workload is not
out-of-sample: see the tuning protocol in `docs/TESTING.md` section 5 before
believing any of it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (os.path.join(ROOT, "scripts"), HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import engines  # noqa: E402
import judge as judge_mod  # noqa: E402
import metrics  # noqa: E402
import myelinate  # noqa: E402

# The recommended configuration from the report: query similarity on, value-per-
# character packing on, supersession off. That is arm M8, so the row at the
# constant's committed value must reproduce M8's published numbers - an internal
# control for this probe.
ARM = dict(similarity=True, knapsack=True, stale_retirement=False)
DEFAULT_VALUES = (0.0, 0.1, 0.2, 0.35, 0.5, 1.0)
DEFAULT_TIERS = ("curated", "synthetic", "staleness", "locomo")
SOURCE_KEYS = ("curated", "synthetic", "staleness (supersession)", "staleness (decay only)", "locomo (public)")


def sweep(name: str, values: Sequence[float], tiers: Sequence[str] = DEFAULT_TIERS,
          seed: int = 0, budget: int = 2200, locomo_limit: int = 3,
          locomo_queries: int = 20) -> List[Tuple[float, Dict, Dict]]:
    """Replay every tier once per value, with `name` patched on the engine module."""
    import run_bench  # imported here so the module stays importable without side effects

    original = getattr(myelinate, name)
    judge = judge_mod.make_judge("oracle")
    scenarios = run_bench.load_scenarios(list(tiers), locomo_limit, locomo_queries, seed)
    rows: List[Tuple[float, Dict, Dict]] = []
    try:
        for value in values:
            setattr(myelinate, name, value)
            arm = engines.MyelinatedArm("%s=%g" % (name, value), **ARM)
            # evaluate() returns (records_by_arm, judge_ledgers). Unpacking it as a
            # bare dict crashed every sweep with "tuple indices must be integers",
            # so the one instrument that can attribute an engine constant had never
            # actually produced a curve. The sweep only needs the records.
            by_arm, _ledgers = run_bench.evaluate(scenarios, [arm], judge, budget, 0)
            records = by_arm[arm.name]
            rows.append((value, metrics.summarize(records), metrics.by_field(records, "source")))
    finally:
        setattr(myelinate, name, original)
    return rows


def format_sweep(name: str, rows: Sequence[Tuple[float, Dict, Dict]]) -> str:
    header = ["value", "task_succ", "hit_rate", "ndcg@10", "chars/hit", "lat p95"] + [k[:11] for k in SOURCE_KEYS]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for value, summary, by_source in rows:
        cells = [
            "%g" % value,
            "%.3f" % summary["task_success"],
            "%.3f" % summary["hit_rate"],
            "%.3f" % summary["ndcg@10"],
            "%.0f" % summary["chars_per_hit"],
            "%.2f" % summary["p95_latency_ms"],
        ]
        for key in SOURCE_KEYS:
            group = by_source.get(key)
            cells.append("%.3f" % group["hit_rate"] if group else "-")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("*`lat p95` is the maximum of a handful of scale queries - see defect D2.*")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep one engine constant (never writes the report)")
    parser.add_argument("--name", default="PRIOR_WEIGHT", help="module constant in scripts/myelinate.py")
    parser.add_argument("--values", default=",".join("%g" % v for v in DEFAULT_VALUES),
                        help="comma-separated values")
    parser.add_argument("--tiers", default=",".join(DEFAULT_TIERS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--budget", type=int, default=2200)
    args = parser.parse_args(argv)

    values = [float(v) for v in args.values.split(",") if v.strip()]
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    committed = getattr(myelinate, args.name)
    print("%s: committed value %g; sweeping %s on %s (seed %d)"
          % (args.name, committed, values, tiers, args.seed), file=sys.stderr)
    rows = sweep(args.name, values, tiers, seed=args.seed, budget=args.budget)
    print("\n### %s sweep\n" % args.name)
    print(format_sweep(args.name, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
