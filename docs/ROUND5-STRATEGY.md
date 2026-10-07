# Round 5 — closing the gap to the best non-engine arm

The goal for this round was stated plainly: **the engine must match or beat the other memory
models on this benchmark, or the report has to say why it cannot.** This document is the
working-out. Every number in it was measured with the commands given at the bottom; none was
carried over from an earlier round without saying so.

## 1. What was actually wrong

The published deficit looked like a storage problem. It is not. It is two smaller things, and
the instrument that should have found them was broken.

**The instrument was broken.** `benchmarks/tune_probe.py` is the one tool that attributes an
engine constant, and it crashed on every invocation:

```
File "benchmarks/tune_probe.py", line 61, in sweep
    by_arm = run_bench.evaluate(scenarios, [arm], judge, budget, 0)
TypeError: tuple indices must be integers or slices, not str
```

`evaluate()` returns `(records_by_arm, judge_ledgers)`. The sweep unpacked it as a bare dict, so
**the `PRIOR_WEIGHT` curve had never actually been produced by this tool.** The front page
carried a note saying the sweep "must be re-run before anyone acts on it"; the re-run was
impossible, and the note sat there reading like a deliberate deferral rather than a traceback.
That is fixed, and the internal control it was missing now holds (see §3).

**The engine is not missing a ranker.** It already has a real one — TF-IDF cosine over the live
store (`_build_similarity` / `similarity_scores`) — combined with retrieval strength:

```
value = sim + PRIOR_WEIGHT × strength          # scripts/myelinate.py, _utility()
```

**The problem is that this fusion adds a query-independent constant to the ordering.** Similarity
is a cosine in `[0, 1]`; strength is also in `[0, 1]`. Adding `0.35 × strength` means a memory
that says nothing about the question but is strongly myelinated can outrank the memory that
answers it. The prior is useful for *allocation* (how much detail a memory has earned) and
harmful for *ordering* (which memory the question is about). Round 3 had already guessed this in
a sweep that predated the round-4 decay repair; nothing since had re-measured it.

**The candidate caps were not the problem, and now that is measured rather than assumed.** Two
new arms isolate the two suspects: `M11` is `M10` with the prior weight at `0.0`, and `M12` is
`M10` with `max_candidates`/`max_postings_scan`/`recall_pool` all unbounded. `M12` reproduces
`M10` **exactly** — same hit rate, same nDCG, same characters, same LoCoMo — on both hold-out
seeds. The capped path feeds duplicate detection, not query ranking, so the ceilings cannot be
the cause on these tiers. A control that fails to matter is evidence, and it is reported as such.

## 2. The bar, frozen before the measurement

`docs/FIX-PLAN.md` **F18** pre-registers the test and its thresholds, on **hold-out seeds 3–4**,
which are not the seeds the engine was tuned on:

> hit rate ≥ 0.874 · nDCG@10 ≥ 0.672 · characters per hit ≤ 1336 · task success ≥ 0.533
> → passes: a labelled `M11` ships and the prior is demoted to a tie-breaker

The thresholds came from the best non-engine arm of the previous round, so they are a genuine
"match the leader" bar rather than a convenient one.

## 3. The result

`python3 benchmarks/run_bench.py --tier all --skip-scale --seed 3` (and `--seed 4`), 206 queries
per arm, offline oracle judge, 2200-character budget:

| arm | seed | task success | hit rate | nDCG@10 | nDCG@10 packed | chars/hit | LoCoMo |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **M3k** BM25 + engine packer *(previous leader)* | 3 | 0.539 | **0.893** | **0.734** | 0.737 | 1294 | **0.717** |
| | 4 | 0.539 | **0.893** | **0.731** | 0.733 | 1301 | **0.717** |
| `M4` semantic/TF-IDF | 3 | **0.563** | 0.874 | 0.732 | 0.732 | 1540 | 0.600 |
| | 4 | **0.563** | 0.874 | 0.727 | 0.727 | 1543 | 0.600 |
| **M11** myelinated + lexical ranking **(shipped)** | 3 | 0.539 | **0.893** | 0.702 | 0.690 | **1276** | 0.683 |
| | 4 | 0.539 | **0.893** | 0.697 | 0.689 | **1283** | 0.683 |
| `M8` myelinated + knapsack *(round-4 engine)* | 3 | 0.558 | 0.835 | 0.693 | 0.641 | 1572 | 0.433 |
| | 4 | 0.558 | 0.835 | 0.688 | 0.641 | 1572 | 0.433 |
| `M10` myelinated + reinforcement | 3 | 0.553 | 0.830 | 0.677 | 0.615 | 1596 | 0.433 |
| | 4 | 0.553 | 0.830 | 0.672 | 0.615 | 1597 | 0.433 |
| `M12` myelinated + unbounded candidates | 3 | 0.553 | 0.830 | 0.677 | 0.615 | 1596 | 0.433 |

