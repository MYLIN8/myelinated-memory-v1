# Myelinated Memory — How It Works

*A retrieval-strength memory system for Hermes Agent, built on the biology-inspired idea that memories you use a lot should be easy to recall, and memories you don't should cost less.*

## 1. The Big Idea
Normally an AI agent has a fixed memory allowance. Everything gets the same treatment regardless of how often it matters. Myelinated Memory assigns each memory a **score (0 → 1)** that rises when used and falls when ignored. High-scoring memories get full detail in context; low-scoring ones are demoted to a summary or a short gist — so the agent naturally spends its limited memory on what you actually rely on.

## 2. Mechanics
- **Boost on access (Hebbian):** When recalled and used, the score rises with diminishing returns.
- **Decay on dormancy (Ebbinghaus × category):** at each session boundary an unprotected memory loses strength at a per-category rate, based on **time since it was last used** — not on its age since creation (user preferences decay slowly; one-off task facts decay fast).

  > ⚠️ **Known defect (D1):** the rate is currently applied through an arithmetic bug — because `refresh()` multiplies by the *total* dormancy every time it runs, repeated refreshes compound the decay (`rate × d(d+1)/2` instead of `rate × d`). Measured: 0.259026 after 7 daily refreshes where this page's formula predicts 0.486351. Fix: W0.1 in [PLAN.md](PLAN.md).

## 3. Tiers
| Tier | Score Range | What the agent sees |
|------|-------------|----------------------|
| **Active** | > 0.5 | Full text, every time |
| **Latent** | above 0.1, up to 0.5 | One-line summary only (≤ 160 characters) |
| **Archived** | 0.1 or below | A short content gist (≤ 64 characters) |

> **Archived rendering changed in round 2 (R7).** The original design said "ID stub only". On a large
> store almost everything decays to Archived, and id stubs filled the budget with text that no
> answering model can read. `recall` now injects a 64-character content gist instead. The bare id stub
> is still available programmatically via `render(memory, "stub")`.

## 4. How recall decides what to show
`recall()` walks memories in priority order and renders each at the highest detail level that still fits the character budget. With the defaults it does more than the original specification described, and each capability can be switched off independently (the benchmark relies on that to attribute its results):

- **Query-aware ranking** (`similarity=True`) — when a question is supplied, ranking blends TF-IDF cosine similarity with retrieval strength. With no question it is score-only, exactly as originally specified.
- **Value-per-character packing** (`knapsack=True`) — rather than filling the budget in tier order, it greedily takes the `(memory, detail)` pairs with the best expected value per character, so a cheap summary can outrank an expensive full text.
- **Supersession** (`stale_retirement=True`) — `add(..., supersedes=id)` or `retire(id)` stops a replaced fact from being surfaced.
- **Duplicate collapsing** — near-duplicates merge on write, found through a MinHash sketch index rather than a full scan.

`python3 scripts/myelinate.py --pure ...` restores the original query-blind, tier-ordered behaviour with no supersession. That is the configuration the benchmark reports as *"Myelinated — as specified"*.

## 5. What the benchmark says

This page describes the design; it is not evidence that the design wins. Measured against BM25 and TF-IDF cosine over identical event streams and an identical 2,200-character budget:

- The engine **as specified** retrieves **0.650** of the evidence that the best semantic arm finds (**0.874**).
- The **configured** engine reaches **0.825** — a gap of 4.9 points, down from 22.4.
- **Decay is unproven**: with no explicit retire signal, superseded facts still leak at **1.000**.
- Semantic retrieval still ranks better (nDCG@10 0.732 vs 0.606).

See [benchmarks/RESULTS.md](../benchmarks/RESULTS.md) for the full tables, [PLAN.md](PLAN.md) for the round-3 plan — which starts by repairing the measurement, including the `PRIOR_WEIGHT` sweep that shows this page's ranking contribution is currently a net cost — and [REMEDIATION.md](REMEDIATION.md) for what each round changed.
