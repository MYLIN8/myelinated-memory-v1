#!/usr/bin/env python3
"""Myelinated Memory - reference implementation.

A retrieval-strength ("myelination") memory engine: each memory carries a score
in [0, 1] that rises with use (Hebbian boost) and falls with dormancy
(Ebbinghaus decay, modulated by category). Score maps to a tier, and the tier
decides how much of a memory's text is rendered into the context budget:

    Active   (score >  0.5)  full text
    Latent   (0.1 - 0.5)     one-line summary
    Archived (score <= 0.1)  short gist (64 chars; recall no longer emits raw id stubs)

Zero dependencies, stdlib only. Targets Python 3.11 but runs on 3.10.

Where the docs are silent the behaviour chosen here is marked ASSUMPTION so a
benchmark can argue with it rather than with an unstated guess.

Capabilities that are opt-in via the constructor, because the benchmark must be
able to attribute any improvement to a named change:

    similarity=True   blend query similarity into recall ranking   (R1)
    knapsack=True     pack the budget by expected value per char   (R4/R7)
    stale_retirement=True   honour supersede/retire                (R5)
    auto_supersede=True     store a changed-value update instead of absorbing it (D14/R5b)

With the first three off the engine behaves exactly as the original specification
describes, which is what the "M6 pure" benchmark arm measures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import zlib
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 3
# The released version, in one place: scripts/myelinated_mcp.py advertises it to
# every MCP client, and benchmarks/test_engine.py pins it to the newest heading in
# CHANGELOG.md, so a release cannot leave the two disagreeing.
__version__ = "5.4.0"
DEFAULT_STORE = os.path.expanduser("~/.hermes/memory/myelinated.json")
DEFAULT_BUDGET = 2200
SECONDS_PER_DAY = 86400.0

# --- ASSUMPTION: the docs say categories decay at different speeds ("User prefs
# decay slowly; one-off tasks decay fast") but never give numbers. These are the
# reference rates, expressed as the fraction of remaining strength lost per day
# of dormancy.
CATEGORY_DECAY_PER_DAY: Dict[str, float] = {
    "identity": 0.002,
    "preference": 0.010,
    "user": 0.010,
    "general": 0.030,
    "task": 0.050,
    "ephemeral": 0.100,
}
DEFAULT_CATEGORY = "general"

# ASSUMPTION: fresh memories start just above the Active threshold so new
# information is visible immediately; decay is what demotes them.
INITIAL_SCORE = 0.60
# ASSUMPTION: Hebbian boost with diminishing returns -> asymptotic approach to 1.
BOOST_ALPHA = 0.35
# ASSUMPTION: protection pins the score instead of merely disabling decay.
PROTECTED_SCORE = 1.0

ACTIVE_THRESHOLD = 0.5
LATENT_THRESHOLD = 0.1

# ASSUMPTION: duplicate collapsing threshold (Jaccard over content tokens).
DUPLICATE_JACCARD = 0.9
SUMMARY_MAX_CHARS = 160
# ASSUMPTION (R7): an archived memory renders a short gist of its content, not
# an opaque id stub. The specification says "ID stub only", but on a large
# transcript almost everything decays to archived, and id stubs filled the whole
# budget with text the answering model cannot read. A 64-character gist is
# ~2.5x the cost of a stub and carries the actual content.
GIST_MAX_CHARS = 64
# ASSUMPTION: storage is unbounded by default; archived entries are kept.
DEFAULT_MAX_ENTRIES = 0  # 0 = unbounded

# Near-duplicate candidates come from a MinHash-style bottom-k sketch index in
# LSH bands rather than from a full token postings scan. A postings scan
# compares a new memory against every memory sharing a common word, which
# profiled as ~2.9M Jaccard calls for 10,000 memories (12.5s of ingest).
#
# Banding: with k hashes split into b bands of r rows, two sets of similarity j
# share at least one band key with probability 1-(1-j^r)^b. r=1 (every sketch
# hash its own key) gives ~1-(1-j)^k, which at k=8 is >99.9999% recall at the
# 0.9 duplicate threshold.
#
# r=2 was tried and rejected: a single token entering the bottom-k sketch shifts
# the sketch by one position, which breaks *all* consecutive bands at once. It
# measured only 62.6% recall on pairs at Jaccard 0.93 - a documented feature
# silently missing a third of real duplicates. Selectivity is recovered with the
# rarest-key-first scan cap below instead of by coarsening the keys.
SKETCH_SIZE = 8
BAND_ROWS = 1
# Hard ceiling on candidates compared per memory.
MAX_CANDIDATES = 64
# Hard ceiling on index postings examined while gathering candidates. Rarest
# key first, and candidates are then ranked by how many sketch keys they share,
# so the scan cap bounds work without discarding the most likely match.
MAX_POSTINGS_SCAN = 96
# Recall only ranks this many memories before packing. The budget fills long
# before the pool is exhausted, and an unbounded scan makes every recall O(n).
RECALL_POOL = 600
# ASSUMPTION (D17): how many of the highest-weighted query terms contribute to the
# cosine. It is a work bound on the per-memory dot product, not a measured
# optimum. It used to be a bare literal inside ``similarity_scores``, which hid a
# ranking parameter from the tuning inventory and from every sweep.
QUERY_TERM_LIMIT = 48

# ---- knapsack packing weights (R4/R7) -------------------------------------
# ASSUMPTION: how much of a memory's usefulness survives at each detail level.
DETAIL_VALUE: Dict[str, float] = {"full": 1.0, "summary": 0.55, "gist": 0.25}
# MEASURED (round 5) - how much retrieval strength contributes to a memory's
# value for the current question, relative to query similarity.
#
# This term adds a *query-independent* constant to a cosine score in [0, 1], so a
# strong but irrelevant memory could outrank a relevant one. The round-5 re-run of
# `benchmarks/tune_probe.py` (the instrument that had been crashing, D-sweep) on
# the report's own workload, then confirmed on hold-out seeds 3-4 on 206 queries,
# measured the committed 0.35 as the single largest cost to ranking: arm M11
# (M10 with this weight at 0.0) gained +0.058 hit rate (0.835 -> 0.893, level with
# the best non-engine arm), +0.049 nDCG@10, -296 characters per hit and +0.250 on
# the LoCoMo tier (0.433 -> 0.683) at identical supersession behaviour. That passes
# the criterion frozen in docs/FIX-PLAN.md F18 on both hold-out seeds.
#
# The prior still governs budget allocation and tie-breaks (see _utility); it no
# longer decides the order. LEGACY_PRIOR_WEIGHT keeps the pre-round-5 arms
# (M6-M10, M12) reproducible as the before/after control.
PRIOR_WEIGHT = 0.0
LEGACY_PRIOR_WEIGHT = 0.35
# Pinned memories are safety-critical or identity-fixed: they must not be
# crowded out by relevance, so they carry an effectively infinite prior.
PROTECTED_PRIOR = 1e6

TIER_DETAILS: Dict[str, Tuple[str, ...]] = {
    "active": ("full", "summary", "gist"),
    "latent": ("summary", "gist"),
    "archived": ("gist",),
}

_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_RE = re.compile(r"[.!?\n]")

# Index postings are only built for informative tokens; indexing function words
# makes every memory a candidate of every other memory.
INDEX_STOPWORDS = frozenset(
    """a an and are as at be but by for from had has have he her his i if in is it its
    me my no not of on or our she that the their them then there these they this to was
    we were what when which who will with you your""".split()
)


def now_seconds() -> float:
    return time.time()


def _tokens(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def could_match(size_a: int, size_b: int, threshold: float) -> bool:
    """Cheap prefilter: can two token sets of these sizes reach ``threshold``?

    Jaccard(a, b) <= min(|a|, |b|) / max(|a|, |b|), so when the sizes are far
    apart the full comparison cannot succeed and is skipped.
    """
    high = max(size_a, size_b)
    if high == 0:
        return True
    return min(size_a, size_b) >= threshold * high


def _keywords(text: str) -> List[str]:
    """Informative tokens used for indexing/sketching.

    Indexing function words makes every memory a candidate of every other
    memory, so they are excluded; the Jaccard comparison still uses the full
    token set.
    """
    words = [w for w in _WORD_RE.findall((text or "").lower()) if w not in INDEX_STOPWORDS]
    if not words:
        words = _WORD_RE.findall((text or "").lower())[:4]
    return words


def _token_hash(token: str) -> int:
    """Stable across processes (unlike ``hash``), which keeps runs replayable."""
    return zlib.crc32(token.encode("utf-8"))


def bottom_k_sketch(tokens: Iterable[str], k: int = SKETCH_SIZE) -> Tuple[int, ...]:
    """The ``k`` smallest token hashes: a MinHash sketch of the set."""
    hashes = sorted({_token_hash(token) for token in tokens})
    return tuple(hashes[:k])


def make_summary(content: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """First sentence if it fits, otherwise a hard truncation."""
    text = " ".join((content or "").split())
    if len(text) <= limit:
        return text
    cut = _SENTENCE_RE.search(text)
    if cut and 0 < cut.start() <= limit:
        return text[: cut.start() + 1].strip()
    return text[: limit - 3].rstrip() + "..."


def decay_rate(category: str) -> float:
    return CATEGORY_DECAY_PER_DAY.get((category or DEFAULT_CATEGORY).lower(),
                                      CATEGORY_DECAY_PER_DAY[DEFAULT_CATEGORY])


def tier_for(score: float, protected: bool = False) -> str:
    if protected:
        return "active"
    if score > ACTIVE_THRESHOLD:
        return "active"
    if score > LATENT_THRESHOLD:
        return "latent"
    return "archived"


@dataclass
class Memory:
    id: str
    content: str
    summary: str = ""
    category: str = DEFAULT_CATEGORY
    created: float = 0.0
    last_access: float = 0.0
    access_count: int = 0
    score: float = INITIAL_SCORE
    # D1: ``score`` is the strength *as of* ``score_at``. Decay is a pure function of
    # the time between ``score_at`` and ``now`` (see ``MyelinatedMemory.strength``),
    # so realising a memory twice at the same instant is a no-op and a session
    # boundary can never compound it.
    score_at: float = 0.0
    protected: bool = False
    tier: str = "active"
    retired: bool = False
    superseded_by: Optional[str] = None

    def days_dormant(self, now: float) -> float:
        """Time since the memory was last used (informational; decay uses score_at)."""
        return max(0.0, (now - self.last_access) / SECONDS_PER_DAY)

    def days_since_realized(self, now: float) -> float:
        """Dormancy that has not been folded into ``score`` yet (D1)."""
        return max(0.0, (now - self.score_at) / SECONDS_PER_DAY)


@dataclass
class RecallResult:
    text: str
    used_ids: List[str] = field(default_factory=list)
    # The pre-packing rank order the packer was handed. Without it the harness has
    # nothing to score the *ranker* with, so it falls back to ``used_ids`` and
    # scores the allocator's order instead (defect D8/F21). Ranking quality and
    # allocation quality are then indistinguishable in the report.
    ranked_ids: List[str] = field(default_factory=list)
    tiers: Dict[str, int] = field(default_factory=dict)
    chars: int = 0
    budget: int = DEFAULT_BUDGET
    similarity_used: bool = False
    knapsack_used: bool = False
    retired_excluded: int = 0


class MyelinatedMemory:
    """The engine. Pass an explicit ``now`` to drive virtual time in tests."""

    def __init__(
        self,
        path: Optional[str] = None,
        clock: Callable[[], float] = now_seconds,
        in_memory: bool = False,
        similarity: bool = True,
        knapsack: bool = True,
        stale_retirement: bool = True,
        auto_supersede: bool = True,
        *,
        prior_weight: Optional[float] = None,
        max_candidates: Optional[int] = None,
        max_postings_scan: Optional[int] = None,
        recall_pool: Optional[int] = None,
    ):
        # ``in_memory=True`` keeps the engine entirely in RAM and never reads or
        # writes the user store. The benchmark harness embeds it this way.
        self.path = "" if in_memory else (path or os.environ.get("HERMES_MEMORY_STORE") or DEFAULT_STORE)
        self.clock = clock
        self.similarity = similarity
        self.knapsack = knapsack
        self.stale_retirement = stale_retirement
        # D14/R5b: an incoming memory that is >= DUPLICATE_JACCARD similar to a live one
        # but is not the same token set is an *update* - it is stored and the older
        # wording is retired, instead of the new text being silently absorbed. Its own
        # switch so a future ablation arm can attribute it (BM-002).
        self.auto_supersede = auto_supersede

        # Recall parameters, resolvable per instance. These were module constants,
        # so attributing a ranking change to the constant it varies meant editing
        # the engine between sweeps - which makes a sweep easy to contaminate with
        # an edit nobody recorded. Each defaults to the module constant; for the
        # three ceilings, 0 means *unbounded* (no cap), so a probe can lift a
        # ceiling without inventing a sentinel of its own. Default behaviour is
        # unchanged: passing nothing resolves to exactly the old constants.
        self.prior_weight = PRIOR_WEIGHT if prior_weight is None else prior_weight
        self.max_candidates = MAX_CANDIDATES if max_candidates is None else max_candidates
        self.max_postings_scan = MAX_POSTINGS_SCAN if max_postings_scan is None else max_postings_scan
        self.recall_pool = RECALL_POOL if recall_pool is None else recall_pool

        self.memories: Dict[str, Memory] = {}
        # Content never changes after creation, so these are cached by id.
        self._tok: Dict[str, frozenset] = {}
        self._norm: Dict[str, str] = {}
        # Per-memory bottom-k sketch, cached by id (probe side: _sketch_for_content).
        self._sketch_cache: Dict[str, Tuple[int, ...]] = {}
        # LSH band key -> memory ids (candidate generation).
        self._sketch_index: Dict[Tuple[int, ...], List[str]] = {}
        # Memories added or accessed since the last refresh.
        self._touched: set = set()
        # TF-IDF state for query similarity; rebuilt lazily when the store moves.
        self._sim_dirty = True
        self._idf: Dict[str, float] = {}
        self._dvec: Dict[str, Dict[str, float]] = {}
        self._dnorm: Dict[str, float] = {}

        if self.path and os.path.exists(self.path):
            self.load()

    # --------------------------------------------------------------- strength
    def strength(self, mem: "Memory", now: float) -> float:
        """The memory's current retrieval strength. Pure: it never mutates.

        ``score`` is stored as of ``score_at``, so the decay accrued since then is
        applied here exactly once however often it is asked for. That is what makes
        D1 (compounding decay) impossible rather than merely fixed.
        """
        if mem.protected:
            return PROTECTED_SCORE
        return mem.score * math.exp(-decay_rate(mem.category) * mem.days_since_realized(now))

    def realize(self, mem: "Memory", now: float) -> float:
        """Fold the accrued decay into ``mem.score``. Idempotent at a fixed ``now``."""
        mem.score = self.strength(mem, now)
        mem.score_at = now
        return mem.score

    def _touch(self, mem: "Memory") -> None:
        """The single funnel for "this memory moved": dirty set + index invalidation.

        Every mutator goes through here, which is why ``retire()`` can no longer
        leave a stale TF-IDF index behind (D9 / W0.7).
        """
        self._touched.add(mem.id)
        self._sim_dirty = True

    # ------------------------------------------------------------------ util
    def _tok_of(self, mem: "Memory") -> frozenset:
        cached = self._tok.get(mem.id)
        if cached is None:
            cached = frozenset(_tokens(mem.content))
            self._tok[mem.id] = cached
        return cached

    def _sketch_for_memory(self, mem: "Memory") -> Tuple[int, ...]:
        """The stored memory's sketch, cached by id."""
        cached = self._sketch_cache.get(mem.id)
        if cached is None:
            cached = bottom_k_sketch(self._tok_of(mem))
            self._sketch_cache[mem.id] = cached
        return cached

    def _sketch_for_content(self, content: str) -> Tuple[int, ...]:
        """The sketch of content that is not in the store yet (a probe)."""
        return bottom_k_sketch(_keywords(content))

    @staticmethod
    def _bands(sketch: Tuple[int, ...]) -> Tuple[Tuple[int, ...], ...]:
        return tuple(
            tuple(sketch[i:i + BAND_ROWS])
            for i in range(0, len(sketch) - BAND_ROWS + 1, BAND_ROWS)
        )

    def _new_id(self, content: str) -> str:
        digest = hashlib.sha1(normalize(content).encode("utf-8")).hexdigest()[:10]
        candidate = "m-" + digest
        suffix = 1
        while candidate in self.memories:
            suffix += 1
            candidate = "m-%s-%d" % (digest, suffix)
        return candidate

    # --------------------------------------------------------------- indexing
    def _index_insert(self, mem: Memory) -> None:
        self._tok[mem.id] = frozenset(_tokens(mem.content))
        # The sketch must be built from the SAME token set the lookup uses, or
        # inserted memories and probed memories land in different buckets.
        sketch = self._sketch_for_content(mem.content)
        self._sketch_cache[mem.id] = sketch
        self._norm[normalize(mem.content)] = mem.id
        for key in self._bands(sketch):
            self._sketch_index.setdefault(key, []).append(mem.id)
        self._sim_dirty = True

    def _index_remove(self, mem: Memory) -> None:
        self._touched.discard(mem.id)
        sketch = self._sketch_cache.pop(mem.id, None)
        self._tok.pop(mem.id, None)
        self._norm.pop(normalize(mem.content), None)
        for key in self._bands(sketch or ()):
            ids = self._sketch_index.get(key)
            if ids and mem.id in ids:
                ids.remove(mem.id)
                if not ids:
                    del self._sketch_index[key]
        self._sim_dirty = True

    def _rank_by_shared_keys(
        self,
        sketch: Tuple[int, ...],
        postings: Dict[Tuple[int, ...], List[str]],
        scan_cap: int,
        cap: int,
    ) -> List[str]:
        """Ids ranked by how many sketch keys they share with ``sketch``.

        It backs ``_candidates`` (duplicate and update lookup), the only caller:
        the ingest-time clustering pass that shared this body was deleted in
        5.4.0, because nothing in the tree read its output.

        Keys are read rarest-first and the scan stops after ``scan_cap`` postings,
        so one common key can never make a lookup scan the whole store. Candidates
        are then ordered by shared-key count rather than by insertion order: with a
        small vocabulary many memories collide on a few keys, and taking the first
        N seen discarded the real match about a third of the time.

        A ``scan_cap`` or ``cap`` of 0 means unbounded: no postings ceiling and no
        candidate ceiling is applied, which is the configuration a probe uses to
        ask whether a capped candidate set is what cost it the match.
        """
        shared: Dict[str, int] = {}
        scanned = 0
        for key in sorted(self._bands(sketch), key=lambda k: len(postings.get(k, ()))):
            ids = postings.get(key)
            if not ids:
                continue
            for candidate in ids:
                shared[candidate] = shared.get(candidate, 0) + 1
                scanned += 1
            if scan_cap and scanned >= scan_cap:
                break
        ranked = sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))
        if cap == 0 or len(ranked) <= cap:
            return [candidate for candidate, _ in ranked]
        return [candidate for candidate, _ in ranked[:cap]]

    def _candidates(self, sketch: Tuple[int, ...], limit: Optional[int] = None) -> List[str]:
        """Most likely near-duplicates in the whole store.

        ``limit`` defaults to the instance's ``max_candidates``; 0 means unbounded,
        as in ``_rank_by_shared_keys``.
        """
        cap = self.max_candidates if limit is None else limit
        return self._rank_by_shared_keys(sketch, self._sketch_index, self.max_postings_scan, cap)

    def _find_duplicate(self, content: str) -> Optional[Memory]:
        """The *live* memory this content restates, if any.

        A retired memory is never returned. D13 was a correction being absorbed by
        the dead entry it replaced: the new text was stored nowhere and recall came
        back empty.
        """
        exact_id = self._norm.get(normalize(content))
        if exact_id is not None:
            exact = self.memories.get(exact_id)
            if exact is not None and not exact.retired:
                return exact
        tokens = frozenset(_tokens(content))
        size = len(tokens)
        for candidate_id in self._candidates(self._sketch_for_content(content)):
            other = self.memories.get(candidate_id)
            if other is None or other.retired:
                continue
            other_tokens = self._tok_of(other)
            if not could_match(size, len(other_tokens), DUPLICATE_JACCARD):
                continue
            if jaccard(tokens, other_tokens) >= DUPLICATE_JACCARD:
                return other
        return None

    def _same_fact(self, content: str, other: "Memory") -> bool:
        """True when the content has the same token *set* as ``other``.

        A restatement (punctuation, whitespace, case) collapses into one entry as
        before. A same-subject pair whose token set differs is an update, and an
        update must keep its own text (D14).
        """
        return frozenset(_tokens(content)) == self._tok_of(other)

    # ------------------------------------------------------------------- api
    def add(
        self,
        content: str,
        category: str = DEFAULT_CATEGORY,
        protected: bool = False,
        memory_id: Optional[str] = None,
        now: Optional[float] = None,
        supersedes: Optional[str] = None,
    ) -> str:
        """Add a memory. Near-duplicates collapse into the existing entry."""
        now = self.clock() if now is None else now
        content = (content or "").strip()
        if not content:
            raise ValueError("content must not be empty")

        duplicate = self._find_duplicate(content)
        if duplicate is not None and self._same_fact(content, duplicate):
            mem_id = self._collapse_into(duplicate, protected=protected, now=now)
        else:
            mem_id = self._insert_new(
                content, category=category, protected=protected,
                memory_id=memory_id, now=now, duplicate=duplicate)

        if supersedes and self.stale_retirement:
            self.retire(supersedes, superseded_by=mem_id)
        return mem_id

    def _collapse_into(self, duplicate: "Memory", protected: bool, now: float) -> str:
        """Absorb a restatement into the entry it restates (same token set).

        "dupes auto-collapse": the existing entry wins and is strengthened; the
        only new state is a pin. Split out of ``add()`` so the write path reads as
        the distinct outcomes it implements rather than as one long branch.
        """
        self.access(duplicate.id, now=now)
        if protected and not duplicate.protected:
            duplicate.protected = True
            # Pin the stored score too, so ``score`` and the derived
            # ``strength()`` agree - a protected memory's strength is
            # PROTECTED_SCORE whatever ``score`` holds (``pin()`` does the
            # same). Leaving it at 0.60 made ``refresh()`` report this memory
            # as *decayed* on the next boundary, because realize() raised its
            # score from 0.60 to 1.0.
            duplicate.score = PROTECTED_SCORE
            duplicate.tier = tier_for(duplicate.score, True)
            # D9 invariant: every mutator funnels through _touch() so the cached
            # state (dirty set, TF-IDF index) cannot go stale. This branch was
            # the one mutator that skipped it.
            self._touch(duplicate)
        return duplicate.id

    def _insert_new(
        self,
        content: str,
        category: str,
        protected: bool,
        memory_id: Optional[str],
        now: float,
        duplicate: Optional["Memory"],
    ) -> str:
        """Store ``content`` as a new entry, superseding ``duplicate``'s wording.

        When ``duplicate`` is a live memory with a *different* token set, this is an
        update (same subject, changed value), so the older wording is retired and
        what recall surfaces is the correction rather than the stale text.
        """
        mem = Memory(
            id=memory_id or self._new_id(content),
            content=content,
            summary=make_summary(content),
            category=(category or DEFAULT_CATEGORY).lower(),
            created=now,
            last_access=now,
            access_count=0,
            score=PROTECTED_SCORE if protected else INITIAL_SCORE,
            score_at=now,
            protected=bool(protected),
        )
        mem.tier = tier_for(mem.score, mem.protected)
        self.memories[mem.id] = mem
        self._index_insert(mem)
        self._touch(mem)
        if duplicate is not None and self.auto_supersede and self.stale_retirement:
            self.retire(duplicate.id, superseded_by=mem.id)
        return mem.id

    def access(self, memory_id: str, now: Optional[float] = None) -> bool:
        """Hebbian boost: strengthen a memory because it was used.

        The boost is applied to the *decayed* strength (D1). Decay and boost only
        commute if this is done, otherwise one use would erase an arbitrarily long
        dormancy.
        """
        mem = self.memories.get(memory_id)
        if mem is None or mem.retired:
            # A retired memory is never strengthened - the same dead-entry rule
            # as pin() (D18). A caller that means to use a dead fact gets False
            # and the store stays honest.
            return False
        now = self.clock() if now is None else now
        current = self.realize(mem, now)
        if not mem.protected:
            mem.score = current + BOOST_ALPHA * (1.0 - current)
        else:
            mem.score = PROTECTED_SCORE
        mem.access_count += 1
        mem.last_access = now
        mem.tier = tier_for(mem.score, mem.protected)
        self._touch(mem)
        return True

    def reinforce(self, memory_ids: Iterable[str], now: Optional[float] = None) -> int:
        """Utility-credit reinforcement (R2): boost memories that were in the
        context when a question was answered correctly. Distinct from ``access``
        only in intent - it is the harness's learning signal, not a usage event.
        """
        count = 0
        for memory_id in memory_ids:
            count += 1 if self.access(memory_id, now=now) else 0
        return count

    def retire(self, memory_id: str, superseded_by: Optional[str] = None) -> bool:
        """Supersession (R5): the fact changed, so stop surfacing the old one."""
        mem = self.memories.get(memory_id)
        if mem is None:
            return False
        mem.retired = True
        if superseded_by:
            mem.superseded_by = superseded_by
        # D9: retiring changes which memories the similarity index should contain, so
        # it invalidates it exactly like any other mutation.
        self._touch(mem)
        return True

    def pin(self, memory_id: str) -> bool:
        mem = self.memories.get(memory_id)
        if mem is None or mem.retired:
            # Pinning a retired memory would resurrect it as a hidden 1.0 instead of
            # bringing it back, so refuse rather than pretend (D18).
            return False
        mem.protected = True
        mem.score = PROTECTED_SCORE
        mem.tier = tier_for(mem.score, True)
        self._touch(mem)
        return True

    def get(self, memory_id: str) -> Optional[Memory]:
        return self.memories.get(memory_id)

    def all(self) -> List[Memory]:
        return list(self.memories.values())

    def size(self) -> int:
        return len(self.memories)

    # ------------------------------------------------------------ similarity
    def _build_similarity(self) -> None:
        """TF-IDF vectors over the live store, for query-conditioned recall."""
        n = 0
        df: Dict[str, int] = {}
        counts: Dict[str, Dict[str, int]] = {}
        for mem in self.memories.values():
            if mem.retired:
                continue
            tf: Dict[str, int] = {}
            for token in self._tok_of(mem):
                tf[token] = tf.get(token, 0) + 1
            counts[mem.id] = tf
            n += 1
            for token in tf:
                df[token] = df.get(token, 0) + 1
        total = n or 1
        self._idf = {t: math.log((total + 1.0) / (c + 1.0)) + 1.0 for t, c in df.items()}
        self._dvec = {}
        self._dnorm = {}
        for mem_id, tf in counts.items():
            vec = {t: c * self._idf[t] for t, c in tf.items()}
            self._dvec[mem_id] = vec
            # fsum is exactly rounded, so the norm cannot depend on the
            # hash-order iteration of the token set (cross-process replay
            # must be byte-identical, not just metric-identical).
            self._dnorm[mem_id] = math.sqrt(math.fsum(w * w for w in vec.values()))
        self._sim_dirty = False

    def similarity_scores(self, query: str) -> Dict[str, float]:
        """Cosine similarity of ``query`` against every live memory, in [0, 1]."""
        if self._sim_dirty:
            self._build_similarity()
        if not self._dvec:
            return {}
        q_tf: Dict[str, int] = {}
        for token in _tokens(query):
            q_tf[token] = q_tf.get(token, 0) + 1
        q_vec = {t: c * self._idf.get(t, 0.0) for t, c in q_tf.items()}
        q_norm = math.sqrt(math.fsum(w * w for w in q_vec.values()))
        if q_norm == 0.0:
            return {}
        # The term tiebreak keeps the QUERY_TERM_LIMIT cut deterministic:
        # without it, equal-weight terms keep their hash-order input order
        # and two processes can hand the dot product different term sets.
        items = sorted(q_vec.items(), key=lambda kv: (-kv[1], kv[0]))[:QUERY_TERM_LIMIT]
        out: Dict[str, float] = {}
        for mem_id, vec in self._dvec.items():
            norm = self._dnorm.get(mem_id, 0.0)
            if norm == 0.0:
                out[mem_id] = 0.0
                continue
            dot = math.fsum(weight * vec[token] for token, weight in items if token in vec)
            out[mem_id] = dot / (q_norm * norm)
        return out

    # --------------------------------------------------------------- refresh
    def refresh(self, now: Optional[float] = None, max_entries: int = DEFAULT_MAX_ENTRIES) -> Dict[str, int]:
        """Session boundary: decay dormancy, then consolidate.

        ASSUMPTION: consolidation inspects the memories touched since the last
        refresh (added or accessed) rather than the whole store, because
        re-scanning everything each session is quadratic. Candidate lookup still
        spans the whole store, so duplicates are collapsed across sessions.
        """
        now = self.clock() if now is None else now
        decayed = 0
        for mem in self.memories.values():
            before = mem.score
            # Folding at most the decay that has not been applied yet (D1): calling
            # refresh() hourly, daily or once a week now gives the same score curve.
            self.realize(mem, now)
            if mem.score < before - 1e-12:
                decayed += 1
            mem.tier = tier_for(mem.score, mem.protected)

        touched = [mem_id for mem_id in self._touched if mem_id in self.memories]
        self._touched = set()
        merged = self._collapse_duplicates(touched)
        pruned = self._prune(max_entries)
        deficit = max(0, len(self.memories) - max_entries) if max_entries > 0 else 0
        return {
            "decayed": decayed,
            "merged": merged,
            "pruned": pruned,
            "prune_deficit": deficit,
            "total": len(self.memories),
        }

    def _merge_into(self, target: Memory, mem: Memory) -> None:
        target.access_count += mem.access_count
        target.score = max(target.score, mem.score)
        target.last_access = max(target.last_access, mem.last_access)
        target.created = min(target.created, mem.created)
        if mem.protected:
            target.protected = True
            target.score = PROTECTED_SCORE
        target.tier = tier_for(target.score, target.protected)
        del self.memories[mem.id]
        self._index_remove(mem)
        self._touch(target)

    def _collapse_duplicates(self, touched_ids: List[str]) -> int:
        merged = 0
        ordered = [self.memories[i] for i in touched_ids if i in self.memories]
        # Raw scores are comparable here for exactly one reason: refresh() realizes
        # every memory at ``now`` immediately before calling this, so every
        # comparison below (and in _merge_into) shares a single reference time.
        # A future caller that has NOT realized first must compare
        # ``strength(m, now)`` instead, or it compares values measured at
        # different times - which is the D1 error in a new coat.
        # The id tiebreak keeps merge order deterministic: without it, exact
        # ties keep the hash-order input order of the touched set and two
        # processes can merge a three-way duplicate race in different orders.
        ordered.sort(key=lambda m: (-m.score, m.created, m.id))
        for mem in ordered:
            if mem.id not in self.memories or mem.retired:
                # Already merged away by an earlier pass, or retired: a retired memory
                # is never a merge candidate and never a merge winner (D13).
                continue
            tokens = self._tok_of(mem)
            size = len(tokens)
            match = None
            for other_id in self._candidates(self._sketch_for_memory(mem)):
                if other_id == mem.id:
                    continue
                other = self.memories.get(other_id)
                if other is None or other.retired:
                    continue
                other_tokens = self._tok_of(other)
                if not could_match(size, len(other_tokens), DUPLICATE_JACCARD):
                    continue
                if jaccard(tokens, other_tokens) >= DUPLICATE_JACCARD:
                    if match is None or (other.score, -other.created) > (match.score, -match.created):
                        match = other
            if match is None:
                continue
            if tokens == self._tok_of(match):
                # A restatement: keep the stronger entry and fold the weaker into it.
                if (mem.score, -mem.created) > (match.score, -match.created):
                    winner, loser = mem, match
                else:
                    winner, loser = match, mem
            else:
                # Same subject, changed wording (D14): the newer text is the update, so
                # it must win the merge rather than be replaced by the older wording.
                if mem.created >= match.created:
                    winner, loser = mem, match
                else:
                    winner, loser = match, mem
            self._merge_into(winner, loser)
            merged += 1
        return merged

    def _prune(self, max_entries: int) -> int:
        if max_entries <= 0 or len(self.memories) <= max_entries:
            return 0
        candidates = [m for m in self.memories.values() if not m.protected]
        candidates.sort(key=lambda m: (m.score, -m.last_access))
        to_remove = len(self.memories) - max_entries
        pruned = 0
        for mem in candidates:
            if pruned >= to_remove:
                break
            if mem.tier == "archived":
                del self.memories[mem.id]
                self._index_remove(mem)
                pruned += 1
        return pruned

    # ---------------------------------------------------------------- recall
    def ordered(self) -> List[Memory]:
        """Priority order: pinned first, then score, recency and creation."""
        return sorted(
            self.memories.values(),
            key=lambda m: (m.retired, not m.protected, -m.score, -m.last_access, -m.created, m.id),
        )

    def render(self, mem: Memory, detail: str) -> str:
        """Text for a memory at a given tier detail: full, summary or gist."""
        if detail == "full":
            return mem.content
        if detail == "summary":
            return mem.summary or make_summary(mem.content)
        if detail == "gist":
            return make_summary(mem.content, GIST_MAX_CHARS)
        return "[%s] (archived)" % mem.id

    def _utility(self, mem: Memory, sims: Optional[Dict[str, float]], now: float,
                 current: Optional[Dict[str, float]] = None) -> float:
        score = current[mem.id] if current else self.strength(mem, now)
        sim = sims.get(mem.id, 0.0) if sims else 0.0
        value = sim + self.prior_weight * score
        if mem.protected:
            value += PROTECTED_PRIOR
        return value

    def _recall_tiered(self, candidates: List[Memory], sims: Optional[Dict[str, float]],
                       budget: int, now: float,
                       current: Optional[Dict[str, float]] = None) -> RecallResult:
        """The specification's behaviour: walk the priority order, render at the
        highest tier that fits, fill until the budget is exhausted."""
        lines: List[str] = []
        used: List[str] = []
        tiers: Dict[str, int] = {}
        spent = 0
        for mem in candidates:
            score = current[mem.id] if current else self.strength(mem, now)
            tier = tier_for(score, mem.protected)
            mem.tier = tier
            for detail in TIER_DETAILS[tier]:
                body = self.render(mem, detail)
                cost = len(body) + (1 if lines else 0)
                if spent + cost <= budget:
                    lines.append(body)
                    used.append(mem.id)
                    spent += cost
                    tiers[tier] = tiers.get(tier, 0) + 1
                    break
        return RecallResult(text="\n".join(lines), used_ids=used, tiers=tiers,
                            chars=spent, budget=budget, similarity_used=sims is not None)

    def _recall_knapsack(self, candidates: List[Memory], sims: Optional[Dict[str, float]],
                         budget: int, now: float,
                         current: Optional[Dict[str, float]] = None) -> RecallResult:
        """Greedy knapsack over (memory, detail) pairs by expected value per
        character, so a cheap summary can outrank an expensive full text."""
        scored: List[Tuple[float, str, str, int]] = []
        for mem in candidates:
            score = current[mem.id] if current else self.strength(mem, now)
            tier = tier_for(score, mem.protected)
            mem.tier = tier
            utility = self._utility(mem, sims, now, current)
            if utility <= 0.0:
                continue
            for detail in TIER_DETAILS[tier]:
                body = self.render(mem, detail)
                cost = len(body)
                if cost <= 0:
                    continue
                value = utility * DETAIL_VALUE[detail]
                scored.append((value / cost, mem.id, detail, cost))
        scored.sort(key=lambda item: (-item[0], item[1]))

        lines: List[str] = []
        used: List[str] = []
        tiers: Dict[str, int] = {}
        chosen: set = set()
        spent = 0
        for _ratio, mem_id, detail, cost in scored:
            if mem_id in chosen:
                continue
            total = cost + (1 if lines else 0)
            if spent + total > budget:
                continue
            mem = self.memories[mem_id]
            lines.append(self.render(mem, detail))
            used.append(mem_id)
            chosen.add(mem_id)
            spent += total
            tiers[mem.tier] = tiers.get(mem.tier, 0) + 1
        return RecallResult(text="\n".join(lines), used_ids=used, tiers=tiers,
                            chars=spent, budget=budget, similarity_used=sims is not None,
                            knapsack_used=True)

    def _ordered_candidates(self, query: Optional[str], now: float,
                            want_similarity: bool) -> Tuple[List[Memory],
                                                            Optional[Dict[str, float]],
                                                            Dict[str, float]]:
        """The pool the packer is given: live memories, query-ranked, then capped.

        One funnel for both ``recall()`` and ``candidate_pool()``, so the pool a
        probe measures is the pool recall actually used and the two can never
        drift apart - the D9 lesson (one ``_touch`` funnel) applied to the ranker.
        """
        live = [m for m in self.memories.values() if not m.retired]
        # Strength is a pure function of (score, score_at, category, now), so a query
        # ranks and packs the current value instead of whatever the last refresh left
        # behind (D1). Computed once per candidate and reused by both packers.
        current = {m.id: self.strength(m, now) for m in live}

        sims: Optional[Dict[str, float]] = None
        if query and want_similarity:
            sims = self.similarity_scores(query)

        if sims is not None:
            live.sort(key=lambda m: (
                not m.protected,
                -(sims.get(m.id, 0.0) + self.prior_weight * current[m.id]),
                -current[m.id],
                m.id,
            ))
        else:
            live.sort(key=lambda m: (not m.protected, -current[m.id], -m.last_access,
                                     -m.created, m.id))
        # ``recall_pool`` of 0 is unbounded: rank the whole live store.
        pool = live if not self.recall_pool else live[:self.recall_pool]
        return pool, sims, current

    def candidate_pool(self, query: str, now: Optional[float] = None) -> List[str]:
        """Read-only: the ids recall would rank for ``query``, before packing.

        This is the pool *after* candidate generation and the ``recall_pool`` cut
        but *before* scoring, packing or the character budget: the set the ranker
        is handed. It exists so a probe can separate "the engine never considered
        the right memory" (a candidate-generation ceiling) from "it considered it
        and ranked or packed it too low" (a ranking or allocation loss) - two
        failures that the recall metrics alone cannot tell apart.

        It mutates nothing: no score, no tier, no index, no counters. The only
        thing it can do is rebuild the lazily-cached TF-IDF state, which
        ``similarity_scores`` would rebuild on the next query anyway.
        """
        now = self.clock() if now is None else now
        pool, _sims, _current = self._ordered_candidates(query, now, self.similarity)
        return [m.id for m in pool]

    def recall(self, budget: int = DEFAULT_BUDGET, now: Optional[float] = None,
               query: Optional[str] = None, use_similarity: Optional[bool] = None,
               force_similarity: Optional[bool] = None) -> RecallResult:
        """Context injection, filled to the character budget.

        ``query`` is optional. When present and ``similarity`` is enabled the
        ranking blends query similarity with retrieval strength (R1); otherwise
        recall stays query-blind exactly as the original specification describes.
        ``use_similarity`` overrides the engine default when given;
        ``force_similarity`` overrides both the default and any ``use_similarity``
        value, so a caller can pin the behaviour for one recall without mutating
        the engine. The CLI's ``--force-similarity``/``--no-similarity`` map to it.
        """
        now = self.clock() if now is None else now
        live = [m for m in self.memories.values() if not m.retired]
        excluded = len(self.memories) - len(live)
        want_similarity: Optional[bool]
        if force_similarity is not None:
            want_similarity = force_similarity
        elif use_similarity is not None:
            want_similarity = use_similarity
        else:
            want_similarity = self.similarity
        pool, sims, current = self._ordered_candidates(query, now, want_similarity)
        result = (self._recall_knapsack(pool, sims, budget, now, current) if self.knapsack
                  else self._recall_tiered(pool, sims, budget, now, current))
        result.retired_excluded = excluded
        # Recorded at the one return point, so both packers and both the
        # query-ranked and query-blind paths report the same order. No extra scan:
        # this is the pool the packer was already handed.
        result.ranked_ids = [m.id for m in pool]
        return result

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> Dict:
        return {
            "schema": SCHEMA_VERSION,
            "memories": [asdict(m) for m in self.memories.values()],
        }

    def save(self, path: Optional[str] = None) -> str:
        target = path or self.path
        if not target:
            return ""
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, target)
        return target

    def load(self, path: Optional[str] = None) -> int:
        target = path or self.path
        if not target or not os.path.exists(target):
            return 0
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        # The file carries a schema version; loading must honour it. A newer
        # store is refused rather than loaded lossily (unknown fields dropped by
        # the field filter below), and older stores fall through to migration.
        if not isinstance(payload, dict):
            raise ValueError("store %s is not a memory store object" % target)
        schema = payload.get("schema", 2)
        if not isinstance(schema, int) or isinstance(schema, bool):
            raise ValueError("store %s has a malformed schema version: %r" % (target, schema))
        if schema > SCHEMA_VERSION:
            raise ValueError(
                "store %s is schema v%d but this build understands up to v%d; "
                "loading it would silently drop fields - upgrade the engine instead"
                % (target, schema, SCHEMA_VERSION))
        # The top level is not the only shape that can be wrong. A file that is
        # valid JSON but not a list of memory objects used to reach ``raw.items()``
        # and ``Memory(**known)`` uncaught, so a wrong-shaped store escaped as an
        # AttributeError/TypeError traceback and exit 1 - the opposite of the clean
        # ``error: ...`` line and exit 2 that SECURITY.md promises for a corrupt
        # store. Every shape error is now a ValueError, like the schema errors above.
        raw_memories = payload.get("memories", [])
        if not isinstance(raw_memories, list):
            raise ValueError(
                "store %s has a malformed memories section: expected a list, got %s"
                % (target, type(raw_memories).__name__))
        # Parse and validate the whole file before touching live state, so a
        # malformed entry cannot leave the engine half-loaded.
        parsed: List[Memory] = []
        for index, raw in enumerate(raw_memories):
            if not isinstance(raw, dict):
                raise ValueError(
                    "store %s has a malformed memory at index %d: expected an object, got %s"
                    % (target, index, type(raw).__name__))
            known = {k: v for k, v in raw.items() if k in Memory.__dataclass_fields__}
            missing = [name for name in ("id", "content") if name not in known]
            if missing:
                raise ValueError(
                    "store %s has a malformed memory at index %d: missing %s"
                    % (target, index, ", ".join(missing)))
            mem = Memory(**known)
            if not mem.summary:
                mem.summary = make_summary(mem.content)
            if not mem.score_at:
                # A v2 file has no ``score_at``: its score was last realized at its last
                # access (or its creation), so decay from there and never from 0.
                mem.score_at = mem.last_access or mem.created or 0.0
            mem.tier = tier_for(mem.score, mem.protected)
            parsed.append(mem)

        self.memories = {}
        self._tok = {}
        self._sketch_cache = {}
        self._norm = {}
        self._sketch_index = {}
        self._touched = set()
        self._sim_dirty = True
        for mem in parsed:
            self.memories[mem.id] = mem
            self._index_insert(mem)
        return len(self.memories)

    def stats(self) -> Dict:
        """Tier distribution and mean strength, derived at the current clock.

        Both read ``strength(mem, now)``, not the stored ``mem.score``. ``score``
        is only the strength *as of* ``score_at`` (D1), so reporting it directly
        made this disagree with what recall would do: a store left dormant for a
        month reported Active memories that recall would have packed as archived.
        """
        now = self.clock()
        counts = {"active": 0, "latent": 0, "archived": 0}
        protected = 0
        retired = 0
        total_strength = 0.0
        for mem in self.memories.values():
            strength = self.strength(mem, now)
            counts[tier_for(strength, mem.protected)] += 1
            total_strength += strength
            protected += 1 if mem.protected else 0
            retired += 1 if mem.retired else 0
        return {
            "total": len(self.memories),
            "protected": protected,
            "retired": retired,
            "tiers": counts,
            "avg_score": (total_strength / len(self.memories)) if self.memories else 0.0,
        }


