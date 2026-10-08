# Testing and Tuning Review (round 3)

A second review pass, this time on **what verifies the code** and **what the
constants are**. Produced from reading every module in the repo; no code was
changed by the review itself.

**Headline findings.**

1. **The engine has no automated checks at all.** `scripts/myelinate.py` ends at
   `sys.exit(main())`. Every real defect found in this project — compounding
   decay, the similarity index not invalidated by `retire()`, double
   tokenisation on `add()`, a write-only cluster pass on the refresh hot path —
   lives in the one file nothing tests. **Round-4 status:** closed —
   `benchmarks/test_engine.py` now covers the engine with 181 checks (139 after
   round 4, 170 after round 5), and the decay, index-invalidation and update/restatement defects are
   asserted for real rather than registered as known. **Round-5 additions:** the
   settable recall parameters (`prior_weight` and the three candidate ceilings,
   `0` meaning unbounded), the read-only `candidate_pool()` diagnostic, and the
   pin that keeps `M6`–`M10`/`M12` on the legacy prior weight while `M11` tracks
   the shipped default — that last one exists because dropping the pin would
   silently turn six arms into `M11` and erase the before/after evidence with no
   test failing.
2. **The metric definitions are untested.** `benchmarks/metrics.py` has no
   `__main__`; the numbers that decide the verdict (hit rate, nDCG, MRR,
   `stale_leak`, `chars_per_hit`) have never been checked against a
   hand-computed value.
3. **The decision rule is untested**, and two of its bugs are logic bugs a
   20-line test would have caught (a criterion that passes when *either* flat
   arm is beaten, and a criterion scored as FAIL when the run never measured it).
4. **Almost nothing has been tuned.** Of ~20 engine constants, three families
   were chosen by measurement (`SKETCH_SIZE`, `BAND_ROWS`, `MAX_POSTINGS_SCAN`);
   everything else is hand-set and marked `ASSUMPTION`. `PRIOR_WEIGHT` was the
   one whose value visibly degraded ranking — **round 5 took it to its
   pre-registered test and it passed, so it is now the first engine constant with
   a measured provenance** (see §5). One serious instrument defect came with that
   work: the sweep tool itself could not run at all.

---

## 1. Inventory: what is checked today

