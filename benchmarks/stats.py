"""Dependency-free statistics for the memory benchmark reporter.

Methods and their assumptions
-----------------------------
The harness compares memory arms on *paired* measurements: every arm answers
the same query on the same scenario, so differences are paired per query and
the natural test is the **Wilcoxon signed-rank** test, which assumes the paired
differences are symmetric about their median but makes no normality assumption
about the values themselves. Effect sizes are reported with **Cliff's delta**
(non-parametric, bounded in [-1, 1]) and uncertainty with a **percentile
bootstrap CI** over the per-query values. Multiple metrics are corrected with
**Holm-Bonferroni**, a step-down procedure that is uniformly more powerful than
a plain Bonferroni correction.

Everything is pure Python: ranks use average ties, the signed-rank variance
carries the standard tie correction, and small samples (n <= 20 non-zero pairs)
use the **exact** null distribution computed by dynamic programming instead of
the normal approximation.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Sequence, Tuple

EXACT_MAX_N = 20


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile. ``q`` is a fraction in [0, 1]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return float(ordered[low]) * (1 - frac) + float(ordered[high]) * frac


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _ranks(values: Sequence[float]) -> List[float]:
    """Average ranks, ascending."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def _tie_sizes(ranks: Sequence[float]) -> List[int]:
    counts: Dict[float, int] = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    return [c for c in counts.values() if c > 1]


def _exact_two_sided_p(ranks: Sequence[float], w_plus: float) -> float:
    """Exact two-sided p by DP over sign combinations (ranks scaled by 2).

    ``w_plus`` is the observed positive rank sum; the caller has already folded
    in ``w_minus`` through the min-statistic (D18: the parameter used to be
    passed here and never read).
    """
    scaled = [int(round(r * 2)) for r in ranks]
    total = sum(scaled)
    counts = [0] * (total + 1)
    counts[0] = 1
    for weight in scaled:
        for s in range(total, weight - 1, -1):
            if counts[s - weight]:
                counts[s] += counts[s - weight]
    cumulative = []
    running = 0
    for c in counts:
        running += c
        cumulative.append(running)
    denom = float(2 ** len(scaled))
    obs = int(round(w_plus * 2))
    at_or_below = cumulative[min(obs, total)] / denom
    at_or_above = 1.0 - (cumulative[obs - 1] / denom if obs > 0 else 0.0)
    return min(1.0, 2.0 * min(at_or_below, at_or_above))


def wilcoxon(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float]:
    """Two-sided Wilcoxon signed-rank test on paired samples."""
    n_pairs = min(len(a), len(b))
    diffs = [a[i] - b[i] for i in range(n_pairs)]
    diffs = [d for d in diffs if d != 0]
    n = len(diffs)
    if n == 0:
        return (0.0, 1.0)

    ranks = _ranks([abs(d) for d in diffs])
    w_plus = sum(r for r, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs) if d < 0)
    statistic = min(w_plus, w_minus)

    if n <= EXACT_MAX_N:
        return (statistic, _exact_two_sided_p(ranks, w_plus))

    mu = n * (n + 1) / 4.0
    variance = n * (n + 1) * (2 * n + 1) / 24.0
    for t in _tie_sizes(ranks):
        variance -= (t ** 3 - t) / 48.0
    if variance <= 0:
        return (statistic, 1.0)
    sigma = math.sqrt(variance)
    correction = 0.5 if w_plus > mu else (-0.5 if w_plus < mu else 0.0)
    z = (w_plus - mu - correction) / sigma
    p = 2.0 * (1.0 - _phi(abs(z)))
    return (statistic, max(0.0, min(1.0, p)))


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = mean,
    n_boot: int = 10000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Tuple[float, float]:
    """Percentile bootstrap confidence interval; deterministic for a given seed."""
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)
    n = len(values)
    stats = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        stats.append(statistic(sample))
    return (percentile(stats, alpha / 2.0), percentile(stats, 1.0 - alpha / 2.0))


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Cliff's delta: (wins - losses) / (len(a) * len(b)) over all pairs."""
    if not a or not b:
        return 0.0
    wins = losses = 0
    for x in a:
        for y in b:
            if x > y:
                wins += 1
            elif x < y:
                losses += 1
    return (wins - losses) / (len(a) * len(b))


def holm(pvalues: Dict[str, float], alpha: float = 0.05) -> Dict[str, Dict]:
    """Holm-Bonferroni step-down correction. ``adjusted`` is the running max."""
    m = len(pvalues)
    if m == 0:
        return {}
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    out: Dict[str, Dict] = {}
    running = 0.0
    for index, (key, p) in enumerate(ordered):
        adjusted = min(1.0, p * (m - index))
        running = max(running, adjusted)
        out[key] = {"p": p, "adjusted": running, "reject": running <= alpha}
    return out


def format_p(p: float) -> str:
    if p < 0.001:
        return "p<0.001"
    return "p=%.3f" % p


def paired_summary(a: Sequence[float], b: Sequence[float], seed: int = 0) -> Dict:
    """Paired comparison of arm ``a`` against arm ``b`` on matched queries."""
    n = min(len(a), len(b))
    pairs_a = [float(a[i]) for i in range(n)]
    pairs_b = [float(b[i]) for i in range(n)]
    diffs = [pairs_a[i] - pairs_b[i] for i in range(n)]
    _, p_value = wilcoxon(pairs_a, pairs_b)
    ci_low, ci_high = bootstrap_ci(diffs, seed=seed)
    return {
        "n": n,
        "mean_a": mean(pairs_a),
        "mean_b": mean(pairs_b),
        "mean_diff": mean(diffs),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_value": p_value,
        "delta": cliffs_delta(pairs_a, pairs_b),
        "significant": p_value < 0.05,
    }


if __name__ == "__main__":
    identical = wilcoxon([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert identical == (0.0, 1.0), identical

    shifted = wilcoxon([9, 8, 10, 9, 9, 10, 9, 8], [1, 2, 1, 2, 1, 1, 2, 1])
    assert shifted[1] < 0.05, shifted

    assert cliffs_delta([3, 4, 5], [1, 2, 3]) > 0
    assert cliffs_delta([1, 2, 3], [3, 4, 5]) < 0

    lo, hi = bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0], seed=7)
    assert lo <= 3.0 <= hi, (lo, hi)
    assert bootstrap_ci([1.0, 2.0, 3.0], seed=7) == bootstrap_ci([1.0, 2.0, 3.0], seed=7)

    corrected = holm({"a": 0.001, "b": 0.04, "c": 0.9})
    assert corrected["a"]["reject"] and not corrected["c"]["reject"]

    # With n pairs the exact two-sided p bottoms out at 2 / 2**n, so a
    # significance check needs at least 6 non-zero pairs, not 3.
    summary = paired_summary(
        [5, 6, 7, 8, 9, 10, 11, 12], [1, 2, 3, 4, 5, 6, 7, 8]
    )
    # The two samples overlap, so Cliff's delta is large but not 1.0.
    assert summary["significant"] and summary["delta"] > 0.5, summary
    assert not paired_summary([5, 6, 7], [1, 2, 3])["significant"]

    assert format_p(0.0004) == "p<0.001" and format_p(0.032) == "p=0.032"
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    print("stats ok")