**M11 passes all four frozen thresholds on both hold-out seeds** (0.893 ≥ 0.874; 0.702/0.697 ≥
0.672; 1276/1283 ≤ 1336; 0.539 ≥ 0.533). Against the previous leader it is:

| | M11 vs M3k |
| :--- | :--- |
| hit rate | **tied** — 0.893 against 0.893 on both seeds |
| characters per hit | **won** — 1276/1283 against 1294/1301 |
| task success | **tied** — 0.539 on both |
| stale-leak under an explicit retire signal | **tied** — 0.000 for both |
| nDCG@10 | **behind** — 0.702/0.697 against 0.734/0.731 |
| LoCoMo hit rate | **behind** — 0.683 against 0.717 |

And against the pure cosine semantic arm `M4`, which is the same ranker formula with no engine
around it, M11 **wins hit rate (0.893 vs 0.874), characters per hit (1276 vs 1540) and LoCoMo
(0.683 vs 0.600)** while trailing on task success (0.539 vs 0.563) and nDCG (0.702 vs 0.732).

So the honest summary is: **matching has been achieved on the primary retrieval metric and on
context cost — beating on cost — and the remaining gap is now isolated to ordering quality
(nDCG) and to the LoCoMo tier.** The engine also retains what the BM25 baselines do not have:
supersession, decay, protected memories, a persisted store and deterministic allocation.

## 4. What shipped

| change | why |
| :--- | :--- |
| `PRIOR_WEIGHT` **0.35 → 0.0** in `scripts/myelinate.py` | the measured cost is the ordering dilution; the prior keeps its allocation and tie-break role |
| `LEGACY_PRIOR_WEIGHT = 0.35`, pinned onto `M6`–`M10` and `M12` | otherwise every round-4 arm silently becomes `M11` and the before/after evidence disappears from the report |
| `M11` leaves `prior_weight=None` | it tracks the shipped default instead of hardcoding the measured value a second time |
| `RecallResult.ranked_ids`, set at `recall()`'s single return point | see §5 |
| `benchmarks/tune_probe.py` unpacking fixed | the sweep instrument now runs at all |
| `benchmarks/engines.py`: `MyelinatedArm._recall` returns its pre-packing order | see §5 |
| `test_engine.py` guards the pin, the new default, and that the legacy weight differs | the same silent-erasure failure, caught automatically |

## 5. A measurement defect found on the way, and fixed

Every engine arm's two nDCG columns were **identical** (`M11`: 0.690 and 0.690; `M8`: 0.641 and
0.641) while the BM25 arms' columns differed (`M3k`: 0.734 and 0.737). Identical columns are
impossible when an allocator reorders context, and the reason is defect **D8/F21**: the arm
returned only `(text, used_ids)`, so the harness had no pre-packing order to score and fell back
to the *packed* order. **The report was scoring the allocator as if it were the ranker.**

`RecallResult` now carries `ranked_ids` — the pool the packer was handed — set at the single
return point in `recall()`, so both packers and both the query-ranked and query-blind paths agree,
with no extra scan. The effect is purely on what the numbers mean: hit rate, characters per hit,
task success and LoCoMo are byte-identical, while the honest nDCG@10 rises for every engine arm
(`M11` 0.690 → 0.702 on seed 3, `M8` 0.641 → 0.693) and the new `nDCG@10 packed` column shows what
the allocator costs (`M11` 0.012, `M8` 0.052 — the prior was making the allocator scramble much
harder). The committed `benchmarks/RESULTS.md` still holds the round-4 columns, which were
measured under the old fallback.

## 6. What stands between us and "beat" — ranked next levers

