---
name: myelinated-memory
description: "Use when managing a limited context budget across stored memories: tiered recall, session-boundary consolidation, supersession."
version: 2.0.0
author: Hermes Agent
tags: [memory, myelination, recall, prioritization, context-budget]
---

# Myelinated Memory System

A retrieval-strength ("myelination") memory system that simulates neural consolidation: memories used often get stronger and easier to recall; dormant memories decay to cheaper tiers, so the limited context budget goes to what actually matters.

The mechanism is implemented and benchmarked; the claim that comes with the name is not proven. See "Known limits" before relying on it.

## Engine & paths

- **Engine:** `scripts/myelinate.py`
- **Store:** `~/.hermes/memory/myelinated.json` — override with `--store PATH` or `$HERMES_MEMORY_STORE`

## Session Protocol

1. **Session start:** `refresh` (decay, then consolidate). Then `recall --query "<the question>"`.
2. **During session:** `access <memory_id>` for a memory that was actually used. `pin <memory_id>` for anything safety-critical or identity-fixed — pinned memories never decay and are never crowded out.
3. **Adding memories:** `add --content "..." --category <category>`. Near-duplicates collapse into the existing entry; protected entries are never archived.
4. **Changing facts:** `retire <old_id>`, or `add --content "..." --supersedes <old_id>`.

## Commands

```bash
# Session-boundary automation: decay every memory, then consolidate (merge near-duplicates)
python3 scripts/myelinate.py refresh

# Add a memory. --category sets the decay rate per dormant day:
#   identity 0.002 | preference 0.010 | general 0.030 (default) | task 0.050 | ephemeral 0.100
python3 scripts/myelinate.py add --content "User prefers concise replies." --category preference

# Strengthen a memory when it is actually used
python3 scripts/myelinate.py access <memory_id>

# Context injection. Pass the question: without --query the ranking is query-blind
python3 scripts/myelinate.py recall --query "where do we deploy?" --budget 2200

# Stop surfacing a fact that changed
python3 scripts/myelinate.py retire <old_id>

# Inspect
python3 scripts/myelinate.py list
python3 scripts/myelinate.py stats
```

`--store` and `--pure` are **global** flags: they come before the subcommand
(`myelinate --pure recall --query "…"`), and `recall --pure` is an argument error. `--pure` turns
off similarity, packing and supersession and ignores `--query`; it exists to reproduce the original
specification, not for daily use.

## Known limits

Read these before relying on this skill. Each is tracked with a fix in
[`docs/FIX-PLAN.md`](docs/FIX-PLAN.md).

- **Decay compounds (D1).** `refresh()` re-applies `exp(-rate × dormant days)` while the decay
  baseline does not advance, so the real exponent is `rate × d(d+1)/2`: after 30 daily refreshes a
  score is ~0.000001 instead of the documented 0.244. Frequent `refresh` calls over-decay the
  store. Prefer `--category` and `access` over long unattended dormancy.
- **A correction can be absorbed by the fact it replaces (D13/D14).** An update worded ≥ 0.9
  Jaccard-similar to the memory it supersedes collapses into it; if that memory was retired, the
  correction is lost and nothing surfaces. Reword updates so they differ in more than one value
  word, or `retire` the old entry and add the new one with distinct phrasing.
- **Decay-only retirement is unproven.** With an explicit `retire`/`--supersedes` signal the
  superseded fact stops surfacing (leak 0.000); with no signal the same scenarios leak 1.000. The
  mechanism the project is named after does not yet retire a stale fact on its own.
- **Ranking is fine, not best.** On the project's own benchmark a plain BM25 baseline orders
  evidence better (nDCG@10 0.732 vs 0.606); the measured win is budget allocation (1589 characters
  per evidence hit against a flat store's 1734).
