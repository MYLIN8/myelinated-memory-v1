"""Shared contracts for the Myelinated Memory benchmark harness.

Nothing in this module depends on the engine under test, and every other
benchmark module depends on this one. Treat the names below as frozen: the
engine adapters, scenario generators, judges and reporters all meet here.

Data model
----------
A *scenario* is a replayable session history plus questions about it.

    Scenario
      events  : ordered memory operations (add / access) with a virtual day
      queries : questions whose answers require specific memories

Ground truth for scoring lives on the query:

    evidence_ids : memories that *should* be surfaced to answer it
    stale_ids    : memories that are superseded and must NOT be surfaced

Virtual time is a float number of seconds since the scenario epoch; every arm
receives the same ``now`` for the same event so decay is comparable.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_BUDGET = 2200
SECONDS_PER_DAY = 86400.0

CATEGORIES = ("identity", "preference", "task", "ephemeral", "general")

# Query kinds, used for per-kind reporting.
QUERY_KINDS = (
    "lookup",         # single specific fact
    "preference",     # durable user preference
    "continuity",     # multi-session task state
    "contradiction",  # value was updated; the old one must not surface
    "budget",         # many memories, little room: prioritisation
    "coldstart",      # nothing has been accessed yet
    "adversarial",    # importance and access frequency disagree
)
# NOTE: this tuple is the set of kinds ``synthetic.generate`` knows how to
# build, not every kind that can appear in a record. The dedicated staleness
# suites emit ``kind="staleness"`` and ``metrics.by_kind`` groups by whatever
# label the records actually carry.


# --------------------------------------------------------------------- text
_WORD_RE = re.compile(r"[a-z0-9]+")

# A deliberately small stopword list: enough for BM25 arms to be sane without
# dragging in a corpus dependency.
STOPWORDS = frozenset(
    """a an and are as at be but by for from had has have he her his i if in is it its
    me my no not of on or our she that the their them then there these they this to was we
    were what when which who will with you your""".split()
)


def tokenize(text: str, drop_stopwords: bool = False) -> List[str]:
    words = _WORD_RE.findall((text or "").lower())
    if drop_stopwords:
        words = [w for w in words if w not in STOPWORDS]
    return words


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def day(day_index: float) -> float:
    """Virtual timestamp for a day offset."""
    return float(day_index) * SECONDS_PER_DAY


# ------------------------------------------------------------------- schema
@dataclass
class Event:
    op: str                       # "add" | "access" | "retire"
    day: float = 0.0             # virtual day offset
    content: str = ""
    id: str = ""
    category: str = "general"
    protected: bool = False
    # Set on an ``add`` event whose fact supersedes an older one. The stale id
    # is also emitted as its own ``retire`` event, so an arm can honour
    # supersession either way; arms with no versioning simply ignore it.
    supersedes: str = ""

    def timestamp(self) -> float:
        return day(self.day)


@dataclass
class Query:
    id: str
    query: str
    answer: str
    evidence_ids: List[str] = field(default_factory=list)
    stale_ids: List[str] = field(default_factory=list)
    session: int = 0
    kind: str = "lookup"
    answer_check: str = "contains"   # "contains" | "exact" | "rubric"


@dataclass
class Scenario:
    id: str
    kind: str
    description: str
    events: List[Event] = field(default_factory=list)
    queries: List[Query] = field(default_factory=list)
    source: str = "synthetic"

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "source": self.source,
            "events": [asdict(e) for e in self.events],
            "queries": [asdict(q) for q in self.queries],
        }

    @classmethod
    def from_dict(cls, raw: Dict) -> "Scenario":
        return cls(
            id=raw["id"],
            kind=raw.get("kind", "mixed"),
            description=raw.get("description", ""),
            source=raw.get("source", "synthetic"),
            events=[Event(**e) for e in raw.get("events", [])],
            queries=[Query(**q) for q in raw.get("queries", [])],
        )


@dataclass
class Recall:
    """What an arm put into the context budget for one query."""
    text: str
    used_ids: List[str] = field(default_factory=list)
    # The arm's own rank order BEFORE packing. An arm that reorders while
    # packing (the engine's value-per-character knapsack does) must report the
    # order it ranked in, or nDCG and MRR silently score the allocator as if it
    # were the retriever (defect D8). Arms that do not distinguish the two
    # orders leave this empty and the replay records ``used_ids`` instead.
    ranked_ids: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    chars: int = 0
    budget: int = DEFAULT_BUDGET

    def __post_init__(self) -> None:
        if not self.chars:
            self.chars = len(self.text)


# --------------------------------------------------------------------- arms
class Arm:
    """One memory system under test. All arms honour the same char budget."""

    name = "arm"
    uses_network = False
    # Whether this arm has an explicit supersession capability. Only arms that
    # set this can be credited on the supersession staleness metric.
    supports_retirement = False
    # Whether this arm implements utility-credit reinforcement (R2).
    supports_reinforcement = False

    def reset(self) -> None:  # pragma: no cover - trivial
        raise NotImplementedError

    def add(self, content: str, *, memory_id: Optional[str] = None, category: str = "general",
            protected: bool = False, now: float = 0.0) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def access(self, memory_id: str, now: float = 0.0) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def retire(self, memory_id: str, now: float = 0.0) -> None:
        """Supersession hook. Arms without versioning ignore it."""
        return None

    def reinforce(self, memory_ids: Sequence[str], now: float = 0.0) -> None:
        """Utility-credit hook: boost memories that were in the context when a
        question was answered correctly. Applied identically to every arm."""
        return None

    def refresh(self, now: float = 0.0) -> None:  # pragma: no cover - trivial
        return None

    def recall(self, query: str, budget: int = DEFAULT_BUDGET, now: float = 0.0) -> Recall:  # pragma: no cover
        raise NotImplementedError

    def size(self) -> int:  # pragma: no cover - abstract
        raise NotImplementedError


# ------------------------------------------------------------ budget packing
def pack_blocks(blocks: Sequence[Tuple[str, str]], budget: int) -> Tuple[str, List[str], int]:
    """Greedily fill ``budget`` chars with ``(id, text)`` blocks in order.

    Returns ``(text, used_ids, chars)``. A block that would overflow the budget
    is skipped rather than truncated, so no arm gains an advantage from
    half-rendered text.
    """
    lines: List[str] = []
    used: List[str] = []
    spent = 0
    for block_id, text in blocks:
        cost = len(text) + (1 if lines else 0)
        if spent + cost > budget:
            continue
        lines.append(text)
        used.append(block_id)
        spent += cost
    return "\n".join(lines), used, spent


def downgrade(text: str, limit: int) -> str:
    """Fit ``text`` into ``limit`` characters, marking a cut with an ellipsis.

    Never returns more than ``limit`` characters, whatever the limit is. The
    original form returned the bare ``"..."`` marker for any ``limit`` below 3,
    which is *longer* than the budget it was asked to fit - harmless at its only
    call site (``engines.Bm25TruncateArm`` refuses to call it with less than
    ``MIN_BLOCK``), but a contract violation in a shared helper, and a quiet way
    for a future arm to overrun the budget it was handed.
    """
    flat = " ".join((text or "").split())
    if limit <= 0:
        return ""
    if len(flat) <= limit:
        return flat
    if limit <= 3:
        # No room for the marker: a plain slice stays inside the limit, whereas
        # an ellipsis alone would not fit.
        return flat[:limit]
    return flat[: limit - 3].rstrip() + "..."


def replay(scenario: Scenario, arm: Arm, budget: int = DEFAULT_BUDGET,
           on_result=None) -> List[Dict]:
    """Replay a scenario against one arm.

    Returns one record per query: the arm's ``Recall`` plus bookkeeping the
    metrics module needs. Events are applied in day order; a refresh happens at
    the start of every day, mirroring the documented session protocol
    (``refresh`` at session start, then ``recall``).

    ``on_result(arm, query, recall, record)`` is called immediately after each
    recall, before the timeline continues. That is where the harness judges the
    answer and may reinforce memories the arm just used, so learning happens
    inside the timeline rather than after the scenario has finished.
    """
    arm.reset()
    records: List[Dict] = []
    order = {"add": 0, "retire": 1, "access": 2}
    events = sorted(scenario.events, key=lambda e: (e.day, order.get(e.op, 3)))
    cursor = 0
    last_day = None

    first_recall = True
    for query in scenario.queries:
        q_day = float(query.session)
        while cursor < len(events) and events[cursor].day <= q_day:
            event = events[cursor]
            if last_day is None or event.day != last_day:
                arm.refresh(event.timestamp())
                last_day = event.day
            if event.op == "add":
                arm.add(
                    event.content,
                    memory_id=event.id or None,
                    category=event.category,
                    protected=event.protected,
                    now=event.timestamp(),
                )
            elif event.op == "retire":
                arm.retire(event.id, event.timestamp())
            elif event.op == "access":
                arm.access(event.id, event.timestamp())
            cursor += 1

        if last_day is None or q_day != last_day:
            arm.refresh(day(q_day))
            last_day = q_day

        result = arm.recall(query.query, budget=budget, now=day(q_day))
        record = {
            "scenario": scenario.id,
            "query_id": query.id,
            "kind": query.kind,
            "session": query.session,
            "query": query.query,
            "answer": query.answer,
            "evidence_ids": list(query.evidence_ids),
            "stale_ids": list(query.stale_ids),
            "used_ids": list(result.used_ids),
            # Rank order as reported by the arm; falls back to the packing order
            # for arms that do not distinguish them.
            "ranked_ids": list(result.ranked_ids) or list(result.used_ids),
            # True for the first recall of this scenario, which is the one that
            # pays any lazy index build. Latency percentiles are computed over
            # the warm samples only (defect D2).
            "cold": first_recall,
            "context": result.text,
            "chars": result.chars,
            "budget": result.budget,
            "latency_ms": result.latency_ms,
            "answer_check": query.answer_check,
        }
        if on_result is not None:
            on_result(arm, query, result, record)
        records.append(record)
        first_recall = False

    return records


def arm_size(arm: Arm) -> int:
    # Deliberately tolerant: this is only used for a report annotation, and an
    # arm that cannot answer ``size()`` must not abort the whole run.
    try:
        return int(arm.size())
    except Exception:
        return 0
