"""Memory systems under test ("arms") for the Myelinated Memory benchmark.

Every arm implements ``benchmarks.common.Arm`` and therefore sees the identical
event stream, the identical virtual clock and the identical character budget.
The only thing that differs is the memory policy being tested.

Arms:

    M0  no memory            control (context is always empty)
    M1  flat / FIFO          append everything; the budget keeps the oldest
    M2  recency / LRU        most recently touched first
    M3  semantic - lexical   BM25 top-k over the store
    M4  semantic - vector    TF-IDF cosine over the store
    M5  semantic - dense     embedding cosine (needs a key: OPENAI_API_KEY
                             or NVIDIA_CLOUD_KEY; opt-in via --network)
    M6  myelinated (pure)    the specification: query-blind, tier-ordered
    M7  = M6 + similarity    R1, blend query similarity into recall
    M8  = M7 + knapsack      R4/R7, expected-value-per-character packing
    M9  = M8 + supersession  R5, honour retire/supersede signals
    M10 = M9 + reinforcement R2, learn from memories used in correct answers

Ranking attribution controls (round 5). Both are M10 with exactly one thing
changed, so the ranking loss against the BM25 arms can be attributed rather
than guessed at:

    M11 = M10 - prior weight   ordering is pure lexical similarity
    M12 = M10, caps lifted     candidate generation is unbounded (0 = unbounded)

Allocator attribution controls (round 4, `docs/ROUND4-DESIGN.md` 4). All three
reuse the BM25 ranking, so the ranking policy is identical and the ONLY thing
that differs is how text is allocated into the budget - which is what decides
whether the engine's budget-efficiency win belongs to its store or to its
packer:

    M3t  BM25 +truncated text   full text, sliced into the budget (downgrade)
    M3p  BM25 +engine ladder    the engine's tier ladder, tier = rank position
    M3k  BM25 +engine packer    the engine's value-per-char packer, utility = 1/(1+rank)

M6 through M10 are the SAME engine with capabilities switched on one at a time,
so any improvement can be attributed to a named change instead of to "the
engine" (board ruling BM-002).

Fairness note on R5: every arm is offered the supersession signal and every
baseline honours it by deleting the retired memory. Supersession is therefore
not an engine advantage; it is a capability the workload can supply to any
store, and the interesting measurement is the decay-only suite where no retire
signal exists at all (board ruling BM-003).

Arms record per-operation latency in ``add_ms`` / ``access_ms`` / ``retire_ms``
/ ``reinforce_ms`` / ``refresh_ms`` / ``recall_ms`` so the report can quote real
p50/p95 numbers.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

from common import (
    DEFAULT_BUDGET,
    Arm,
    Recall,
    downgrade,
    pack_blocks,
    tokenize,
)

# The engine is a script, not an installed package: benchmark modules run with
# benchmarks/ as the sys.path root, so add the repository's scripts/ directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from myelinate import (  # noqa: E402  (path set up above)
    DETAIL_VALUE,
    GIST_MAX_CHARS,
    LEGACY_PRIOR_WEIGHT,
    SUMMARY_MAX_CHARS,
    TIER_DETAILS,
    MyelinatedMemory,
    make_summary,
    tier_for,
)


class TimedArm(Arm):
    """Base arm: wraps every operation to record its latency."""

    def __init__(self) -> None:
        self.add_ms: List[float] = []
        self.access_ms: List[float] = []
        self.retire_ms: List[float] = []
        self.reinforce_ms: List[float] = []
        self.refresh_ms: List[float] = []
        self.recall_ms: List[float] = []
        # An arm is usable the moment it is constructed, not only after the
        # harness calls reset() on it.
        self.reset()

    # -- public API ---------------------------------------------------------
    def reset(self) -> None:
        self.add_ms = []
        self.access_ms = []
        self.retire_ms = []
        self.reinforce_ms = []
        self.refresh_ms = []
        self.recall_ms = []
        self._reset_store()

    # -- mid-ingest snapshots (defect D22) ----------------------------------
    # The scale probe can be cut off by a command timeout. An arm that can
    # serialize and restore its live ingest state lets the probe checkpoint an
    # in-flight arm and resume it in a later pass; an arm that cannot keeps the
    # old behaviour, where a timeout loses only that arm's row.
    def supports_snapshot(self) -> bool:
        return False

    def snapshot_state(self) -> object:
        raise NotImplementedError

    def restore_state(self, state: object) -> None:
        raise NotImplementedError

    def add(self, content: str, *, memory_id: Optional[str] = None, category: str = "general",
            protected: bool = False, now: float = 0.0) -> str:
        start = time.perf_counter()
        out = self._add(content, memory_id=memory_id, category=category,
                        protected=protected, now=now)
        self.add_ms.append((time.perf_counter() - start) * 1000.0)
        return out

    def access(self, memory_id: str, now: float = 0.0) -> None:
        start = time.perf_counter()
        self._access(memory_id, now)
        self.access_ms.append((time.perf_counter() - start) * 1000.0)

    def retire(self, memory_id: str, now: float = 0.0) -> None:
        start = time.perf_counter()
        self._retire(memory_id, now)
        self.retire_ms.append((time.perf_counter() - start) * 1000.0)

    def reinforce(self, memory_ids: Sequence[str], now: float = 0.0) -> None:
        start = time.perf_counter()
        self._reinforce(memory_ids, now)
        self.reinforce_ms.append((time.perf_counter() - start) * 1000.0)

    def refresh(self, now: float = 0.0) -> None:
        start = time.perf_counter()
        self._refresh(now)
        self.refresh_ms.append((time.perf_counter() - start) * 1000.0)

    def recall(self, query: str, budget: int = DEFAULT_BUDGET, now: float = 0.0) -> Recall:
        start = time.perf_counter()
        out = self._recall(query, budget, now)
        elapsed = (time.perf_counter() - start) * 1000.0
        self.recall_ms.append(elapsed)
        # An arm may return (text, used_ids) or (text, used_ids, ranked_ids).
        # The ranked order is the arm's own order BEFORE packing, which is what
        # lets the report score ranking separately from allocation instead of
        # scoring the packer as if it were the retriever (defect D8). Arms that
        # do not declare a rank order are scored on their packed order.
        if len(out) == 3:
            text, used, ranked = out
        else:
            text, used = out
            ranked = used
        return Recall(text=text, used_ids=list(used), ranked_ids=list(ranked),
                      chars=len(text), latency_ms=elapsed, budget=budget)

    # -- subclass hooks -----------------------------------------------------
    def _reset_store(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        raise NotImplementedError  # pragma: no cover

    def _access(self, memory_id, now=0.0) -> None:  # pragma: no cover - optional
        return None

    def _retire(self, memory_id, now=0.0) -> None:  # pragma: no cover - optional
        return None

    def _reinforce(self, memory_ids, now=0.0) -> None:  # pragma: no cover - optional
        return None

    def _refresh(self, now=0.0) -> None:  # pragma: no cover - optional
        return None

    def _recall(self, query, budget, now) -> Tuple[str, List[str]]:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------- M0
class NoMemoryArm(TimedArm):
    """Control: no memory at all, so the context is always empty."""

    name = "M0 no-memory"

    def _reset_store(self) -> None:
        self._count = 0

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        self._count += 1
        return memory_id or "none-%d" % self._count

    def _recall(self, query, budget, now):
        return "", []

    def size(self) -> int:
        return 0


# ---------------------------------------------------------------- M1 / M2
class FlatFifoArm(TimedArm):
    """Static store: everything is kept in insertion order, all equally ranked.

    Honours supersession by deleting, because with no versioning there is
    nothing else it could do.
    """

    name = "M1 flat/FIFO"
    supports_retirement = True

    def _reset_store(self) -> None:
        self.items: List[Tuple[str, str]] = []
        self._ids: Dict[str, int] = {}
        self._counter = 0

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        self._counter += 1
        mem_id = memory_id or "flat-%d" % self._counter
        self._ids[mem_id] = len(self.items)
        self.items.append((mem_id, content))
        return mem_id

    def _retire(self, memory_id, now=0.0) -> None:
        if memory_id in self._ids:
            self.items = [(i, c) for i, c in self.items if i != memory_id]
            self._ids = {i: n for n, (i, _) in enumerate(self.items)}

    def _recall(self, query, budget, now):
        text, used, _ = pack_blocks(self.items, budget)
        return text, used

    def size(self) -> int:
        return len(self.items)


class RecencyArm(TimedArm):
    """LRU: the most recently stored or used memories win the budget.

    Also consumes the utility-credit signal, because "use it a lot" is exactly
    its ranking rule - so it is offered the same reinforcement as the engine.
    """

    name = "M2 recency/LRU"
    supports_retirement = True
    supports_reinforcement = True

    def _reset_store(self) -> None:
        self.items: Dict[str, List] = {}
        self._counter = 0

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        self._counter += 1
        mem_id = memory_id or "lru-%d" % self._counter
        self.items[mem_id] = [content, now, now]
        return mem_id

    def _access(self, memory_id, now=0.0) -> None:
        if memory_id in self.items:
            self.items[memory_id][2] = now

    def _reinforce(self, memory_ids, now=0.0) -> None:
        for memory_id in memory_ids:
            self._access(memory_id, now)

    def _retire(self, memory_id, now=0.0) -> None:
        self.items.pop(memory_id, None)

    def _recall(self, query, budget, now):
        ordered = sorted(self.items.items(), key=lambda kv: (-kv[1][2], -kv[1][1], kv[0]))
        text, used, _ = pack_blocks([(k, v[0]) for k, v in ordered], budget)
        return text, used

    def size(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------- M3 / M4
class Bm25Arm(TimedArm):
    """Traditional lexical retrieval: BM25 top-k, best match first."""

    name = "M3 semantic/BM25"
    K1 = 1.2
    B = 0.75
    supports_retirement = True

    def _reset_store(self) -> None:
        self.docs: Dict[str, Dict] = {}
        self._counter = 0
        self._dirty = True

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        self._counter += 1
        mem_id = memory_id or "bm25-%d" % self._counter
        self.docs[mem_id] = {
            "content": content,
            "tokens": tokenize(content),
            "created": now,
            "last_access": now,
        }
        self._dirty = True
        return mem_id

    def _access(self, memory_id, now=0.0) -> None:
        if memory_id in self.docs:
            self.docs[memory_id]["last_access"] = now

    def _retire(self, memory_id, now=0.0) -> None:
        if self.docs.pop(memory_id, None) is not None:
            self._dirty = True

    def _build(self) -> None:
        self.lengths = {i: len(d["tokens"]) for i, d in self.docs.items()}
        self.avgdl = (sum(self.lengths.values()) / len(self.lengths)) if self.lengths else 0.0
        self.tfs: Dict[str, Dict[str, int]] = {}
        df: Dict[str, int] = {}
        for mem_id, doc in self.docs.items():
            tf: Dict[str, int] = {}
            for token in doc["tokens"]:
                tf[token] = tf.get(token, 0) + 1
            self.tfs[mem_id] = tf
            for token in tf:
                df[token] = df.get(token, 0) + 1
        n = len(self.docs) or 1
        self.idf = {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self._dirty = False

    # -- ranking (shared with the M3t/M3p/M3k allocator controls) ------------
    def _rank(self, query: str) -> List[str]:
        """BM25-ranked memory ids, best match first.

        Shared by this arm and the three allocator controls, so the only thing
        that differs between them is how the text is allocated. Only
        positively-scoring memories are returned, exactly as before.
        """
        if self._dirty:
            self._build()
        if not self.docs:
            return []
        q_tokens = tokenize(query)
        scored = []
        for mem_id, doc in self.docs.items():
            tf = self.tfs.get(mem_id, {})
            length = self.lengths.get(mem_id, 0)
            score = 0.0
            for token in q_tokens:
                f = tf.get(token)
                if not f:
                    continue
                idf = self.idf.get(token, 0.0)
                score += idf * (f * (self.K1 + 1.0)) / (
                    f + self.K1 * (1.0 - self.B + self.B * (length / (self.avgdl or 1.0)))
                )
            if score > 0:
                scored.append((score, doc["last_access"], doc["created"], mem_id))
        scored.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
        return [row[3] for row in scored]

    def _render_detail(self, memory_id: str, detail: str) -> str:
        """Render one memory at a ladder detail, using the engine's own caps."""
        content = self.docs[memory_id]["content"]
        if detail == "full":
            return content
        if detail == "summary":
            return make_summary(content, SUMMARY_MAX_CHARS)
        return make_summary(content, GIST_MAX_CHARS)

    def _recall(self, query, budget, now):
        ranked = self._rank(query)
        blocks = [(mem_id, self.docs[mem_id]["content"]) for mem_id in ranked]
        text, used, _ = pack_blocks(blocks, budget)
        return text, used

    def size(self) -> int:
        return len(self.docs)


