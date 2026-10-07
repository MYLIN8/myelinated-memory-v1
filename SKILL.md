---
name: myelinated-memory
description: "Use when managing a limited context budget across stored memories: tiered recall, session-boundary consolidation, supersession."
version: 4.0.0
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
3. **Adding memories:** `add --content "..." --category <category>`. Near-duplicates collapse into the existing entry; a memory that is ≥ 0.90 similar but whose value words differ is treated as an **update** — the new wording is stored and the old entry is retired for you. Protected entries are never archived.
4. **Changing facts:** `retire <old_id>`, or `add --content "..." --supersedes <old_id>`. An explicit signal is still the clearest one — do not rely on the engine inferring an update.

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

`recall` accepts only `--budget` and `--query`: there is no per-category filter, and the command
**saves the store**, so a read is a write. Always pass `--query` — without it recall is
query-blind and returns the top of the strength ranking instead of the answer to your question.

Decay is now honest: strength is *derived* from a stored `(score, score_at)` pair rather than
re-applied on every `refresh`, so refreshing hourly, daily or once a week gives the same score
curve and frequent `refresh` calls cannot over-decay the store (defect D1, fixed in round 4).

## Known limits

Read these before relying on this skill. Each is tracked with a fix in
[`docs/FIX-PLAN.md`](docs/FIX-PLAN.md).

- **Decay-only retirement is still unproven.** With an explicit `retire`/`--supersedes` signal the
  superseded fact stops surfacing (leak 0.000). With **no** signal the configured arm still leaks
  1.000; the *pure* configuration now leaks 0.000 while retrieving 1.000 of the evidence, which is
  the first time in this project that decay alone demoted a stale fact — a lead on a three-scenario
  suite, not a proven capability. Retire what you know has been replaced.
- **The budget win belongs to the allocator, not the store.** A BM25 arm that borrows only the
  engine's packer leads the whole suite (hit rate 0.893 against the best engine arm's 0.835, at
  1296 characters per evidence hit against 1572). The strength, decay and tier machinery has not
  been shown to earn its cost — see the allocator controls in
  [benchmarks/RESULTS.md](benchmarks/RESULTS.md).
- **Ranking is fine, not best.** A plain BM25 baseline orders evidence better (nDCG@10 0.732
  against 0.639 for the best engine arm). The measured win over *flat* memory is budget
  allocation: 1572 characters per evidence hit against a flat store's 1734.
