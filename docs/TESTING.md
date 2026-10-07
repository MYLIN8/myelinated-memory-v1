# Testing and Tuning Review (round 3)

A second review pass, this time on **what verifies the code** and **what the
constants are**. Produced from reading every module in the repo; no code was
changed by the review itself.

**Headline findings.**

1. **The engine has no automated checks at all.** `scripts/myelinate.py` ends at
   `sys.exit(main())`. Every real defect found in this project — compounding
   decay, the similarity index not invalidated by `retire()`, double
   tokenisation on `add()`, a write-only cluster pass on the refresh hot path —
   lives in the one file nothing tests.
2. **The metric definitions are untested.** `benchmarks/metrics.py` has no
   `__main__`; the numbers that decide the verdict (hit rate, nDCG, MRR,
   `stale_leak`, `chars_per_hit`) have never been checked against a
   hand-computed value.
3. **The decision rule is untested**, and two of its bugs are logic bugs a
   20-line test would have caught (a criterion that passes when *either* flat
   arm is beaten, and a criterion scored as FAIL when the run never measured it).
4. **Almost nothing has been tuned.** Of ~20 engine constants, three families
   were chosen by measurement (`SKETCH_SIZE`, `BAND_ROWS`, `MAX_POSTINGS_SCAN`);
   everything else is hand-set and marked `ASSUMPTION`. `PRIOR_WEIGHT` is the
   one whose value visibly degrades ranking.

---

## 1. Inventory: what is checked today

| Module | Self-check | What it asserts |
| :--- | :--- | :--- |
| `scripts/myelinate.py` | **none** | the CLI is the only entry point; no assertions anywhere in the file |
| `benchmarks/common.py` | **none** | `pack_blocks`, `replay`, `downgrade`, `jaccard`, `day` are unverified |
| `benchmarks/metrics.py` | **none** | every metric definition is unverified |
| `benchmarks/run_bench.py` | **none** | `verdict()`, `pairwise()`, the report renderer and the judge-limit path are unverified |
| `benchmarks/stats.py` | **yes**, ~12 asserts | Wilcoxon identical samples → `(0.0, 1.0)`; a clear shift → `p < 0.05`; `cliffs_delta` sign; bootstrap CI contains the mean and is seed-deterministic; Holm rejects/passes; `paired_summary` significance, and non-significance at n = 3; `format_p`; `percentile` |
| `benchmarks/engines.py` | **yes**, 2 asserts | 10 offline arms are built; each has `recall_ms` |
| `benchmarks/judge.py` | **yes**, 7 asserts | oracle extracts a line from a context; `contains` and `exact` grading; empty context → empty answer; `make_judge("oracle")` mode; `describe()` refuses the LLM label |
| `benchmarks/stub_llm.py` | **yes**, 5 asserts | the stub's request→response mapping: answering, forced 0 score, HTTP 500 |
| `benchmarks/test_llm_judge.py` | **yes**, 22 assertions | the OpenAI-protocol path end to end against the stub: request shape, parsing, cache, HTTP-500 error, judge selection, descriptions |
| `benchmarks/test_engine.py` | **yes**, 69 checks | the engine: bounded diminishing boost, pinned memories, tier thresholds, rendering caps, the budget guarantee on both recall paths, retired-memory exclusion, duplicate collapsing, persistence and store-path precedence — plus a `known_defect()` registry that reports tracked defects instead of failing |
| `benchmarks/synthetic.py` | **yes**, a validator over every scenario | unique ids and event ordering; evidence/stale exist and precede the query; the staleness suites retire (or provably do not retire) their stale ids; filler > 200 so the budget binds; stale/evidence Jaccard < 0.9 so collapsing cannot be mistaken for staleness handling; **globally unique query ids** (a collision would silently corrupt paired statistics) |
| `benchmarks/public_locomo.py` | prints counts, **asserts nothing** | the LoCoMo conversion that defines that tier's ground truth is unverified |

Run them all:

```bash
python3 benchmarks/stats.py            # stats ok
python3 benchmarks/engines.py          # engines ok
python3 benchmarks/judge.py            # judge ok
python3 benchmarks/stub_llm.py         # stub ok
python3 benchmarks/synthetic.py        # synthetic ok: 14 scenarios, 33 queries, ...
python3 benchmarks/test_llm_judge.py   # llm judge ok: 22 assertions
python3 benchmarks/run_bench.py        # writes RESULTS.md + results/raw.json
```

So roughly **48 assertions, all of them about the harness**. The best check in
the repo is `synthetic.py`'s validator, and it prevents a class of failure
(query-id collisions) that would have quietly merged two different questions in
the paired statistics. Nothing in that list touches the engine.