# --------------------------------------- M3t / M3p / M3k: allocator controls
# All three reuse Bm25Arm._rank and none of them reads an engine score: the
# allocation decision is driven by rank position only, so the engine's store
# cannot contribute to their result.
class Bm25TruncateArm(Bm25Arm):
    """M3t: BM25 rank order, full text, TRUNCATED into the remaining budget.

    The existing lexical baselines SKIP a block that does not fit; this one
    slices it with ``common.downgrade`` instead, which is what a real truncating
    store does. It is the fairness control D7 asked for, and the first caller
    ``common.downgrade`` has ever had.
    """

    name = "M3t BM25 +truncated text"
    MIN_BLOCK = 40  # a slice shorter than this is not worth a line of context

    def _recall(self, query, budget, now):
        ranked = self._rank(query)
        lines: List[str] = []
        used: List[str] = []
        spent = 0
        for mem_id in ranked:
            remaining = budget - spent - (1 if lines else 0)
            if remaining < self.MIN_BLOCK:
                break
            body = downgrade(self.docs[mem_id]["content"], remaining)
            if not body.strip():
                continue
            lines.append(body)
            used.append(mem_id)
            spent += len(body) + (1 if len(lines) > 1 else 0)
        return "\n".join(lines), used, ranked


class Bm25LadderArm(Bm25Arm):
    """M3p: BM25 rank order rendered with the ENGINE's tier/detail ladder.

    The tier comes from the rank position (the top band gets the tier the engine
    calls "active", the middle band "latent", the tail "archived") and the
    detail allowed by that tier is read from the engine's own ``TIER_DETAILS``,
    so the ladder is the engine's while the ranking signal stays BM25's. One
    detail per memory is chosen up front and a block that would overflow is
    skipped, exactly as the other lexical baselines do - so the comparison
    isolates the ladder rather than a retry rule.
    """

    name = "M3p BM25 +engine ladder"
    FULL_UNTIL = 8      # rank 0-7   -> "active"
    SUMMARY_UNTIL = 24  # rank 8-23  -> "latent"; the rest -> "archived"

    def _tier_for_rank(self, position: int) -> str:
        if position < self.FULL_UNTIL:
            return "active"
        if position < self.SUMMARY_UNTIL:
            return "latent"
        return "archived"

    def _recall(self, query, budget, now):
        ranked = self._rank(query)
        blocks = [
            (mem_id, self._render_detail(mem_id, TIER_DETAILS[self._tier_for_rank(position)][0]))
            for position, mem_id in enumerate(ranked)
        ]
        text, used, _ = pack_blocks(blocks, budget)
        return text, used, ranked


