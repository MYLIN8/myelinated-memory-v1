# Remediation Plan - Making Myelinated Memory Beat Semantic Memory

The first benchmark came back **negative**: as specified, the engine does not
beat semantic retrieval. This document tracks the plan to close that gap. Round
two is done; round three is what is left.

Reproduce with:

```bash
python3 benchmarks/run_bench.py            # 33 scenarios, 206 queries, 2200-char budget
```

> **Round 3 is planned in [`docs/PLAN.md`](PLAN.md), not here.** A four-analyst
> review found that four of this log's numbers are produced by broken
> instruments - the decay path compounds (a real arithmetic bug), the reported
> `p95` is the maximum of five queries, the headline leak column mixes 22
> records of which 16 have no retire signal, and the budget comparison pits the
> engine's packer against baselines that truncate. The plan fixes those first
> and re-measures; the table below records what round 2 measured **with those
> instruments**, and decay figures from rounds 1-2 are superseded by W0.1.

## Round 2 outcome

| Arm (same code, one capability at a time) | Change | Task success | Hit rate | nDCG@10 | Chars/hit |
| :--- | :--- | ---: | ---: | ---: | ---: |
| M6 myelinated (pure) | *specification* | 0.505 | 0.650 | 0.426 | 2019 |
| M7 myelinated +similarity | R1 | 0.553 | 0.796 | 0.690 | 1651 |
| **M8 myelinated +knapsack** | R4/R7 | 0.553 | **0.825** | 0.606 | **1589** |
| M9 myelinated +supersession | R5 | 0.553 | 0.825 | 0.606 | 1589 |
| M10 myelinated +reinforcement | R2 | 0.534 | 0.801 | 0.571 | 1652 |
| *reference:* M3 semantic BM25 | - | 0.549 | 0.864 | **0.732** | **1336** |
| *reference:* M4 semantic TF-IDF | - | **0.563** | **0.874** | 0.729 | 1541 |

**The gap closed from 22.4 hit-rate points to 4.9** (0.650 -> 0.825 against
0.874), which meets the pre-registered "within 5 points" criterion. Budget
efficiency now beats flat memory (1589 vs 1734 characters per hit) instead of
losing to it (2019 vs 1734 before). The verdict went from 2 of 5 criteria to
**4 of 7**, with the two failures that matter being R2 and decay.

Three results are negative and are reported as such:

1. **R2 (utility reinforcement) made things worse** - 0.801 vs 0.825 hit rate and
   0.534 vs 0.553 task success. Reinforcing every memory that was in the context
   of a *correct* answer rewards the memories that happened to be nearby, not the
   ones that caused the answer. The pre-registered hero (M10) is left in the
   report at its measured value rather than being swapped for the better arm.
2. **R5 does not make decay work.** With an explicit retire signal the engine
   leaks 0.000 of superseded facts, but the decay-only suite (the same scenarios
   with no retire signal) still leaks **1.000**. Retrieval-strength decay alone
   does not retire a stale fact at this horizon. Decay remains unproven.
3. **Query-aware recall costs latency at scale** - 93 ms p95 against BM25's 59 ms
   and the pure engine's 10.6 ms, because every query now scans the whole store.

## What was actually changed

| # | Change | Status | Evidence |
| :--- | :--- | :--- | :--- |
| **R1** | Blend query similarity into recall ranking | **done** | hit rate 0.650 -> 0.796 |
| **R4/R7** | Pack by expected value per character; archived memories render a content gist instead of an opaque id stub | **done** | 0.796 -> 0.825, chars/hit 1651 -> 1589 |
| **R5** | Supersession: `add(supersedes=...)` and `retire()` | **done** | leak with supersession 1.000 -> 0.000 |
| **R2** | Utility-credit reinforcement | **done, net negative** | 0.825 -> 0.801 |
| **R8** | Sketch-index duplicate lookup and incremental consolidation | **done, target missed** | ingest 12.5 s -> 4.4 s per 10,000 memories (target was < 1 s) |
| **R7** | Summary ladder | **done** | see R4/R7; fixed the LoCoMo tier, 0.100 -> 0.400 hit rate |
| **R3** | Spaced-repetition decay | **not started** | decay-only leak is still 1.000, so this is now the highest-value item |
| **R6** | Auto-tune decay constants | **not started** | constants are named and re-runnable; no search performed |

