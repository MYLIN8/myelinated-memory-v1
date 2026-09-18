# Myelinated Memory

> **A Hebbian retrieval-strength memory engine for Hermes Agent.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Version: 2.0](https://img.shields.io/badge/Version-2.0-green.svg)]()

Myelinated Memory is a context-management system that simulates neural consolidation. By assigning retrieval strength ("myelination") scores to memories, the engine naturally prioritizes what's important, letting hot memories hog the context budget while letting dormant trivia decay.

---

## 🚀 Why Use Myelinated Memory?

In a standard agent, all memories are treated equally, causing the context budget (~2,200 chars) to get stuffed with stale information. Myelinated Memory solves this by:

*   **Hebbian Prioritization:** Memories used often are "myelinated" (strengthened, easier to recall).
*   **Intelligent Decay:** Dormant memories decay over time, demoting them to summaries or stubs to free up space.
*   **Zero-Dependency Engine:** A single file, no vector database, no APIs, no Docker.
*   **Session-Boundary Consolidation:** Cleans up, clusters related knowledge, and removes redundancies automatically.

---

## 📊 Comparison

| Feature | Flat/Static Memory | Vector DB (Mem0/Zep) | Myelinated Memory |
| :--- | :--- | :--- | :--- |
| **Budget Management** | None (cluttered) | Expensive/Overhead | **Optimized (Tiered)** |
| **Infrastructure** | None | High (Server/DB) | **Zero (Single File)** |
| **Logic** | Static | Semantic | **Hebbian/Biomimetic** |
| **Latency** | Instant | High (API call) | **Instant (Local)** |

---

## 🛠️ Quick Start

```bash
# 1. Initialize decay and consolidate (run at session start)
python3 scripts/myelinate.py refresh

# 2. Add a memory (pinned memories never decay)
python3 scripts/myelinate.py add --content "User prefers concise replies." --protected

# 3. Retrieve context (sorted by myelination score)
python3 scripts/myelinate.py recall
```

---

## 📖 Learn More

- [Deep Dive: How It Works](docs/HOW-IT-WORKS.md)
- [Hermes Integration Guide](SKILL.md)

---

## 🤝 Contributing

We welcome contributions to make the memory engine more efficient, robust, or biomimetic. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