class Bm25PackerArm(Bm25LadderArm):
    """M3k: BM25 rank order packed by the ENGINE's value-per-character rule.

    Mirrors ``MyelinatedMemory._recall_knapsack``: every (memory, detail) option
    that memory's rank-derived tier allows is valued at ``DETAIL_VALUE[detail] *
    utility`` with ``utility = 1 / (1 + rank)``, then taken greedily by value per
    character with the engine's own newline separator and budget guard. The
    retrieval-strength score is deliberately replaced by rank position, so the
    engine's store cannot contribute to this control.
    """

    name = "M3k BM25 +engine packer"

    def _recall(self, query, budget, now):
        ranked = self._rank(query)
        scored: List[Tuple[float, str, str, int]] = []
        for position, mem_id in enumerate(ranked):
            utility = 1.0 / (1.0 + position)
            for detail in TIER_DETAILS[self._tier_for_rank(position)]:
                body = self._render_detail(mem_id, detail)
                cost = len(body)
                if cost <= 0:
                    continue
                scored.append((utility * DETAIL_VALUE[detail] / cost, mem_id, detail, cost))
        scored.sort(key=lambda item: (-item[0], item[1]))

        lines: List[str] = []
        used: List[str] = []
        chosen: set = set()
        spent = 0
        for _ratio, mem_id, detail, cost in scored:
            if mem_id in chosen:
                continue
            total = cost + (1 if lines else 0)
            if spent + total > budget:
                continue
            lines.append(self._render_detail(mem_id, detail))
            used.append(mem_id)
            chosen.add(mem_id)
            spent += total
        return "\n".join(lines), used, ranked