# ------------------------------------------------------------------ CLI ----
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myelinate", description="Myelinated Memory engine")
    parser.add_argument("--store", default=None, help="path to the memory store")
    parser.add_argument("--pure", action="store_true",
                        help="disable similarity, knapsack and supersession (spec-only behaviour)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add a memory (dupes auto-collapse)")
    p_add.set_defaults(handler=_cmd_add)
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--category", default=DEFAULT_CATEGORY)
    p_add.add_argument("--protected", action="store_true")
    p_add.add_argument("--id", dest="memory_id", default=None)
    p_add.add_argument("--supersedes", default=None,
                       help="id of the memory this one replaces")

    p_access = sub.add_parser("access", help="strengthen a memory when it is used")
    p_access.set_defaults(handler=_cmd_access)
    p_access.add_argument("memory_id")

    p_retire = sub.add_parser("retire", help="stop surfacing a superseded memory")
    p_retire.set_defaults(handler=_cmd_retire)
    p_retire.add_argument("memory_id")

    p_pin = sub.add_parser("pin", help="protect a memory from decay and pruning")
    p_pin.set_defaults(handler=_cmd_pin)
    p_pin.add_argument("memory_id")

    p_recall = sub.add_parser("recall", help="context injection, filled to the budget")
    p_recall.set_defaults(handler=_cmd_recall)
    p_recall.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_recall.add_argument("--query", default=None,
                          help="blend query similarity into ranking")
    sim_mode = p_recall.add_mutually_exclusive_group()
    sim_mode.add_argument("--force-similarity", dest="force_similarity",
                          action="store_const", const=True, default=None,
                          help="enable query similarity for this recall even when the engine default is off")
    sim_mode.add_argument("--no-similarity", dest="force_similarity",
                          action="store_const", const=False,
                          help="disable query similarity for this recall even when the engine default is on")

    p_refresh = sub.add_parser("refresh", help="session-boundary decay + consolidation")
    p_refresh.set_defaults(handler=_cmd_refresh)
    p_refresh.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES,
                           help="optional storage ceiling; 0 keeps everything (default)")

    p_list = sub.add_parser("list", help="list memories")
    p_list.set_defaults(handler=_cmd_list)
    p_stats = sub.add_parser("stats", help="show tier distribution")
    p_stats.set_defaults(handler=_cmd_stats)

    return parser


