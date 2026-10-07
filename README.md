# Myelinated Memory

> **A Hebbian retrieval-strength memory engine for Hermes Agent.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE.md)
[![Python 3.10 | 3.11](https://img.shields.io/badge/python-3.10%20%7C%203.11-3776ab.svg)](CONTRIBUTING.md)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](SECURITY.md)
[![Checks](https://img.shields.io/badge/checks-7%20offline%20suites%20%7C%2069%20engine%20checks-brightgreen.svg)](#4-how-it-is-tested)
[![CI](https://github.com/TWS07-gif/myelinated-memory/actions/workflows/checks.yml/badge.svg)](https://github.com/TWS07-gif/myelinated-memory/actions/workflows/checks.yml)
[![Benchmark](https://img.shields.io/badge/benchmark-results-orange.svg)](benchmarks/RESULTS.md)

![The session-boundary pass and the recall ladder](docs/assets/hero.svg)

Myelinated Memory is a context-management system that simulates neural consolidation. Each memory carries a retrieval-strength score that rises when it is used and decays when it is ignored; the score decides **how much of that memory's text occupies a fixed context budget**, and a memory that has been replaced can be retired so it stops surfacing.

It is a single Python file with **zero dependencies** — no vector database, no embedding API, no Docker, no service to run.

> [!IMPORTANT]
> **The honest headline, before any numbers below:** the engine *as specified* does **not** beat
> semantic retrieval. A reconfigured version of it closes the measured gap to **4.9 evidence-hit-rate
> points**, and it does win on budget efficiency — but semantic ranking (BM25) still orders evidence
> better, and the decay mechanism this project is named after is, so far, **not proven to do what its
> name claims**. The project publishes that verdict itself, in
> [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md); the plan to change it is [`docs/PLAN.md`](docs/PLAN.md),
> the ordered fix list is [`docs/FIX-PLAN.md`](docs/FIX-PLAN.md), and the design that makes the fixes
> one mechanism is [`docs/ROUND4-DESIGN.md`](docs/ROUND4-DESIGN.md).

**Start here** — no dependencies, no service, no build; one file and a JSON store:

```bash
python3 scripts/myelinate.py add --content "User prefers concise replies." --category preference
python3 scripts/myelinate.py recall --query "What does the user prefer?" --budget 2200
python3 benchmarks/test_engine.py                    # engine ok: 69 checks
```

All seven check suites are offline, free and run in seconds ([§4](#4-how-it-is-tested)); the full session protocol is [§7](#7-quick-start).

**What it is good for today, with the caveats attached:**

*   **Zero infrastructure.** One file, standard library only, no vector database, no embedding API, no service. You still need an answering model and a session hook that calls `refresh` and `access`.
*   **Pinned memories that cannot be crowded out.** `pin` sets the score to 1.0, exempts the memory from decay and pruning, and gives it an effectively infinite prior.
*   **Duplicate collapsing on write**, so a store does not fill with paraphrases of the same fact. Measured on a probe corpus (97.5% collapse at Jaccard 0.96, zero false merges), not yet on the benchmark.
*   **A tiered budget.** Full text, then a summary, then a 64-character gist — plus value-per-character packing, so a cheap summary can outrank an expensive full text. This is the part that measurably wins: 1589 characters per evidence hit against a flat store's 1734.
*   **It ships its own benchmark and reports its own losses**, including the pre-registered criterion its own hero arm fails. The failures on this page are here on purpose.

---

## Contents

| Section | What it answers |
| :--- | :--- |
| [1. How it works](#1-how-it-works) | the mechanism, formula by formula, with the constants |
| [2. Measured results](#2-measured-results) | every table, including the arms that beat it |
| [3. What the numbers actually say](#3-what-the-numbers-actually-say) | the reading, including where the remaining gap lives |
| [4. How it is tested](#4-how-it-is-tested) | what is verified, what is not, and how to run the checks |
| [5. How it is tuned](#5-how-it-is-tuned) | which constants are measured, which are guesses, and the protocol |
| [6. Known defects](#6-known-defects) | the bugs found by review, and what they affect |
| [7. Quick start](#7-quick-start) | the session protocol and the importable API |
| [8. Reproducing the benchmark](#8-reproducing-the-benchmark) | one command, plus the method notes |
| [9. Project layout](#9-project-layout) | what every file is for |
| [10. Limitations, security, contributing](#10-limitations-security-contributing) | the fine print |

---

## 1. How it works

Everything below is the behaviour of [`scripts/myelinate.py`](scripts/myelinate.py). Where the original specification was silent, the implementation chose a value and marked it `ASSUMPTION` in the source so that a benchmark can argue with it rather than with an unstated guess. The values are listed in [§5](#5-how-it-is-tuned).

### 1.1 The score

Every memory carries `score ∈ [0, 1]`. New memories start at `INITIAL_SCORE = 0.60`, deliberately just above the Active threshold so new information is visible immediately and decay is what demotes it.

### 1.2 Boost on use (Hebbian)

`access(id)` raises the score with diminishing returns:

```
score ← score + BOOST_ALPHA × (1 − score)          # BOOST_ALPHA = 0.35
```

The increments shrink as the score approaches 1, so a memory can be used indefinitely without saturating the arithmetic. `last_access` is updated, and the access count is recorded. Pinned memories are set to `PROTECTED_SCORE = 1.0` instead.

### 1.3 Decay on dormancy (Ebbinghaus × category)

`refresh()` — the session-boundary call — walks the store and multiplies each unprotected, unretired memory's score by a category-dependent decay factor:

```
score ← score × exp(−rate(category) × days_since_last_access)
```

Rates per day: `identity 0.002`, `user`/`preference 0.010`, `general 0.030`, `task 0.050`, `ephemeral 0.100`. The intent is that a durable preference survives weeks of silence while a one-off task fact fades in days.

> [!WARNING]
> **This formula has a bug, found by review and confirmed by probe.** Because `refresh()` uses the *total* time since the last access and does not advance `last_access` while a memory is dormant, the decay is applied again on every refresh: the effective exponent is `rate × d(d+1)/2`, not `rate × d`. Measured over 7 daily refreshes, a fresh general memory scores **0.259026**; the documented rule predicts **0.486351**. Every decay-derived figure in rounds 1–2 was produced through this bug. It is defect **D1** in the plan, fixed by **W0.1**, and it is why the decay claim is listed as unproven rather than failed.
>
> **It is also not a local repair.** On the 900-memory scale corpus, correcting the rule leaves **0 archived memories instead of 636** and raises the mean rendered characters per memory by **33%** — so the tier thresholds, the rendering caps and the packer have to move in the same change, or the budget-efficiency claim is lost. Measured in [`docs/ROUND4-DESIGN.md`](docs/ROUND4-DESIGN.md) §3.

### 1.4 Tiers and rendering

The score maps to a tier, and the tier decides how much text the memory may occupy:

| Tier | Score | What recall renders |
| :--- | :--- | :--- |
| **Active** | > 0.5 | full text |
| **Latent** | 0.1 – 0.5 | a one-line summary (≤ 160 chars) |
| **Archived** | ≤ 0.1 | a short content gist (≤ 64 chars) |

The specification said the Archived tier should render an *opaque id stub*. Round 2 changed that (**R7**): on a large store almost everything decays to Archived, and id stubs filled the budget with text no answering model can read. The stub is still available programmatically via `render(memory, "stub")`; the recall ladder no longer uses it. Pinned memories are always treated as Active.

### 1.5 Recall: ranking, packing, similarity

`recall(budget=2200, query=None)` returns a context string filled to a character budget. Three capabilities are independent switches, because the benchmark has to attribute any improvement to one named change:

* **Query-aware ranking** (`similarity=True`) — when a question is supplied, a TF-IDF cosine similarity (`log((n+1)/(c+1)) + 1` weighting) is blended with the score:
  `value = similarity + PRIOR_WEIGHT × score` (`PRIOR_WEIGHT = 0.35`). With no question, recall is score-only — exactly as originally specified.
* **Value-per-character packing** (`knapsack=True`) — instead of walking the priority order and taking each memory at the highest detail that fits, it builds every `(memory, detail)` pair, values it at `value × DETAIL_VALUE[detail]` (`full 1.0`, `summary 0.55`, `gist 0.25`) and greedily takes the best expected value per character. A cheap summary can therefore outrank an expensive full text.
* **Supersession** (`stale_retirement=True`) — retired memories are excluded before ranking.

Both packing paths respect the budget by construction: a block that would overflow is skipped, never truncated, and `result.chars ≤ budget` always.

The remaining engine-side machinery:

* **Duplicate collapsing** — near-duplicates merge on write and again at refresh (`Jaccard ≥ 0.90` over content tokens). Candidates come from a MinHash bottom-8 sketch index in LSH bands rather than a full postings scan: the postings version profiled at ~2.9M Jaccard calls per 10,000 memories. Single-hash keys were chosen after 2-hash bands measured only 62.6% recall at Jaccard 0.93 — a documented feature silently missing a third of real duplicates.
* **Clustering at refresh** (`Jaccard ≥ 0.50`) — assigns each touched memory a cluster representative. **Nothing reads it.** It is currently pure overhead on the refresh path, noted in [§6](#6-known-defects).
* **Supersession** — `retire(id)` marks a fact dead; `add(content, supersedes=old_id)` adds the replacement and retires the old one in a single step. `retired_excluded` reports how many memories were filtered out of a recall.
* **Pinning** — `pin(id)` (or `--protected`) sets the score to 1.0, exempts the memory from decay, and gives it an effectively infinite prior (`1e6`) so it is never crowded out by relevance, and exempts it from pruning.
* **Pruning** — optional storage ceiling (`refresh --max-entries N`), which removes the weakest **archived** memories only, never pinned or live ones. The default is unbounded: archived entries are kept.
* **Persistence** — `save()` writes JSON atomically (temp file + `os.replace`), `load()` rebuilds the indexes, tolerates unknown fields, and marks the similarity index dirty. The store path resolves as explicit `--store` → `HERMES_MEMORY_STORE` → `~/.hermes/memory/myelinated.json`. The schema field is written but **not validated on load** — there is no migration path.

---

## 2. Measured results

These are not estimates. The full run is reproducible with one command:

```bash
python3 benchmarks/run_bench.py
```

33 scenarios / 206 queries / a 2,200-character budget, on a virtual clock. Ten memory policies compete over an identical event stream, so every arm sees the same adds, accesses, retirements and questions. The engine rows are **the same engine with one capability switched on at a time**, which is what makes each line attributable to a named change:

| Memory system | Task success | Evidence hit rate | nDCG@10 | Chars per hit | p95 recall (last run) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| No memory (control) | 0.000 | 0.000 | 0.000 | - | 0.00 ms |
| Flat / FIFO store | 0.558 | 0.777 | 0.361 | 1734 | 0.05 ms |
| Recency / LRU | 0.485 | 0.612 | 0.342 | 2201 | 0.23 ms |
| Semantic - BM25 | 0.549 | 0.864 | **0.732** | **1336** | 1.06 ms |
| Semantic - TF-IDF cosine | **0.563** | **0.874** | 0.729 | 1541 | 1.96 ms |
| Myelinated - as specified | 0.505 | 0.650 | 0.426 | 2019 | 1.55 ms |
| Myelinated + query similarity | 0.553 | 0.796 | 0.690 | 1651 | 2.03 ms |
| **Myelinated + value-per-char packing** | 0.553 | **0.825** | 0.606 | **1589** | 3.02 ms |
| Myelinated + supersession | 0.553 | 0.825 | 0.606 | 1589 | 2.78 ms |
| Myelinated + utility reinforcement | 0.534 | 0.801 | 0.571 | 1652 | 2.92 ms |

### 2.1 The ablation ladder

| Arm | One change | Task success | Hit rate | nDCG@10 | Chars/hit |
| :--- | :--- | ---: | ---: | ---: | ---: |
| M6 myelinated (pure) | *the specification* | 0.505 | 0.650 | 0.426 | 2019 |
| M7 myelinated + similarity | R1 query similarity | 0.553 | 0.796 | 0.690 | 1651 |
| **M8 myelinated + knapsack** | R4/R7 value-per-char packing | 0.553 | **0.825** | 0.606 | **1589** |
| M9 myelinated + supersession | R5 retire/supersede | 0.553 | 0.825 | 0.606 | 1589 |
| M10 myelinated + reinforcement | R2 utility reinforcement | 0.534 | 0.801 | 0.571 | 1652 |

### 2.2 Staleness: supersession vs decay alone

Two different claims, measured separately and never merged (board ruling BM-003). The supersession suite emits an explicit retire signal and every arm is offered it; the decay-only suite is the *same scenarios with no retire signal at all*, so it measures whether retrieval-strength decay alone retires a stale fact.

| Arm | Supports retire | Leak (with retire) | Leak (decay only) | Hit rate (decay only) |
| :--- | :---: | ---: | ---: | ---: |
| M0 no-memory | no | 0.000 | 0.000 | 0.000 |
| M1 flat/FIFO | yes | 0.000 | 1.000 | 1.000 |
| M2 recency/LRU | yes | 0.000 | 0.000 | 0.000 |
| M3 semantic/BM25 | yes | 0.000 | 1.000 | 1.000 |
| M4 semantic/TF-IDF | yes | 0.000 | 1.000 | 1.000 |
| M6 myelinated (pure) | no | 0.000 | 0.000 | 0.000 |
| M7 myelinated + similarity | no | 1.000 | 1.000 | 1.000 |
| M8 myelinated + knapsack | no | 1.000 | 1.000 | 1.000 |
| M9 myelinated + supersession | yes | **0.000** | 1.000 | 1.000 |
| M10 myelinated + reinforcement | yes | **0.000** | 1.000 | 1.000 |

Read the `0.000`s carefully: M0, M2 and M6 "pass" the decay-only test only because they retrieved **nothing** (`hit_rate_decay_only = 0.000`). A leak criterion without a retrieval floor can be satisfied by a store that surfaces no evidence at all — which is why the round-3 plan gates it (BM-009).

### 2.3 Where the rounds are won and lost, by tier (hit rate)

| Tier (queries) | M6 pure | **M8 packing** | BM25 | TF-IDF |
| :--- | ---: | ---: | ---: | ---: |
| curated (112) | 0.946 | **1.000** | 0.964 | 0.991 |
| synthetic (28) | 1.000 | **1.000** | 0.964 | 0.964 |
| staleness (3 + 3) | 0.000 | **1.000** | 1.000 | 1.000 |
| locomo, public (60) | 0.000 | 0.400 | **0.617** | 0.600 |
| **all 206** | 0.650 | 0.825 | 0.864 | **0.874** |

### 2.4 At 10,000 memories over 60 virtual days

| Arm | Stored | Ingest | Session refresh | Recall p50 | Recall p95 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Semantic - BM25 | 10000 | 0.04 s | 0.00 s | 21.6 ms | **47.3 ms** |
| Semantic - TF-IDF | 10000 | 0.01 s | 0.00 s | 20.9 ms | 99.5 ms |
| Myelinated - as specified | 9998 | 4.8 s | 2.7 s | **5.6 ms** | **10.5 ms** |
| Myelinated + value-per-char packing | 9998 | 5.4 s | 3.0 s | 19.7 ms | 115.8 ms |

### 2.5 The pre-registered decision rule

The rule below was fixed **before** the run:

> The recommended configuration is *proven better* only if it wins the budget-efficiency and query-conditioned-retrieval criteria simultaneously. Anything else is a loss that needs the remediation list.

| # | Criterion | Result | Evidence |
| :--- | :--- | :---: | :--- |
| 1 | Budget efficiency: no more characters per evidence hit than flat/FIFO | **PASS** | 1652 vs flat 1734 |
| 2 | Query-conditioned retrieval (pre-registered hero M10): within 5 hit-rate points of the best semantic arm | **FAIL** | M4 leads by −0.073 |
| 3 | Best measured engine configuration within 5 hit-rate points of the best semantic arm | **PASS** | M8 at 0.825, M4 leads by −0.049 |
| 4 | Staleness with supersession: under 20% of superseded facts still surface | **PASS** | leak 0.000 vs flat 0.000 |
| 5 | Decay alone: superseded facts fade without an explicit retire signal | **FAIL** | leak 1.000 |
| 6 | Scale: recall p95 faster than BM25 at 10,000 memories | **FAIL** | 108.7 ms vs 47.3 ms |
| 7 | Significantly better end-to-end score than a flat baseline (Holm-adjusted p<0.05) | **PASS** | attributed to M2 recency/LRU |

**Verdict: 4 of 7 criteria passed.** The pre-registered hero arm is *not* swapped for whichever arm won — M10 is reported at its measured value, and M8 (a different arm) is reported alongside it as the best configuration. Three of these rows carry known instrumentation defects (the doubled `%` in row 4, the one-of-two-flat-arms loophole in row 7, and the five-sample "p95" in row 6) — see [§6](#6-known-defects) and [`docs/PLAN.md`](docs/PLAN.md) §3.

---

## 3. What the numbers actually say

*   **Closing the gap.** The specification's behaviour scores **0.650** hit rate. Adding query similarity takes it to **0.796**, and packing the budget by expected value per character takes it to **0.825** — against the best semantic arm's **0.874**. A gap of 22.4 points became **4.9 points**. That meets the "within 5 points" bar through the *best measured configuration* criterion (M8); the pre-registered hero arm — M10, utility reinforcement — is a different arm and still **fails** it by 7.3 points, which the report states rather than hides.
*   **Much of the gap is one hand-set number.** [§5.1](#51-one-sweep-has-been-measured) swept `PRIOR_WEIGHT` on the same 206 queries: the committed 0.35 gives 0.825 hit rate and 0.606 nDCG, while 0.0 gives 0.893 and 0.672. The retrieval-strength prior, hand-set at 0.35 and never swept before now, costs more than the whole distance to semantic retrieval on this workload. That sweep is exploratory (one seed, no hold-out), so the committed configuration and hero arm stay as measured.
*   **Where the gap lives.** §2.3 answers this exactly. On the curated and synthetic tiers the configured engine hits **1.000** of the evidence (BM25 0.964, TF-IDF 0.964/0.991) — at ceiling. Nearly the whole 4.9-point deficit comes from the 60 LoCoMo queries, where it finds **0.400** against BM25's **0.617**. The arithmetic is exact: M8 is perfect on 146 of 206 queries and 0.400 on the 60 LoCoMo ones; the weighted total is 0.825. That tier is a ~1,450-turn transcript scored against a 2,200-character budget, and every arm scores near zero task success there.
*   **Budget efficiency now wins, with one open question.** The configured engine spends **1589** characters per evidence hit, better than a flat FIFO store's **1734**; as specified it lost this comparison (2019). But the baselines fill the budget with full text while the engine renders a tier ladder and packs by value per character, so part of this win may be the allocator rather than the memory policy. The control that settles it — BM25 ranked but packed by the engine's own allocator — is W1.1 in the plan. The metric itself is also a reward-to-cost ratio (`mean chars ÷ mean hit rate`), not literally "characters per hit".
*   **Supersession works.** With an explicit retire signal, stale facts leak **0.000**. But every store can implement that by deleting, so it is not a myelination advantage; it is a feature, not evidence for the thesis.
*   **Decay is still unproven** — and the arithmetic behind it is now known to be wrong. On the suite with no retire signal the engine leaks **1.000**, exactly like flat memory and BM25. The "intelligent decay" claim does not survive contact with the measurement, and the compounding bug means the numbers that *were* produced are not the ones the documentation describes.
*   **Utility reinforcement hurt.** Learning from memories that were present when an answer came out right *lowered* hit rate (0.825 → 0.801) and task success (0.553 → 0.534). Reinforcing everything that was nearby rewards proximity, not causation.
*   **Two real costs.** Ingest is 5.4 s per 10,000 memories against flat memory's 0.010 s, and query-aware recall is slower than BM25 at scale (116 ms vs 47 ms p95) because every query rescans the store. That 116 ms needs a caveat: it is the **maximum of five queries**, and one of them pays a one-off index rebuild — measured per query, the same store answers in 69.5, 15.5, 14.5, 14.4 and 13.9 ms, with a warm similarity scan costing 6.9 ms. The figure therefore moves between runs. The query-*blind* engine measures **10.5 ms** in the same harness — the fastest *myelinated* configuration, though a flat store answers in 0.60 ms because it does no scoring at all.
*   **BM25 still ranks better.** nDCG@10 is 0.732 for BM25 against 0.606 for the configured engine, so semantic retrieval puts the evidence *higher* even when the engine eventually surfaces it. Part of that is the allocator reordering results (nDCG is computed on the packed order, defect D8), and part is real.

**The honest summary: use the configured engine for its zero infrastructure, pinned-memory guarantees and duplicate collapsing — not because it retrieves better than a semantic index.** If you only care about retrieval quality and can run BM25, BM25 competes with or beats it on every ranking metric.

The round-3 plan is [`docs/PLAN.md`](docs/PLAN.md). It starts by repairing the *measurement* rather than the engine, because several instruments above are known to be unsound. What was tried in each round, including the negative results, is logged in [`docs/REMEDIATION.md`](docs/REMEDIATION.md).

---

## 4. How it is tested

**Until this review the engine had no automated checks at all** — while holding every real defect this project has found: compounding decay, the similarity index that `retire()` leaves stale, double tokenisation on `add()`, and a clustering pass on the refresh hot path whose output nothing reads. `scripts/myelinate.py` ended at `sys.exit(main())`, so the decay bug was measured, tabulated and explained across two rounds of reports before anyone read the formula. That gap is now closed. **What exists today** is the engine's own suite (69 checks) plus a harness suite of roughly 48 assertions:

| Check | Command | What it verifies |
| :--- | :--- | :--- |
| **Engine** | `python3 benchmarks/test_engine.py` | 69 checks on the engine itself: bounded diminishing boost, pinned memories never decaying, tier thresholds, rendering caps (including that the archived tier is readable rather than a bare id stub), the budget never exceeded on both recall paths for budgets 0/50/500/2200, retired memories excluded, duplicate collapsing, persistence round-trip, store-path precedence — plus the known-defect registry |
| Statistics | `python3 benchmarks/stats.py` | Wilcoxon (identical → p 1.0; a clear shift → p<0.05), Cliff's δ sign, bootstrap CI contains the mean and is seed-deterministic, Holm correction, `format_p`, percentiles |
| Scenario fixtures | `python3 benchmarks/synthetic.py` | unique ids, event ordering, evidence and stale ids exist and precede their query, the staleness suites retire (or provably do not retire) their stale ids, filler > 200 so the budget binds, stale/evidence Jaccard < 0.9 so collapsing cannot masquerade as staleness handling, and **globally unique query ids** — a collision would silently merge two questions in the paired statistics |
| Judge semantics | `python3 benchmarks/judge.py` | the deterministic oracle extracts a line, grades both `contains` and `exact`, and refuses the LLM label |
| LLM judge protocol | `python3 benchmarks/test_llm_judge.py` | 22 assertions over request shape, JSON parsing, response caching, the HTTP-500 error path and judge selection, all against a local OpenAI-protocol stub — **no API key needed** |
| Stub protocol | `python3 benchmarks/stub_llm.py` | the stub's request→response mapping, including the forced-failure path |
| Arm registry | `python3 benchmarks/engines.py` | 10 offline arms are built and timed |
| CI | [`.github/workflows/checks.yml`](.github/workflows/checks.yml) | every suite above plus `py_compile`, on Python 3.10 and 3.11, no network and no secrets |

**The engine now has its own suite**, committed and executed — [`benchmarks/test_engine.py`](benchmarks/test_engine.py), 69 checks covering the score, the tier thresholds, the rendering caps, the budget guarantee on both recall paths, retired-memory exclusion, pinning, duplicate collapsing, persistence round-trip and store-path precedence:

```bash
python3 benchmarks/test_engine.py    # engine ok: 69 checks
```

It uses a **known-defect registry**: behaviour that is wrong but tracked is registered with its defect id and reported as `KNOWN DEFECT W0.1 ...` instead of failing, so the suite is green now and stays green the day the fix lands, and a tracked defect cannot be quietly forgotten. It registers **four** today — the compounding decay (W0.1), the similarity index that `retire()` leaves stale (W0.7), and the two defects an offline self-test pass found afterwards: an update that is ≥ 0.9 similar to a **retired** memory is absorbed by that entry and lost (D13), and the same threshold against a live memory discards the newer wording (D14). Those checks assert each defect's *signature*, so if the code changes for any other reason the registry notices and reports it as `RESOLVED` rather than silently passing.

**Still unverified:** the metric definitions (`metrics.py` has no self-check; `chars_per_hit` is a ratio of two means, `ndcg` normalises against `len(evidence)` gains — neither is pinned to a hand-computed value), the decision rule (`verdict()`, where two logic bugs live), replay determinism, the budget invariant across all ten arms, the LoCoMo conversion, and the report renderer. Their fixtures are W0.9–W0.10 in the plan.

---

## 5. How it is tuned

Most of this engine is **hand-set**, and the source says so: every value chosen where the specification was silent is marked `ASSUMPTION`. Measured provenance, in full:

| Constant | Value | How it was chosen |
| :--- | :--- | :--- |
| `CATEGORY_DECAY_PER_DAY` | 0.002 identity, 0.010 preference, 0.030 general, 0.050 task, 0.100 ephemeral | **guess** — and currently applied through the compounding bug, so tuning it now would fit the bug |
| `INITIAL_SCORE` / `BOOST_ALPHA` | 0.60 / 0.35 | **guess** |
| `ACTIVE_THRESHOLD` / `LATENT_THRESHOLD` | 0.5 / 0.1 | **guess** — these decide the tier mix, and therefore how much text each memory contributes to the budget |
| `DUPLICATE_JACCARD` | 0.90 | measured **off-benchmark** on a probe (97.5% collapse at Jaccard 0.96, 0 false merges on a 500-memory control), never swept against the benchmark itself |
| `SKETCH_SIZE` / `BAND_ROWS` / `MAX_POSTINGS_SCAN` | 8 / 1 / 96 | **the only genuinely measured values**: chosen after 2-hash banding measured 62.6% recall at Jaccard 0.93 and was rejected |
| `SUMMARY_MAX_CHARS` / `GIST_MAX_CHARS` | 160 / 64 | **guess** — jointly decide how many memories fit the budget, so they move hit rate and chars/hit directly |
| `DETAIL_VALUE` | full 1.0 / summary 0.55 / gist 0.25 | **guess** — the packer's whole preference order rests on these three numbers |
| `PRIOR_WEIGHT` | 0.35 | hand-set, and now **measured as costly**: the same engine at 0.0 scores 0.893 hit rate and 0.672 nDCG against 0.825/0.606 at 0.35 — see the sweep below |
| `RECALL_POOL` / `MAX_CANDIDATES` | 600 / 64 | reasoned, never swept; the pool is probably slack at a 2,200-character budget |
| `CLUSTER_JACCARD` | 0.50 | **dead** — nothing reads `mem.cluster` |
| `PROTECTED_SCORE` / `PROTECTED_PRIOR` | 1.0 / 1e6 | reasoned (effectively infinite) |
| Workload: budget, 60 days, 10 000 memories, scenario counts | — | the benchmark definition itself; changing any of it redefines the benchmark |

### 5.1 One sweep has been measured

[`benchmarks/tune_probe.py`](benchmarks/tune_probe.py) replays the same 206 queries with the recommended configuration and one constant changed, and never touches the committed report. The `0.35` row is an internal control: it reproduces M8's published numbers exactly (0.825 hit rate, 0.606 nDCG, 1589 chars/hit, LoCoMo 0.400).

| `PRIOR_WEIGHT` | Task success | Hit rate | nDCG@10 | Chars/hit | LoCoMo hit rate |
| ---: | ---: | ---: | ---: | ---: | ---: |
| **0.00** | 0.539 | 0.893 | **0.672** | **1265** | **0.683** |
| 0.10 | **0.553** | **0.898** | 0.653 | 1460 | 0.650 |
| 0.20 | 0.553 | 0.854 | 0.626 | 1535 | 0.500 |
| 0.35 *(committed)* | 0.553 | 0.825 | 0.606 | 1589 | 0.400 |
| 0.50 | 0.553 | 0.811 | 0.593 | 1617 | 0.350 |
| 1.00 | 0.539 | 0.738 | 0.569 | 1776 | 0.167 |

**The committed value is the second-worst point on the curve.** At `PRIOR_WEIGHT = 0` the same engine scores **0.893** hit rate (above the best semantic arm's 0.874), **0.672** nDCG@10 (against BM25's 0.732), **1265** characters per hit (better than BM25's 1336), and **0.683** on the LoCoMo tier (against BM25's 0.617). The reading is that blending a retrieval-strength prior into a cosine ranker was diluting a ranker that is competitive on its own; the prior helps *allocation* and hurts *ordering*.

The one exception is judged task success, 0.539 at 0.00 against 0.553 at 0.35 — a 0.014 difference inside the noise floor of an evidence-containment oracle ([§8](#8-reproducing-the-benchmark)).

**Three caveats, and they matter:** this is **one seed**, swept on the **report's own 206 queries** with no hold-out, and it was **not pre-registered** — it is exploratory. It therefore changes nothing on this page: the committed value and the pre-registered hero arm stay exactly as measured (BM-004 forbids swapping after seeing the numbers). What it does is justify running the M11 control properly, with the acceptance criterion frozen *before* a hold-out run — that is W1.2 and W6 in [`docs/PLAN.md`](docs/PLAN.md). Reproduce with:

```bash
python3 benchmarks/tune_probe.py                        # the sweep above
python3 benchmarks/tune_probe.py --values 0,0.1,0.2     # a subset
```

**The tuning protocol** (in full in [`docs/TESTING.md`](docs/TESTING.md) §5):

1. Fix the decay arithmetic first — otherwise a sweep fits the bug.
2. One knob per arm, shipped as its own labelled ablation, never folded into M8 (the BM-002 attribution rule).
3. Tune on seeds 0–4, report on 5–9. Today there is **one** seed, so nothing here is out-of-sample yet.
4. Freeze the criterion before the sweep; never pick the winning configuration afterwards (BM-004).
5. Report the sensitivity curve, not the winner. These are pseudo-parameters with no physical meaning.
6. **Tuning cannot repair an attribution problem.** If BM25 with the engine's packer matches M8, the packer did the work and tuning `PRIOR_WEIGHT` is fitting the loss surface of a ranker that contributes nothing.

Ranked sweeps, highest information first: `PRIOR_WEIGHT` (it decides the headline "ranks worse"), then `DETAIL_VALUE` + the summary/gist caps together, then the tier thresholds, then `DUPLICATE_JACCARD` measured on the benchmark corpus, then `RECALL_POOL`, then the decay rates *after* the fix, then the sketch caps against ingest cost.

---

## 6. Known defects

Found by review, verified by probe where marked, and listed with the plan item that closes each.

| # | Defect | Effect on what you read here |
| :--- | :--- | :--- |
| D1 | **Decay compounds.** `rate × d(d+1)/2` instead of `rate × d` (verified by probe: 0.259026 vs 0.486351 at day 7) | every decay and tier number in rounds 1–2; the decay claim below is unmeasurable until fixed |
| D2 | **"p95" is the maximum of five queries**, one of which pays a full index rebuild | the latency columns move between runs; treat small differences as noise |
| D3 | The headline leak column mixes **22 records, 16 of which carry no retire signal** (14 curated + 2 synthetic contradiction + 6 staleness) | the per-arm leak column in the full report; the split §2.2 table is the honest one |
| D4 | A scoped run still scores criteria it never measured (`--tier staleness` prints "3/7") | verdict counts from partial runs |
| D5 | `--judge-limit` writes `0.0` for skipped queries and averages them | any cost-capped LLM run understates task success |
| D6 | The significance criterion passes if *either* flat arm is beaten | row 7 of the decision rule: M2 lost to the hero and is credited while M1 beat the hero and is not consulted |
| D7 | The budget comparison is the engine's **packer** against baselines that **truncate at full text** (`downgrade()` exists unused) | the chars/hit win is partly allocation, not memory policy |
| D8 | nDCG and MRR are computed on the **packed order**, not the ranking order | "BM25 ranks better" is partly an allocator artifact |
| D9 | `retire()` does not invalidate the similarity index, so other memories' IDF is stale until the next add | the M9/M10 similarity numbers during retirement-heavy scenarios |
| D10 | `SKILL.md`'s documented `recall --budget 2200` passes **no question**, so it runs query-blind; `--category` is never set by the skill; `--pure` is a global flag (`recall --pure` is an argparse error) and silently ignores `--query` | following the skill verbatim gets you an unmeasured, query-blind configuration |
| D11 | Unsupported wording in older docs: "fastest recall in the suite" (a flat store is 0.60 ms), "committed results" (`benchmarks/` was untracked when round 2 was written; it is committed now), the tier ladder "fits a large store into a small budget" (no arm succeeds on LoCoMo), `SECURITY.md`'s "no network calls" and "sanitized inputs" | documentation only; the README you are reading is corrected, and `docs/HOW-IT-WORKS.md` was corrected in this round (it said decay tracks a memory's **age**; the code uses time since last *use*, and the page now says so and flags D1) |
| D12 | A literal `%%` leaks into the generated criterion text (row 4 above) | cosmetic, in a generated artefact |
| D13 | **An update ≥ 0.9 similar to a *retired* memory is absorbed by it and lost** (probe: `add` → `retire` → `add` a one-word change returns the retired id, stores nothing new, and recall comes back empty) | the correction workflow the quick start documents; nothing in the benchmark exercises it, because the staleness fixtures stay *below* the collapse threshold |
| D14 | An update ≥ 0.9 similar to a **live** memory silently drops the newer wording | updates phrased close to the fact they replace |
| D15 | A scoped run (`--tier staleness`) **overwrites the committed report** with a partial one (172 → 152 lines, measured in a throwaway copy) | the reproducibility claim: the published tables are one command away from being replaced |

Full detail, with the fix and acceptance criterion for each, is in [`docs/PLAN.md`](docs/PLAN.md) §3; the ordered fix plan — data loss first, then harness honesty, then engine work — is [`docs/FIX-PLAN.md`](docs/FIX-PLAN.md). It also records what the absent `OPENAI_API_KEY` blocks: the LLM-judged task success and the dense-embedding arm (the judge *protocol* is still verified, against a local stub, with no key).

---

## 7. Quick start

The engine is [`scripts/myelinate.py`](scripts/myelinate.py) and its store is `~/.hermes/memory/myelinated.json`.

```bash
# 1. Initialize decay and consolidate (run at session start)
python3 scripts/myelinate.py refresh

# 2. Add a memory — category matters (see the decay rates above), and pinned memories never decay
python3 scripts/myelinate.py add --content "User prefers concise replies." --category preference --protected

# 3. Strengthen a memory when it is actually used
python3 scripts/myelinate.py access <memory_id>

# 4. Retrieve context — pass the question, or you get the query-blind configuration
python3 scripts/myelinate.py recall --query "What does the user prefer?" --budget 2200

# 5. Retire a fact that has been superseded
python3 scripts/myelinate.py add --content "Deploy target is production." --supersedes <old_id>
python3 scripts/myelinate.py retire <old_id>          # or retire it directly

# 6. Inspect
python3 scripts/myelinate.py list
python3 scripts/myelinate.py stats
```

Notes that matter: `--pure` is a **global** flag, so it goes before the subcommand (`myelinate --pure recall …`), and it disables query similarity, packing and supersession — it restores the specification's behaviour and is what the "as specified" row measures. `--store PATH` or `HERMES_MEMORY_STORE` relocates the store (the explicit flag wins). `recall` also *saves* the store, so a read is a write.

It is importable, and can run entirely in memory:

```python
import time
from scripts.myelinate import MyelinatedMemory

memory = MyelinatedMemory(in_memory=True)
now = time.time()
memory.add("User prefers concise replies.", category="preference", protected=True)
memory.add("One-off: rename the temp csv.", category="ephemeral", now=now)
memory.refresh(now=now + 40 * 86400)      # 40 virtual days later
print(memory.recall(budget=2200, query="What does the user prefer?").text)
```

The agent-facing session protocol (what to call, and when) is [`SKILL.md`](SKILL.md); the mechanism walkthrough with worked numbers is [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md).

---

## 8. Reproducing the benchmark

```bash
python3 benchmarks/run_bench.py                    # offline, deterministic, free
python3 benchmarks/run_bench.py --tier staleness   # only the staleness suites
python3 benchmarks/run_bench.py --judge openai     # real LLM judge (needs OPENAI_API_KEY)
python3 benchmarks/run_bench.py --network          # add the dense-embedding arm (needs a key)
python3 benchmarks/test_llm_judge.py               # verifies the LLM judge protocol, no key needed
```

The harness replays identical event streams and virtual clocks against every arm, then scores each returned context with an LLM or with a deterministic evidence-containment oracle, **inside the timeline**, so an arm that consumes the reinforcement signal can learn as it goes. It writes [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) and `benchmarks/results/raw.json`, recording the exact command, judge, seed and Python version.

Scenarios come from four tiers: a curated suite, a seeded synthetic generator, two dedicated staleness suites (one with an explicit retire signal, one without), and the public [LoCoMo](https://github.com/snap-research/locomo) long-conversation benchmark, downloaded on demand into `benchmarks/data/` (git-ignored).

**Method notes that matter when reading the numbers**

* The tables above used the **offline oracle judge**, which rewards *surfacing* evidence rather than reasoning over it — so the task-success column is a lower bound, not a substitute for an LLM-judged run. It is labelled as not-a-language-model in the report itself. The top two arms differ by 0.010 task success (TF-IDF 0.563 vs M8 0.553), which is smaller than any plausible judge noise, so **"answers more questions correctly" is unproven** until `--judge openai` runs.
* The two staleness suites are reported separately because a supersession win is not a decay win.
* On the LoCoMo tier — a ~1,450-turn transcript against a 2,200-character budget, with turns labelled ephemeral — every arm scores near zero task success. It is kept because it is public, not because it flatters anything, and §2.3 shows it is where the headline gap lives.
* Hit rate, nDCG, chars-per-hit and task success are stable across runs. The latency columns are **not**: they come from a single run, and the reported p95 is the maximum of five queries — M8 measured 93 ms in the round-2 run and 116 ms in the current one on identical code.
* Every figure here is taken from [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md), the report produced by the run recorded at the top of that file.

---

## 9. Project layout

| Path | What it is |
| :--- | :--- |
| `scripts/myelinate.py` | the engine: score, decay, tiers, recall, packing, similarity, dedupe, supersession, persistence, CLI |
| `SKILL.md` | the agent-facing skill: the session protocol (`refresh` → `recall` → `access` → `add`) |
| `benchmarks/run_bench.py` | the runner: replays every scenario against every arm, judges in-timeline, writes the report |
| `benchmarks/engines.py` | the ten arms under test (flat/FIFO, LRU, BM25, TF-IDF, optional dense embeddings, and the myelinated family) |
| `benchmarks/common.py` | the frozen contracts: `Scenario`/`Event`/`Query`, the replay loop, budget packing |
| `benchmarks/metrics.py` | hit rate, MRR, nDCG@10, evidence precision, `stale_leak`, chars-per-hit |
| `benchmarks/stats.py` | paired statistics: Wilcoxon signed-rank (exact below n=21), bootstrap CI, Cliff's δ, Holm correction |
| `benchmarks/test_engine.py` | the engine's regression suite: 69 checks plus a known-defect registry |
| `benchmarks/tune_probe.py` | the constant-sweep harness (never writes the report) |
| `benchmarks/synthetic.py` | the seeded generators, the curated-free synthetic tiers, and the two staleness suites (built from one builder so they cannot drift) |
| `benchmarks/scenarios.py` | the hand-written curated scenarios and their ground truth |
| `benchmarks/public_locomo.py` | the public LoCoMo loader and its conversion to scenarios |
| `benchmarks/judge.py` | the deterministic oracle judge and the OpenAI-compatible LLM judge |
| `benchmarks/stub_llm.py` | a local OpenAI-protocol stub so the judge path is testable without a key |
| `benchmarks/RESULTS.md`, `benchmarks/results/raw.json` | the generated report and its raw evidence |
| `docs/PLAN.md` | the round-3 plan: measurement defects first, then attribution, then claims |
| `docs/FIX-PLAN.md` | the ordered fix plan (round 3.5): the verified defect list, D13–D18, and what to fix first |
| `docs/ROUND4-DESIGN.md` | the round-4 design: the two root causes behind the eighteen defects, the unified mechanism, and the measured decay/tier coupling |
| `docs/project-state.json` | the orchestration log: rounds, board rulings and the verification trace behind every claim on this page |
| `docs/assets/hero.svg` | the front-page diagram |
| `.github/` | CI ([`checks.yml`](.github/workflows/checks.yml)), issue forms and the pull-request template |
| `CODE_OF_CONDUCT.md`, `CHANGELOG.md` | Contributor Covenant 2.1, and what each round shipped (including the losses) |
| `docs/TESTING.md` | the testing and tuning review: check inventory, coverage gaps, proposed suite, sweep list |
| `docs/REMEDIATION.md` | what each round changed, including the negative results |
| `docs/HOW-IT-WORKS.md` | a shorter narrative walkthrough of the mechanism |

---

## 10. Limitations, security, contributing

**Limitations, stated plainly**

* The engine here is a **reference implementation** built from the specification. Where the specification was silent, values were chosen and marked `ASSUMPTION`; every open constant is listed in [§5](#5-how-it-is-tuned).
* **No LLM-judged task-success number exists yet.** `OPENAI_API_KEY` has never been set in the environment where this was developed, so the judge is implemented and its protocol tested, but no language model has scored these runs.
* **One seed.** The staleness suites are three hand-written scenarios with one query each, so those criteria are low-power: the leak can only take the values `{0, ⅓, ⅔, 1}`. Multi-seed reporting is W6.
* **The BLEU-style caveat on "zero dependencies."** The engine needs no infrastructure; using it still requires an answering model and a session hook that calls `refresh` and `access`, and the benchmark's BM25/TF-IDF baselines are hand-rolled in this repo, so they understate what a real search service would cost to run.
* **Duplicate-collapsing recall is measured on a probe corpus**, not the benchmark, and one earlier probe was mis-calibrated (a 24-word vocabulary made every memory a candidate of every other) before being re-measured on realistic text.
* Latency and ingest figures are single-run and noisy; see D2.

**Security.** The engine itself is offline: local file access only, no network, no subprocesses, standard library only. The *harness* is not: `--network` and `--judge openai` call an API, and `public_locomo.py` downloads a dataset once into `benchmarks/data/` (git-ignored). Nothing in this project sanitises memory content — it is tokenised for indexing and JSON-encoded for storage — so treat stored memory text as untrusted input to whatever model consumes it. MIT licensed; see [`SECURITY.md`](SECURITY.md) and [`LICENSE.md`](LICENSE.md).

**Contributing.** The most useful contribution right now is a measured improvement to one of the round-3 targets in [`docs/PLAN.md`](docs/PLAN.md): the harness will tell you immediately whether it worked. Start with `python3 benchmarks/test_engine.py` (it must stay green — a newly registered `KNOWN DEFECT` line is fine, a failing assertion is not), then the highest-value sweep is already scripted: `python3 benchmarks/tune_probe.py`. The engine is stdlib-only and must stay that way. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

---

## Learn more

- [Benchmark results](benchmarks/RESULTS.md) — every table, the pre-registered decision rule and the method notes
- [Round 3 plan](docs/PLAN.md) — measurement defects, attribution experiments, pre-registered acceptance criteria
- [Fix plan](docs/FIX-PLAN.md) — the ordered bug-fix list, the six defects found by the offline self-test pass, and what an API key would unblock
- [Round 4 design](docs/ROUND4-DESIGN.md) — the two root causes behind the eighteen defects, and the unified mechanism that replaces them
- [Testing and tuning review](docs/TESTING.md) — what is verified, what is not, and the sweep list
- [Remediation log](docs/REMEDIATION.md) — what each round changed, including the losses
- [Changelog](CHANGELOG.md) — the same history in release form
- [How it works](docs/HOW-IT-WORKS.md) — a shorter narrative walkthrough

`docs/PLAN.md`, `docs/TESTING.md`, `docs/FIX-PLAN.md` and `docs/ROUND4-DESIGN.md` are this project's **engineering log**, written in-house for the maintainer (they refer to analyst pods, board rulings such as BM-004, and pre-registered criteria by id). They are kept public because the reasoning behind a number is as load-bearing as the number. The user-facing documents are this README, [`SKILL.md`](SKILL.md) and [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md).
- [Hermes integration guide](SKILL.md) — the session protocol