class TfidfArm(TimedArm):
    """Vector-space semantic memory: TF-IDF cosine similarity over the store."""

    name = "M4 semantic/TF-IDF"
    supports_retirement = True

    def _reset_store(self) -> None:
        self.docs: Dict[str, Dict] = {}
        self._counter = 0
        self._dirty = True

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        self._counter += 1
        mem_id = memory_id or "tfidf-%d" % self._counter
        self.docs[mem_id] = {"content": content, "created": now, "last_access": now}
        self._dirty = True
        return mem_id

    def _access(self, memory_id, now=0.0) -> None:
        if memory_id in self.docs:
            self.docs[memory_id]["last_access"] = now

    def _retire(self, memory_id, now=0.0) -> None:
        if self.docs.pop(memory_id, None) is not None:
            self._dirty = True

    def _build(self) -> None:
        n = len(self.docs) or 1
        df: Dict[str, int] = {}
        counts: Dict[str, Dict[str, int]] = {}
        for mem_id, doc in self.docs.items():
            tf: Dict[str, int] = {}
            for token in tokenize(doc["content"]):
                tf[token] = tf.get(token, 0) + 1
            counts[mem_id] = tf
            for token in tf:
                df[token] = df.get(token, 0) + 1
        self.idf = {t: math.log((n + 1.0) / (c + 1.0)) + 1.0 for t, c in df.items()}
        self.vecs = {}
        self.norms = {}
        for mem_id, tf in counts.items():
            vec = {t: c * self.idf[t] for t, c in tf.items()}
            self.vecs[mem_id] = vec
            self.norms[mem_id] = math.sqrt(sum(w * w for w in vec.values()))
        self._dirty = False

    def scores(self, query: str) -> Dict[str, float]:
        """Cosine similarity of the query against every stored memory."""
        if self._dirty:
            self._build()
        if not self.docs:
            return {}
        q_tf: Dict[str, int] = {}
        for token in tokenize(query):
            q_tf[token] = q_tf.get(token, 0) + 1
        q_vec = {t: c * self.idf.get(t, 0.0) for t, c in q_tf.items()}
        q_norm = math.sqrt(sum(w * w for w in q_vec.values()))
        if q_norm == 0.0:
            return {mem_id: 0.0 for mem_id in self.docs}
        out: Dict[str, float] = {}
        for mem_id, vec in self.vecs.items():
            doc_norm = self.norms.get(mem_id, 0.0)
            if doc_norm == 0.0:
                out[mem_id] = 0.0
                continue
            dot = 0.0
            for token, weight in q_vec.items():
                if weight:
                    dot += weight * vec.get(token, 0.0)
            out[mem_id] = dot / (q_norm * doc_norm)
        return out

    def _recall(self, query, budget, now):
        sims = self.scores(query)
        ordered = sorted(
            self.docs.items(),
            key=lambda kv: (-sims.get(kv[0], 0.0), -kv[1]["last_access"], kv[0]),
        )
        blocks = [(mem_id, doc["content"]) for mem_id, doc in ordered]
        text, used, _ = pack_blocks(blocks, budget)
        return text, used

    def size(self) -> int:
        return len(self.docs)