# ------------------------------------------------------------------ handlers
def _cmd_add(engine: MyelinatedMemory, args) -> int:
    memory_id = engine.add(
        args.content, category=args.category, protected=args.protected,
        memory_id=args.memory_id, supersedes=args.supersedes,
    )
    engine.save()
    print(memory_id)
    return 0


def _cmd_access(engine: MyelinatedMemory, args) -> int:
    ok = engine.access(args.memory_id)
    engine.save()
    print("strengthened %s" % args.memory_id if ok else "unknown memory %s" % args.memory_id)
    return 0 if ok else 1


def _cmd_retire(engine: MyelinatedMemory, args) -> int:
    ok = engine.retire(args.memory_id)
    engine.save()
    print("retired %s" % args.memory_id if ok else "unknown memory %s" % args.memory_id)
    return 0 if ok else 1


def _cmd_pin(engine: MyelinatedMemory, args) -> int:
    ok = engine.pin(args.memory_id)
    engine.save()
    print("pinned %s" % args.memory_id if ok else "unknown memory %s" % args.memory_id)
    return 0 if ok else 1


def _cmd_recall(engine: MyelinatedMemory, args) -> int:
    result = engine.recall(budget=args.budget, query=args.query,
                           force_similarity=args.force_similarity)
    # recall() also saves: a read is a write, because the memories it packed were
    # strengthened by being used.
    engine.save()
    print(result.text)
    return 0


