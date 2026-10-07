"""Retrieval and context metrics computed from replay records.

A *record* is one dictionary produced by ``common.replay``: it holds the arm's
``used_ids`` (in rank order), the ground-truth ``evidence_ids``, the superseded
``stale_ids`` and the rendered ``context``.

Records with no ``evidence_ids`` have no ground truth and are skipped by the
retrieval metrics rather than being counted as failures.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

DEFAULT_K = 10


def _with_evidence(records: Sequence[Dict]) -> List[Dict]:
    return [r for r in records if r.get("evidence_ids")]


def hit(record: Dict) -> float:
    """1.0 if any evidence memory made it into the context."""
    return 1.0 if set(record.get("evidence_ids") or ()) & set(record.get("used_ids") or ()) else 0.0


def reciprocal_rank(record: Dict) -> float:
    evidence = set(record.get("evidence_ids") or ())
    for position, mem_id in enumerate(record.get("used_ids") or (), start=1):
        if mem_id in evidence:
            return 1.0 / position
    return 0.0


def evidence_precision(record: Dict) -> Optional[float]:
    """Share of the injected memories that are actually evidence."""
    used = record.get("used_ids") or ()
    if not used:
        return None
    evidence = set(record.get("evidence_ids") or ())
    return sum(1 for mem_id in used if mem_id in evidence) / float(len(used))


def _dcg(relevances: Sequence[float]) -> float:
    return sum(rel / math.log2(position + 2) for position, rel in enumerate(relevances))


def ndcg(record: Dict, k: int = DEFAULT_K) -> Optional[float]:
    """Normalised discounted gain over the arm's own rank order."""
    evidence = set(record.get("evidence_ids") or ())
    if not evidence:
        return None
    used = list(record.get("used_ids") or ())[:k]
    relevances = [1.0 if mem_id in evidence else 0.0 for mem_id in used]
    ideal = [1.0] * min(len(evidence), k)
    ideal_gain = _dcg(ideal)
    if ideal_gain == 0.0:
        return None
    return _dcg(relevances) / ideal_gain


def stale_leak(record: Dict) -> Optional[float]:
    """Fraction of superseded memories that still reached the context."""
    stale = set(record.get("stale_ids") or ())
    if not stale:
        return None
    used = set(record.get("used_ids") or ())
    return len(stale & used) / float(len(stale))


def _mean(values: Iterable[float]) -> float:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def _percentile(values: Sequence[float], q: float) -> float:
    values = sorted(v for v in values if v is not None)
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    pos = q * (len(values) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(values) - 1)
    frac = pos - low
    return float(values[low] * (1.0 - frac) + values[high] * frac)


def summarize(records: Sequence[Dict]) -> Dict[str, float]:
    """Arm-level summary across every scored query."""
    scored = _with_evidence(records)
    stale_records = [r for r in records if r.get("stale_ids")]
    precisions = [evidence_precision(r) for r in scored]
    latencies = [r.get("latency_ms", 0.0) for r in records]
    chars = [r.get("chars", 0) for r in records]
    budgets = [r.get("budget", 1) or 1 for r in records]
    hits = [hit(r) for r in scored]
    return {
        "queries": float(len(records)),
        "scored_queries": float(len(scored)),
        # End-to-end: share of queries whose judged answer was correct.
        "task_success": _mean([r.get("judge_score", 0.0) for r in records]),
        "hit_rate": _mean(hits),
        "mrr": _mean([reciprocal_rank(r) for r in scored]),
        "ndcg@10": _mean([ndcg(r) for r in scored]),
        "evidence_precision": _mean(precisions),
        "stale_leak_rate": _mean([stale_leak(r) for r in stale_records]),
        "mean_chars": _mean(chars),
        "budget_utilisation": _mean([c / b for c, b in zip(chars, budgets)]),
        "mean_latency_ms": _mean(latencies),
        "p95_latency_ms": _percentile(latencies, 0.95),
        # Characters spent per query that actually hit evidence: the headline
        # "budget efficiency" number. Lower is better.
        "chars_per_hit": (_mean(chars) / _mean(hits)) if _mean(hits) > 0 else float("inf"),
    }


def by_field(records: Sequence[Dict], field: str = "kind") -> Dict[str, Dict[str, float]]:
    """Break a record set down by one of its labels (kind or tier/source)."""
    groups: Dict[str, List[Dict]] = {}
    for record in records:
        groups.setdefault(record.get(field, "unknown"), []).append(record)
    return {key: summarize(group) for key, group in sorted(groups.items())}


def by_kind(records: Sequence[Dict]) -> Dict[str, Dict[str, float]]:
    return by_field(records, "kind")


def success_scores(records: Sequence[Dict], key: str = "judge_score") -> List[float]:
    """Per-query end-to-end score, in stable query-id order for pairing."""
    return [float(r.get(key, 0.0)) for r in sorted(records, key=lambda r: r["query_id"])]


def paired_keys(records: Sequence[Dict]) -> List[str]:
    return [r["query_id"] for r in sorted(records, key=lambda r: r["query_id"])]


def align(left: Sequence[Dict], right: Sequence[Dict], key: str = "judge_score") -> List[List[float]]:
    """Align two arms' per-query scores by query id.

    Arms occasionally score a different number of queries (a scenario may be
    skipped), so pairing is done on the intersection of query ids rather than
    on list position.
    """
    left_map = {r["query_id"]: float(r.get(key, 0.0)) for r in left}
    right_map = {r["query_id"]: float(r.get(key, 0.0)) for r in right}
    shared = sorted(set(left_map) & set(right_map))
    return [[left_map[q] for q in shared], [right_map[q] for q in shared]]


def format_table(rows: Sequence[Dict[str, object]], columns: Sequence[str]) -> str:
    """Minimal markdown table renderer (no dependencies)."""
    widths = []
    for column in columns:
        width = len(column)
        for row in rows:
            width = max(width, len(_cell(row.get(column, ""))))
        widths.append(width)
    lines = ["| " + " | ".join(c.ljust(w) for c, w in zip(columns, widths)) + " |"]
    lines.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        lines.append(
            "| " + " | ".join(_cell(row.get(name, "")).ljust(w)
                             for name, w in zip(columns, widths)) + " |"
        )
    return "\n".join(lines)


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if value == float("inf"):
            return "inf"
        if abs(value) >= 1000:
            return "%.0f" % value
        return "%.3f" % value
    return str(value)