class DenseEmbeddingArm(TfidfArm):
    """Dense semantic memory backed by an OpenAI-compatible embeddings endpoint.

    Two request styles are supported. ``"openai"`` posts ``{model, input}``.
    ``"nvidia"`` (NVIDIA NIM) additionally posts ``input_type``, which that
    endpoint requires and rejects requests without: ``"passage"`` for a stored
    memory, ``"query"`` for the recall query.
    """

    name = "M5 semantic/dense-embeddings"
    uses_network = True

    # ASSUMPTION: transport batching only. The endpoint embeds each input
    # independently; a 32-item batch was measured against single-item requests at
    # cosine 1.00000000 (max |diff| 6e-8), so the batch size cannot move a
    # ranking. It exists because one request per memory (~410 ms measured) cannot
    # fit the LoCoMo tier inside one bounded command; the vectors are unchanged.
    EMBED_BATCH = 32

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0,
                 embed_style: str = "openai") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.embed_style = embed_style
        self.embeddings: Dict[str, List[float]] = {}
        # Set before super().__init__(), which calls reset() -> _reset_store().
        self._pending: Dict[str, str] = {}
        super().__init__()

    def _reset_store(self) -> None:
        self.embeddings.clear()
        self._pending.clear()
        super()._reset_store()

    @classmethod
    def from_env(cls) -> Optional["DenseEmbeddingArm"]:
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            return cls(
                api_key=key,
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
            )
        key = os.environ.get("NVIDIA_CLOUD_KEY")
        if not key:
            return None
        return cls(
            api_key=key,
            base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            # Verified live: `nvidia/embed-qa-4` and `nvidia/llama-3.2-nv-embedqa-1b-v1`
            # both answer HTTP 404 "Function not found for account" on this key,
            # while the one below returns 2048-dimension vectors.
            model=os.environ.get("NVIDIA_EMBED_MODEL", "nvidia/nemotron-3-embed-1b"),
            embed_style="nvidia",
        )

    def _embed(self, texts: Sequence[str], input_type: str = "passage") -> List[List[float]]:
        payload: Dict[str, object] = {"model": self.model, "input": list(texts)}
        if self.embed_style == "nvidia":
            # NVIDIA's embed endpoint rejects a request without ``input_type``.
            payload["input_type"] = input_type
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            # urllib needs bytes here; passing the dict itself raised
            # "TypeError: can't concat str to bytes" on every call.
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer %s" % self.api_key,
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            raise RuntimeError("embedding request failed: %s" % exc) from exc
        rows = sorted(body.get("data", []), key=lambda row: row.get("index", 0))
        return [row["embedding"] for row in rows]

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        mem_id = super()._add(content, memory_id=memory_id, category=category,
                              protected=protected, now=now)
        self._pending[mem_id] = content
        if len(self._pending) >= self.EMBED_BATCH:
            self._flush()
        return mem_id

    def _retire(self, memory_id, now=0.0) -> None:
        self._pending.pop(memory_id, None)
        self.embeddings.pop(memory_id, None)
        super()._retire(memory_id, now)

    def _flush(self) -> None:
        """Embed queued passages in batches of ``EMBED_BATCH`` (transport only)."""
        while self._pending:
            chunk = list(self._pending.items())[:self.EMBED_BATCH]
            vectors = self._embed([text for _, text in chunk], input_type="passage")
            for (mem_id, _), vec in zip(chunk, vectors):
                self.embeddings[mem_id] = vec
            # Pop by chunk, not inside the zip: a short reply must not leave a
            # queued id behind and spin this loop forever.
            for mem_id, _ in chunk:
                self._pending.pop(mem_id, None)

    def _recall(self, query, budget, now):
        # Queued passages are embedded here at the latest, so ingest pays for
        # most of the embedding work and only this arm's final partial batch
        # lands in a recall latency sample.
        self._flush()
        if not self.docs:
            return "", []
        q_vec = self._embed([query], input_type="query")[0]
        q_norm = math.sqrt(sum(v * v for v in q_vec)) or 1.0
        scored = []
        for mem_id, doc in self.docs.items():
            vec = self.embeddings.get(mem_id)
            if not vec:
                continue
            dot = sum(a * b for a, b in zip(q_vec, vec))
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            scored.append((dot / (q_norm * norm), doc["last_access"], mem_id))
        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        blocks = [(row[2], self.docs[row[2]]["content"]) for row in scored]
        text, used, _ = pack_blocks(blocks, budget)
        return text, used


