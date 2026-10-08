"""Retrieval and context metrics computed from replay records.

A *record* is one dictionary produced by ``common.replay``: it holds the arm's
``used_ids`` (what actually reached the context, in packing order), the
``ranked_ids`` the arm reported *before* packing, the ground-truth
``evidence_ids``, the superseded ``stale_ids`` and the rendered ``context``.

Rank metrics read ``ranked_ids`` and fall back to ``used_ids`` only for arms
that do not distinguish the two orders. Reading the packing order as the rank
order is defect D8: it scored the allocator's reordering as ranking skill. Do
not "simplify" that fallback away on the assumption that the two are equal -
some arms, the engine among them, deliberately reorder while packing.

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


def _order(record: Dict, order: str = "ranked") -> List[str]:
    """The ids a rank metric should read.

    ``ranked`` is the arm's own order *before* packing, ``packed`` is what
    actually reached the context. Arms that do not distinguish the two report
    the same list for both. Scoring the packed order as retrieval quality is
    defect D8: the allocator's reordering was being read as ranking skill.
    """
    if order == "packed":
        return list(record.get("used_ids") or ())
    return list(record.get("ranked_ids") or record.get("used_ids") or ())


def reciprocal_rank(record: Dict, order: str = "ranked") -> float:
    evidence = set(record.get("evidence_ids") or ())
    for position, mem_id in enumerate(_order(record, order), start=1):
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


def ndcg(record: Dict, k: int = DEFAULT_K, order: str = "ranked") -> Optional[float]:
    """Normalised discounted gain over the arm's own rank order."""
    evidence = set(record.get("evidence_ids") or ())
    if not evidence:
        return None
    used = _order(record, order)[:k]
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


def summarize(records: Sequence[Dict], judged_only: bool = True) -> Dict[str, float]:
    """Arm-level summary across every scored query.

    ``judged_only`` keeps a record the run never judged (a cost cap or a failed
    call) out of ``task_success`` instead of averaging it in as a zero, and the
    judged/skipped/errored counts are reported either way (defects D5/D16).
    """
    scored = _with_evidence(records)
    stale_records = [r for r in records if r.get("stale_ids")]
    judged = [r for r in records if r.get("judge_state", "judged") == "judged"]
    precisions = [evidence_precision(r) for r in scored]
    chars = [r.get("chars", 0) for r in records]
    budgets = [r.get("budget", 1) or 1 for r in records]
    hits = [hit(r) for r in scored]
    # Warm latency only: the first recall of a scenario pays any lazy index
    # build, and a single cold sample inside a five-sample "p95" was the whole
    # of defect D2. Cold samples are reported separately.
    warm = [r.get("latency_ms", 0.0) for r in records if not r.get("cold")]
    cold = [r.get("latency_ms", 0.0) for r in records if r.get("cold")]
    pool = judged if judged_only else records
    return {
        "queries": float(len(records)),
        "scored_queries": float(len(scored)),
        "judged_queries": float(len(judged)),
        "judge_skipped": float(sum(1 for r in records if r.get("judge_state") == "skipped")),
        "judge_errors": float(sum(1 for r in records if r.get("judge_state") == "errored")),
        # End-to-end: share of JUDGED queries whose answer was correct.
        "task_success": _mean([r.get("judge_score", 0.0) for r in pool]),
        "hit_rate": _mean(hits),
        # Ranking is measured on the arm's rank order; the packed order is
        # reported beside it so the allocator's effect stays visible (D8).
        "mrr": _mean([reciprocal_rank(r) for r in scored]),
        "ndcg@10": _mean([ndcg(r) for r in scored]),
        "mrr_packed": _mean([reciprocal_rank(r, "packed") for r in scored]),
        "ndcg@10_packed": _mean([ndcg(r, order="packed") for r in scored]),
        "evidence_precision": _mean(precisions),
        "stale_leak_rate": _mean([stale_leak(r) for r in stale_records]),
        "mean_chars": _mean(chars),
        "budget_utilisation": _mean([c / b for c, b in zip(chars, budgets)]),
        "mean_latency_ms": _mean(warm),
        "p50_latency_ms": _percentile(warm, 0.50),
        "p95_latency_ms": _percentile(warm, 0.95),
        "p99_latency_ms": _percentile(warm, 0.99),
        "cold_ms": _mean(cold),
        "warm_samples": float(len(warm)),
        # Characters spent per query that actually hit evidence: the headline
        # "budget efficiency" number. Lower is better, and `inf` means the arm
        # never surfaced evidence at all (reported, never silently zero).
        # NOTE: this is a ratio of two means over potentially different record
        # sets - `chars` spans every record, `hits` only the ones carrying
        # evidence. On the committed corpora every query has evidence, so the two
        # sets coincide and the ratio is unambiguous; on a corpus with
        # evidence-free queries, their spending would count in the numerator
        # while their misses would not count in the denominator. Left as it is
        # because the definition is published (README 2/3) and changing it would
        # move every figure on the page - but a future corpus should decide this
        # deliberately rather than inherit it.
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
