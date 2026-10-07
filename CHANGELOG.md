# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is a measurement-first project, so the changelog records the negative
results as well as the shipped ones. Where a number is quoted it is the one in
`benchmarks/RESULTS.md` and `benchmarks/results/raw.json` for the same commit;
nothing here is rounded up or restated more favourably than the report.

## [4.0.0] - 2026-10-07

Round 4: the defects repaired as one mechanism, the report guarded, and a model
judge. The engine, the harness and the store schema all change, which is why this
is a major version: an existing store is migrated on load.

### Fixed

- **D1 - decay no longer compounds.** A memory stores the score *and* the
timestamp it was realised at (`score_at`), and its strength is derived from that
pair, so realising it twice at the same timestamp is a no-op and a session
`refresh()` folds only the decay not yet applied. Refreshing hourly, daily or
weekly now gives the same curve, which is the per-day `exp(-rate x d)` rule the
docs always described.
- **D9 - `retire()` and `pin()` invalidate the similarity index.** Every mutator
goes through one `_touch()` funnel that marks the memory dirty and invalidates
the cached TF-IDF state, so retiring a memory no longer leaves every other
memory's IDF stale until some later add.
- **D13/D14 - an update is no longer absorbed and lost.** Above the 0.90 Jaccard
threshold the engine separates a *restatement* (identical token set - punctuation
and whitespace differences still collapse) from an *update* (different token
set). An update is stored as a new memory and the superseded entry is retired
with `superseded_by` set, against a live or a retired candidate alike. Gated by a
new `auto_supersede=True` flag so an ablation arm can attribute it.
- **D18 - `pin()` refuses a retired memory** rather than holding it at strength
1.0 as a hidden entry.
- **D15 - only the full default run writes the published artefacts.** A scoped
run (`--tier`, `--skip-scale`) writes `RESULTS-<tier>.md` and `raw-<tier>.json`;
`benchmarks/RESULTS.md` and `results/raw.json` are replaced by the full default
run alone. The raw evidence had the same data-loss bug one file over.
- **D5/D16 - an unjudged query is no longer a wrong answer.** Every record
carries an explicit `judge_state` (`judged`, `skipped`, `errored`), the report
prints the judged/skipped/errored ledger, and `task_success` averages over
judged queries only. A cost-capped or failed call used to be written as `0.0`
and averaged in as a wrong answer.
- **D4 - a criterion the run never measured reports `NOT MEASURED`** and leaves
the denominator, instead of scoring a phantom `FAIL`.
- **D6 - the significance criterion requires BOTH flat baselines.** It passed
when *either* arm lost, while the other could beat the hero.
- **D8 - ranking metrics are scored on the rank order**, with the packed order
reported beside them (`ndcg@10` against `ndcg@10_packed`). Scoring the packed
order read the allocator's work as ranking skill.
- **D3 - the two staleness suites are never merged.** The mixed leak column is
gone from the headline table; the per-suite split has its own section.
- **D2 - a cold start is no longer folded into a warm percentile.** The mean cold
recall is reported separately as `cold_ms`, warm percentiles are p50/p95/p99,
and the scale section reports the median of three timed passes.
- **D12** - the literal `%%` artifact is gone from the generated criterion text.

### Added