# ------------------------------------------------------------ M6 - M12
class MyelinatedArm(TimedArm):
    """The engine from scripts/myelinate.py, in memory-only mode.

    ``similarity``, ``knapsack``, ``stale_retirement`` and ``reinforcement`` are
    independent switches so the report can attribute any gain to one change.

    ``prior_weight`` and the three candidate caps additionally override the
    engine's retrieval SHAPE (round 5): the first says how much stored strength
    is allowed to outrank lexical similarity, the other three say how large a
    candidate set the ordering formula is even offered. ``None`` means "the
    engine's own module constant"; ``0`` means unbounded.
    """

    def __init__(self, name: str, similarity: bool = True, knapsack: bool = True,
                 stale_retirement: bool = False, reinforcement: bool = False,
                 auto_supersede: bool = True,
                 prior_weight: Optional[float] = None,
                 max_candidates: Optional[int] = None,
                 max_postings_scan: Optional[int] = None,
                 recall_pool: Optional[int] = None) -> None:
        self.arm_name = name
        self.use_similarity = similarity
        self.use_knapsack = knapsack
        self.stale_retirement = stale_retirement
        self.reinforcement = reinforcement
        # The auto-update half of supersession (D13/D14): an update retires the
        # older wording without any caller signal. Gated here so R12's control
        # arm can switch just this off.
        self.use_auto_supersede = auto_supersede
        self.prior_weight = prior_weight
        self.max_candidates = max_candidates
        self.max_postings_scan = max_postings_scan
        self.recall_pool = recall_pool
        self.supports_retirement = stale_retirement
        self.supports_reinforcement = reinforcement
        super().__init__()

    @property
    def name(self) -> str:
        return self.arm_name

    def _reset_store(self) -> None:
        self.engine = MyelinatedMemory(
            in_memory=True,
            similarity=self.use_similarity,
            knapsack=self.use_knapsack,
            stale_retirement=self.stale_retirement,
            auto_supersede=self.use_auto_supersede,
            prior_weight=self.prior_weight,
            max_candidates=self.max_candidates,
            max_postings_scan=self.max_postings_scan,
            recall_pool=self.recall_pool,
        )

    def _add(self, content, *, memory_id=None, category="general", protected=False, now=0.0) -> str:
        return self.engine.add(content, category=category, protected=protected,
                               memory_id=memory_id, now=now)

    def _access(self, memory_id, now=0.0) -> None:
        self.engine.access(memory_id, now=now)

    def _retire(self, memory_id, now=0.0) -> None:
        if self.stale_retirement:
            self.engine.retire(memory_id)

    def _reinforce(self, memory_ids, now=0.0) -> None:
        if self.reinforcement:
            self.engine.reinforce(memory_ids, now=now)

    def _refresh(self, now=0.0) -> None:
        self.engine.refresh(now=now)

    def _recall(self, query, budget, now):
        result = self.engine.recall(budget=budget, now=now, query=query)
        # Hand the harness the engine's own pre-packing order, so nDCG@10 scores
        # the ranker and nDCG@10_packed scores the allocator. Returning only
        # ``used_ids`` silently scored the packer as if it were the ranker (D8/
        # F21), which is why every engine arm's two nDCG columns were identical.
        return result.text, result.used_ids, result.ranked_ids or result.used_ids

    def size(self) -> int:
        return self.engine.size()

    def supports_snapshot(self) -> bool:
        return True

    def snapshot_state(self) -> object:
        # The engine object itself, pickled whole. A JSON store round trip would
        # lose ``_touched`` - the set of memories added or accessed since the
        # last refresh, which decides what the next ``refresh()`` consolidates -
        # so a resumed arm would diverge from an uninterrupted one (D22). The
        # checkpoint is harness scratch state written and read only by the same
        # kind of run, never a store interchange format.
        return self.engine

    def restore_state(self, state: object) -> None:
        self.engine = state


