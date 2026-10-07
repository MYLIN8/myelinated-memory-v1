# Myelinated Memory — How It Works

*A retrieval-strength memory system for Hermes Agent, built on the biology-inspired idea that memories you use a lot should be easy to recall, and memories you don't should cost less.*

## 1. The Big Idea
Normally an AI agent has a fixed memory allowance. Everything gets the same treatment regardless of how often it matters. Myelinated Memory assigns each memory a **score (0 → 1)** that rises when used and falls when ignored. High-scoring memories get full detail in context; low-scoring ones are demoted to a summary or a short gist — so the agent naturally spends its limited memory on what you actually rely on.

## 2. Mechanics
- **Boost on access (Hebbian):** When recalled and used, the score rises with diminishing returns.
- **Decay on dormancy (Ebbinghaus × category):** at each session boundary an unprotected memory loses strength at a per-category rate, based on **time since it was last used** — not on its age since creation (user preferences decay slowly; one-off task facts decay fast).

  Strength is **derived, not accumulated**. Each memory stores its score together with the moment
  that score was realized (`score_at`), and
  `strength(mem, now) = score × exp(−rate × (now − score_at) / 86400)`. Closing the books on
  accrued decay (`realize`) is idempotent at a fixed instant, so refreshing hourly, daily or once a
  week produces the same curve: the exponent is `rate × d`, never `rate × d(d+1)/2`.
  *(This corrected defect D1 in round 4. The pre-fix behaviour compounded — 0.259026 after seven
  daily refreshes where the rule above predicts 0.486351 — and the earlier version of this page
  documented the bug rather than the rule.)*

## 3. Tiers
| Tier | Score Range | What the agent sees |
|------|-------------|----------------------|
| **Active** | > 0.5 | Full text, every time |
| **Latent** | above 0.1, up to 0.5 | One-line summary only (≤ 160 characters) |
| **Archived** | 0.1 or below | A short content gist (≤ 64 characters) |

> **Archived rendering changed in round 2 (R7).** The original design said "ID stub only". Under the
> compounding decay bug of round 1 almost everything on a large store fell to Archived, and id stubs
> filled the budget with text that no answering model can read, so `recall` now injects a
> 64-character content gist instead. The bare id stub is still available programmatically via
> `render(memory, "stub")`.
>
> **The tier mix itself changed in round 4.** With the decay arithmetic repaired (defect D1), the
> archived tier is reached by real dormancy rather than by an arithmetic accident. Measured on a
> model of the engine's own scoring on the 900-memory scale corpus (seed 0, 60 virtual days,
> validated against the shipped engine to 1.7e-16): the pre-fix tier mix was **636 archived** of
> 900, the fixed rule archives **none**, and the same budget therefore sees **+33% more characters**
> per memory — which is why the decay fix and the tier policy had to move together
> ([ROUND4-DESIGN.md](ROUND4-DESIGN.md) §3). On the published run the budget-efficiency criterion
> still passes (1572 characters per evidence hit for the best engine arm against flat memory's
> 1734).

## 4. How recall decides what to show
`recall()` walks memories in priority order and renders each at the highest detail level that still fits the character budget. With the defaults it does more than the original specification described, and each capability can be switched off independently (the benchmark relies on that to attribute its results):

- **Query-aware ranking** (`similarity=True`) — when a question is supplied, ranking blends TF-IDF cosine similarity with retrieval strength. With no question it is score-only, exactly as originally specified.
- **Value-per-character packing** (`knapsack=True`) — rather than filling the budget in tier order, it greedily takes the `(memory, detail)` pairs with the best expected value per character, so a cheap summary can outrank an expensive full text.
- **Supersession** (`stale_retirement=True`) — `add(..., supersedes=id)` or `retire(id)` stops a replaced fact from being surfaced.
- **Duplicate collapsing** — near-duplicates merge on write, found through a MinHash sketch index rather than a full scan. Above the 0.90 similarity threshold the engine separates a *restatement* (same value words, so punctuation and whitespace differences collapse) from an **update** (a value changed): an update is stored as its own memory and the superseded entry is retired with `superseded_by` set, even when the caller passes no signal (defects D13/D14, fixed in round 4).

`python3 scripts/myelinate.py --pure ...` restores the original query-blind, tier-ordered behaviour with no supersession. That is the configuration the benchmark reports as *"Myelinated — as specified"*.

## 5. What the benchmark says

This page describes the design; it is not evidence that the design wins. Measured against BM25 and TF-IDF cosine over identical event streams and an identical 2,200-character budget:

- The engine **as specified** retrieves **0.680** of the evidence that the best semantic baseline
  finds (**0.874**, TF-IDF). It measured 0.650 before round 4 repaired the decay arithmetic.
- The **configured** engine reaches **0.835** — a gap of 3.9 points, down from 19.4 for the
  as-specified configuration.
- **Decay is unproven**: with no explicit retire signal, superseded facts still leak at **1.000**,
  exactly as flat memory does.
- Semantic retrieval still ranks better (nDCG@10 0.732 against 0.639 for the best engine arm).
- **The budget win is allocation, not memory policy.** This is the finding of round 4's allocator
  controls: a plain BM25 arm given only the engine's packer leads the entire suite — **0.893** hit
  rate and **1296** characters per evidence hit, against the best engine arm's 0.835 and 1572. What
  the strength, decay and tier machinery contributes is not yet demonstrated, which is why this page
  describes a design rather than a result.

See [benchmarks/RESULTS.md](../benchmarks/RESULTS.md) for the full tables, [PLAN.md](PLAN.md) for the round-3 plan — which starts by repairing the measurement, including the `PRIOR_WEIGHT` sweep that found this page's ranking contribution to be a net cost — and [REMEDIATION.md](REMEDIATION.md) for what each round changed. That sweep ran **before** the decay repair, against the compounding curve and the archived tier it produced, so it is exploratory and is being re-measured rather than quoted as current.