---

## 2. Coverage map

| Behaviour | Checked? | By what |
| :--- | :--- | :--- |
| Decay over time | **no** | — (this is how the compounding bug survived two rounds) |
| Tier transitions (active → latent → archived) | **no** | — |
| Rendering per tier (full / summary / gist) | **no** | — (the R7 change is only checked by eyeballing the LoCoMo tier) |
| Protected / pinned memories | **no** | — |
| Duplicate collapsing on write and on refresh | **partly** | a one-off probe in the round-2 session, not committed; `synthetic.py` only asserts the *fixture* stays under the threshold |
| Session refresh / consolidation | **no** | — |
| Supersession (`retire`, `add(supersedes=…)`) | **no** | — (`synthetic.py` validates the fixtures, not the engine) |
| Similarity index invalidation | **no** | — (the `retire()` bug is live) |
| Budget enforcement (never exceed `--budget`) | **no** | — |
| Persistence round-trip, schema version | **no** | — (the schema field is written and never read) |
| Store path resolution (`--store` > env > default) | **no** | — |
| CLI contract (subcommands, exit codes, `--pure`) | **no** | — |
| Metric definitions (hit, nDCG, MRR, `stale_leak`, `chars_per_hit`) | **no** | — |
| Statistics (Wilcoxon, Holm, bootstrap, Cliff's δ) | **yes** | `stats.py` `__main__` |
| Replay determinism (same seed → same records) | **no** | — |
| Judge protocol (LLM path) | **yes** | `test_llm_judge.py` against the local stub |
| Judge semantics (oracle false positives) | **partly** | `judge.py` asserts the *generous* rule on purpose — it grades the retrieved context, so a gold of `7 May 2023` passes on a context containing `2023` |
| Scenario fixture integrity | **yes** | `synthetic.py` |
| Decision rule / verdict | **no** | — |
| Report assembly and rendering | **no** | — |
| LoCoMo conversion | **no** | — |

---

## 3. Gaps ranked by risk

1. **The engine is untested (critical).** Failure mode: a wrong arithmetic path
   produces plausible numbers that get published. That is exactly what
   happened — compounding decay inflated dormancy by `d(d+1)/2` and was
   measured, tabulated and explained in the report twice before anyone read the
   formula. A 15-line test would have failed on day one. Same for `retire()` not
   setting `_sim_dirty`, which makes every other memory's IDF stale for the rest
   of the session and is exercised by the M9/M10 arms.
2. **The metric definitions are untested (high).** Failure mode: the verdict is
   computed with a formula that is not the one its label promises.
   `chars_per_hit` is `mean(chars) / mean(hit_rate)` — a reward-to-cost ratio
   that improves if hit rate rises with no change in budget behaviour — and
   nothing in the repo pins that down. `ndcg` builds its ideal from
   `len(evidence)` gains only, so a query with more evidence than slots is
   normalised oddly; nothing checks it against a hand-computed value.
3. **The decision rule is untested (high).** Failure mode: a criterion reports a
   PASS or a FAIL the data does not support. Both bugs found in the round-3
   review are pure logic, testable with a dictionary of fake summaries and no
   engine at all: the significance criterion passes if *either* flat arm is
   beaten (M1 flat/FIFO beat the hero 0.558 vs 0.534 and was not consulted), and
   a scoped run scores criteria it never measured (`--tier staleness` prints
   `3/7`).
4. **`replay` and the budget invariant are untested (medium).** Replay is the
   foundation of every number: if it is not deterministic, nothing is
   reproducible; if `pack_blocks` can exceed the budget, every arm comparison is
   invalid. Neither is asserted.
5. **LoCoMo conversion is untested (medium).** The LoCoMo tier's evidence ids
   come from a conversion nothing verifies; a wrong mapping silently changes
   that tier's ground truth.
6. **`stats.py`'s asymptotic path is untested (low–medium).** Its own tests stay
   in the exact-test regime (n ≤ 20). The normal-approximation branch *with tie
   correction* — the one the 206-query comparisons actually use — has no test.
   `_exact_two_sided_p(ranks, w_plus, w_minus)` also takes `w_minus` and never
   reads it.
7. **CLI contract untested (low–medium).** `--pure` is global, so
   `recall --pure` is an argparse error while `--pure recall` works; exit codes
   for unknown ids are unverified.

---

## 4. Proposed regression suite

Plain Python, no pytest, sibling imports — the same style as
`benchmarks/test_llm_judge.py`, so each file runs as a script.

**Status: the engine suite is implemented and green.**
`benchmarks/test_engine.py` landed with **69 checks** and is listed in §1. The
metric, verdict and harness suites below are still W0.9–W0.10.

Two entries in the engine list below describe behaviour that is currently
**wrong**. Rather than commit assertions that fail from the start (a red test
suite is ignored within a week), the suite registers them with
`known_defect(tracking, description, present)`:

```
engine ok: 69 checks
  KNOWN DEFECT W0.1 decay compounds (rate x d(d+1)/2 instead of rate x d)
  KNOWN DEFECT W0.7 retire() does not invalidate the similarity index
```

The registration asserts each defect's **signature**, not the wrong value: if
the code changes for an unrelated reason the registry notices, and on the day
the fix lands the line flips to `RESOLVED … remove it from this registry and
assert the fix instead`. So the suite is green before and after every fix, and a
tracked defect cannot be silently forgotten — which is how the compounding decay
survived two rounds of reports.

### `benchmarks/test_engine.py`

```python
from myelinate import MyelinatedMemory, INITIAL_SCORE, CATEGORY_DECAY_PER_DAY, GIST_MAX_CHARS
# 1. decay is per-day, not compounded          <-- FAILS TODAY
m = MyelinatedMemory(in_memory=True); t0 = 1700000000.0
m.add("A fact worth remembering about deployment.", now=t0)
mid = list(m.memories)[0]
for d in range(1, 31):
    m.refresh(now=t0 + d * 86400)
    want = INITIAL_SCORE * math.exp(-CATEGORY_DECAY_PER_DAY["general"] * d)
    assert abs(m.memories[mid].score - want) < 1e-9, (d, m.memories[mid].score, want)
# 2. boost is bounded, diminishing, and never exceeds 1
# 3. protected memory stays at 1.0 / tier "active" after 10,000 dormant days
# 4. tier thresholds: 0.9 -> active, 0.3 -> latent, 0.05 -> archived
# 5. rendering: active -> full; latent -> <= SUMMARY_MAX_CHARS; archived -> <= GIST_MAX_CHARS
#    and no rendered block is ever the bare "[id] (archived)" stub
# 6. budget invariant, both recall paths, with and without a query:
#       result.chars <= budget  for budget in (0, 50, 500, 2200)
# 7. retired memory never appears in used_ids; result.retired_excluded == 1
# 8. retire() invalidates the similarity index            <-- FAILS TODAY
#       before = engine.similarity_scores("deploy target")
#       engine.retire(old_id)
#       assert old_id not in engine.similarity_scores("deploy target")
#       assert scores == MyelinatedMemory(...).load(fresh).similarity_scores("deploy target")
# 9. duplicate collapsing: two memories at Jaccard >= 0.9 collapse to one on refresh,
#    and the survivor keeps the higher score
# 10. supersession: add(..., supersedes=old) retires old and records superseded_by
# 11. persistence: save -> load in a new engine -> identical ids/scores/tiers;
#     a field the class does not know is ignored, not fatal
# 12. store precedence: explicit path > HERMES_MEMORY_STORE > default;
#     in_memory=True never writes a file
```

### `benchmarks/test_metrics.py`

```python
# hand-computed fixtures, asserted exactly
#   hit:         evidence in used -> 1.0, else 0.0
#   reciprocal_rank: position 1 -> 1.0, position 2 -> 0.5, absent -> 0.0
#   ndcg:        perfect 2-of-2 -> 1.0; evidence last -> the hand-computed value
#   stale_leak:  1 stale surfaced -> 1.0; suppressed -> 0.0; no stale_ids -> skipped, not zero
#   chars_per_hit: assert it IS mean(chars)/mean(hit_rate) and that raising hit rate
#                  alone lowers it -- i.e. pin the definition, because the label
#                  "characters per hit" describes something else
#   summarize:   a record with no evidence_ids is skipped, not counted as a miss
```

### `benchmarks/test_verdict.py`

```python
# fake summaries only - no engine needed
# 1. hero beats M2 but loses to M1 flat/FIFO  -> significance criterion must NOT pass  <-- FAILS TODAY
# 2. scale rows empty (--skip-scale / tier run) -> latency criterion is NOT MEASURED
#    and is excluded from the denominator            <-- FAILS TODAY
# 3. a record with judge_skipped=True must not contribute to task_success <-- FAILS TODAY
# 4. the hero is never swapped when another arm has a higher hit rate (BM-004 guard)
```

### `benchmarks/test_harness.py`

```python
# 1. replay determinism: same scenario + same arm twice -> identical query_ids, used_ids, chars
# 2. every one of the 10 arms respects the budget on a fixed scenario
# 3. a retire event on the query's own day is applied before the recall
# 4. downgrade() (currently called from nowhere) returns <= limit chars and is idempotent
```

Which of these would have caught a shipped defect: **#1** (compounding decay),
**#8** (stale similarity index), **#6** (budget), **test_verdict #1–#3** (the
decision-rule bugs). The existing suite would have failed none of them — it
never instantiates the engine and never calls `verdict()`.