# ---------------------------------------------------------------- registry
def build_arms(include_network: bool = False) -> List[Arm]:
    """All arms in report order. Network arms are opt-in."""
    arms: List[Arm] = [
        NoMemoryArm(),
        FlatFifoArm(),
        RecencyArm(),
        Bm25Arm(),
        Bm25TruncateArm(),
        Bm25LadderArm(),
        Bm25PackerArm(),
        TfidfArm(),
    ]
    if include_network:
        dense = DenseEmbeddingArm.from_env()
        if dense is not None:
            arms.append(dense)
    # M6-M10 are the round-4 engine and M12 is its candidate-cap control: all of
    # them pin `prior_weight=LEGACY_PRIOR_WEIGHT` so the published round-4 rows
    # stay reproducible after round 5 lowered the engine default. Without the pin
    # every one of these arms would silently become M11 and the before/after
    # comparison would vanish from the report.
    arms.extend([
        MyelinatedArm("M6 myelinated (pure)", similarity=False, knapsack=False,
                      prior_weight=LEGACY_PRIOR_WEIGHT),
        MyelinatedArm("M7 myelinated +similarity", similarity=True, knapsack=False,
                      prior_weight=LEGACY_PRIOR_WEIGHT),
        MyelinatedArm("M8 myelinated +knapsack", similarity=True, knapsack=True,
                      prior_weight=LEGACY_PRIOR_WEIGHT),
        MyelinatedArm("M9 myelinated +supersession", similarity=True, knapsack=True,
                      stale_retirement=True, prior_weight=LEGACY_PRIOR_WEIGHT),
        MyelinatedArm("M10 myelinated +reinforcement", similarity=True, knapsack=True,
                      stale_retirement=True, reinforcement=True,
                      prior_weight=LEGACY_PRIOR_WEIGHT),
        # Round-5 ranking controls. Both are M10 with exactly one change, so a
        # ranking loss can be attributed to the ordering formula or to the size
        # of the candidate set it is handed - never to "the engine".
        # M11 leaves `prior_weight` at None, so it tracks the shipped engine
        # default (0.0) instead of hardcoding the measured value twice.
        MyelinatedArm("M11 myelinated +lexical ranking", similarity=True, knapsack=True,
                      stale_retirement=True, reinforcement=True),
        MyelinatedArm("M12 myelinated +unbounded candidates", similarity=True, knapsack=True,
                      stale_retirement=True, reinforcement=True,
                      prior_weight=LEGACY_PRIOR_WEIGHT,
                      max_candidates=0, max_postings_scan=0, recall_pool=0),
        # R12 attribution control: M9 with the auto-update half of supersession
        # switched off - exactly one change. On the `staleness (update)` suite
        # the pair measures what auto-retire supersession is worth when the
        # correction arrives with no signal of any kind.
        MyelinatedArm("M13 myelinated +supersession (auto-update off)",
                      similarity=True, knapsack=True, stale_retirement=True,
                      auto_supersede=False, prior_weight=LEGACY_PRIOR_WEIGHT),
    ])
    return arms