def _cmd_refresh(engine: MyelinatedMemory, args) -> int:
    report = engine.refresh(max_entries=args.max_entries)
    engine.save()
    print(json.dumps(report, sort_keys=True))
    return 0


def _cmd_list(engine: MyelinatedMemory, args) -> int:
    # Derived strength, not the stored score: ``score`` is only the value as of
    # ``score_at`` (D1), so printing it showed pre-decay numbers that recall
    # would never have produced.
    now = engine.clock()
    for mem in engine.ordered():
        print("%s\t%.4f\t%s%s\t%s" % (mem.id, engine.strength(mem, now), mem.tier,
                                      " retired" if mem.retired else "", mem.summary))
    return 0


def _cmd_stats(engine: MyelinatedMemory, args) -> int:
    print(json.dumps(engine.stats(), indent=2, sort_keys=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if getattr(args, "budget", 0) < 0:
        print("error: --budget must be >= 0", file=sys.stderr)
        return 2
    kwargs = {"similarity": False, "knapsack": False, "stale_retirement": False} if args.pure else {}
    try:
        engine = MyelinatedMemory(path=args.store, **kwargs)
    except ValueError as exc:
        # Corrupt, wrong-shaped or future-schema store: a clean message and exit 2,
        # never a traceback on the terminal that asked for a one-liner.
        print("error: %s" % exc, file=sys.stderr)
        return 2
    # Every subcommand binds its own handler in the parser, so a command that
    # appears in --help always has something behind it: a phantom init/save/load
    # once shipped with no handler, and reached users as a bare exit 2.
    return args.handler(engine, args)


if __name__ == "__main__":
    sys.exit(main())
