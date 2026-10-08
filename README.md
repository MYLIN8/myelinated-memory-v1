# Myelinated Memory

> **A Hebbian retrieval-strength memory engine for LLM agents.**

[![License: Source-Available](https://img.shields.io/badge/license-Source--Available-red.svg)](LICENSE)
[![Python 3.10 | 3.11](https://img.shields.io/badge/python-3.10%20%7C%203.11-3776ab.svg)](CONTRIBUTING.md)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](SECURITY.md)
[![Checks](https://img.shields.io/badge/checks-10%20offline%20suites%20%7C%20181%20engine%20%7C%20204%20harness%20%7C%2088%20adversarial%20%7C%2071%20judge-brightgreen.svg)](#4-how-it-is-tested)
[![CI](https://github.com/MYLIN8/myelinated-memory-v1/actions/workflows/checks.yml/badge.svg)](https://github.com/MYLIN8/myelinated-memory-v1/actions/workflows/checks.yml)
[![Benchmark](https://img.shields.io/badge/benchmark-results-orange.svg)](benchmarks/RESULTS.md)

![The session-boundary pass and the recall ladder](docs/assets/hero.svg)

## What this is

**In plain English.** An AI assistant can only hold so much in front of it at once. Myelinated
Memory decides **which memories get that space, and how much of each one** — the way a brain does:
what you use often stays sharp, what you stop using fades, and a vague memory still gets a chance to
be seen at a cheaper size. Every memory carries a strength score that rises when it is used and
falls when it is ignored, and that score decides whether the memory appears in full, as a one-line
summary, or as a one-line gist.

**Technically.** It is one Python file — `scripts/myelinate.py` — with **zero dependencies**: no
vector database, no embedding API, no Docker, no service to run, and a single readable JSON store.
Each memory keeps an explicit `(strength, score_at)` pair that decays on a virtual clock, a category
that sets its decay rate, a tier that sets how much text it may occupy, and a value-per-character
packer that fills a fixed character budget with whatever is worth the most per character. Replaced
facts can be retired, duplicates collapse on write, and the whole thing is measured by a benchmark
harness that ships in the same repository.

**This is a hobby research project**, maintained by Thomas Sturgeon, and it is written the way a
hobby project should be: the data is real, the losses are published next to the wins, and the
reasoning is logged as it happened — including the ideas that turned out to be wrong. The
plain-language story is **[docs/THOUGHT-PROCESS.md](docs/THOUGHT-PROCESS.md)** (the research log —
no code, no background needed); the round-by-round engineering record is
[docs/REMEDIATION.md](docs/REMEDIATION.md), [docs/PLAN.md](docs/PLAN.md) and
[docs/FIX-PLAN.md](docs/FIX-PLAN.md).

> [!IMPORTANT]
> **The headline, before any numbers.** On this project's own benchmark the shipped engine **ties
> the best arm in the suite on finding evidence — 0.893 hit rate — while spending fewer characters
> to do it: 1279 per evidence hit against the BM25-plus-packer control's 1296**, and it beats flat
> FIFO and recency stores by a wide margin on both metrics at once (0.893 against 0.777 and 0.612;
> 1279 against 1734 and 2201). Three caveats belong next to that result, which is why this page is
> worth reading rather than skimming: a plain BM25 index still **orders** evidence better (nDCG@10
> 0.732 against 0.699) and leads the public LoCoMo tier (0.717 against 0.683); a dense embedding arm
> with one API key beats the engine decisively on that same tier (0.833 against 0.683, scoped run);
> and the decay mechanism the project is named after is **not proven to do what its name claims —
> that claim is formally retired** ([§2.3](#23-the-pre-registered-decision-rule-version-3), criterion 5).
>
> The honest position: **competitive with a semantic index at a lower context cost, clearly ahead
> of flat and recency memory, short of being the best pure ranker, and behind dense embeddings on
> paraphrase-heavy text.** The measure-then-report discipline is the point:
> [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) holds every table, including the arms that beat
> it.

**Start here** — no dependencies, no service, no build; one file and a JSON store:

```bash
python3 scripts/myelinate.py add --content "User prefers concise replies." --category preference
python3 scripts/myelinate.py recall --query "What does the user prefer?" --budget 2200
python3 benchmarks/test_engine.py                    # engine ok: 181 checks
```

All ten check suites are offline, free and run in seconds ([§4](#4-how-it-is-tested)); the full
session protocol is [§7](#7-quick-start).

### How it compares, in one table

Measured on this project's own offline suite (206 queries, 2200-character budget, offline oracle
judge). The comparison set is the arms in [`benchmarks/engines.py`](benchmarks/engines.py) — **not**
third-party memory frameworks, because no such comparison has been run — and no number here has been
scored by a language model.

| | vs flat FIFO / recency | vs BM25 / TF-IDF | vs dense embeddings |
| :--- | :--- | :--- | :--- |
| **Evidence hit rate** | **far ahead** — 0.893 against 0.777 / 0.612 | **level** — 0.893 against BM25's 0.864, the packer control's 0.893 and TF-IDF's 0.874 | **behind where measured** — dense 0.833 against the engine's 0.683 on LoCoMo (scoped run R10); not run on the full suite |
| **Characters per evidence hit** | **far ahead** — 1279 against 1734 / 2201 | **ahead** — 1279 against 1296 / 1541 | **behind where measured** — 2629 against 3213 on LoCoMo (same run) |
| **nDCG@10** *(the order of what it does return)* | ahead | **behind** — 0.699 against 0.732 / 0.729 | **behind where measured** — 0.573 against 0.316 on LoCoMo (same run) |
| **Public LoCoMo tier** | far ahead — they score 0.283 / 0.000 | **behind** — 0.683 against 0.717 / 0.600 | **behind** — dense 0.833 against 0.683 (scoped run R10) |
| **Infrastructure** | comparable — neither needs any | comparable — neither needs any | **ahead** — no vector store to run, no embedding API and no key |
| **Supersession, pinning, decay, tiers** | **ahead** — no equivalent exists in either | **ahead** — a lexical index has no store semantics at all | n/a |
| **Ingest cost** | **behind** — ~5 s per 10,000 memories against 0.010 s | behind | behind — plus one API call per memory |

The dense column is a **different run** from the rest of the table: [`docs/PLAN.md`](docs/PLAN.md)
criterion **R10**, one scoped LoCoMo run (`--tier locomo --skip-scale --network`, 60 queries, seed 0,
offline oracle judge), compared within that run. Dense was not run on the full suite, so nothing is
claimed about it there.

**The one-line read.** Use it for its zero infrastructure, its store semantics — pinning,
supersession, categories, tiers — and its budget efficiency, and because it now finds evidence level
with a BM25 index while spending less context to do it. Do not use it because it is the best pure
ranker (it is not, by 0.03 nDCG) or because the strength-and-decay mechanism its name comes from has
been proven to earn its keep (it has not).

Which is worth saying plainly, because it is the most interesting result in this repository: the
architecture's *store* won on budget efficiency and the architecture's *ranking* won once a hand-set
weight stopped diluting it, while the strength prior itself — the thing the project is named after —
measured as a **cost**, and was demoted to a tie-breaker in round 5. That is a real finding about the
design, and it is reported here rather than buried in a footnote.

### When not to use this

*   **You need the best ordering quality, full stop.** BM25 orders evidence better (nDCG@10 0.732
    against 0.699), and on paraphrase-heavy transcripts dense embeddings are far ahead of both
    ([`docs/PLAN.md`](docs/PLAN.md) R10).
*   **Your workload is paraphrase-heavy and an embedding API is acceptable.** The one tier where the
    engine trails is exactly where dense retrieval wins outright: 0.833 evidence-hit rate against
    0.683, and it wins on characters per hit too. Buy the embeddings instead.
*   **Facts change and you cannot signal it.** Supersession works with an explicit
    `retire`/`--supersedes` call; decay alone is unproven (leak 1.000 with no signal — decision rule
    criterion 5 **fails**, and the claim is formally retired).
*   **You need predictable latency figures.** Recall latency flips sign between identical runs on one
    host, and the report marks that criterion **informational**. The retrieval metrics are stable;
    the timing is not.
*   **You feed untrusted text straight into a model.** Memory text is rendered verbatim into the
    context budget — it is a prompt-injection surface ([`SECURITY.md`](SECURITY.md)). Filter
    upstream.
*   **Several processes write one store.** It is a single JSON file with an atomic rename on save and
    no locking.

---

## Contents

| Section | What it answers | Depth |
| :--- | :--- | :--- |
| [1. How it works](#1-how-it-works) | the mechanism, formula by formula, with the constants | technical |
| [2. Measured results](#2-measured-results) | the headline numbers, and where the full tables live | both |
| [3. What the numbers actually say](#3-what-the-numbers-actually-say) | the reading, including where the remaining gap lives | both |
| [4. How it is tested](#4-how-it-is-tested) | what is verified, what is not, and how to run the checks | technical |
| [5. How it is tuned](#5-how-it-is-tuned) | which constants are measured and which are guesses | technical |
| [6. Known defects](#6-known-defects) | the bugs found by review, and what they affect | technical |
| [7. Quick start](#7-quick-start) | the session protocol and the importable API | both |
| [8. Reproducing the benchmark](#8-reproducing-the-benchmark) | one command, plus the method notes | technical |
| [9. Project layout](#9-project-layout) | what every file is for | both |
| [10. Limitations, security, contributing, licence](#10-limitations-security-contributing-licence) | the fine print | both |

New here, or not technical? Read **What this is**, the boxed headline above, **§3** and the
**When not to use this** list, then jump to
[docs/THOUGHT-PROCESS.md](docs/THOUGHT-PROCESS.md) for the research log. The engineering log
([`docs/PLAN.md`](docs/PLAN.md), [`docs/FIX-PLAN.md`](docs/FIX-PLAN.md),
[`docs/TESTING.md`](docs/TESTING.md)) is written for the maintainer and assumes it.

---

## 1. How it works

Everything below is the behaviour of [`scripts/myelinate.py`](scripts/myelinate.py). Where the
original specification was silent the implementation chose a value and marked it `ASSUMPTION` in the
source, so a benchmark can argue with a stated guess instead of an unstated one; the values are
listed in [§5](#5-how-it-is-tuned). The longer narrative walkthrough, with worked numbers, is
[`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md).

**The score.** Every memory carries `score ∈ [0, 1]`. New memories start at
`INITIAL_SCORE = 0.60` — deliberately just above the Active threshold, so new information is visible
immediately and decay is what demotes it.

**Boost on use (Hebbian).** `access(id)` raises the score with diminishing returns, so a memory can
be used indefinitely without saturating:

```
score ← score + BOOST_ALPHA × (1 − score)          # BOOST_ALPHA = 0.35
```

**Decay on dormancy (Ebbinghaus × category).** Decay is **derived, never compounded** (round 4
repaired defect **D1**). A memory stores the pair `(score, score_at)` — its strength *as of* that
moment — and its strength at any later time is computed rather than accumulated:

```
strength(mem, now) = score × exp(−rate(category) × (now − score_at) / 86400)
realize(mem, now):  score ← strength(mem, now);  score_at ← now      # idempotent at a fixed `now`
```

`refresh()` — the session-boundary call — realizes each unprotected, unretired memory and reassigns
its tier, folding only the decay that has not been applied yet. Calling it hourly, daily or once a
week produces the same score curve. Rates per dormant day: `identity 0.002`, `user`/`preference`
`0.010`, `general 0.030`, `task 0.050`, `ephemeral 0.100` — a durable preference survives weeks of
silence; a one-off task fact fades in days.

**Tiers and rendering.** The score maps to a tier, and the tier decides how much text the memory may
occupy:

| Tier | Score | What recall renders |
| :--- | :---: | :--- |
| **Active** | > 0.5 | full text |
| **Latent** | 0.1 – 0.5 | a one-line summary (≤ 160 chars) |
| **Archived** | ≤ 0.1 | a short content gist (≤ 64 chars) |

The specification said the Archived tier should render an opaque *id stub*; round 2 changed that
(**R7**) because on a large store almost everything decayed to Archived and id stubs filled the
budget with text no answering model can read. The stub is still available programmatically via
`render(memory, "stub")`. Pinned memories are always treated as Active.

**Recall: ranking, packing, supersession.** `recall(budget=2200, query=None)` returns a context
string filled to a character budget. The capabilities are independent switches, because the
benchmark has to attribute any improvement to one named change:

*   **Query-aware ranking** (`similarity=True`) — a TF-IDF cosine similarity blended with the score
    as `value = similarity + PRIOR_WEIGHT × score`. **`PRIOR_WEIGHT = 0.0`** since round 5 (it was
    0.35), acting as a tie-break rather than an ordering term; with no question, recall is score-only,
    exactly as originally specified. See [§5.1](#51-one-sweep-has-been-measured).
*   **Value-per-character packing** (`knapsack=True`) — every `(memory, detail)` pair is valued at
    `value × DETAIL_VALUE[detail]` (`full 1.0`, `summary 0.55`, `gist 0.25`) and the best expected
    value per character is taken greedily, so a cheap summary can outrank an expensive full text. A
    block that would overflow is skipped, never truncated: `result.chars ≤ budget` always.
*   **Supersession** (`stale_retirement=True`) — `retire(id)`, or `add(..., supersedes=id)`, stops a
    replaced fact from surfacing. Since round 4 an update that is ≥ 0.90 similar to a live or retired
    memory is detected automatically (D13/D14): a *restatement* collapses into one entry, while a
    changed value is stored as its own memory with the old one retired.
*   **The rest of the machinery** — duplicate collapsing through a MinHash sketch index (measured:
    97.5% recall at Jaccard 0.96, no false merges on a negative control), pinning (`pin(id)` sets the
    score to 1.0, exempts the memory from decay and from crowding out), optional pruning of the
    weakest archived memories, and atomic JSON persistence with a versioned schema
    (`SCHEMA_VERSION = 3`) and a migration path. One dead path is documented rather than hidden:
    the refresh-time clustering pass (`JACCARD ≥ 0.50`) writes `mem.cluster`, which nothing reads —
    see [§6](#6-known-defects).

---

## 2. Measured results

These are not estimates — every number on this page comes from a report you can regenerate, and the
report prints the command, the judge, the seed, the Python version and the criteria version it came
from. **This section keeps the headline tables; the full set — the ablation ladder, the allocator
controls, the staleness suites, the per-tier and per-question-kind breakdowns, the 10,000-memory
scale probe, the method notes and the raw evidence — is
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md)**, regenerated from the round-5 code.

The committed run: 33 scenarios, 206 queries, a 2,200-character budget, seed 0, **offline oracle
judge** (not a language model), decision rule version 3, generated 2026-10-07.

### 2.1 Every arm, at a glance

| Memory system | Task success | Evidence hit rate | nDCG@10 | Characters per hit |
| :--- | ---: | ---: | ---: | ---: |
| No memory (control) | 0.000 | 0.000 | 0.000 | — |
| Flat / FIFO store | 0.558 | 0.777 | 0.361 | 1734 |
| Recency / LRU | 0.485 | 0.612 | 0.342 | 2201 |
| Semantic — BM25 | 0.549 | 0.864 | **0.732** | 1336 |
| Control — BM25 + engine packer (`M3k`) | 0.539 | **0.893** | **0.732** | **1296** |
| Semantic — TF-IDF cosine | **0.563** | 0.874 | 0.729 | 1541 |
| Myelinated — as specified (`M6`) | 0.534 | 0.680 | 0.443 | 1935 |
| Myelinated + packing (`M8`, round-4 configuration) | 0.558 | 0.835 | 0.689 | 1572 |
| **Myelinated + lexical ranking (`M11`, shipped)** | 0.539 | **0.893** | 0.699 | **1279** |

The engine rows are **the same engine with one capability switched on at a time**, which is what
makes each line attributable; the `M3k` row is a **control** — BM25's ranking filled by the engine's
own packer — not a baseline the engine is judged against. Declining or ambiguous rows are kept in the
report, including the arm that loses: `M10` (utility reinforcement, the pre-registered hero) at
0.830 hit rate and 1596 characters per hit. `chars per hit` is a reward-to-cost ratio (mean characters
÷ mean hit rate); lower is better.

### 2.2 The shipped configuration, on the pre-registered hold-out seeds

`M11` is `M10` with the prior weight set to zero; the criterion behind it was frozen in
[`docs/FIX-PLAN.md`](docs/FIX-PLAN.md) **F18** before the run, on hold-out seeds 3–4 (pairs are
`seed 3 / seed 4`). Full account: [`docs/ROUND5-STRATEGY.md`](docs/ROUND5-STRATEGY.md).

| Arm | Evidence hit rate | nDCG@10 *(rank order)* | Characters per hit | LoCoMo tier |
| :--- | ---: | ---: | ---: | ---: |
| **`M11` myelinated + lexical ranking** *(shipped default)* | **0.893 / 0.893** | 0.702 / 0.697 | **1276 / 1283** | 0.683 |
| `M12` myelinated + unbounded candidate ceilings | 0.830 | 0.677 / 0.672 | 1596 | 0.433 |
| `M3k` BM25 + engine packer *(previous leader)* | **0.893 / 0.893** | 0.734 / 0.731 | 1294 / 1301 | **0.717** |

The shipped engine ties the previous leader on finding evidence and beats it on characters per hit,
while still trailing it on ordering and on the public LoCoMo tier. `M12` reproduces `M10` exactly on
both seeds, so the candidate ceilings are not the cause of the ranking loss. This is a scoped hold-out
run, not the committed report; the committed row for `M11` is 0.893 / 0.699 / 1279.

**Latency is deliberately not quoted here.** It flips sign between identical runs on this host —
the report prints the numbers and marks the scale criterion **informational** rather than scoring it.

### 2.3 The pre-registered decision rule (version 3)

The rule was fixed **before** the run, and its version number is printed in the report, so a
criterion changed after a run is visible:

> The recommended configuration is *proven better* only if it wins the budget-efficiency and
> query-conditioned-retrieval criteria simultaneously. Anything else is a loss that needs the
> remediation list.

| # | Criterion | Result | Evidence |
| :--- | :--- | :---: | :--- |
| 1 | Budget efficiency: no more characters per evidence hit than flat/FIFO | **PASS** | hero 1596 vs flat 1734; shipped `M11` 1279 |
| 2 | Query-conditioned retrieval (pre-registered hero `M10`): within 5 hit-rate points of the best baseline | **PASS** | TF-IDF leads by −0.044 |
| 3 | Best measured engine configuration within 5 hit-rate points of the best baseline | **PASS** | `M11` at 0.893; TF-IDF leads by +0.019 |
| 4 | Staleness with supersession: under 20% of superseded facts still surface | **PASS** | leak 0.000 vs flat 0.000 |
| 5 | Decay alone: superseded facts fade without an explicit retire signal | **FAIL** | hero leak 1.000 — claim **formally retired**; only the bare `M6` mechanism passes (0.000 leak at 1.000 hit), and no shipped configuration does |
| 6 | Scale: recall p95 faster than BM25 at 10,000 memories | **INFORMATIONAL** | 28.6 vs 30.8 ms this run; the sign flips between identical runs |
| 7 | Significantly better end-to-end score than **both** flat baselines (Holm-adjusted p<0.05) | **FAIL** | `M1` −0.005 p=1.000; `M2` +0.068 p=0.002 |

**Verdict: 4 of 6 scored criteria passed**, one informational, none unmeasured. Criterion 6 is printed
and *not scored*, because a criterion that flips between identical runs cannot decide a verdict.
Criteria 5 and 7 are genuine failures. The pre-registered hero (`M10`) is **not** swapped for
whichever arm won: `M11` is reported alongside it as the best measured configuration, and changing the
hero is a decision-rule change that needs a version bump, not an edit.

### 2.4 Where the rest of the numbers live

| In [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) | What it adds |
| :--- | :--- |
| [Retrieval and context, per arm](benchmarks/RESULTS.md#retrieval-and-context-per-arm) | every arm, every metric, including `leak_supersession`, `leak_decay_only` and `chars_per_hit` |
| [The ablation ladder](benchmarks/RESULTS.md#ablation-ladder) | one capability added per row, so each gain is attributable |
| [Allocator controls](benchmarks/RESULTS.md#allocator-controls-is-the-win-the-store-or-the-packer) | the three controls that separate the engine's *store* from its *packer* |
| [Staleness: supersession vs decay alone](benchmarks/RESULTS.md#staleness-supersession-vs-decay-alone) | the two suites reported separately, never merged |
| [Hit rate by tier](benchmarks/RESULTS.md#hit-rate-by-tier) | curated / synthetic / staleness / LoCoMo, per arm |
| [End-to-end task success](benchmarks/RESULTS.md#end-to-end-task-success) | paired differences against the hero, with confidence intervals and Holm-adjusted p-values |
| [Where it wins and loses, by question kind](benchmarks/RESULTS.md#where-it-wins-and-loses-by-question-type) | lookup, contradiction, preference, adversarial, budget, continuity |
| [Scale: 10,000 memories over 60 virtual days](benchmarks/RESULTS.md#scale-10000-memories-over-60-virtual-days) | stored counts, ingest, refresh, recall latency — median of three passes |
| [Method notes](benchmarks/RESULTS.md#method-notes) | the caveats that decide how the numbers above may be read |
| [The raw evidence](benchmarks/results/raw.json) | per-query ids, scores and latencies for the whole run |

**Scoped runs** use the same harness on one tier and never overwrite the committed report: they
write `benchmarks/RESULTS-<tier>.md` and `benchmarks/results/raw-<tier>.json`, which are generated on
demand and git-ignored, so they are not committed here. The scoped runs quoted on this page — R10
(dense embeddings on LoCoMo) and R12 (auto-retire supersession) — are recorded with their
pre-registered criteria in [`docs/PLAN.md`](docs/PLAN.md).

---

## 3. What the numbers actually say

*   **The retrieval gap closed, and the ceiling is not built-in.** The specification's behaviour
    scores **0.680** hit rate; similarity and packing take it to **0.835**, and demoting the
    strength prior to a tie-breaker takes the shipped engine to **0.893** — level with the best arm in
    the suite, from a 19.4-point deficit at the start — at **1279** characters per evidence hit, the
    lowest of any arm. What has *not* closed is ordering quality and the public LoCoMo tier.
*   **The budget win is the allocator, not the store.** This is round 4's headline and it is a
    *negative* result for the thesis: the control `M3k` — BM25's ranking, the engine's packer, no
    engine state — beat every round-4 engine arm on hit rate, on LoCoMo and on characters per hit.
    Round 5 narrowed it to a tie on retrieval (0.893 at 1279 against 0.893 at 1296) while leaving
    LoCoMo and nDCG with the control. The strength/decay/tier machinery contributed nothing to the
    budget result on this workload; the value-per-character packing contributed all of it.
*   **The remaining gap was one hand-set number.** On the public LoCoMo tier — the only tier where the
    engine trailed — hit rate fell with every point of prior weight: **0.683** at 0.00 against 0.433
    at the old 0.35. The sweep that decided it is in [§5.1](#51-one-sweep-has-been-measured); the
    lesson is that adding a query-independent constant to a cosine lets a strong but irrelevant memory
    outrank a relevant one, so importance should decide *how much room* a memory gets, not who wins
    the argument about what the question is about.
*   **Where the gap lives is term overlap.** On curated and synthetic scenarios the shipped engine
    hits **1.000** of the evidence; almost the whole deficit came from the 60 public LoCoMo queries
    (a ~1,450-turn transcript against a 2,200-character budget), where the engine finds **0.683**
    against BM25's 0.617 and the control's 0.717. In the scoped run of [`docs/PLAN.md`](docs/PLAN.md)
    **R10**, a dense embedding arm reached **0.833** there — semantic recall closes that tier for the
    price of an API and a key.
*   **The claim the project is named after is retired, not hedged.** Measured on the current tree,
    with no explicit retire signal, the hero leaks **1.000** — and every configured engine (`M8`–`M13`)
    does, so the project no longer claims decay alone retires stale facts. The bare `M6` mechanism
    passes (leak 0.000 at hit 1.000), recorded as a lead on a three-scenario suite, not a shipped
    capability.
*   **Supersession works — and is a feature, not evidence.** With an explicit `retire`/`--supersedes`
    signal stale facts leak **0.000**, but any store can do that by deleting. What round 4 added is
    that a *correction* is stored rather than absorbed into the dead entry (D13/D14), and the
    benchmark's staleness fixtures stay deliberately below the collapse threshold, so the harness does
    not exercise it. The R12 arm pair built to separate the two mechanisms failed its own frozen
    criterion, and that negative result is kept.
*   **Ranking: BM25 is better by 0.03, and that number only became trustworthy in round 5.** Both
    nDCG columns used to be identical for every engine arm while the BM25 arms' columns differed —
    impossible when an allocator reorders context, because the harness was scoring the packing order
    as if it were the ranking order (**D8**). With the rank order recorded, the gap is real but small:
    **0.732** against **0.699** in the committed report.
*   **Reinforcement hurts slightly, and ingest is a real cost.** Learning from memories that were
    present when an answer came out right *lowered* hit rate (0.835 → 0.830) and task success
    (0.558 → 0.553) — reinforcing everything nearby rewards proximity, not causation. Ingest is ~5 s
    per 10,000 memories against flat memory's 0.010 s, and session refresh is ~3 s.

**The summary.** Worth using on its own merits: it finds evidence level with the best arm in the
suite, it is cheaper per evidence hit than any other arm, it beats flat and recency stores by a wide
margin on both metrics at once, it carries store semantics a lexical index does not have, and it runs
with no service, no vector database, no API and no key. Not worth using as the best pure ranker (BM25
orders evidence 0.03 nDCG better, and dense embeddings lead the paraphrase-heavy tier outright), and
not worth using as proof that the strength-and-decay mechanism earns its keep — that prior measured
as a *cost* and was demoted to a tie-breaker, and the decay claim still fails its criterion.

---

## 4. How it is tested

**Until this project's own review the engine had no automated checks at all** — while holding every
real defect it has since found: compounding decay, a similarity index `retire()` left stale, double
tokenisation on `add()`, and a clustering pass on the refresh hot path whose output nothing reads.
That gap is closed. Ten offline suites, plus CI on both Python versions — no key, no network,
seconds:

| Check | Command | What it verifies |
| :--- | :--- | :--- |
| **Engine** | `python3 benchmarks/test_engine.py` | **181 checks**: decay for 1–30 days with a no-op re-realise, boost from a decayed value, the update-versus-restatement path, a correction after a retire, tier thresholds, rendering caps, the budget never exceeded on both recall paths, retired memories excluded, index invalidation compared against a full rebuild, pinning, `prune_deficit`, duplicate collapsing, persistence round-trip, the version-2 store migration, store-path precedence and CLI wiring |
| Harness | `python3 benchmarks/test_harness.py` | **204 checks** pinning what measured everything except itself: metric definitions against hand-computed values, the verdict logic (unmeasured criteria report `NOT MEASURED`, informational rows stay out of the denominator, **both** flat baselines required), replay determinism, the budget invariant across every offline arm, the LoCoMo conversion, the report renderer, and the output-path guards that stop a scoped run overwriting the committed report |
| Adversarial | `python3 benchmarks/test_adversarial.py` | **88 checks** from the project's adversarial review: store/persistence (schema refusal, corrupt stores, save atomicity, retire-then-restate ids), the algebra of decay (the decay semigroup fuzzed over refresh schedules, realize idempotence), the recall/budget contract fuzzed over unicode stores at six budgets, and protocol robustness (the MCP server survives hostile JSON-RPC and keeps serving; the CLI fails cleanly) |
| LLM judge protocol | `python3 benchmarks/test_llm_judge.py` | **71 assertions** over request shape, JSON parsing, caching, the HTTP-500 path, the 429 retry path with an injected sleeper, the call budget and free-tier pacing — all against a local OpenAI-protocol stub, **no key needed** |
| Statistics | `python3 benchmarks/stats.py` | Wilcoxon, Cliff's δ, bootstrap CI determinism, Holm correction, percentiles |
| Scenario fixtures | `python3 benchmarks/synthetic.py` | unique ids, event ordering, evidence and stale ids exist and precede their query, filler large enough that the budget binds, stale/evidence Jaccard below the collapse threshold, globally unique query ids |
| Judge semantics | `python3 benchmarks/judge.py` | the oracle extracts a line and grades `contains`/`exact`; it refuses the LLM label |
| Stub protocol | `python3 benchmarks/stub_llm.py` | the stub's request→response mapping, including the forced-failure path |
| Arm registry | `python3 benchmarks/engines.py` | all 16 offline arms build and time, including the three allocator controls, `M11`/`M12` and the R12 `M13` control |
| MCP server | `python3 scripts/myelinated_mcp.py --selftest` | stdio JSON-RPC framing and all seven memory tools, in-process against a throwaway store — **no MCP client needed** |
| CI | [`.github/workflows/checks.yml`](.github/workflows/checks.yml) | every suite above plus `py_compile`, on Python 3.10 and 3.11, with no network and no secrets |

The engine suite uses a **known-defect registry**: behaviour that is wrong but tracked is registered
with its defect id and prints `KNOWN DEFECT <id> …` instead of failing, so the suite stays green
across a fix and a tracked defect cannot be quietly forgotten. **The registry is empty today**: the four it used to
carry are now real assertions that fail if the behaviour regresses, and the mechanism stays in the
file so a newly found defect is registered rather than silently ignored. The gap mattered — the decay
bug was measured, tabulated and explained across two rounds of reports before anyone read the
formula.

**Still unverified:** the R12 mechanism separation (auto-update versus near-duplicate consolidation
needs a different fixture), and any latency figure on a host other than the one that produced the
report. **Never run at all:** a *complete* model-judged pass. The judge is integrated and verified
live against two providers, but judging 206 queries is network-bound, so every task-success figure on
this page — including the README's headline — is the offline oracle's, which rewards *surfacing*
evidence rather than reasoning over it. Treat the task-success column as a lower bound.

---

## 5. How it is tuned

Most of this engine is **hand-set**, and the source says so: every value chosen where the
specification was silent is marked `ASSUMPTION`. The honest split:

| Constant | Value | How it was chosen |
| :--- | :--- | :--- |
| `SKETCH_SIZE` / `BAND_ROWS` / `MAX_POSTINGS_SCAN` | 8 / 1 / 96 | **measured** — chosen after 2-hash banding measured 62.6% recall at Jaccard 0.93 and was rejected |
| `PRIOR_WEIGHT` | **0.0** shipped (`LEGACY_PRIOR_WEIGHT` 0.35) | **measured** in round 5 — see [§5.1](#51-one-sweep-has-been-measured) |
| `DUPLICATE_JACCARD` | 0.90 | measured **off-benchmark** on a probe (97.5% collapse at Jaccard 0.96, no false merges), never swept against the benchmark |
| `RECALL_POOL` / `MAX_CANDIDATES` | 600 / 64 | reasoned, then **measured as immaterial** on these tiers: `M12` lifts all three ceilings and reproduces `M10` exactly. Never swept at the scale tier |
| `CATEGORY_DECAY_PER_DAY`, `INITIAL_SCORE`, `BOOST_ALPHA`, the tier thresholds, `SUMMARY_MAX_CHARS`/`GIST_MAX_CHARS`, `DETAIL_VALUE` | — | **guesses**, marked in the source. Sweeping them is the open work: the summary/gist caps and `DETAIL_VALUE` are next on the ranked list |
| `CLUSTER_JACCARD` | 0.50 | **dead** — nothing reads `mem.cluster` ([§6](#6-known-defects)) |

The full provenance table and the tuning protocol (one knob per arm, tune on seeds 0–4 and report on
5–9, freeze the criterion before the sweep, report the curve rather than the winner) are in
[`docs/TESTING.md`](docs/TESTING.md) §5.

### 5.1 One sweep has been measured

[`benchmarks/tune_probe.py`](benchmarks/tune_probe.py) replays the same 206 queries with one constant
changed, and never touches the committed report. **The instrument was broken until round 5**: it
unpacked the runner's two-part return value as a dictionary and raised `TypeError` on every
invocation, so the curve quoted in earlier rounds had never been produced by the tool that claims to
produce it — and the "re-run before acting on it" note was a note that could not be acted on. Fixing
the ruler was the single most valuable hour in this project.

| `PRIOR_WEIGHT` | Dev-tier hit rate | Dev-tier nDCG@10 | Public LoCoMo hit rate |
| ---: | ---: | ---: | ---: |
| **0.00** *(shipped)* | 0.979 | 0.856 | **0.683** |
| 0.10 | **1.000** | **0.859** | 0.567 |
| 0.20 | **1.000** | 0.847 | 0.517 |
| 0.35 *(round-4 value)* | **1.000** | 0.840 | 0.433 |
| 0.50 | **1.000** | 0.840 | 0.433 |
| 1.00 | **1.000** | 0.810 | 0.317 |

The direction is monotone exactly where the engine was losing. On the dev tiers the constant barely
moves anything; on the public LoCoMo tier — the one tier where it trailed — hit rate falls with every
added point of prior weight. **This is a decision, not an open question.** Following the protocol
(choose on the dev tiers, confirm on a hold-out), 0.10 is the dev-optimal point, but what shipped is
**0.0**, because the confirmatory run measured the full pre-registered criterion rather than the
sweep: on hold-out seeds 3–4, `M11` passes all four frozen thresholds of
[`docs/FIX-PLAN.md`](docs/FIX-PLAN.md) **F18** and ties the previous leader on hit rate while beating
it on characters per hit. The round-4 arms pin the legacy 0.35 so their published rows stay
reproducible, and the pin is asserted in `benchmarks/test_engine.py`.

```bash
python3 benchmarks/tune_probe.py                       # the sweep above
python3 benchmarks/tune_probe.py --tiers locomo --values 0,0.1,0.2,0.35,0.5,1.0
```

---

## 6. Known defects

Twenty defects were found by review and probing (`D1`–`D20`), each with the fix and the acceptance
criterion that closes it. **Round 4 repaired thirteen of them, round 5 repaired `D8`, `D19` and most
of `D20`**, and this page, `SECURITY.md`, `SKILL.md` and `docs/HOW-IT-WORKS.md` were realigned to the
regenerated report where earlier wording was unsupported (`D11`). The full registry with status is
[`docs/PLAN.md`](docs/PLAN.md) §3 and [`docs/FIX-PLAN.md`](docs/FIX-PLAN.md). What is still open:

| # | Defect | What it affects | Status |
| :--- | :--- | :--- | :--- |
| D10 | `SKILL.md`'s documented `recall --budget 2200` passes **no question** (query-blind), `--category` is never set, and `--pure` is a global flag, so `recall --pure` is an argument error | following the skill verbatim used to get you an unmeasured configuration | **open** — the skill's command notes are corrected; the CLI is unchanged |
| D17 | dead paths in the engine (`_cluster()` writing an unread `mem.cluster`, an unused `w_minus` argument) | refresh overhead | **open** |
| D18 | `_prune()`'s shortfall handling | the pruning guarantee | **partly fixed** — `pin()` refuses a retired memory, `refresh()` reports `prune_deficit` |
| D2 | latency is measured through a warm/cold split, a median of three passes and p50/p95/p99, but still flips sign between identical runs | every timing figure; the scale criterion is reported **informational** rather than scored | **partly fixed** — the measurement is honest now, the criterion is not scorable on one host |

---

## 7. Quick start

The engine is [`scripts/myelinate.py`](scripts/myelinate.py); its store is
`~/.hermes/memory/myelinated.json`.

```bash
# 1. Initialise decay and consolidate (run at session start)
python3 scripts/myelinate.py refresh

# 2. Add a memory — category sets the decay rate; --protected pins it against decay and crowding out
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

Notes that matter: `--pure` is a **global** flag, so it goes before the subcommand
(`myelinate --pure recall …`); it disables similarity, packing and supersession and restores the
specification's behaviour — it is what the "as specified" row measures, not a daily-use mode.
`--store PATH` or `$HERMES_MEMORY_STORE` relocates the store (the flag wins), and `recall` **saves**
the store, so a read is a write. For MCP clients (Claude Code, Claude Desktop, any MCP client) the
same engine runs as a stdio MCP server with `python3 scripts/myelinated_mcp.py --store PATH` — see
[`docs/PORTABILITY.md`](docs/PORTABILITY.md) for the tool list.

It is importable and can run entirely in memory:

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

The agent-facing session protocol (what to call, and when) is [`SKILL.md`](SKILL.md); the mechanism
walkthrough with worked numbers is [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md).

---

## 8. Reproducing the benchmark

```bash
python3 benchmarks/run_bench.py                                  # offline, deterministic, free (rewrites the committed report)
python3 benchmarks/run_bench.py --tier staleness --skip-scale     # a scoped, fast run; writes RESULTS-staleness.md instead
python3 benchmarks/run_bench.py --judge llm                       # model judge: first available key (Gemini, then OpenAI, then NVIDIA)
python3 benchmarks/run_bench.py --network                         # add the dense-embedding arm (needs a key)
python3 benchmarks/test_llm_judge.py                              # verifies the LLM judge protocol, no key needed
```

**The default judge is not a model, and a key in the environment will not change that.** `--judge
auto` and `--judge oracle` stay on the offline oracle, because this command has to remain
deterministic and free: a run must not become a network run because a key happens to be set.
`--judge llm` picks the first available model judge (Gemini → OpenAI → NVIDIA) and a pinned provider
with no key set exits 2 with a message rather than a traceback.

**Only the full default run writes the published artefacts.** `benchmarks/RESULTS.md` and
`benchmarks/results/raw.json` are the report and its evidence; a scoped run writes
`RESULTS-<tier>.md` and `results/raw-<tier>.json` instead, so a partial run can never replace what you
are reading. On a machine whose command timeout is shorter than the run, the same run can be produced
in two bounded passes — `--phase queries --state /tmp/run.json` then `--phase scale --state
/tmp/run.json` — which must agree on seed, tiers, budget, judge and arm list or the second refuses to
render; the committed report in this repository was produced that way.

**How the harness works, and the caveats that decide how to read it.** It replays identical event
streams and virtual clocks against every arm, then scores each returned context with an LLM or with
the deterministic evidence-containment oracle, *inside the timeline*, so an arm that consumes the
reinforcement signal can learn as it goes. Scenarios come from four tiers: a curated suite, a seeded
synthetic generator, two dedicated staleness suites (one with an explicit retire signal, one without)
and the public [LoCoMo](https://github.com/snap-research/locomo) long-conversation benchmark,
downloaded on demand into `benchmarks/data/` (git-ignored).

* The oracle judge rewards *surfacing* evidence rather than reasoning over it, so task success is a
  lower bound. The top two arms differ by 0.005 task success, smaller than any plausible judge noise,
  so **"answers more questions correctly" is unproven** until a full `--judge llm` run completes.
* The two staleness suites are reported separately, because a supersession win is not a decay win.
* On LoCoMo every offline arm scores near zero task success; the tier is kept because it is public,
  not because it flatters anything, and it is where the headline gap lives.
* Retrieval metrics are stable across identical runs — two runs of the same commit produced identical
  hit rate, nDCG and chars-per-hit for every arm. Latency is not: treat any difference below a factor
  of two on this machine as noise, and read the numbers from the report you reproduce rather than
  from a page like this one.
* Every figure on this page is taken from [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) as
  regenerated by round 5.1, except where a row says otherwise (the hold-out run in
  [§2.2](#22-the-shipped-configuration-on-the-pre-registered-hold-out-seeds) and the scoped R10 and
  R12 runs, which are labelled).

---

## 9. Project layout

| Path | What it is |
| :--- | :--- |
| `scripts/myelinate.py` | the engine: score, decay, tiers, recall, packing, similarity, dedupe, supersession, persistence, CLI |
| `scripts/myelinated_mcp.py` | the same engine behind a stdio MCP server (zero dependencies), with `--selftest` |
| `benchmarks/run_bench.py` | the runner: replays every scenario against every arm, judges in-timeline, writes the report |
| `benchmarks/engines.py` | the arms under test: flat/FIFO, LRU, BM25, TF-IDF, optional dense embeddings, the myelinated family, the three allocator controls, `M11`/`M12`, and R12's `M13` control |
| `benchmarks/common.py`, `benchmarks/metrics.py`, `benchmarks/stats.py` | the frozen contracts and replay loop; hit rate, MRR, nDCG@10, evidence precision, `stale_leak`, chars-per-hit; paired statistics (Wilcoxon, bootstrap CI, Cliff's δ, Holm) |
| `benchmarks/test_engine.py`, `test_harness.py`, `test_adversarial.py`, `test_llm_judge.py` | the four suites: 181 + 204 + 88 + 71 checks, all offline |
| `benchmarks/synthetic.py`, `benchmarks/scenarios.py`, `benchmarks/public_locomo.py` | the scenario generators, the curated suite and its ground truth, and the LoCoMo loader |
| `benchmarks/judge.py`, `benchmarks/stub_llm.py` | the deterministic oracle and the OpenAI-compatible LLM judge; a local stub so the judge path is testable without a key |
| `benchmarks/tune_probe.py`, `benchmarks/pool_probe.py`, `benchmarks/chart.py` | the constant sweep, the R11 candidate-pool diagnostic, and the cost-versus-hit-rate chart |
| `benchmarks/RESULTS.md`, `benchmarks/results/raw.json` | the generated report and its raw evidence — written **only** by the full default run |
| `docs/THOUGHT-PROCESS.md` | **the research log**: how the design was reasoned through, in plain language, including what turned out to be wrong |
| `docs/REMEDIATION.md`, `docs/ROUND4-DESIGN.md`, `docs/ROUND5-STRATEGY.md` | what each round changed (including the losses), the round-4 root causes, and the round-5 result with the levers still open |
| `docs/PLAN.md`, `docs/FIX-PLAN.md`, `docs/TESTING.md`, `docs/project-state.json` | the engineering log: the defect registry, the ordered fix plan, the testing and tuning review, and the orchestration record |
| `docs/HOW-IT-WORKS.md`, `docs/PORTABILITY.md`, `docs/DISTRIBUTION.md`, `docs/POST.md` | the narrative walkthrough, the three front doors (library, CLI, MCP), and the release and write-up material |
| `SKILL.md` | the agent-facing skill: the session protocol (`refresh` → `recall` → `access` → `add`) |
| `docs/assets/hero.svg` | the front-page diagram |
| `.github/` | CI ([`checks.yml`](.github/workflows/checks.yml)), issue forms and the pull-request template |
| `LICENSE` | the one licence document: *Myelinated Memory Source-Available Licence 1.0* |
| `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md` | the contribution protocol, Contributor Covenant 2.1, the security policy, and what each round shipped |

---

## 10. Limitations, security, contributing, licence

**Limitations, stated plainly**

*   The engine is a **reference implementation** built from the specification; where the specification
    was silent, values were chosen and marked `ASSUMPTION`.
*   **No LLM-judged task-success number exists yet.** Every task-success figure comes from the
    deterministic evidence-containment oracle.
*   **Very few seeds.** The staleness suites are three hand-written scenarios with one query each, so
    those criteria are low-power — the leak can only take the values `{0, ⅓, ⅔, 1}`. Round 5 held out
    seeds 3–4 rather than one seed, which is closer to the protocol but still short of the five it
    asks for.
*   **The caveat on "zero dependencies."** The engine needs no infrastructure, but using it still
    needs an answering model and a session hook that calls `refresh` and `access`, and the benchmark's
    BM25/TF-IDF baselines are hand-rolled in this repository, so they understate what a real search
    service would cost to run.
*   Duplicate-collapsing recall is measured on a probe corpus, not the benchmark. Latency and ingest
    figures are single-run and noisy (D2).
*   **A public fork cannot be prevented.** GitHub lets anyone fork a public repository regardless of
    its licence; the licence tells you what a fork may then be *used* for, and the restrictions above
    are what the copyright holder will enforce.

**Security.** The engine itself is offline: local file access only, no network, no subprocesses,
standard library only. The *harness* is not — `--network`, `--judge llm` and the pinned provider flags
call an API, and `public_locomo.py` downloads a dataset once into `benchmarks/data/` (git-ignored).
Keys are read from the environment by name only, at call time, and are never written into a report;
the default run makes no request at all. Nothing in this project sanitises memory content: treat
stored memory text as untrusted input to whatever model consumes it, and filter upstream. See
[`SECURITY.md`](SECURITY.md).

**Contributing.** The most useful contribution is a measured improvement to one of the open items,
and the harness will tell you immediately whether it worked. Start with
`python3 benchmarks/test_engine.py` — it must stay green; a newly registered `KNOWN DEFECT` line is
legitimate, a failing assertion is not. The engine is stdlib-only and must stay that way. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

**Licence.** Source-available, not open source: the complete and only terms are in
[`LICENSE`](LICENSE) — the *Myelinated Memory Source-Available Licence 1.0*, © 2026 Thomas Sturgeon.
Personal, educational, research and evaluation use is permitted; commercial use, redistribution,
hosting and publishing a modified version are restricted, and a contribution comes with an inbound
grant so a pull request can be merged at all. That file supersedes every earlier statement about
licensing in this repository.

---

## Learn more

* [The research log](docs/THOUGHT-PROCESS.md) — the design reasoning in plain language, including the
  findings that contradicted the design
* [What each round changed](docs/REMEDIATION.md) — the running record, including the negative results
* [Benchmark results](benchmarks/RESULTS.md) — every table, the pre-registered decision rule and the
  method notes
* [How it works](docs/HOW-IT-WORKS.md) — the shorter narrative walkthrough of the mechanism
* [Round 5 strategy](docs/ROUND5-STRATEGY.md) — what the deficit actually was, the criterion the
  shipped arm passes, and what is still open
* [Round 4 design](docs/ROUND4-DESIGN.md) — the root causes behind the defects, and the unified
  mechanism that replaces them
* [Testing and tuning review](docs/TESTING.md) — what is verified, what is not, and the sweep list
* [The plan](docs/PLAN.md) and the [fix plan](docs/FIX-PLAN.md) — the defect registry and the ordered
  bug-fix list
* [Changelog](CHANGELOG.md) — the same history in release form
* [Portability](docs/PORTABILITY.md) — the library API, the MCP server, and a LangChain-style retriever
* [Session protocol](SKILL.md) — what an agent should call, and when

`docs/PLAN.md`, `docs/FIX-PLAN.md`, `docs/TESTING.md` and `docs/ROUND4-DESIGN.md` are this project's
**engineering log**, written in-house for the maintainer: they refer to analyst pods, board rulings
such as BM-004, and pre-registered criteria by id. They are kept public because the reasoning behind a
number is as load-bearing as the number. The user-facing documents are this README,
[`SKILL.md`](SKILL.md), [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) and
[`docs/THOUGHT-PROCESS.md`](docs/THOUGHT-PROCESS.md), the last of which assumes no technical
background at all.
