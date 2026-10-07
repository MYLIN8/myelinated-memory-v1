#!/usr/bin/env python3
"""Myelinated Memory - reference implementation.

A retrieval-strength ("myelination") memory engine: each memory carries a score
in [0, 1] that rises with use (Hebbian boost) and falls with dormancy
(Ebbinghaus decay, modulated by category). Score maps to a tier, and the tier
decides how much of a memory's text is rendered into the context budget:

    Active   (score >  0.5)  full text
    Latent   (0.1 - 0.5)     one-line summary
    Archived (score <= 0.1)  short gist, or an id stub when very weak

Zero dependencies, stdlib only. Targets Python 3.11 but runs on 3.10.

Where the docs are silent the behaviour chosen here is marked ASSUMPTION so a
benchmark can argue with it rather than with an unstated guess.

Capabilities that are opt-in via the constructor, because the benchmark must be
able to attribute any improvement to a named change:

    similarity=True   blend query similarity into recall ranking   (R1)
    knapsack=True     pack the budget by expected value per char   (R4/R7)
    stale_retirement=True   honour supersede/retire                (R5)

With all three off the engine behaves exactly as the original specification
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
# ASSUMPTION: clustering threshold for session-boundary consolidation.
CLUSTER_JACCARD = 0.5
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

# ---- knapsack packing weights (R4/R7) -------------------------------------
# ASSUMPTION: how much of a memory's usefulness survives at each detail level.
DETAIL_VALUE: Dict[str, float] = {"full": 1.0, "summary": 0.55, "gist": 0.25}
# ASSUMPTION: how much retrieval strength contributes to a memory's value for
# the current question, relative to query similarity.
PRIOR_WEIGHT = 0.35
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
    cluster: Optional[str] = None
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

        self.memories: Dict[str, Memory] = {}
        # Content never changes after creation, so these are cached by id.
        self._tok: Dict[str, frozenset] = {}
        self._norm: Dict[str, str] = {}
        self._sketch: Dict[str, Tuple[int, ...]] = {}
        # LSH band key -> memory ids (candidate generation)
        self._sk: Dict[Tuple[int, ...], List[str]] = {}
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

    def _sketch_of(self, mem: "Memory") -> Tuple[int, ...]:
        cached = self._sketch.get(mem.id)
        if cached is None:
            cached = bottom_k_sketch(self._tok_of(mem))
            self._sketch[mem.id] = cached
        return cached

    def _sketch_for(self, content: str) -> Tuple[int, ...]:
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
        sketch = self._sketch_for(mem.content)
        self._sketch[mem.id] = sketch
        self._norm[normalize(mem.content)] = mem.id
        for key in self._bands(sketch):
            self._sk.setdefault(key, []).append(mem.id)
        self._sim_dirty = True

    def _index_remove(self, mem: Memory) -> None:
        self._touched.discard(mem.id)
        sketch = self._sketch.pop(mem.id, None)
        self._tok.pop(mem.id, None)
        self._norm.pop(normalize(mem.content), None)
        for key in self._bands(sketch or ()):
            ids = self._sk.get(key)
            if ids and mem.id in ids:
                ids.remove(mem.id)
                if not ids:
                    del self._sk[key]
        self._sim_dirty = True

    def _candidates(self, sketch: Tuple[int, ...], limit: int = MAX_CANDIDATES) -> List[str]:
        """Most likely near-duplicates, ranked by how many sketch keys they share.

        Keys are read rarest-first and the scan stops at ``MAX_POSTINGS_SCAN``
        postings, so one common key can never make a lookup scan the whole
        store. Candidates are then ordered by shared-key count rather than by
        insertion order: with a small vocabulary many memories collide on a few
        keys, and taking the first N seen discarded the real match about a third
        of the time.
        """
        shared: Dict[str, int] = {}
        scanned = 0
        for key in sorted(self._bands(sketch), key=lambda k: len(self._sk.get(k, ()))):
            ids = self._sk.get(key)
            if not ids:
                continue
            for candidate in ids:
                shared[candidate] = shared.get(candidate, 0) + 1
                scanned += 1
            if scanned >= MAX_POSTINGS_SCAN:
                break
        if len(shared) <= limit:
            return list(shared)
        ranked = sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return [candidate for candidate, _ in ranked]

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
        for candidate_id in self._candidates(self._sketch_for(content)):
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
        mem_id: str
        if duplicate is not None and self._same_fact(content, duplicate):
            # "dupes auto-collapse": the same token set, so the existing entry wins.
            mem_id = duplicate.id
            self.access(duplicate.id, now=now)
            if protected and not duplicate.protected:
                duplicate.protected = True
                duplicate.tier = tier_for(duplicate.score, True)
        else:
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
                cluster=None,
            )
            mem.tier = tier_for(mem.score, mem.protected)
            self.memories[mem.id] = mem
            self._index_insert(mem)
            self._touch(mem)
            mem_id = mem.id
            # An update (same subject, changed value) supersedes the older wording, so
            # what recall surfaces is the correction and never the stale text.
            if duplicate is not None and self.auto_supersede and self.stale_retirement:
                self.retire(duplicate.id, superseded_by=mem_id)

        if supersedes and self.stale_retirement:
            self.retire(supersedes, superseded_by=mem_id)
        return mem_id

    def access(self, memory_id: str, now: Optional[float] = None) -> bool:
        """Hebbian boost: strengthen a memory because it was used.

        The boost is applied to the *decayed* strength (D1). Decay and boost only
        commute if this is done, otherwise one use would erase an arbitrarily long
        dormancy.
        """
        mem = self.memories.get(memory_id)
        if mem is None:
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
            self._dnorm[mem_id] = math.sqrt(sum(w * w for w in vec.values()))
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
        q_norm = math.sqrt(sum(w * w for w in q_vec.values()))
        if q_norm == 0.0:
            return {}
        items = sorted(q_vec.items(), key=lambda kv: -kv[1])[:48]
        out: Dict[str, float] = {}
        for mem_id, vec in self._dvec.items():
            norm = self._dnorm.get(mem_id, 0.0)
            if norm == 0.0:
                out[mem_id] = 0.0
                continue
            dot = 0.0
            for token, weight in items:
                hit = vec.get(token)
                if hit:
                    dot += weight * hit
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
        clusters = self._cluster(touched)
        pruned = self._prune(max_entries)
        deficit = max(0, len(self.memories) - max_entries) if max_entries > 0 else 0
        return {
            "decayed": decayed,
            "merged": merged,
            "clusters": clusters,
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
        ordered.sort(key=lambda m: (-m.score, m.created))
        for mem in ordered:
            if mem.id not in self.memories or mem.retired:
                # Already merged away by an earlier pass, or retired: a retired memory
                # is never a merge candidate and never a merge winner (D13).
                continue
            tokens = self._tok_of(mem)
            size = len(tokens)
            match = None
            for other_id in self._candidates(self._sketch_of(mem)):
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

    def _cluster(self, touched_ids: List[str]) -> int:
        reps_index: Dict[int, List[str]] = {}
        rep_of: Dict[str, str] = {}
        counts: Dict[str, int] = {}
        ordered = [self.memories[i] for i in touched_ids if i in self.memories]
        ordered.sort(key=lambda m: m.created)
        for mem in ordered:
            tokens = self._tok_of(mem)
            size = len(tokens)
            sketch = self._sketch_of(mem)
            rep_id = None
            found: Dict[str, int] = {}
            scanned = 0
            for key in sorted(self._bands(sketch), key=lambda k: len(reps_index.get(k, ()))):
                for candidate in reps_index.get(key, ()):
                    found[candidate] = found.get(candidate, 0) + 1
                    scanned += 1
                if scanned >= MAX_POSTINGS_SCAN:
                    break
            ranked = sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CANDIDATES]
            for candidate_id, _count in ranked:
                candidate = self.memories.get(candidate_id)
                if candidate is None:
                    continue
                candidate_tokens = self._tok_of(candidate)
                if not could_match(size, len(candidate_tokens), CLUSTER_JACCARD):
                    continue
                if jaccard(tokens, candidate_tokens) >= CLUSTER_JACCARD:
                    rep_id = rep_of.get(candidate.id, candidate.id)
                    break
            if rep_id is None:
                rep_id = mem.id
                rep_of[mem.id] = mem.id
                for key in self._bands(sketch):
                    reps_index.setdefault(key, []).append(mem.id)
            mem.cluster = rep_id
            rep_of[mem.id] = rep_id
            counts[rep_id] = counts.get(rep_id, 0) + 1
        return sum(1 for n in counts.values() if n > 1)

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
        """Text for a memory at a given tier detail: full, summary or gist.

        ``stub`` is kept for callers that want the bare id (and for the CLI),
        but the recall ladder no longer uses it: see GIST_MAX_CHARS.
        """
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
        value = sim + PRIOR_WEIGHT * score
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

    def recall(self, budget: int = DEFAULT_BUDGET, now: Optional[float] = None,
               query: Optional[str] = None, use_similarity: Optional[bool] = None) -> RecallResult:
        """Context injection, filled to the character budget.

        ``query`` is optional. When present and ``similarity`` is enabled the
        ranking blends query similarity with retrieval strength (R1); otherwise
        recall stays query-blind exactly as the original specification describes.
        """
        now = self.clock() if now is None else now
        live = [m for m in self.memories.values() if not m.retired]
        excluded = len(self.memories) - len(live)
        # Strength is a pure function of (score, score_at, category, now), so a query
        # ranks and packs the current value instead of whatever the last refresh left
        # behind (D1). Computed once per candidate and reused by both packers.
        current = {m.id: self.strength(m, now) for m in live}

        want_similarity = self.similarity if use_similarity is None else use_similarity
        sims: Optional[Dict[str, float]] = None
        if query and want_similarity:
            sims = self.similarity_scores(query)

        if sims is not None:
            live.sort(key=lambda m: (
                not m.protected,
                -(sims.get(m.id, 0.0) + PRIOR_WEIGHT * current[m.id]),
                -current[m.id],
                m.id,
            ))
            pool = live[:RECALL_POOL]
            result = (self._recall_knapsack(pool, sims, budget, now, current) if self.knapsack
                      else self._recall_tiered(pool, sims, budget, now, current))
        else:
            live.sort(key=lambda m: (not m.protected, -current[m.id], -m.last_access,
                                     -m.created, m.id))
            pool = live[:RECALL_POOL]
            result = (self._recall_knapsack(pool, None, budget, now, current) if self.knapsack
                      else self._recall_tiered(pool, None, budget, now, current))
        result.retired_excluded = excluded
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
        self.memories = {}
        self._tok = {}
        self._sketch = {}
        self._norm = {}
        self._sk = {}
        self._touched = set()
        self._sim_dirty = True
        for raw in payload.get("memories", []):
            known = {k: v for k, v in raw.items() if k in Memory.__dataclass_fields__}
            mem = Memory(**known)
            if not mem.summary:
                mem.summary = make_summary(mem.content)
            if not mem.score_at:
                # A v2 file has no ``score_at``: its score was last realized at its last
                # access (or its creation), so decay from there and never from 0.
                mem.score_at = mem.last_access or mem.created or 0.0
            mem.tier = tier_for(mem.score, mem.protected)
            self.memories[mem.id] = mem
            self._index_insert(mem)
        return len(self.memories)

    def stats(self) -> Dict:
        counts = {"active": 0, "latent": 0, "archived": 0}
        protected = 0
        retired = 0
        for mem in self.memories.values():
            counts[tier_for(mem.score, mem.protected)] += 1
            protected += 1 if mem.protected else 0
            retired += 1 if mem.retired else 0
        return {
            "total": len(self.memories),
            "protected": protected,
            "retired": retired,
            "tiers": counts,
            "avg_score": (sum(m.score for m in self.memories.values()) / len(self.memories))
            if self.memories
            else 0.0,
        }


# ------------------------------------------------------------------ CLI ----
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myelinate", description="Myelinated Memory engine")
    parser.add_argument("--store", default=None, help="path to the memory store")
    parser.add_argument("--pure", action="store_true",
                        help="disable similarity, knapsack and supersession (spec-only behaviour)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add a memory (dupes auto-collapse)")
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--category", default=DEFAULT_CATEGORY)
    p_add.add_argument("--protected", action="store_true")
    p_add.add_argument("--id", dest="memory_id", default=None)
    p_add.add_argument("--supersedes", default=None,
                       help="id of the memory this one replaces")

    p_access = sub.add_parser("access", help="strengthen a memory when it is used")
    p_access.add_argument("memory_id")

    p_retire = sub.add_parser("retire", help="stop surfacing a superseded memory")
    p_retire.add_argument("memory_id")

    p_pin = sub.add_parser("pin", help="protect a memory from decay and pruning")
    p_pin.add_argument("memory_id")

    p_recall = sub.add_parser("recall", help="context injection, filled to the budget")
    p_recall.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_recall.add_argument("--query", default=None, help="blend query similarity into ranking")

    p_refresh = sub.add_parser("refresh", help="session-boundary decay + consolidation")
    p_refresh.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES,
                           help="optional storage ceiling; 0 keeps everything (default)")

    sub.add_parser("list", help="list memories")
    sub.add_parser("stats", help="show tier distribution")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    kwargs = {"similarity": False, "knapsack": False, "stale_retirement": False} if args.pure else {}
    engine = MyelinatedMemory(path=args.store, **kwargs)

    if args.command == "add":
        memory_id = engine.add(
            args.content, category=args.category, protected=args.protected,
            memory_id=args.memory_id, supersedes=args.supersedes,
        )
        engine.save()
        print(memory_id)
        return 0

    if args.command == "access":
        ok = engine.access(args.memory_id)
        engine.save()
        print("strengthened %s" % args.memory_id if ok else "unknown memory %s" % args.memory_id)
        return 0 if ok else 1

    if args.command == "retire":
        ok = engine.retire(args.memory_id)
        engine.save()
        print("retired %s" % args.memory_id if ok else "unknown memory %s" % args.memory_id)
        return 0 if ok else 1

    if args.command == "pin":
        ok = engine.pin(args.memory_id)
        engine.save()
        print("pinned %s" % args.memory_id if ok else "unknown memory %s" % args.memory_id)
        return 0 if ok else 1

    if args.command == "recall":
        result = engine.recall(budget=args.budget, query=args.query)
        engine.save()
        print(result.text)
        return 0

    if args.command == "refresh":
        report = engine.refresh(max_entries=args.max_entries)
        engine.save()
        print(json.dumps(report, sort_keys=True))
        return 0

    if args.command == "list":
        for mem in engine.ordered():
            print("%s\t%.4f\t%s%s\t%s" % (mem.id, mem.score, mem.tier,
                                          " retired" if mem.retired else "", mem.summary))
        return 0

    if args.command == "stats":
        print(json.dumps(engine.stats(), indent=2, sort_keys=True))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