ARM_ORDER = [
    "M0 no-memory",
    "M1 flat/FIFO",
    "M2 recency/LRU",
    "M3 semantic/BM25",
    "M3t BM25 +truncated text",
    "M3p BM25 +engine ladder",
    "M3k BM25 +engine packer",
    "M4 semantic/TF-IDF",
    "M5 semantic/dense-embeddings",
    "M6 myelinated (pure)",
    "M7 myelinated +similarity",
    "M8 myelinated +knapsack",
    "M9 myelinated +supersession",
    "M10 myelinated +reinforcement",
    "M11 myelinated +lexical ranking",
    "M12 myelinated +unbounded candidates",
    "M13 myelinated +supersession (auto-update off)",
]


def arm_names() -> List[str]:
    return list(ARM_ORDER)


if __name__ == "__main__":
    arms = build_arms()
    print("arms:", ", ".join(a.name for a in arms))
    # ARM_ORDER also names the opt-in network arm, which build_arms() omits, so
    # the expectation is derived rather than hardcoded: a literal count is how
    # this drifted out of sync before.
    offline = [name for name in ARM_ORDER if name != DenseEmbeddingArm.name]
    assert [a.name for a in arms] == offline, (
        "build_arms() and ARM_ORDER disagree: %r vs %r"
        % ([a.name for a in arms], offline))
    assert all(hasattr(a, "recall_ms") for a in arms)
    # M5's batched transport, checked offline with a stubbed embedder: ingest
    # queues and flushes in EMBED_BATCH chunks, a retired id never reaches the
    # wire, and the remainder flushes before the first recall scores anything.
    class _StubDense(DenseEmbeddingArm):
        def __init__(self) -> None:
            self.calls: List[List[str]] = []
            super().__init__(api_key="offline", base_url="https://invalid", model="stub")

        def _embed(self, texts: Sequence[str], input_type: str = "passage") -> List[List[float]]:
            self.calls.append(list(texts))
            return [[0.0] * 4 for _ in texts]

    probe = _StubDense()
    for i in range(5):
        probe.add("probe memory number %d about the lake house" % i)
    assert probe.calls == [], "ingest must batch, not embed one request per memory"
    probe.recall("lake house")
    assert [len(c) for c in probe.calls] == [5, 1], [len(c) for c in probe.calls]
    assert len(probe.embeddings) == 5
    for i in range(40):
        probe.add("filler memory %d" % i)
    assert len(probe.calls[-1]) == 32, "flush must fire at EMBED_BATCH"
    doomed = probe.add("this one gets retired before it is embedded")
    probe.retire(doomed)
    probe.recall("filler")
    assert [len(c) for c in probe.calls[-2:]] == [8, 1], [len(c) for c in probe.calls[-2:]]
    assert doomed not in probe.embeddings, "a retired id must never be embedded"
    assert max(len(c) for c in probe.calls) <= DenseEmbeddingArm.EMBED_BATCH
    print("engines ok")
