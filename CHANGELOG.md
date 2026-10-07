# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is a measurement-first project, so the changelog records the negative
results as well as the shipped ones. Where a number is quoted it is the one in
`benchmarks/RESULTS.md` and `benchmarks/results/raw.json` for the same commit;
nothing here is rounded up or restated more favourably than the report.

## [Unreleased]

Rounds 3 and 3.5: measurement integrity and verification. No engine behaviour
has changed yet - this section is planning, tests and defects, not features.

### Added

- `benchmarks/test_engine.py` - the engine's regression suite: **69 checks**
  covering the score, tier thresholds, rendering caps, the budget guarantee on
  both recall paths (budgets 0/50/500/2200), retired-memory exclusion, pinning,
  duplicate collapsing, persistence round-trip and store-path precedence. It uses
  a **known-defect registry**: a tracked defect is reported as
  `KNOWN DEFECT <id>` instead of failing the run, so the suite is green before
  and after a fix and a defect cannot be quietly forgotten.
- `benchmarks/tune_probe.py` - a constant-sweep harness that replays the same
  206 queries with one constant changed and never writes the committed report.
- `docs/PLAN.md` - the round-3 plan: repair the measurement (Gate 0) before
  making any engine claim.
- `docs/TESTING.md` - the testing and tuning review: check inventory, coverage
  map, ranked gaps, the tuning protocol and the ranked sweep list.
- `docs/FIX-PLAN.md` - the ordered bug-fix plan (round 3.5), with the verified
  evidence for every open defect and what the absent `OPENAI_API_KEY` blocks.

### Changed

- `README.md`, `docs/HOW-IT-WORKS.md` - corrected: decay tracks the time since a
  memory was last **used**, not its age, and the page now carries the D1 warning
  (the implemented decay path compounds; the documented per-day rule does not
  match the code).
- One constant has now been swept. `PRIOR_WEIGHT`, hand-set at 0.35, is the
  second-worst point on its own curve on the same 206 queries: at 0.00 the engine
  scores 0.893 hit rate (vs 0.825), 0.672 nDCG@10 (vs 0.606), 1265 characters per
  hit (vs 1589) and 0.683 on the LoCoMo tier (vs 0.400). The internal control
  holds - the 0.35 row reproduces the committed M8 numbers exactly. The sweep is
  **exploratory** (one seed, no hold-out, not pre-registered), so nothing
  published changed; the confirmatory criterion is frozen in `docs/PLAN.md` W1.2.

### Fixed

- Nothing shipped in this range. Four live defects are registered rather than
  repaired (`W0.1`, `W0.7`, `D13`, `D14`), because fixing `W0.1` invalidates the
  decay-dependent numbers from rounds 1-2 and needs the BM-006 protocol change.
  The suite reports each of them on every run.

### Known defects (open at this commit)

- `W0.1` - decay compounds: `refresh()` re-applies `exp(-rate x days)` while
  `last_access` never advances, so the effective exponent is `rate x d(d+1)/2`
  (day 7 measures 0.259026, the documented per-day rule predicts 0.486351).
- `W0.7` - `retire()` does not invalidate the similarity index, so every other
  memory's IDF is stale until some later add.
- `D13` - an update that is at least 0.9 Jaccard similar to a *retired* memory is
  absorbed by that entry and lost: the new text is never stored and recall
  returns nothing.
- `D14` - the same threshold against a live memory silently discards the newer
  wording.
- `D15` - any benchmark run overwrites the committed `benchmarks/RESULTS.md`, so
  a scoped run replaces the published report with a partial one.
- `D1`-`D12`, `D16`-`D18` - the full list, with evidence, is in
  `docs/FIX-PLAN.md` and `docs/PLAN.md` section 3.

## [2.0.0] - 2026-10-07

Round 2: the remediation pass. The engine gains three opt-in capabilities, each
measurable as its own ablation arm, and the report grows from 5 to 7
pre-registered criteria.

### Added

- **R1** - query similarity blended into recall ranking (`similarity=True`).
- **R4/R7** - value-per-character budget packing (`knapsack=True`), and an
  archived memory rendering a 64-character content gist instead of an opaque id
  stub.
- **R5** - supersession: `add(..., supersedes=...)` and `retire()`.
- **R2** - utility-credit reinforcement (`reinforce()`), the pre-registered hero
  arm.
- **R8** - MinHash sketch index for duplicate candidates and incremental
  consolidation, replacing the full postings scan.
- Ten ablation arms (`M0`-`M10`) over one identical event stream, so every
  engine row is attributable to a single named change.

### Changed

- Retrieval gap closed from **22.4 to 4.9** evidence-hit-rate points: 0.650
  (as specified) to 0.796 (R1) to **0.825** (R4/R7), against the best semantic
  arm's 0.874.
- Budget efficiency now wins: **1589** characters per evidence hit against a flat
  FIFO store's 1734, where the specification's behaviour lost (2019).
- Decision rule: **4 of 7** criteria passed, up from 2 of 5. The two failures
  that matter are the hero arm (criterion 2) and decay (criterion 5).
- The LoCoMo tier went from 0.100 to **0.400** hit rate after R7.
- Ingest from 12.5 s to **4.4 s** per 10,000 memories (R8); repeated runs measure
  4.4-5.4 s, and the pre-registered target of under 1 s was **missed**.

### Negative results (shipped and reported as such)

- **R2 (utility reinforcement) is net-negative**: hit rate 0.825 to 0.801 and
  task success 0.553 to 0.534. Reinforcing every memory that was in the context
  of a correct answer rewards proximity, not causation. The pre-registered hero
  arm is left in the report at its measured value rather than swapped for the
  better arm.
- **Decay is still unproven.** With an explicit retire signal the engine leaks
  0.000 of superseded facts; on the decay-only suite (the same scenarios with no
  retire signal) it still leaks **1.000**, exactly like flat memory and BM25.
  Retrieval-strength decay alone does not retire a stale fact at this horizon.
- **Query-aware recall is slower than BM25 at scale** (93 ms p95 against 59 ms at
  the time of the run), because every query rescans the store.
- **BM25 still ranks better**: nDCG@10 0.732 against the configured engine's
  0.606.

## [1.0.0] - 2026-10-07

Round 1: the reference implementation and the first benchmark.

### Added

- `scripts/myelinate.py` - the specification implemented as one stdlib-only
  file: a retrieval-strength score, Hebbian boost on access, category-modulated
  decay, tiered rendering into a fixed character budget, duplicate collapsing,
  and session-boundary consolidation. Every value the specification left open is
  marked `ASSUMPTION` in the source.
- `benchmarks/` - the offline harness: replay over a virtual clock, ten memory
  policies, an evidence-containment judge, paired statistics (Wilcoxon,
  Holm-Bonferroni, Cliff's delta, bootstrap CI) and a generated report.
- `SKILL.md` - the session protocol (refresh at session start, `access` on use,
  `recall` for context injection).

### Negative results

- The first benchmark came back **negative**: verdict **2 of 5** criteria, engine
  hit rate 0.67 against the best semantic arm's 0.87, 1867 characters per
  evidence hit against flat memory's 1718, ingest 12.5 s per 10,000 memories, and
  a stale-fact leak of 1.000. The engine as specified does not beat semantic
  retrieval, and this file records that as the starting point.
