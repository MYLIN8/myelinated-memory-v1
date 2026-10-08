# Portability — using the engine outside the CLI

The engine is one Python file (`scripts/myelinate.py`) with zero dependencies and
three front doors. None of them requires the Hermes session protocol.

| Front door | Use it when | Entry point |
| :--- | :--- | :--- |
| **Library** | embedding in a Python agent | `from myelinate import MyelinatedMemory` |
| **CLI** | shell scripts, quick start | `python3 scripts/myelinate.py …` |
| **MCP server** | Claude Code, Claude Desktop, any MCP client | `python3 scripts/myelinated_mcp.py` |

## 1. Library API

```python
from myelinate import MyelinatedMemory

m = MyelinatedMemory(in_memory=True)          # or path="store.json" for persistence
m.add("User prefers concise replies.", category="preference")
m.add("The deploy target is production.", category="identity", protected=True)

r = m.recall(query="where do we deploy?", budget=2200)
print(r.text)          # the packed context, <= budget characters
print(r.used_ids)      # which memories were rendered
print(r.ranked_ids)    # the pre-packing rank order (score the ranker, not the packer)
print(r.tiers)         # {"active": 2} — how many at each detail tier

m.access(r.used_ids[0])          # Hebbian boost when a memory is actually used
m.retire(old_id, superseded_by=new_id)   # the fact changed
m.pin(important_id)              # never decays, never crowded out
m.refresh()                      # session boundary: decay + consolidate
m.save()                         # atomic JSON write (temp file + rename)
```

Constructor knobs that matter:

- `in_memory=True` — never touches disk (tests, sandboxes).
- `clock=…` — inject a virtual clock for replayable experiments.
- `similarity`, `knapsack`, `stale_retirement`, `auto_supersede` — capability
  switches, so an ablation can attribute any change to one named mechanism.
- `prior_weight`, `max_candidates`, `max_postings_scan`, `recall_pool` — per-instance
  recall parameters; `None` means module default, `0` means unbounded.
- `recall(..., force_similarity=True/False)` — pin query-similarity on or off for
  one call without mutating the engine (the CLI's `--force-similarity` /
  `--no-similarity` map to this).

## 2. MCP server

```bash
python3 scripts/myelinated_mcp.py --store ~/.myelinated/store.json
python3 scripts/myelinated_mcp.py --selftest    # protocol round trip, no client needed
```

Claude Code / Claude Desktop config:

```json
{
  "mcpServers": {
    "myelinated-memory": {
      "command": "python3",
      "args": ["/path/to/myelinated-memory/scripts/myelinated_mcp.py",
               "--store", "/path/to/store.json"]
    }
  }
}
```

Tools exposed: `memory_add`, `memory_access`, `memory_retire`, `memory_pin`,
`memory_recall`, `memory_refresh`, `memory_stats`. The protocol is plain
JSON-RPC 2.0 over stdio, implemented in the standard library — the server has no
third-party dependency and neither does the engine it wraps.

## 3. LangChain-style retriever

The engine maps onto any retriever interface that wants documents plus scores.
The adapter is ~20 lines and lives in your project, not ours — here is a
drop-in shape:

```python
from myelinate import MyelinatedMemory

class MyelinatedRetriever:
    """Drop-in shape for LangChain-style retrievers."""

    def __init__(self, store_path=None, budget=2200):
        self.engine = MyelinatedMemory(path=store_path, in_memory=store_path is None)
        self.budget = budget

    def add_texts(self, texts, category="general", **kwargs):
        return [self.engine.add(t, category=category) for t in texts]

    def get_relevant_documents(self, query):
        r = self.engine.recall(query=query, budget=self.budget)
        # one Document-like object per rendered memory, in packing order
        return [{"page_content": self.engine.render(self.engine.get(i), "full"),
                 "metadata": {"id": i, "tier": self.engine.get(i).tier}}
                for i in r.used_ids]

    async def aget_relevant_documents(self, query):
        return self.get_relevant_documents(query)
```

The same shape works for LlamaIndex retrievers, AutoGPT-style tool loops, or a
plain RAG pipeline: store on the way in, `recall()` on the way out.

## Session protocol (for agent integrations)

1. **Session start** — `memory_refresh`, then `memory_recall` with the question.
2. **During the session** — `memory_access` for each memory actually used;
   `memory_pin` for safety-critical or identity facts.
3. **New facts** — `memory_add` (near-duplicates collapse; a changed-value update
   supersedes the older wording automatically).
4. **Corrections** — `memory_retire`, or `memory_add` with `supersedes=…`.

## Invariants every front door shares

- **Zero dependencies**, standard library only, Python 3.10/3.11.
- **The budget is a hard ceiling**: `result.chars <= budget` always; an overflowing
  block is skipped, never truncated.
- **Strength is derived, never compounded**: a memory stores `(score, score_at)`;
  refreshing hourly, daily or weekly gives the same curve.
- **Untrusted content is an injection surface** — memory text is rendered verbatim
  into the context budget. Filter upstream if you ingest third-party text
  (see `SECURITY.md`).

## Known limits (read before relying on it)

- Decay-alone retirement is unproven: with an explicit `retire`/`supersede` signal
  stale facts leak 0.000; without one the default arm still leaks 1.000.
- BM25 orders evidence better (nDCG 0.732 against 0.699). This engine wins on
  context cost and store semantics, not on being the best pure ranker.
- The MCP mutators (`memory_add`, `memory_access`, `memory_retire`, `memory_pin`,
  `memory_refresh`) call `save()` after every change; `memory_recall` is
  read-only. Call `memory_access` for memories the agent actually used, and
  `memory_refresh` at a session boundary.