---

## 5. Fine tuning: what is tuned, and what is not

### The constants and their provenance

| Constant | Value | Provenance |
| :--- | :--- | :--- |
| `CATEGORY_DECAY_PER_DAY` | identity .002, user/preference .010, general .030, task .050, ephemeral .100 | **hand-set** (`ASSUMPTION`); never swept — and currently applied through the compounding bug, so sweeping them now would fit the bug |
| `INITIAL_SCORE` | 0.60 | hand-set, just above `ACTIVE_THRESHOLD` so new memories are visible |
| `BOOST_ALPHA` | 0.35 | hand-set; diminishing returns via `s + α(1−s)` |
| `ACTIVE_THRESHOLD` / `LATENT_THRESHOLD` | 0.5 / 0.1 | hand-set; these decide the tier mix, and therefore how much of each memory reaches the budget |
| `DUPLICATE_JACCARD` | 0.9 | **measured only off-benchmark**: 97.5% collapse at Jaccard 0.96 on a probe, 0 false merges on a 500-memory negative control. Never swept against the benchmark corpus |
| `CLUSTER_JACCARD` | 0.5 | **dead**: `_cluster()` writes `mem.cluster`, and nothing reads it |
| `SUMMARY_MAX_CHARS` / `GIST_MAX_CHARS` | 160 / 64 | hand-set; jointly decide how many memories fit a 2,200-char budget, so they move hit rate and `chars_per_hit` directly |
| `SKETCH_SIZE` / `BAND_ROWS` / `MAX_POSTINGS_SCAN` | 8 / 1 / 96 | **the only measured values in the engine**: bottom-8 single-hash banding chosen after `r=2` banding measured 62.6% recall at Jaccard 0.93 and was rejected |
| `MAX_CANDIDATES` | 64 | hand-set cap on comparisons per memory |
| `RECALL_POOL` | 600 | reasoned (the budget fills long before the pool empties), never swept — probably slack at a 2,200-char budget |
| `DETAIL_VALUE` | full 1.0, summary 0.55, gist 0.25 | hand-set; the packer's entire preference order rests on these three numbers |
| `PRIOR_WEIGHT` | 0.35 | hand-set. **The most consequential value in the file**: it is why the same cosine ranker scores nDCG 0.729 as a baseline (M4) but 0.690 with the prior blended in (M7) and 0.606 once the packer reorders (M8) |
| `PROTECTED_PRIOR` | 1e6 | reasoned (effectively infinite) |
| Workload: budget 2200, 60 days, 10 000 memories, filler rates, scenario counts | — | the workload definition itself; changing any of it redefines the benchmark and invalidates comparisons |