## Round 3: what is left, ranked

| # | Change | Why | Acceptance |
| :--- | :--- | :--- | :--- |
| **R3** | Spaced-repetition decay, or an explicit relevance half-life per category | Decay-only leak is 1.000; the "intelligent decay" claim fails without it | leak < 0.50 **with a retrieval floor** (`hit_rate_decay_only` >= 0.9 x flat), and the negative control (M0 no-memory) must **fail** the criterion - today M0, M2 and M6 "pass" it only by retrieving nothing |
| **R2b** | Reinforce on *evidence overlap within a correct answer*, or abandon the signal | R2 as built rewards proximity, not causation | task success must not regress vs M8 |
| **R9** | Cache the similarity index so query-aware recall does not rescore the store | 93 ms p95 at 10k is 1.6x BM25 and 9x the pure engine | recall p95 < BM25 at 10k memories, measured over >= 50 warm queries with the cold start reported separately (today's "p95" is the max of five, so it flips between runs) |
| **R8b** | Batch the per-session decay sweep, or decay lazily on read | Ingest is still 4.4 s / 10k; the decay loop is O(n) per session | ingest < 1 s per 10k |
| **R6** | Tune `PRIOR_WEIGHT`, `DETAIL_VALUE`, category decay rates against the synthetic suite | These are the only free parameters and were hand-set | no metric regresses |
| **R10** | Raise the budget for long transcripts, or chunk by session | LoCoMo scores ~0 task success for every arm at 2,200 chars over ~1,450 turns; the tier cannot discriminate | a budget where the best arm beats the worst by a real margin |

## Assumptions the specification left open

`README.md`, `SKILL.md` and `docs/HOW-IT-WORKS.md` describe the mechanism but not
its constants. The reference implementation fills the gaps and marks each one
`ASSUMPTION` in `scripts/myelinate.py`:

| Constant | Value | Where |
| :--- | :--- | :--- |
| Initial score for a new memory | 0.60 | `INITIAL_SCORE` |
| Hebbian boost per access | `s + 0.35 x (1 - s)` | `BOOST_ALPHA` |
| Category decay per dormant day | identity 0.002, preference 0.010, general 0.030, task 0.050, ephemeral 0.100 | `CATEGORY_DECAY_PER_DAY` |
| Tier thresholds | active > 0.5, latent > 0.1 | `ACTIVE_THRESHOLD`, `LATENT_THRESHOLD` |
| Archived rendering | 64-character content gist, not an id stub | `GIST_MAX_CHARS` (R7) |
| Detail value weights | full 1.0, summary 0.55, gist 0.25 | `DETAIL_VALUE` |
| Retrieval-strength prior in packing | 0.35 | `PRIOR_WEIGHT` |
| Duplicate / cluster similarity | Jaccard 0.90 / 0.50 | `DUPLICATE_JACCARD`, `CLUSTER_JACCARD` |
| Duplicate candidate index | MinHash bottom-8 sketch, 97.5% recall at Jaccard 0.96, zero false merges | `SKETCH_SIZE`, `MAX_POSTINGS_SCAN` |
| Consolidation scope | memories touched since the last refresh | `refresh()` |
| Storage ceiling | unbounded (archived entries are kept) | `DEFAULT_MAX_ENTRIES` |

## Re-run protocol

```bash
python3 benchmarks/run_bench.py                    # deterministic, offline, free
python3 benchmarks/run_bench.py --tier staleness   # only the staleness suites
python3 benchmarks/run_bench.py --judge openai     # real LLM judge (needs OPENAI_API_KEY)
python3 benchmarks/run_bench.py --network          # add the dense-embedding arm
python3 benchmarks/test_llm_judge.py               # verifies the LLM judge protocol, no key needed
```

Every run rewrites `benchmarks/RESULTS.md` and `benchmarks/results/raw.json`,
recording the exact command, judge, seed and Python version.

## What would falsify the fix

Round 3 is done when a re-run shows the decay-only leak below 0.50 **without**
any retire signal, query-aware recall p95 below BM25's at 10,000 memories, and
no regression in hit rate or task success. If R3 and R9 move those numbers and
the gap to semantic retrieval does not close further, the honest conclusion is
that a retrieval-strength score is the wrong *primary* signal for
query-conditioned recall, and it should be demoted to a tie-breaker rather than
a ranker - which is what the M8 configuration already does.