1. **Where the last 0.03 nDCG goes.** M11's ranker is a cosine and `M4`'s is the same cosine, yet
   `M4` records 0.732 and M11 records 0.702. The allocator accounts for only 0.012 of it
   (`nDCG@10 packed`), so roughly 0.02 is lost *before* packing — in the candidate set or the
   store. **Pre-registered next experiment:** use `engine.candidate_pool(query)` (added this
   round, read-only) to measure **pool recall@k** — the share of queries whose gold memory is in
   the pool the ranker is handed. If pool recall ≈ hit rate, the loss is candidate generation
   (consolidation, duplicate collapsing, the `recall_pool` cut, protected ordering) and not the
   ranking formula; if pool recall ≫ hit rate, it is the ordering. This single number decides
   which of the two remaining levers is real.
2. **LoCoMo (0.683 vs 0.717).** The tier is paraphrastic and multi-session, which is exactly
   where term-overlap scoring is weakest and where a dense arm should pay. `M5
   semantic/dense-embeddings` is wired and network-opt-in; with a key present it is the cheapest
   available test of whether semantic recall closes this tier, and it needs no engine change.
3. **Allocation vs ordering, cleanly separated.** With `nDCG@10` and `nDCG@10 packed` now
   distinct, the value-per-character rule and the rank order can be scored separately for the
   first time. If ordering is what the criterion measures, the allocation rule should choose the
   *set* and the rank order should decide the *sequence*.
4. **The decay-only staleness criterion still fails** (`leak_decay_only = 1.000` for M11): decay
   alone does not retire a stale fact. That is unchanged by this round and is a claim problem, not
   a ranking problem.

## 7. Limitations, stated plainly

* **Two hold-out seeds, not five.** Seeds 3 and 4 agree closely, which is the reason to believe
  the effect, but the pre-registered F22 pooled-seed protocol is not met.
* **No *complete* model-scored run exists.** Every number here is the offline oracle judge's.
  `NvidiaJudge` is live-verified (a real answer and grade through `make_judge("nvidia")`, and one
  live call per arm through the full `run_bench.py` pipeline with `judged=1 skipped=27 errored=0`),
  but no run has been judged end to end — the network cost of judging exceeds this host's 180 s
  command clamp, so no report file was produced from a model-scored run.
* **The NVIDIA catalogue is not an entitlement list.** Verified live: `meta/llama-3.3-70b-instruct`
  answers HTTP 410 (end of life 2026-08-26), and `nvidia/llama-3.1-nemotron-70b-instruct`,
  `nvidia/nemotron-4-340b-instruct`, `mistralai/mistral-large-2-instruct`,
  `nvidia/embed-qa-4` and `nvidia/llama-3.2-nv-embedqa-1b-v1` all answer HTTP 404 "Function not
  found for account" despite being listed. The defaults are now models this key actually reaches:
  `nvidia/nemotron-3.5-lightning-30b-a3b` (chat) and `nvidia/nemotron-3-embed-1b` (2048-dim
  embeddings). Both are overridable with `NVIDIA_MODEL` / `NVIDIA_EMBED_MODEL`, and the dense arm
  still requires `--network`.
* **`benchmarks/RESULTS.md` was not regenerated.** It remains the round-4 record measured under
  the old nDCG fallback; the engine columns in it predate both changes in §4 and §5.
* **Round-3 `PRIOR_WEIGHT` rows are superseded.** The old front-page table quoted 0.893/0.672 at
  weight 0; the re-run reproduces the *direction and the LoCoMo effect* (0.683) but not every
  digit, because round 4 changed the engine underneath it.

## 8. Reproducing every number here

```
python3 benchmarks/test_engine.py                # 170 checks
python3 benchmarks/test_llm_judge.py             # 71 assertions
python3 benchmarks/tune_probe.py --tiers locomo --values 0,0.1,0.2,0.35,0.5,1.0
python3 benchmarks/run_bench.py --tier all --skip-scale --seed 3 --report /tmp/r3.md --out /tmp
python3 benchmarks/run_bench.py --tier all --skip-scale --seed 4 --report /tmp/r4.md --out /tmp
```

A scoped run cannot overwrite `benchmarks/RESULTS.md` or `results/raw.json` (D15), so these are
safe to run at any time.
