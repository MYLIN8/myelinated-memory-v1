---
name: myelinated-memory
description: "Use when managing memory priority by access frequency."
version: 2.0.0
author: Hermes Agent
tags: [memory, myelination, recall, prioritization]
---

# Myelinated Memory System

A retrieval-strength ("myelination") memory system that simulates neural consolidation: memories used often get stronger and easier to recall; dormant memories decay to cheaper tiers, so the limited context budget goes to what actually matters.

## Engine & paths

- **Engine:** `scripts/myelinate.py`
- **Store:** `~/.hermes/memory/myelinated.json`

## Session Protocol

1. **Session start:** run `python3 scripts/myelinate.py refresh`. Then `recall` for context injection.
2. **During session:** when a memory is used, `access <id>` to strengthen it. Use `--protected` / `pin` for anything safety-critical or identity-fixed.
3. **Adding memories:** use `add` (dupes auto-collapse; protected entries never archived).

## Commands

```bash
# Session-boundary automation (decay + consolidation)
python3 scripts/myelinate.py refresh

# Add a memory
python3 scripts/myelinate.py add --content "..." [--protected]

# Strengthen a memory when used
python3 scripts/myelinate.py access <memory_id>

# Context injection
python3 scripts/myelinate.py recall [--budget 2200]
```