| Module | Self-check | What it asserts |
| :--- | :--- | :--- |
| `scripts/myelinate.py` | **none** | the CLI is the only entry point; no assertions anywhere in the file |
| `benchmarks/common.py` | **none** | `pack_blocks`, `replay`, `downgrade`, `jaccard`, `day` are unverified |
| `benchmarks/metrics.py` | **none** | every metric definition is unverified |
| `benchmarks/run_bench.py` | **none** | the module still has no `__main__`, but `verdict()` and the report renderer the review flagged are now pinned by `benchmarks/test_harness.py` (W0.9/W0.10); `pairwise()` is exercised through `stats.py` and the judge-limit path through `test_llm_judge.py` |
| `benchmarks/stats.py` | **yes**, ~12 asserts | Wilcoxon identical samples → `(0.0, 1.0)`; a clear shift → `p < 0.05`; `cliffs_delta` sign; bootstrap CI contains the mean and is seed-deterministic; Holm rejects/passes; `paired_summary` significance, and non-significance at n = 3; `format_p`; `percentile` |
| `benchmarks/engines.py` | **yes**, 2 asserts | 16 offline arms are built (`M0`–`M13`, including the `M3k`/`M3p`/`M3t` allocator controls and R12's `M13`); each has `recall_ms` |
| `benchmarks/judge.py` | **yes**, 7 asserts | oracle extracts a line from a context; `contains` and `exact` grading; empty context → empty answer; `make_judge("oracle")` mode; `describe()` refuses the LLM label |
| `benchmarks/stub_llm.py` | **yes**, 5 asserts | the stub's request→response mapping: answering, forced 0 score, HTTP 500 |
| `benchmarks/test_llm_judge.py` | **yes**, 71 assertions (22 at the time of this review, 60 after round 4) | the OpenAI-, Gemini- **and** NVIDIA-protocol paths end to end against the local stub: request shape, parsing, cache, HTTP-500 error, judge selection and descriptions, round-4 coverage for the free-tier pace, the 429 backoff, `Retry-After` and the `JUDGE_MAX_CALLS` budget, and round-5 coverage proving `auto`/`oracle` stay offline with an NVIDIA key present and that Gemini and OpenAI keep selection priority |
| `benchmarks/test_engine.py` | **yes**, 181 checks (69 at the time of this review, 139 after round 4, 170 after round 5) | the engine: bounded diminishing boost, pinned memories, tier thresholds, rendering caps, the budget guarantee on both recall paths, retired-memory exclusion, duplicate collapsing, persistence and store-path precedence — plus a `known_defect()` registry that is now **empty**: the four defects it used to report are fixed and asserted, and the mechanism stays so a new tracked defect can be registered |
| `benchmarks/test_harness.py` | **yes**, 204 checks | W0.9/W0.10, the list this review called "still unverified": metric definitions pinned to hand-computed values (nDCG to its closed form and a literal), `verdict()` fixtures (NOT MEASURED over phantom FAIL, informational rows out of the denominator, BOTH flat baselines for significance), replay determinism apart from wall-clock latency, the budget invariant across every offline arm at three budgets, the LoCoMo conversion when its cache is present (and its absence a clean state), the report renderer, and the D15 output-path guards including the relative-spelling regression |
| `benchmarks/test_adversarial.py` | **yes**, 88 checks | the myelination council's code-and-architecture review, four chairs over every store rule and protocol contract: store & persistence (schema-version refusal S3, corrupt stores, load-replaces-not-merges, save atomicity and id uniqueness, retire-then-restate ids, S6 refuted and pinned), the algebra of decay (the decay semigroup fuzzed over refresh schedules, realize idempotence, dead memories never strengthened — S4), the recall/budget contract (the hard ceiling fuzzed over unicode stores at six budgets, determinism, tier respect), and protocol robustness (the MCP server survives hostile JSON-RPC lines — S1/S2 — and keeps serving; the CLI fails cleanly — S5) |
| `benchmarks/synthetic.py` | **yes**, a validator over every scenario | unique ids and event ordering; evidence/stale exist and precede the query; the staleness suites retire (or provably do not retire) their stale ids; filler > 200 so the budget binds; stale/evidence Jaccard < 0.9 so collapsing cannot be mistaken for staleness handling; **globally unique query ids** (a collision would silently corrupt paired statistics) |
| `benchmarks/public_locomo.py` | **yes**, `validate()` with a non-zero exit | the conversion that defines the LoCoMo tier's ground truth: every query's `evidence_ids` names a turn its Scenario really adds, and every query is asked at the conversation's last session; `test_harness.py`'s LoCoMo gate runs the same validator when the cache is present and checks that a missing cache is a clean `RuntimeError` when it is not |

Run them all:

```bash
python3 benchmarks/stats.py                    # stats ok
python3 benchmarks/engines.py                  # engines ok
python3 benchmarks/judge.py                    # judge ok
python3 benchmarks/stub_llm.py                 # stub ok
python3 benchmarks/synthetic.py                # synthetic ok: 14 scenarios, 33 queries, ...
python3 benchmarks/test_llm_judge.py           # llm judge ok: 71 assertions
python3 benchmarks/test_harness.py             # harness ok: 204 checks
python3 benchmarks/test_adversarial.py         # adversarial ok: 88 checks
python3 benchmarks/test_engine.py              # engine ok: 181 checks
python3 scripts/myelinated_mcp.py --selftest   # mcp ok: 10 checks
python3 -m py_compile scripts/myelinate.py scripts/myelinated_mcp.py benchmarks/*.py

# Deliberate, not a check: the full default run rewrites the committed report
# (benchmarks/RESULTS.md + results/raw.json); a scoped run writes RESULTS-<tier>.md instead.
python3 benchmarks/run_bench.py
```

So the harness assertions have grown well past the roughly **48** this review
counted — 71 of them now live in `test_llm_judge.py` alone — and the engine,
which had none, is covered by **181 checks**. The best check in the repo is still
`synthetic.py`'s validator, and it prevents a class of failure
(query-id collisions) that would have quietly merged two different questions in
the paired statistics. That list now includes the engine, which is the change this
review asked for.

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

**Status: the engine suite is implemented, green, and carries no known-defect
lines.** `benchmarks/test_engine.py` reports **181 checks** (69 at the time of
this review, 139 after round 4, 170 after round 5) and is listed in §1; its `known_defect()` registry is **empty**,
because the four defects it used to report (W0.1, W0.7, D13, D14) are fixed and
asserted. The metric, verdict and harness suites below are still W0.9–W0.10, so
several of the cases they describe are now fixed in code but unasserted by a test.

Two entries in the engine list below describe behaviour that is currently
**wrong**. Rather than commit assertions that fail from the start (a red test
suite is ignored within a week), the suite registers them with
`known_defect(tracking, description, present)`:

```
engine ok: 181 checks
  # and no KNOWN DEFECT line: W0.1, W0.7, D13 and D14 are asserted for real now
```

The registration asserts each defect's **signature**, not the wrong value: if
the code changes for an unrelated reason the registry notices, and on the day
the fix lands the line flips to `RESOLVED … remove it from this registry and
assert the fix instead`. So the suite is green before and after every fix, and a
tracked defect cannot be silently forgotten — which is how the compounding decay
survived two rounds of reports. **Round 4 is the day of the fixes:** the registry
has zero entries, and the four signatures it used to report are asserted directly
(per-day decay for N = 1..30 with a no-op re-realise, incremental similarity
equal to a full rebuild after `retire`/`pin`, and the update-versus-restatement
split at the 0.90 threshold).

### `benchmarks/test_engine.py`

```python
from myelinate import MyelinatedMemory, INITIAL_SCORE, CATEGORY_DECAY_PER_DAY, GIST_MAX_CHARS
# 1. decay is per-day, not compounded          <-- asserted since round 4
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
# 8. retire() invalidates the similarity index            <-- asserted since round 4
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
# 1. hero beats M2 but loses to M1 flat/FIFO  -> significance criterion must NOT pass  <-- the rule does this since round 4
# 2. scale rows empty (--skip-scale / tier run) -> latency criterion is NOT MEASURED
#    and is excluded from the denominator            <-- the rule does this since round 4
#    (on a full run the scale criterion is INFORMATIONAL: its sign is not reproducible on this host)
# 3. a record with judge_skipped=True must not contribute to task_success <-- the harness does this since round 4
# The three cases are fixed in code; they are still not pinned by a committed fixture file.
# 4. the hero is never swapped when another arm has a higher hit rate (BM-004 guard)
```

### `benchmarks/test_harness.py`

```python
# 1. replay determinism: same scenario + same arm twice -> identical query_ids, used_ids, chars
# 2. every one of the 15 arms respects the budget on a fixed scenario
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
| `CATEGORY_DECAY_PER_DAY` | identity .002, user/preference .010, general .030, task .050, ephemeral .100 | **hand-set** (`ASSUMPTION`); never swept. The compounding bug is fixed in round 4 (strength is derived from a stored `(score, score_at)` pair, so `refresh()` folds only the decay not yet applied), which means these rates can finally be swept against a correct decay curve instead of fitting the bug — sweep 6 is unblocked |
| `INITIAL_SCORE` | 0.60 | hand-set, just above `ACTIVE_THRESHOLD` so new memories are visible |
| `BOOST_ALPHA` | 0.35 | hand-set; diminishing returns via `s + α(1−s)` |
| `ACTIVE_THRESHOLD` / `LATENT_THRESHOLD` | 0.5 / 0.1 | hand-set; these decide the tier mix, and therefore how much of each memory reaches the budget |
| `DUPLICATE_JACCARD` | 0.9 | **measured only off-benchmark**: 97.5% collapse at Jaccard 0.96 on a probe, 0 false merges on a 500-memory negative control. Never swept against the benchmark corpus |
| `CLUSTER_JACCARD` | 0.5 | **dead**: `_cluster()` writes `mem.cluster`, and nothing reads it |
| `SUMMARY_MAX_CHARS` / `GIST_MAX_CHARS` | 160 / 64 | hand-set; jointly decide how many memories fit a 2,200-char budget, so they move hit rate and `chars_per_hit` directly |
| `SKETCH_SIZE` / `BAND_ROWS` / `MAX_POSTINGS_SCAN` | 8 / 1 / 96 | **the only measured values in the engine**: bottom-8 single-hash banding chosen after `r=2` banding measured 62.6% recall at Jaccard 0.93 and was rejected |
| `MAX_CANDIDATES` | 64 | hand-set cap on comparisons per memory. Round 5 made all three ceilings settable per instance (`max_candidates`, `max_postings_scan`, `recall_pool`; `0` means unbounded) so they can be ablated, and arm `M12` measures them collectively as **not** the cause of the ranking deficit — `M12` reproduces `M10` exactly on both hold-out seeds |
| `RECALL_POOL` | 600 | reasoned (the budget fills long before the pool empties), never swept — probably slack at a 2,200-char budget. Consistent with `M12`: lifting it changes nothing on these tiers, because the pool cut is applied to the ranker's input while the ranking path itself scans the live store |
| `DETAIL_VALUE` | full 1.0, summary 0.55, gist 0.25 | hand-set; the packer's entire preference order rests on these three numbers |
| `PRIOR_WEIGHT` | **0.0** | **MEASURED (round 5)** — the first engine constant whose value was chosen by a pre-registered experiment rather than hand-set. It was 0.35 since round 1, and it was the most consequential value in the file: it is why the same cosine ranker scores nDCG 0.729 as a baseline (M4) but 0.690 with the prior blended in (M7) and 0.606 once the packer reorders (M8). It adds a query-independent constant to a cosine, so a strongly myelinated memory that says nothing about the question can outrank the one that answers it. Arm `M11` (the prior at 0.0 on hold-out seeds 3–4) passed the criterion frozen in `docs/FIX-PLAN.md` F18 — hit rate 0.893, nDCG 0.702/0.697, chars/hit 1276/1283, task success 0.539 — so the prior is demoted to a tie-breaker and the constant ships at 0.0. `LEGACY_PRIOR_WEIGHT = 0.35` pins `M6`–`M10` and `M12` so the round-4 rows stay reproducible, and `M11` tracks the shipped default instead of hardcoding it |
| `QUERY_TERM_LIMIT` | 48 | **hoisted, not tuned** — this cap on the query vector inside `similarity_scores()` was a bare `[:48]` in the function body, invisible to this inventory and to every sweep (**D17**), until round 5.1 hoisted it into the constants block with an `ASSUMPTION` note. Its value is unchanged, so the top-10 order is byte-identical for a fixed corpus; it is listed so that a sweep can finally see it. Not yet swept — it belongs with sweep 7, since a cap on scored query terms trades ranking quality against recall cost |
| `PROTECTED_PRIOR` | 1e6 | reasoned (effectively infinite) |
| Workload: budget 2200, 60 days, 10 000 memories, filler rates, scenario counts | — | the workload definition itself; changing any of it redefines the benchmark and invalidates comparisons |

### Protocol for tuning (pre-registered)

1. **Fix W0.1 first — done in round 4.** Tuning decay rates against a compounding
   implementation fits the bug rather than the model; the decay path is now
   derived and per-day, and a repair also makes `realize()` idempotent at a fixed
   `now`, so a session that calls `refresh()` hourly and one that calls it daily
   see the same curve.
2. **One knob per arm.** A tuned value ships as its own labelled arm (M11, M12…),
   never folded into M8 — the BM-002 attribution rule.
3. **Hold out seeds.** Tune on seeds 0–4, report on 5–9. When this was written
there was **one** seed, so no result was out-of-sample. Round 5 ran the first
experiment that satisfies the spirit of the rule — the sweep on seed 0, then the
confirmatory run on seeds 3 and 4 — which is why the `PRIOR_WEIGHT` decision is
admissible and the earlier curve was not. Two seeds still fall short of the ≥ 5
the same protocol asks for, and that gap is recorded in
[`ROUND5-STRATEGY.md`](ROUND5-STRATEGY.md) §7 rather than glossed over.
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
| 1 | `PRIOR_WEIGHT` ∈ {0, 0.1, 0.2, 0.35, 0.5, 1.0} | nDCG@10, hit rate | **RUN, and DECIDED (round 5).** It tested the claim "retrieval strength helps ranking" and the answer was no: nDCG climbs toward 0.72 as the weight → 0 on LoCoMo, the internal control holds, and the pre-registered hold-out run (`M11`, frozen F18 criterion) passed on both seeds, so the prior was demoted to a tie-breaker. See "Sweep 1, measured" below |
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
| **0.35** *(the value committed at the time)* | 0.553 | 0.825 | 0.606 | 1589 | 1.000 | 1.000 | 1.000 | 0.400 |
| 0.50 | 0.553 | 0.811 | 0.593 | 1617 | 1.000 | 1.000 | 1.000 | 0.350 |
| 1.00 | 0.539 | 0.738 | 0.569 | 1776 | 0.964 | 1.000 | 1.000 | 0.167 |

**The internal control holds**: the 0.35 row reproduced M8's committed numbers
exactly (0.825 / 0.606 / 1589 / LoCoMo 0.400) at the time of the sweep, so the
probe was measuring the same system the report measured.

**Round-4 caveat:** the numbers in this table are the round-3 measurements, taken
before the decay fix and before the update/restatement split changed the store.
The committed M8 row is now **0.835** hit rate, **0.639** nDCG@10 and **1572**
characters per hit, so the control no longer reproduces it and the sweep must be
re-run before any of it is quoted again — the conclusion (the prior is a cost to
ranking) is the reason to re-run it, not to trust the old curve. **That re-run
has since been done and it decided the constant: see "Sweep 1, re-run and
decided (round 5)" below.** The table above is kept as the round-3 record. The
committed report itself has also been regenerated from the round-5 code (5.1.0),
so `benchmarks/RESULTS.md` now carries the round-5 columns — `M11` at 0.893 hit
rate, 0.699 nDCG@10 (0.689 packed) and 1279 characters per evidence hit — rather
than the round-4 ones this caveat was written about. **And the committed value is the second-worst point
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

**Why this did not change anything at the time:** one seed, swept on the report's
own 206 queries (no hold-out), and not pre-registered. Acting on it then would
have been selecting a configuration after seeing the numbers. The confirmatory
run (hold-out seeds, criterion frozen in advance) was W1.2 + W6.

#### Sweep 1, re-run and decided (round 5)

The confirmatory run happened. Three things had to be true first, and all three
were false or unknown before this round:

1. **The instrument had to run at all.** `benchmarks/tune_probe.py` raised
   `TypeError: tuple indices must be integers or slices, not str` on every
   invocation — it unpacked `run_bench.evaluate()`'s `(records, ledgers)` return
   as a bare dict. The curve in this section, and the one quoted on the front
   page, had therefore **never been produced by the tool that is supposed to
   produce it**. One-line fix; the internal control then held, which is what
   makes the re-run admissible.
2. **The criterion had to be frozen before the run.** It was, in
   `docs/FIX-PLAN.md` F18, on hold-out seeds 3–4: hit rate ≥ 0.874, nDCG@10 ≥
   0.672, chars/hit ≤ 1336, task success ≥ 0.533.
3. **The competing explanation had to be tested.** A second control, `M12`
   (`M10` with all three candidate ceilings unbounded), reproduces `M10`
   **exactly** on both hold-out seeds, so capped candidate generation is measured
   out as the cause. The prior term was the only suspect left standing.

Re-run curve, seed 0 (dev tiers curated/synthetic/staleness, then LoCoMo — the
dev tiers pick the value, LoCoMo confirms it):

| `PRIOR_WEIGHT` | dev hit rate | dev nDCG@10 | LoCoMo hit rate |
| ---: | ---: | ---: | ---: |
| **0.00** | 0.979 | 0.856 | **0.683** |
| **0.10** | **1.000** | **0.859** | 0.567 |
| 0.20 | 1.000 | 0.847 | 0.517 |
| 0.35 *(was committed)* | 1.000 | 0.840 | 0.433 |
| 0.50 | 1.000 | 0.840 | 0.433 |
| 1.00 | 1.000 | 0.810 | 0.317 |

**Result on the hold-out seeds** (`--tier all --skip-scale`, 206 queries per arm,
offline oracle judge), arm `M11` against the frozen thresholds:

| `M11`, seed | task success ≥ 0.533 | hit rate ≥ 0.874 | nDCG@10 ≥ 0.672 | chars/hit ≤ 1336 |
| :--- | ---: | ---: | ---: | ---: |
| 3 | **0.539** ✓ | **0.893** ✓ | **0.702** ✓ | **1276** ✓ |
| 4 | **0.539** ✓ | **0.893** ✓ | **0.697** ✓ | **1283** ✓ |

All four thresholds pass on both seeds, so per the rule F18 wrote the prior is
demoted to a tie-breaker and `PRIOR_WEIGHT` ships at **0.0**. Against the previous
leader (`M3k`, BM25's ranking in the engine's packer) `M11` now **ties on hit rate
(0.893 against 0.893) and on task success, and wins on characters per hit
(1276/1283 against 1294/1301)**, while still trailing on nDCG (0.702/0.697 against
0.734/0.731) and on LoCoMo (0.683 against 0.717). The two-seed run does not meet
this file's own ≥ 5 pooled-seed protocol, and nDCG is the one column where a
ranking claim would still need it.

**What was measured before this sweep:** nothing in the table above. Two of the
three values that *are* measured were measured on hand-built probe corpora, and
one of them (the dedupe probe) was later found to have been mis-calibrated once
already — a 24-word vocabulary made every memory a candidate of every other,
producing a 55.6% figure that had to be withdrawn.

---

## 6. What to do with this review

W0.8 (the engine regression suite) is **delivered** — `benchmarks/test_engine.py`,
181 checks, green, with an **empty** known-defect registry because W0.1, W0.7,
D13 and D14 are all fixed and asserted. The remaining tests are **W0.9 (metric
and verdict fixtures)** and **W0.10 (harness invariants)** in [PLAN.md](PLAN.md),
and round 4 makes them the most valuable open work in this review: the
harness defects they were written to catch (one-of-two flat arms, unmeasured
criteria scored, skipped judge calls scored) are now fixed in code but still
pinned by no test. The scale latency criterion is reported as **INFORMATIONAL**
for the same reason — two identical runs flipped its sign on this host. The sweeps
above are **W6** (seeds and power) and **R6** (auto-tune). Sweep 1 is now
**executed and decided** — the repaired `benchmarks/tune_probe.py` produced the
curve, and the confirmatory run shipped as the attribution control **W1.2**
(§5). None of the remaining sweeps need a decision from the board — they are the
cheapest way to stop the next arithmetic bug from reaching a published table.

**Round-5 outcome against this review.** Sweep 1 is **done and decided** (§5): the
prior now sits at 0.0 on measured provenance, confirmed on hold-out seeds, with
`M12` retiring the candidate-cap explanation that could otherwise have taken its
place. W0.9 (metric and verdict fixtures) and W0.10 (harness invariants) are
**still missing**, and round 5 added a fresh reason to want them: a measurement
defect survived round 4 in exactly the shape this review warns about — every
engine arm's `nDCG@10` and `nDCG@10 packed` columns were identical because the arm
never handed the harness a pre-packing order, so the report scored the allocator
as the ranker (defect D8/F21). Nothing failed; the numbers simply meant something
other than what their labels said. A fixture asserting that the two columns *can*
differ would have caught it. Sweeps 2–7 remain open, and sweep 6 (the decay rates)
is the one that could still move the decay claim.