- **Allocator controls `M3t`, `M3p`, `M3k`** - BM25's ranking with the budget
filled three further ways (truncate each block into the remaining budget; the
engine's tier ladder by rank; the engine's value-per-character packer). They
share the ranking and vary only the allocation, which is what decides whether
the engine's budget win belongs to its *store* or its *allocator*. Thirteen
offline arms now.
- **`GeminiJudge`** in `benchmarks/judge.py`, over Google's OpenAI-compatible
endpoint: key names `GEMINI_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, default
model `gemini-3.8-flash`, a default pace of **4.0 s per request** for a
free-tier key, retries with exponential backoff plus jitter on 429/502/503/504
honouring `Retry-After` (capped at 60 s), a hard `JUDGE_MAX_CALLS` budget
(default 400) that raises rather than scoring, and `JUDGE_MIN_INTERVAL`,
`JUDGE_MAX_RETRIES`, `GEMINI_MODEL`, `GEMINI_BASE_URL` overrides. Counters for
calls, retries, cache hits and seconds waited go into the report.
- `--judge llm` (the first available model judge, Gemini first) beside
`--judge gemini` and `--judge openai`. **`auto` and `oracle` stay the offline
deterministic judge**: a key sitting in the environment must never turn the
canonical, reproducible command into a network run.
- A one-shot `429` (with `Retry-After`) in `benchmarks/stub_llm.py`, so the
limiter and the backoff are tested without a key and without a request.
- The four repaired engine defects are now real assertions rather than registry
entries: `benchmarks/test_engine.py` is **139 checks** and reports **no**
`KNOWN DEFECT` line, and `benchmarks/test_llm_judge.py` is **60 assertions**.

### Changed

- **Decision rule version 3**, three repairs of the same class: an unmeasured
criterion reports `NOT MEASURED`; the scale latency criterion reports
`INFORMATIONAL` - printed, not scored - because its *sign* is not reproducible
between identical runs; and the either-flat-arm test and the merged leak column
are gone. The rule is still frozen before the run and its version is printed in
the report.
- Store `SCHEMA_VERSION` is **3**. `load()` migrates a v2 store by setting
`score_at` from `last_access` (else `created`), because a missing timestamp would
start decay at the epoch and archive everything.
- `refresh()` reports a `prune_deficit` key; existing keys are unchanged.
- One constant that was swept in round 3 now reads differently against the fixed
decay, and the round-3 `PRIOR_WEIGHT` row (0.825 hit rate, 0.606 nDCG, 1589
characters per hit) is that round's internal control, not a current figure.

### Measured results (offline oracle judge, full default run)

- Verdict: **4 of 6 scored criteria passed**, 1 informational row, 0 not
measured. Passing: budget efficiency (the hero arm spends **1596** characters
per evidence hit against flat FIFO's 1734); query-conditioned retrieval within 5
hit-rate points of the best baseline (TF-IDF leads by 0.044); the best measured
engine configuration within 5 points (M8 at 0.835, TF-IDF leads by 0.039); and
staleness with supersession (leak 0.000).
- **Failing: decay alone.** The hero arm still leaks **1.000** of superseded
facts when no retire signal is given. The fixed decay did move that column -
M6 (pure) and M2 (LRU) now leak 0.000 - but not the hero, so the decay claim is
still **not** established at this horizon.
- **Failing: end-to-end significance.** Against *both* flat baselines the hero
has no significant end-to-end win (M1 -0.005, `p=1.000`; M2 +0.068, `p=0.001`),
which the corrected criterion now reports honestly instead of crediting the
weaker comparison.
- **Informational, deliberately not scored: scale latency.** Two identical runs
of the same commit on this host measured 121.9 ms against 101.3 ms, then 108.1 ms
against 126.4 ms, with per-pass spreads up to 5x, so the criterion's sign is not
reproducible. The numbers are printed, the per-pass values stay in `raw.json`,
and they are counted neither way.
- Engine hit rates after the fixes: M6 0.650 to **0.680**, M7 0.796 to **0.816**,
M8 0.825 to **0.835**, M10 0.801 to **0.830**; characters per evidence hit M8
1589 to **1572**, M6 2019 to **1935**.

### Attribution result (a finding, not a win)

`M3k` - **BM25's ranking with the engine's packer** - now leads the whole suite
on hit rate: **0.893** against the best engine arm's (M8) 0.835, at **1296**
characters per hit against 1572, and **0.717** on the LoCoMo tier against 0.433.
The engine's budget win is therefore attributable to its **allocator**, not its
**store**: the strength, decay and tier machinery is not what earned it. It is
reported as a finding, and the control is not swapped in as the hero.

### Not yet established

- **No LLM-judged task success.** The judge is implemented, its protocol is
tested against a local stub, and it was verified live against the real endpoint -
two calls answered and graded, and a real `429` produced nine backoff retries and
then a `JudgeError` the harness counted as `errored` rather than scoring it as a
wrong answer. But no full run has been judged by a language model: the free-tier
quota was exhausted during that verification. Every task-success figure above is
the offline evidence-containment oracle, which rewards surfacing evidence rather
than reasoning over it.
- **One seed**, and the scale figures remain the noisiest numbers on the page.

## [3.0.0] - 2026-10-07

Rounds 3 and 3.5: measurement integrity and verification. No engine behaviour
changed in that range - it was planning, tests and defects, not features.

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

All four of the engine defects below, and `D15`, were repaired in
[4.0.0](#400---2026-10-07); the list is kept as written for that commit.

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