### Protocol for tuning (pre-registered)

1. **Fix W0.1 first.** Tuning decay rates against a compounding implementation
   fits the bug rather than the model.
2. **One knob per arm.** A tuned value ships as its own labelled arm (M11, M12…),
   never folded into M8 — the BM-002 attribution rule.
3. **Hold out seeds.** Tune on seeds 0–4, report on 5–9. Today there is **one**
   seed, so no result here is out-of-sample.
4. **Freeze the criterion before the sweep.** No picking the winning
   configuration after seeing the numbers (BM-004).
5. **Report the curve, not the winner.** These are pseudo-parameters with no
   physical meaning; the sensitivity is the finding.
6. **Tuning cannot repair an attribution problem.** If the control arm (BM25
   ranked, packed by the engine's allocator) matches M8, then the packer did the
   work and tuning `PRIOR_WEIGHT` is fitting the loss surface of a ranker that
   contributes nothing.

### Sweeps worth running, ranked by expected information

| # | Sweep | Moves | Why it is first-order |
| :--- | :--- | :--- | :--- |
| 1 | `PRIOR_WEIGHT` ∈ {0, 0.1, 0.2, 0.35, 0.5, 1.0} | nDCG@10, hit rate | Directly tests the claim "retrieval strength helps ranking". If nDCG climbs back toward 0.72 as it → 0, the prior is a *cost* to ranking and a benefit only to allocation |
| 2 | `DETAIL_VALUE` + `GIST_MAX_CHARS` + `SUMMARY_MAX_CHARS` together | chars/hit, hit rate | The allocation level the engine actually wins on; must be swept together because they trade off against each other |
| 3 | `ACTIVE_THRESHOLD` / `LATENT_THRESHOLD` | tier mix → full-text share | Decides how much content each memory may occupy, which is the same trade as (2) |
| 4 | `DUPLICATE_JACCARD` ∈ {0.80, 0.85, 0.90, 0.95} | merge count, hit rate | Currently justified by an off-benchmark probe; the benchmark can measure it directly |
| 5 | `RECALL_POOL` ∈ {100, 300, 600, 2000} | hit rate, p50/p95 | If 100 ≈ 600 the pool never binds and the constant is slack; if it binds, it is also the latency ceiling |
| 6 | Decay rates + `INITIAL_SCORE` (after W0.1) | tier mix, decay-only leak | The only sweep that could move the decay claim — and the one most contaminated by the arithmetic bug today |
| 7 | `MAX_POSTINGS_SCAN` / `MAX_CANDIDATES` | ingest seconds, dedupe recall | The ingest/latency vs dedupe-recall frontier; measured against the benchmark corpus rather than a probe |

### Sweep 1, measured

`benchmarks/tune_probe.py` replays the same 206 queries (all four tiers, seed 0,
2,200-char budget) with the recommended configuration and one constant changed;
it never writes `RESULTS.md`.

| `PRIOR_WEIGHT` | Task success | Hit rate | nDCG@10 | Chars/hit | Curated | Synthetic | Staleness | LoCoMo |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **0.00** | 0.539 | 0.893 | **0.672** | **1265** | 0.973 | 1.000 | 1.000 | **0.683** |
| 0.10 | **0.553** | **0.898** | 0.653 | 1460 | 1.000 | 1.000 | 1.000 | 0.650 |
| 0.20 | 0.553 | 0.854 | 0.626 | 1535 | 1.000 | 1.000 | 1.000 | 0.500 |
| **0.35** *(committed)* | 0.553 | 0.825 | 0.606 | 1589 | 1.000 | 1.000 | 1.000 | 0.400 |
| 0.50 | 0.553 | 0.811 | 0.593 | 1617 | 1.000 | 1.000 | 1.000 | 0.350 |
| 1.00 | 0.539 | 0.738 | 0.569 | 1776 | 0.964 | 1.000 | 1.000 | 0.167 |

**The internal control holds**: the 0.35 row reproduces M8's committed numbers
exactly (0.825 / 0.606 / 1589 / LoCoMo 0.400), so the probe is measuring the same
system the report measures. **And the committed value is the second-worst point
on the curve.** At 0.00 the same engine beats the best semantic arm on hit rate
(0.893 vs 0.874), beats BM25 on chars/hit (1265 vs 1336) and on the LoCoMo tier
(0.683 vs 0.617), and closes most of the nDCG deficit (0.672 vs BM25's 0.732).
The single counter-example is judged task success (0.539 vs 0.553), a 0.014 gap
inside the oracle's noise floor.

Interpretation: the prior is not a ranking signal here, it is a *cost* to
ranking — it was diluting a cosine scorer that is competitive by itself. That is
the myelination thesis being demoted from "better retriever" to "allocation and
consolidation policy", which is exactly the branch the plan's kill criteria
describe.

**Why this does not change anything yet:** one seed, swept on the report's own
206 queries (no hold-out), and not pre-registered. Acting on it now would be
selecting a configuration after seeing the numbers. The confirmatory run
(hold-out seeds, criterion frozen in advance) is W1.2 + W6.

**What was measured before this sweep:** nothing in the table above. Two of the
three values that *are* measured were measured on hand-built probe corpora, and
one of them (the dedupe probe) was later found to have been mis-calibrated once
already — a 24-word vocabulary made every memory a candidate of every other,
producing a 55.6% figure that had to be withdrawn.

---

## 6. What to do with this review

W0.8 (the engine regression suite) is **delivered** — `benchmarks/test_engine.py`,
69 checks, green, with W0.1 and W0.7 registered as known defects. The remaining
tests are **W0.9 (metric and verdict fixtures)** and **W0.10 (harness
invariants)** in [PLAN.md](PLAN.md); the sweeps above are **W6** (seeds and
power) and **R6** (auto-tune), with sweep 1 now partly executed by
`benchmarks/tune_probe.py` and its confirmatory run scheduled as the attribution
control **W1.2**. None of them need a decision from the board — they are the
cheapest way to stop the next arithmetic bug from reaching a published table.
