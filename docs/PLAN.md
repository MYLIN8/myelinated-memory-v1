# Round 3 Plan — Measurement Integrity First

**Owner:** head of staff (orchestrator). **Status:** proposed, awaiting the board rulings in §4.
**Changes no code.** Every number below was produced by a command in §9.

> **Round 3.5 — the ordered fix plan is [`docs/FIX-PLAN.md`](FIX-PLAN.md).** An offline
> self-test pass re-ran every check, reproduced the live defects from §3, and added six more
> (D13–D18 below). It changes no behaviour; it sequences the work in §5 by what it costs to
> leave alone, and it states what the missing `OPENAI_API_KEY` blocks.

> **Round 4 — much of this plan has now run.** Gate 0 and the first item of Gate 1 have landed: the
> decay arithmetic (W0.1/D1), index invalidation (W0.7/D9), the update/restatement split (D13/D14),
> the leak and judge-state fixes (W0.3–W0.6), the rank-order metrics (W5/D8), the report guard (D15)
> and the attribution control arms (W1.1) are in the code, asserted by `benchmarks/test_engine.py`
> and `benchmarks/test_llm_judge.py`, and the report has been regenerated under decision-rule
> version 3. W1.1 answered in favour of the **allocator**. Per-item status is in the gate tables in §5
> and in [`FIX-PLAN.md`](FIX-PLAN.md) §0; this document keeps its round-3 text and rationale.

Round 2 closed the retrieval gap to 4.9 hit-rate points and flipped budget
efficiency, and the report says so. This plan exists because reviewing that work
with four parallel analyst pods found that **several of round 2's numbers do not
mean what the report says they mean** — one of them because of a straight
arithmetic bug in the decay path, the rest because of how the harness measures
and reports. Any round-3 engine work done before those are fixed will be
measured with the same broken instruments, so Gate 0 is measurement integrity
and it comes first.

---

## 1. How this plan was produced (delegation ledger)

Intake → four analyst pods in parallel → coordinator verification → synthesis.
Analysts are read-only and report; exactly one owner writes each file.

| Pod | Analyst brief | What it produced |
| :--- | :--- | :--- |
| A | Engineering: explain the three measured failures (latency, ingest, decay-unproven) from `scripts/myelinate.py` | 11 numbered findings with proposed fixes; spotted the compounding decay and the un-mutated similarity index |
| B | Research: can this harness decide the claims the plan will make? | Power floors (`2/2ⁿ`), the 22-record leak mix, the one-of-two-flat-arm loophole, `--judge-limit` faking zeros |
| C | Product: claims register and interface drift against the shipped CLI | 31 claims audited, 9 interface drifts, positioning paragraph, 6 real S&M tasks |
| D | Competitive/systems: are the engine's wins where they are claimed to be? | Packing lives only in the engine; ranking vs packing-order nDCG; staleness false positives; infrastructure table |

**Coordinator verification before adoption.** Four analyst claims were
load-bearing enough that no plan should rest on a reasoning-only report, so each
was re-derived by the coordinator with its own probe (§9): the compounding decay,
the "p95 of five samples", the 22-record leak mix, and the packing asymmetry.
All four held. Also verified independently: the CLI drift and the silent
`--pure --query` ignore.

**Corrected, downgraded or rejected in review.**

* Analyst A's line numbers were approximations (its search tool returned whole
  files); every mechanism it reported was re-checked by inspection and probe
  before entering this plan, and the ones I re-checked all held.
* Analyst A's finding that documents are indexed with stopwords filtered but the
  query is not (a systematic short-document bonus) is **plausible but
  unverified**. It enters this plan only as a thing W2 must check, not as a
  finding.
* Analyst B could not time a run; the runtime figures in §4 are mine.
* Analyst C's most quotable claim — "the README says the engine has the fastest
  recall in the suite" — is true of the README and false of the data
  (flat/FIFO: 0.60 ms p95 vs 10.5 ms). It is listed as defect D11.

---

## 2. What four analysts agreed on

The engine's **ranking** signal is not where its value is, and the harness has
been paying for that. TF-IDF cosine alone (M4) scores nDCG@10 0.729; the same
cosine plus the retrieval-strength prior (M7) scores 0.690; adding the packer on
top (M8) drops to 0.606 while hit rate *rises* to 0.825. Myelination as built is
a **budget allocator and consolidation policy**, not a better retriever, and the
honest plan tests it that way (§6, W1).

**Round-4 update — the reasoning above is now a measurement.** The committed M8 row is nDCG@10
**0.639** at hit rate **0.835**, and the new allocator control **M3k** (BM25 ranking filled by the
engine's own packer) reaches hit rate **0.893** against M8's 0.835, **0.717** against M8's 0.433 on
LoCoMo, and fewer characters per hit (1296 against 1572). A control with no store of its own, no
decay and no tiers beats every engine arm, so the demotion this section argues for is measured
rather than inferred.

---

## 3. Measurement defects (Gate 0)

Each row is a defect, the evidence, the claim it distorts, and the criterion
that closes it. Nothing in Gate 1 may be measured before Gate 0 lands.

| # | Defect | Evidence | Distorted claim | Closed when |
| :--- | :--- | :--- | :--- | :--- |
| **D1** | **Decay compounds.** `refresh()` applies `exp(-rate × days_since_last_access)` on every refresh, but `last_access` does not advance while a memory is dormant, so the effective exponent is `rate × d(d+1)/2` | Probe: after 7 daily refreshes the observed score is 0.259026; compounding predicts 0.259026, the documented per-day rule predicts 0.486351 (all 7 days match compounding exactly) | Every decay, tier and staleness number in rounds 1–2; `docs/HOW-IT-WORKS.md` §2 describes behaviour the code does not have | Score after N daily refreshes equals `INITIAL_SCORE × exp(-rate × N)` within 1e-9 for N = 1..30, and the doc states the same formula |
| **D2** | **"p95" is the maximum of five samples**, and one of them pays a one-off full-store index rebuild (`_sim_dirty` set on every add) | `pct(0.95)` indexes `int(0.95 × 5) = 4`; measured per-query recall at 10k: **[69.5, 15.5, 14.5, 14.4, 13.9] ms**, of which a cold rebuild is 66.1 ms and a warm similarity scan is 6.9 ms | "the engine's query-aware recall is slower than BM25 at scale" (the round-2 criterion FAIL). Three runs of identical code gave M8 93.4 / 115.8 ms and BM25 59.3 / 47.3 ms | Report p50/p95/p99 over ≥50 warm queries, cold start separately; p95 stable within ±10% over three identical runs |
| **D3** | **The headline leak column mixes 22 records, 16 of which have no retire signal at all** | Reproduced from `raw.json`: 14 curated `contradiction` + 2 synthetic `contradiction` + 3 + 3 staleness; M9's mixed 0.864 = 19/22 exactly | M9 appears to leak 0.864 of superseded facts while it honours retirement correctly — the exact conflation BM-003 forbade | Arm-table leak equals the staleness-suite value per arm (asserted in the reporter), and the two suites are reported separately there too |
| **D4** | **Criteria that were not measured are still scored as failures** | `--tier staleness` (no scale rows) prints `verdict: 3/7` including the scale criterion | Round-2's "4 of 7" is only meaningful for a full run; scoped runs invent failures | `verdict()` takes the measured set; a scoped run prints `n of m measured` |
| **D5** | **`--judge-limit` writes `judge_score = 0.0` for skipped queries and `task_success` averages them** | `evaluate()`'s `on_result`, `metrics.summarize()` (no `judge_skipped` filter) | Any cost-capped LLM run silently understates task success — the run the project is blocked on | With a constant-1.0 judge and `--judge-limit 20`, judged task success equals the uncapped value; `judged_queries` is reported |
| **D6** | **The significance criterion passes if *either* flat arm is beaten** | `sig = next(row for row in pairs if row["arm"] in FLAT_ARMS …)`; the report attributes the PASS to M2 (p=0.016) while M1 flat/FIFO scored **higher** than the hero (0.558 vs 0.534, p=0.625) | "Significantly better end-to-end score than a flat baseline" | Both flat arms must be beaten; both diffs and p-values printed |
| **D7** | **The budget comparison is between a packer and a truncator, not between two stores.** Every baseline fills the budget with full text via `common.pack_blocks`; the engine renders a detail ladder and packs by value per character. `common.downgrade()` exists and is called from nowhere | `engines.py` `_recall` per baseline vs `_recall_knapsack`/`TIER_DETAILS`; `grep -rn downgrade benchmarks/*.py` → one definition, no callers | "budget efficiency now wins: 1589 vs 1734 chars/hit" | Control arm **M3p = BM25 + the engine's own packer**; the win is restated as allocation unless M8 still wins (§6, W1.1) |
| **D8** | **nDCG and MRR are computed on the packing order**, so the allocator's reordering is scored as retrieval quality | `_recall_knapsack` returns `used` in value-per-char order; `metrics.reciprocal_rank`/`ndcg` read `used_ids` order | "BM25 ranks better (0.732 vs 0.606)" | Pre-packing rank order is recorded and both orders reported (§6, W5) |
| **D9** | **`retire()` does not invalidate the similarity index** (no `_sim_dirty`), so every other memory's IDF is stale until some later add | `retire()` sets `retired`/`superseded_by`/`_touched` only; `_build_similarity` skips retired memories | M9/M10 supersession results | `retire()` invalidates; probe asserts incremental == full rebuild |
| **D10** | **The invocation SKILL.md documents is not the configuration the report measures.** `recall [--budget 2200]` passes no question, so the engine runs query-blind; and the skill's `add` examples never set `--category`, so every skill-written memory is `general` (0.030/day) rather than the `preference` (0.010) the docs advertise | Probe: without `--query` the context keeps insertion order; with it the query-relevant memory leads. `recall --pure --query …` silently ignores the query. `recall --pure` is an argparse error (the flag is global). `list`/`stats` are undocumented | The product's front door | Every SKILL.md command runs verbatim (scripted), the documented default has a row in RESULTS, `--category` is documented, and `--pure --query` either errors or is documented |
| **D11** | Unsupported wording in user-facing docs | "the query-blind engine is the fastest recall in the suite (10.6 ms)" — flat/FIFO is 0.60 ms; "every figure is taken from the *committed* RESULTS.md" — `benchmarks/` is untracked; tier ladder "so a large store still fits a small budget" vs LoCoMo's ~0 task success for every arm; HOW-IT-WORKS says decay is by "age" when the code uses time since last *use*; SECURITY.md "no network calls"/"sanitized inputs" (the harness downloads LoCoMo and calls the API; nothing sanitizes) | §9 register | Claims register empty of UNSUPPORTED, checked by script |
| **D12** | A literal `%%` leaks into generated criteria (`"under 20%% of superseded facts"`) in `RESULTS.md` and `raw.json` | Committed report — and still generated today: `run_bench.py:298` | Cosmetic but user-facing | Reworded in the generator **and** the report regenerated (`grep -c '20%%'` → 0) |
| **D13** | **An update ≥ 0.9 similar to a *retired* memory is absorbed by it and lost.** `retire()` leaves the memory in the duplicate index, so `add()` collapses the correction into the retired entry: the new text is never stored, the entry stays retired, recall returns nothing | Probe: `add(A)` → `retire(old)` → `add(B)` with B = A and one word changed (Jaccard 0.900) → `add` returns the retired id, `B` absent, `recall(...).used_ids == []` | The documented correction workflow (`retire`, `--supersedes`); the staleness suites keep their pairs **below** 0.9 on purpose, so the benchmark cannot see it. The new memory is registered in `test_engine.py` as `KNOWN DEFECT D13` | Retired memories are excluded from candidate generation (or `add` refuses to collapse into one); the probe stores and recalls the update; the registry line flips to `RESOLVED` |
| **D14** | **An update ≥ 0.9 similar to a *live* memory discards the newer wording** — the id returned is the old one and the new text is gone, with no signal | Probe: same pair without `retire` → `add` returns the old id, `"production"` absent from the stored content | "Dupes auto-collapse" is the documented behaviour, but a changed value lives in the same similarity region as a restatement, so the front door cannot tell them apart | `add` keeps an update (or returns an action marker) and `SKILL.md` documents the threshold; a pure restatement still collapses to one entry |
| **D15** | **A scoped run overwrites the committed report.** `run_bench.py` writes `benchmarks/RESULTS.md` for *any* run, so `--tier staleness` (a documented command) replaces the published benchmark with a partial one | Probe in a throwaway copy: `--tier staleness --skip-scale` → `RESULTS.md` rewritten, **152 lines** against the committed report's **172**; the real repo's md5 survived only because the test ran in the copy | Every published table, and the reproducibility claim itself | A scoped run writes a derived artefact (`RESULTS-<tier>.md`) or requires `--report`; the committed report's md5 is unchanged by a scoped run |
| **D16** | **Judge errors are scored as failures and reported nowhere.** `on_result` catches `JudgeError` and writes `judge_score = 0.0`; no run publishes a judged/skipped/errored count | `run_bench.py` `evaluate.on_result` | Any run that loses the API mid-flight reports a *lower* task success rather than an incomplete run | `judged`/`skipped`/`errored` counted and printed, excluded from `task_success` (same fix as D5) |
| **D17** | **A hidden ranking parameter**: `similarity_scores()` truncates the query vector with a literal `[:48]` inside the function body — not in the constants block, not marked `ASSUMPTION`, absent from the tuning inventory | `scripts/myelinate.py`, `sorted(q_vec.items(), …)[:48]` | Every similarity score, and therefore ranking, nDCG and the whole `PRIOR_WEIGHT` result | Hoisted to `QUERY_TERM_LIMIT` with an `ASSUMPTION` note and listed in `TESTING.md` §5; top-10 ids unchanged for a fixed corpus |
| **D18** | Small holes: `pin()` will pin a retired memory (score 1.0, still hidden); `_prune()` cannot honour `--max-entries` without enough archived entries and reports no shortfall; the CLI's `recall` calls `save()`, so a read rewrites the store; `stats._exact_two_sided_p` never reads `w_minus` | Code inspection (`myelinate.py` `pin`/`_prune`/`main`, `stats.py`) | Hygiene; none of them touches a published number | One assertion each, in `test_engine.py` / `stats.py` |
| **D19** | **The constant-sweep instrument had never run.** `tune_probe.py` unpacked `evaluate()`'s `(records, ledgers)` return as a bare dict and died with `TypeError: tuple indices must be integers` on every invocation, so the `PRIOR_WEIGHT` curve the docs quoted had never been produced by the tool that produces it | `benchmarks/tune_probe.py`; the front page's "must be re-run before it is quoted" note | The sweep that decides the headline ranking constant, and the difference between a deferral and a crash | Fixed in round 5; the re-run's internal control, re-based on `LEGACY_PRIOR_WEIGHT` because `M8` is pinned, then held — and it decided the constant |
| **D20** | **The canonical full run could not be finished on hardware slower than CI.** The 10,000-memory scale probe alone outlasts a short command timeout, and `M12`'s unbounded ceilings make that single row cost ~213 s (137.9 s ingest, 75.7 s refresh against ~5 s and ~3 s) | `benchmarks/run_bench.py` and the scale tier it drives | The published report could not be regenerated at all on a slower host, so the committed columns stayed stale while the code moved | Fixed in round 5.1: bounded `--phase queries` / `--phase scale` passes, an identity-checked `--state` file that refuses a mismatched pair, per-arm persistence, and the assembly recorded in a `phases` field. The default is still one process |

---

**Round-4 status for this table.** **Closed and asserted:** D1, D3, D4, D5, D6, D8, D9, D12, D13,
D14, D15, D16. **Partly closed: D2** — warm recalls are separated from cold with p50/p95/p99 plus a
`cold_ms` mean and the scale figures are the median of three timed passes, but the criterion itself
is reported as `INFORMATIONAL` because two identical runs flipped its sign on this host. **Answered
rather than patched: D7** — the control M3k beats every engine arm on hit rate and on characters per
hit, so the budget win is allocation. **Not re-checked in this pass:** D10, D11.

**Round-5 and 5.1 status for this table.** **D17 is closed** — the hidden `[:48]` query-term cap is
now `QUERY_TERM_LIMIT = 48`, marked `ASSUMPTION` and listed in `TESTING.md` §5, with the value
unchanged. **D19 and D20 are new and fixed** (rows above): the sweep instrument that had never run,
and the full run that could not be finished on slower hardware. **Round 5 also lowered the shipped
`PRIOR_WEIGHT` to 0.0** on a criterion frozen in advance (`docs/FIX-PLAN.md` F18, passed on hold-out
seeds 3–4), and **the committed report was regenerated** in 5.1 so `benchmarks/RESULTS.md` carries
the round-5 columns instead of the round-4 ones. **Still open:** of D18, `_prune()`'s shortfall
reporting, `recall` saving the store on a read, and the unused `w_minus` argument; and D11's claims
register, which no script yet checks.

---

## 4. Board rulings this plan needs

The pods disagreed in five places. Each needs one of the existing board
meetings' verdict applied before work starts.

* **BM-006 — the decay bug is a protocol change, not a silent fix.** Engineering:
  fix it and move on. Research: it invalidates every decay-dependent number in
  rounds 1–2, including the R7 gist decision that was made *because* everything
  decayed to archived. **Proposed verdict:** fix it, label it, re-measure every
  decay-dependent table, and mark rounds 1–2 decay figures superseded in
  `RESULTS.md`. No silent arithmetic fix.
* **BM-007 — the budget win may be the packer.** Product: keep saying budget
  efficiency wins. Research: the two systems do not share a packer, so the claim
  is unattributable. **Proposed verdict:** run M3p first; if BM25 + the engine's
  packer matches M8, the README claim becomes "allocation", not "store".
* **BM-008 — the significance criterion.** Research: it passed on one of two
  flat arms while the other beat the hero. **Proposed verdict:** require both,
  print both.
* **BM-009 — is "decay retires stale facts" to be fixed or retired?** Engineering:
  a score floor will flip it. Research: a floor trades long-horizon recall for
  staleness (the `continuity` suite asks day-0 facts at day 45) and the current
  criterion is satisfied by retrieving *nothing* — M0, M2 and M6 all "pass" it
  today with `hit_rate_decay_only` 0.000. **Proposed verdict:** gate every leak
  criterion on a retrieval floor (`hit_rate_decay_only ≥ 0.9 × flat`), assert that
  M0 fails it, and if leak is still > 0.5 after D1 is fixed, formally retire the
  decay claim and ship contradiction-detection (auto-retire on a conflicting
  same-subject update) as the labelled capability instead. BM-003 already says a
  supersession win is never a decay win; this says the converse too.
* **BM-010 — what may be claimed publicly.** No claim ships unless a row in
  `benchmarks/RESULTS.md` supports it; task-success claims are blocked until a
  language model judges, because the oracle rewards evidence containment and the
  top two arms differ by 0.010 (TF-IDF 0.563 vs 0.553).

**Round-4 outcomes.** **BM-006 applied:** the decay bug is fixed, the report regenerated under the
new rule version, and no affected figure is compared across the change. **BM-007 answered:** the M3p
and M3k controls show the win is allocation (see §2). **BM-008 applied:** the significance criterion
requires both flat arms and prints both diffs and p-values. **BM-009 not executed, and it is the one
ruling this round leaves owed:** the decay-only leak is still **1.000** for the hero, so the claim has
neither been proven against a retrieval floor nor formally retired, and the round-4 measurement says
the current answer is no. **BM-010 still stands:** no task-success claim ships — a free-tier Gemini
key now exists and the judge is integrated, but no harness-level model-judged run has completed, so no
table is labelled LLM-judged.

---

## 5. The plan

Sizes are rough: S = an afternoon, M = a day, L = multi-day. Every acceptance
criterion is pre-registered here and must not be adjusted after seeing numbers.

### Gate 0 — measurement integrity (blocks everything else)

| ID | Work | Owner | Files | Acceptance (from → to) | Size |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W0.1** | Fix compounding decay (D1): decay the delta since the last decay, or store `score_at_access` and decay it as a pure function | Engineering (engine) | `scripts/myelinate.py`, `docs/HOW-IT-WORKS.md` | probe: `score(N days) == 0.60 × exp(-rate × N)` for N = 1..30, err < 1e-9; doc formula matches | S |
| **W0.2** | Honest latency (D2): ≥50 warm scale queries, cold start reported separately, p50/p95/p99; also report ingest across three runs | Research (harness) | `benchmarks/run_bench.py`, `benchmarks/synthetic.py` | p95 varies < ±10% across three identical runs; M8 p95 < BM25 p95 on the same corpus | M |
| **W0.3** | Un-mix the leak column (D3) and kill `%%` (D12) | Research (harness) | `benchmarks/run_bench.py`, `benchmarks/metrics.py` | arm-table leak == staleness-suite leak per arm, asserted; separate `contradiction_leak_rate` (16 records, today 1.000 for every arm) | M |
| **W0.4** | Score only what was measured (D4) | Research | `benchmarks/run_bench.py` | `--tier staleness` prints `n of m measured`; no phantom FAIL | S |
| **W0.5** | `--judge-limit` must not fake zeros (D5) | Research | `benchmarks/run_bench.py`, `benchmarks/metrics.py` | constant-1.0 stub judge + `--judge-limit 20` → judged task success equals uncapped | S |
| **W0.6** | Both flat arms or nothing (D6) | Research | `benchmarks/run_bench.py` | criterion requires M1 and M2; both diffs/p printed | S |
| **W0.7** | Invalidate the index on retire (D9) | Engineering | `scripts/myelinate.py` | probe: incremental scores == full rebuild after a retire | S |
| **W0.8** | **Engine regression suite** (69 checks when delivered, **139** now) — the engine had *no* automated check, which is how the decay bug and the stale index survived two rounds. **Delivered:** `benchmarks/test_engine.py`, 69 checks, green, with a `known_defect()` registry | Engineering | `benchmarks/test_engine.py` | **MET, and green with an empty registry** — `python3 benchmarks/test_engine.py` now prints `engine ok: 139 checks` with **no** `KNOWN DEFECT` line: W0.1, W0.7, D13 and D14 are asserted for real, while the `known_defect()` mechanism stays in the file so a new tracked defect can be registered; the metric and verdict fixtures remain W0.9–W0.10 | M |
| **W0.9** | Metric and verdict fixtures — hand-computed nDCG/MRR/hit/leak, and the decision-rule logic with fake summaries | Research | `benchmarks/test_metrics.py`, `benchmarks/test_verdict.py` (new) | the three decision-rule defects (one-of-two flat arms, unmeasured criteria scored, `judge_skipped` counted) are caught by the tests | M |
| **W0.10** | Harness invariants — replay determinism and the budget guarantee for all 10 arms | Research | `benchmarks/test_harness.py` (new) | same scenario twice gives identical records; every arm respects the budget on a fixed scenario | S |

**Round-4 status:** W0.1 ✅, W0.3 ✅, W0.4 ✅, W0.5 ✅, W0.6 ✅, W0.7 ✅, W0.8 ✅ (139 checks, empty
registry). W0.2 **partly** — the measurement landed (warm/cold separation, p50/p95/p99, a median of
three timed passes), but the criterion is informational because it cannot be scored reproducibly on
this host. W0.9 and W0.10 **still open**, and they matter more now than when they were written: the
harness defects they were meant to catch are fixed in code and pinned by no test.

### Gate 1 — attribution and engine work (after Gate 0)

| ID | Work | Owner | Files | Acceptance | Size |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W1.1** | **Control arm M3p**: BM25 ranked, rendered and packed by the engine's own allocator (the unused `common.downgrade()` is the start) | Research + Engineering | `benchmarks/engines.py` | Outcome A (packer explains it): M3p ≥ 0.825 hit rate **and** ≤ 1589 chars/hit → the claim is restated as allocation. Outcome B: M8 still ahead → the score contributes | M |
| **W1.2** | **Control arm M11**: the recommended configuration with `PRIOR_WEIGHT = 0` and nearby values. **RUN AND DECIDED (round 5).** The round-3 sweep was exploratory and the tool behind it (`benchmarks/tune_probe.py`) was in fact **crashing on every invocation** — it unpacked `evaluate()`'s `(records, ledgers)` return as a dict — so it had never produced a curve at all. The crash is fixed and the sweep re-run (seed 0): dev tiers curated/synthetic/staleness hit rate 0.979 at 0.00 against 1.000 at 0.10-1.00, nDCG@10 0.856 at 0.00 against 0.840 at 0.35; the LoCoMo tier is monotone in the weight — 0.683 at 0.00, 0.567 at 0.10, 0.517 at 0.20, 0.433 at 0.35, 0.433 at 0.50, 0.317 at 1.00 — and the 0.35 row reproduces the committed M8 line, so the internal control holds | Engineering | `scripts/myelinate.py`, `benchmarks/engines.py`, `benchmarks/tune_probe.py` | **Criterion, frozen before the run and met on hold-out seeds 3 and 4:** M11 reached hit rate **0.893/0.893** (>= 0.874), nDCG@10 **0.702/0.697** (>= 0.672), chars/hit **1276/1283** (<= 1336) and task success **0.539/0.539** (>= 0.533); its LoCoMo hit rate is 0.683 against the round-4 engine's 0.433, and it ties the previous leader's hit rate while beating its characters per hit. A labelled M11 arm ships, the engine default is `PRIOR_WEIGHT = 0.0`, the round-4 arms are pinned at `LEGACY_PRIOR_WEIGHT = 0.35` so their published rows stay reproducible, and `docs/ROUND5-STRATEGY.md` records the full result and what is still open (nDCG and LoCoMo) | M |
| **W2** | Latency: inverted postings for similarity, rebuild moved into `refresh()`, no per-query O(n) scan | Engineering | `scripts/myelinate.py` | scale p50 ≤ 8 ms and p95 < BM25 at 10k; hit rate ≥ 0.825 and top-10 ids identical to the exhaustive implementation on 200 queries | L |
| **W3** | Ingest: lazy closed-form decay, `_cluster` off the refresh hot path (nothing reads `mem.cluster`), tokenise once per `add` | Engineering | `scripts/myelinate.py` | ingest < 1.0 s per 10k (from 4.4–5.4 s), `refresh_total_s` < 0.1, **zero** metric change | M |
| **W4** | Resolve the decay claim per BM-009 | Engineering + Research | `scripts/myelinate.py`, `benchmarks/synthetic.py`, `docs/REMEDIATION.md` | leak < 0.50 **with** `hit_rate_decay_only ≥ 0.9 × flat`, and the negative control (M0) fails the criterion; otherwise the claim is formally retired and auto-retire supersession ships as its own arm | L |
| **W5** | Report ranking separately from packing (D8): record pre-packing rank order; report nDCG both ways | Research | `benchmarks/common.py`, `benchmarks/metrics.py` | both orders in RESULTS; criterion added: nDCG@10 within 0.05 of the best semantic arm | M |
| **W6** | Statistical power: `--seeds a,b,c`; parameterise the staleness generator (≥20 stale-bearing queries); print `n` and the `2/2ⁿ` floor beside every statistic and mark groups below the Holm floor `NOT TESTABLE` (n ≥ 9 with 9 comparisons) | Research | `benchmarks/run_bench.py`, `benchmarks/synthetic.py`, `benchmarks/stats.py` | staleness verdict decided on a pooled mean over ≥5 seeds with the per-seed range shown | M |

**Round-4 status:** W1.1 ✅ — it shipped as **three** controls rather than one (M3t BM25 + truncated
text, M3p BM25 + the engine ladder, M3k BM25 + the engine packer) and answered Outcome A: M3k reaches
hit rate 0.893 against the 0.825 the criterion named, at 1296 characters per hit, so the win is
restated as allocation. W5's D8 half is **done** (the rank order is recorded and both orders are
reported); its additional criterion is not. W1.2, W2, W3, W4 and W6 are **open**, and W4 is now the
one that matters, because the round-4 measurement is the input it was waiting for.

### Gate 2 — product, claims and distribution

| ID | Work | Owner | Files | Acceptance | Size |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W7** | Make the skill's interface truthful (D10): the documented recall must pass the question, `--category` documented, `--pure` placement fixed, `list`/`stats` documented, `--pure --query` errors or is documented | Product | `SKILL.md`, `README.md` | every SKILL.md command runs verbatim against the engine (scripted check); the documented default configuration has a RESULTS row | M |
| **W8** | Claims register → corrections (D11), then a scripted guard that every quantitative README claim matches a RESULTS row | Product | `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `docs/HOW-IT-WORKS.md` | register empty of UNSUPPORTED; the guard runs in the bench's self-check | M |
| **W9** | LLM-judged run (was blocked on a key; the Gemini judge is now integrated, so this is a run away rather than a key away — still unrun) | Research | `benchmarks/run_bench.py`, `benchmarks/judge.py` | curated + synthetic tiers only, cost-capped, `judged_queries` reported; numbers labelled LLM-judged | S, still unrun |
| **W10** | Positioning + a "choose this if" table whose every cell maps to a report row; SKILL front-matter description naming only supported capabilities | Product / S&M | `README.md`, `docs/CHOOSING.md`, `SKILL.md` | no cell without a source row; no capability in the trigger description that lacks a tested path | M |

---

**Round-4 status:** W9 is **no longer key-blocked** — the Gemini judge is integrated, live-verified on
two real calls, and reachable with `--judge gemini` or `--judge llm` — but it **has not run**: the
free-tier quota was exhausted during verification, so no table in the report is labelled LLM-judged.
W7, W8 and W10 were not re-checked in this pass.

---

## 6. Sequencing and the round-3 definition of done

```
Gate 0 (W0.1–W0.10)  ──►  Gate 1 (W1.1, W1.2, W2, W3, W5, W6)  ──►  re-run  ──►  Gate 2 (W7, W8, W10)
                                     └── W4 depends on W0.1 and W1.2
```

W0.8–W0.10 (the test suite from the companion review
[TESTING.md](TESTING.md)) land inside Gate 0 on purpose: the engine has no
automated check at all today, so every Gate 1 change would otherwise be guarded
by nothing but the re-run. The companions for the tuning half of that review are
W1.2 (the `PRIOR_WEIGHT` sweep) and W6 (seeds and power); the ranked sweep list
is TESTING.md §5.

W1.1 runs early in Gate 1 because its outcome decides what the whole round is
allowed to claim. W9 can run any time the key exists.

**Done means:** Gate 0 closed; the M3p and M11 attribution experiments reported (both shipped; M11 passed its frozen criterion on hold-out seeds 3-4 in round 5, and M12 measured the candidate caps as immaterial);
the decay claim either proven with a retrieval floor or formally retired; every
new criterion pre-registered before its run; and no README sentence without a
supporting row in the report. Round 3 does **not** require beating semantic
retrieval — it requires knowing which component does what.

**Round-4 status.** Gate 0 is substantively closed and the first Gate 1 item is answered, which is the
point the sequence was designed to reach: the engine's claims can now be decided with instruments that
do not manufacture a verdict. What is **not** done: the fixture files (W0.9/W0.10), the seed and power
work (W6), every engine speed-up (W2/W3), the W1.2 confirmatory run, and W4/BM-009 — the decay claim is
still unresolved, and on these numbers the current answer is no. The reframing above is confirmed,
though: the value the measurements keep finding is allocation and consolidation, not ranking.

---

## 7. Delegation plan for round 3

* **Pods of 2–4, not 16.** The pods above were sized to the work: four analysts
  for one read-only review of a 60-file repo, each with a different angle
  (engine / harness / claims / competition). Adding pods to that would have
  produced overlap, which is how you get two analysts "verifying" the same fact
  with the same blind spot.
* **One writer per file, always.** Gate 0's harness items (W0.2–W0.6) all touch
  `benchmarks/run_bench.py`, so they are **one** workstream owned by one hand,
  even though five rows are listed. The engine items (W0.1, W0.7, W2, W3, W4)
  all touch `scripts/myelinate.py`: they must be serialised, not parallelised,
  and each lands as its own ablation arm so it stays attributable.
* **What parallelises safely:** W9 (needs a key, touches nothing else), W10
  (docs), and the read-only verification pod that re-checks a landed change.
* **A second review pod of three ran after this plan was written**, on briefs that
  were independent of each other: testing coverage, constant provenance, and
  whether the metric implementations compute what their names claim. It produced
  [`docs/TESTING.md`](TESTING.md) and two workspace deliverables — the engine
  suite (W0.8, delivered) and the sweep harness (`benchmarks/tune_probe.py`) whose
  first run is the W1.2 evidence above. The three reviewers were read-only by
  brief; one of them wrote files anyway, and every claim it made that reached a
  document was re-derived by the coordinator before publication.
* **Standing rule from BM-002/BM-004:** the harness and the criterion list are
  frozen and committed *before* the arms are added; no criterion is edited after
  a run, and the hero arm is never swapped.

---

## 8. Risks and kill criteria

| Risk | Mitigation |
| :--- | :--- |
| W0.1 changes three rounds of numbers and may make the engine look *worse* on staleness | Expected and pre-registered: leak is re-measured after the fix, never compared across it (BM-006) |
| W1.1 may show the budget win is the packer, not the store | That is the point. The product then ships as a budget allocator + consolidation policy paired with a lexical ranker |
| Every latency criterion is currently decided by one cold query | W0.2 makes the measurement stable before W2 claims an improvement; today the criterion flips between runs |
| R2 already found that reinforcement rewards proximity | W1.2 sizes the *ranking* value of the score; no new learning signal until that is settled |
| Single seed, 3 hand-written staleness facts | W6 parameterises the generator and adds seeds; until then every staleness number is low-power and is labelled so |
| `--pure`-style silent misconfiguration (D10) | W7 replaces it with a scripted check rather than another doc promise |

**Kill criteria.** If, after Gate 0 and W1.1/W1.2, the engine's ranking is still
worse than plain cosine *and* the budget win is reproducible by any ranker with
the same packer, then myelination should be demoted from a retrieval claim to an
allocation/consolidation feature, and `docs/REMEDIATION.md`'s remaining items
(R3, R6, R8b, R9) should be re-scoped to the allocator rather than the ranker.

---

## 9. Reproduce / verification log

Everything this plan asserts, in the order it was checked. All commands from the
repo root, Python 3.10.12, no API key set. **Round 4 note:** a free-tier `GEMINI_KEY` is now
configured in the workspace; `--judge auto` still selects the offline oracle, so every command below
reproduces the same offline numbers it did in round 3.

```bash
# Full benchmark — reproduced the round-2 tables exactly (4 of 7, M8 0.825, 1589 chars/hit)
# Round 4: 4 of 6 scored criteria passed, 1 informational (scale, not scored);
# M8 0.835 hit rate, 0.639 nDCG@10, 1572 chars/hit
python3 benchmarks/run_bench.py                       # about 50 s now, with 13 arms

# Engine regression suite — 69 checks then; 139 now, and no KNOWN DEFECT line
python3 benchmarks/test_engine.py

# Constant sweep — never writes the report; the 0.35 row must reproduce M8 exactly
python3 benchmarks/tune_probe.py

# D1 — decay compounds (observed matches d(d+1)/2, not d)
python3 - <<'PY'
import sys, math; sys.path.insert(0, "scripts")
from myelinate import MyelinatedMemory, CATEGORY_DECAY_PER_DAY as R, INITIAL_SCORE as S
m = MyelinatedMemory(in_memory=True); t0 = 1700000000.0
m.add("The deploy target for the web service is the staging cluster in eu-west.", now=t0)
mid = list(m.memories)[0]
for d in range(1, 8):
    m.refresh(now=t0 + d*86400)
    print(d, m.memories[mid].score, S*math.exp(-R["general"]*d), S*math.exp(-R["general"]*d*(d+1)/2))
PY
# day 7: observed 0.259026 == compounding 0.259026 (!= documented 0.486351)

# D2 — "p95" is one cold query in five (10k store, M8 configuration)
#   per-query recall ms: [69.5, 15.5, 14.5, 14.4, 13.9]; cold rebuild 66.1 ms; warm scan 6.9 ms
#   three runs of the same code: M8 93.4 / 115.8 ms, BM25 59.3 / 47.3 ms

# D3 — the 22 stale records, 16 with no retire signal
python3 - <<'PY'
import json, collections
d = json.load(open("benchmarks/results/raw.json"))
stale = [r for r in d["records_by_arm"]["M9 myelinated +supersession"] if r.get("stale_ids")]
print(collections.Counter((r["source"], r["kind"]) for r in stale))
PY
# 14 curated contradiction + 2 synthetic contradiction + 3 + 3 staleness == 22; M9 mixed = 19/22 = 0.864

# D4 — a scoped run invents failures (historical: it now reports n of m scored, writes
# RESULTS-staleness.md instead of the committed report, and marks unmeasured rows NOT MEASURED)
python3 benchmarks/run_bench.py --tier staleness     # prints "verdict: 3/7"

# D7 — packing lives only in the engine
grep -rn "downgrade" benchmarks/*.py scripts/*.py    # one definition, zero callers

# D10 — documented vs measured configuration
python3 scripts/myelinate.py --store /tmp/p.json add --content "Deploy target is the staging cluster."
python3 scripts/myelinate.py --store /tmp/p.json add --content "The user prefers concise replies."
python3 scripts/myelinate.py --store /tmp/p.json recall --budget 2200                          # query-blind, insertion-ish order
python3 scripts/myelinate.py --store /tmp/p.json recall --query "where do we deploy?"          # query-relevant memory leads
python3 scripts/myelinate.py --store /tmp/p.json --pure recall --query "where do we deploy?"   # query silently ignored
python3 scripts/myelinate.py recall --pure           # argparse error: --pure is global
```

**Disclosure.** The coordinator re-ran the full benchmark to verify these claims,
and re-running it *rewrote* `benchmarks/RESULTS.md` and `benchmarks/results/raw.json`
from the same code and seed. Every table is identical to the round-2 report
except the noisy latency and ingest figures (M8 scale p95 93.4 → 115.8 ms, ingest
4.4 → 5.4 s, BM25 p95 59.3 → 47.3 ms); the README has been realigned to that
report, and D2 is precisely why those figures move.

**Round-4 regeneration.** The report was regenerated after the engine fixes, under decision-rule
version 3, so the figures quoted throughout the round-3 text above (M8 0.825 hit rate, 0.606 nDCG,
1589 chars/hit, 4 of 7) are superseded by the current ones (0.835, 0.639, 1572, 4 of 6 scored). They
are left in place as the record of what round 3 measured; nothing is compared across the change (BM-006).

No source file was modified by this plan.

---

## Addendum 2026-10-08 — criterion R10 pre-registered before its run: the dense arm on LoCoMo

Per the standing rule above (BM-002/BM-004): this criterion is frozen **before**
`benchmarks/run_bench.py --tier locomo --skip-scale --network` produces any number, and it will
not be edited after seeing them.

**Question** (`docs/ROUND5-STRATEGY.md` §6, lever 2). The shipped engine's remaining retrieval gap
is concentrated in the LoCoMo tier (0.683 against M3k's 0.717 on hold-out seeds 3–4), which is
paraphrastic and multi-session — exactly where term-overlap scoring is weakest and where a dense
arm should pay. Does semantic recall close that tier?

**Arms.** One scoped run: `M5 semantic/dense-embeddings` (NVIDIA NIM
`nvidia/nemotron-3-embed-1b`, key present in the environment) against the shipped
`M11 myelinated +lexical ranking` and the control `M3k BM25 +engine packer`, all measured on the
same scenarios in the same run. Offline oracle judge, 2200-character budget, seed 0, default
LoCoMo limits (3 conversations, 20 queries each). Committed-report LoCoMo figures are context
only; the decision uses the within-run comparison.

**Decision rule, fixed now:**

- **closes the tier** — M5 LoCoMo hit rate ≥ M3k's within-run hit rate **and** M5's within-run
  characters per hit ≤ M11's;
- **moves toward closing** — M5 LoCoMo hit rate > M11's within-run hit rate, but not the above;
- **does not close** — otherwise.

Every column measured for all three arms is reported whatever the outcome, including nDCG@10 and
 task success, and a loss is kept.

**Transport caveat, fixed before the run.** The M5 arm embeds in batches of 32 (`EMBED_BATCH`)
instead of one request per memory: the single-item transport cannot fit this tier inside one
bounded command (measured ≈ 410 ms per single-item request; 1451 ingest + 60 query requests
≈ 10 minutes). Batched vectors are not bit-identical to single-item ones — measured on a
three-text probe before this entry was written: cosine 1.00000000, maximum absolute difference
6e-8, i.e. ranking-identical to float noise. `M5` has never appeared in the committed report
(no key during the canonical run), so no published number moves; the run's own report is scoped
(`raw-locomo.json` / `RESULTS-locomo.md`) and cannot overwrite the committed artifacts.

### R10 result — recorded after the run (2026-10-08)

Command: `python3 benchmarks/run_bench.py --tier locomo --skip-scale --network` (exit 0; judge
`auto` = offline oracle; 60 queries per arm — 3 conversations × 20; seed 0). Evidence:
`benchmarks/results/raw-locomo.json`, `benchmarks/RESULTS-locomo.md`.

| arm | task success | hit rate | nDCG@10 | nDCG@10 packed | chars/hit |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `M5 semantic/dense-embeddings` (NVIDIA NIM) | **0.133** | **0.833** | **0.573** | 0.573 | **2629** |
| `M3k` BM25 + engine packer *(control)* | 0.017 | 0.717 | 0.348 | **0.375** | 3064 |
| `M11` myelinated + lexical ranking *(shipped)* | 0.000 | 0.683 | 0.316 | 0.317 | 3213 |
| `M4` semantic/TF-IDF *(lexical semantic, context)* | 0.050 | 0.600 | 0.317 | 0.317 | 3648 |

**Decision, by the frozen rule: closes the tier.** M5's hit rate 0.833 ≥ M3k's 0.717 **and** its
2629 chars/hit ≤ M11's 3213. Both conditions hold, so this is the stronger outcome, not merely
"moves toward closing". The paraphrase hypothesis the run was built to test is confirmed in the
same data: the dense arm's 0.833 against the TF-IDF semantic arm's 0.600 is the term-overlap gap
on this tier, and it is large.

What this result does **not** say:

- It compares retrievers, not engine settings. The shipped engine (`M11`) is unchanged and still
  trails both controls on this tier (0.683); nothing here licenses an "engine beats" claim.
  The open engineering question is now concrete — dense ranking *inside* the engine (or dense
  retrieval with the engine's packing) is the pre-registered next lever.
- One scoped run, one seed, 60 queries, offline oracle judge. It answers the pre-registered
  question; it is not a replacement for the committed report's protocol.
- `M5`'s latency columns are transport-bound (network embeddings, ~483 ms p95) and its add
  latency is lumpy by construction (a ~1.2 s flush every `EMBED_BATCH` adds, the tail at the
  first recall). They are not engine costs.

---

## Addendum 2026-10-08 — criterion R11 pre-registered before its run: pool recall@k

Frozen before `benchmarks/pool_probe.py` produces any number, and not edited after.

**Question** (`docs/ROUND5-STRATEGY.md` §6, lever 1). `M11`'s ranker and `M4`'s are the same
TF-IDF cosine, yet `M4` records nDCG 0.729 and `M11` 0.699 (committed report). The allocator
accounts for 0.010 of that (`nDCG@10 packed` 0.689). The rest is lost *before* packing — either
the ranker was never handed the gold memory (candidate generation) or it was handed it and
ordered it too low (ordering). `engine.candidate_pool()` exists to decide which.

**Method.** One run of the shipped configuration (`M11 myelinated +lexical ranking`, engine
`MyelinatedMemory` defaults) over all four tiers, seed 0, default LoCoMo limits, 2200-character
budget. At every query, inside the replay timeline, record `candidate_pool(query)` — the ids the
ranker is handed, before scoring — and whether the query's gold `evidence_ids` intersect it
("pool hit", the same any-evidence semantics as the hit rate). `benchmarks/pool_probe.py`, offline,
does not touch the committed artifacts.

**Decision rule, fixed now**, on the overall 206-query gap `pool hit rate − hit rate` (hit rate
from the same replay, so the two are measured on identical queries):

- **≤ 0.05** — the residual loss is **candidate generation**: the ranker never saw the gold, and
  consolidation, duplicate collapsing or the recall_pool cut is where to work;
- **≥ 0.15** — the residual loss is **ordering**: the pool had the gold and the ranking or the
  packing dropped it;
- **between** — mixed; both numbers are reported per tier and nothing is collapsed into one word.

The per-tier table (curated / synthetic / staleness / locomo) is reported in full whatever the
outcome, with the same numbers recomputed for the round-4 arm (`M8`) as context if the probe is
cheap. A loss is kept.

### R11 result — recorded after the run (2026-10-08)

Command: `python3 benchmarks/pool_probe.py` (exit 0; offline; writes no artifact). The probe's
replay reproduces the committed hit rates exactly (`M11` 0.893 all-tier / 0.683 LoCoMo, `M8`
0.835), which is the cross-check that it measures the same run the report describes.

| tier (queries) | `M11` pool hit | `M11` hit | gap | `M8` pool hit | `M8` hit | gap |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| curated (112) | 1.000 | 0.973 | +0.027 | 1.000 | 1.000 | +0.000 |
| synthetic (28) | 1.000 | 1.000 | +0.000 | 1.000 | 1.000 | +0.000 |
| staleness (3 + 3) | 1.000 | 1.000 | +0.000 | 1.000 | 1.000 | +0.000 |
| locomo, public (60) | 1.000 | 0.683 | **+0.317** | 1.000 | 0.433 | **+0.567** |
| **all 206** | **1.000** | 0.893 | +0.107 | 1.000 | 0.835 | +0.165 |

Mean pool size 163 ids (max 600).

**Decision, by the frozen rule: MIXED overall (0.107).** But the per-tier rows, which the rule
says must not be collapsed, make the composition unambiguous:

- **Candidate generation is not the bottleneck anywhere.** Pool hit is **1.000 on every tier for
  both arms** — the ranker is always handed at least one gold memory. Consolidation, duplicate
  collapsing and the `recall_pool` cut are measured out as the cause of the residual loss.
- **The loss is ordering-or-packing, and it lives entirely on LoCoMo.** Everywhere else the gap
  is ≤ 0.027 (mostly 0.000); on LoCoMo it is **+0.317** for the shipped engine and +0.567 for the
  round-4 arm. The remaining lever is therefore the *ordering and packing of a pool that already
  contains the answer*, on paraphrase-heavy multi-session text — consistent with R10, where dense
  ranking closed exactly this tier.

What this does not split: ordering (rank position of the gold before packing) from packing (the
knapsack dropping it at 2200 characters). `nDCG@10` vs `nDCG@10 packed` in the report bounds that
second split (0.010 for `M11`); a per-query pool-vs-packed probe would separate them fully and is
the natural follow-up.

---

## Addendum 2026-10-08 — criterion R12 pre-registered before its run: auto-retire supersession

This is the second half of `docs/FIX-PLAN.md` **W4**'s pre-registered fallback — "otherwise the
claim is formally retired **and auto-retire supersession ships as its own arm**" — frozen before
any measurement of it.

**What ships.** A labelled pair and a fixture suite that can actually fire the mechanism:

- `M13 myelinated +supersession (auto-update off)` — `M9` with `auto_supersede=False`, exactly
  one change, so the auto-update half of supersession is attributable;
- a **`staleness (update)`** suite (3 scenarios, same builder as the existing staleness suites):
  the superseding fact is a **near-duplicate update** — token-set Jaccard ≥ 0.90 with one value
  token changed — arriving as a plain `add`, with **no retire event and no `supersedes` link**.
  The engine's update path (D13/D14) is the only thing that can retire the stale fact. The suite's
  validator asserts the *inverse* of the existing suites' rule (Jaccard ≥ 0.90 **and** token sets
differ), so duplicate collapsing cannot masquerade as handling here any more than it can there.

**Decision rule, fixed now.** On the `staleness (update)` suite: auto-retire supersession is
demonstrated as an attributable capability **iff `M9` stale-leak ≤ 0.20 AND `M13` stale-leak
≥ 0.80** (and `M9`'s hit rate is not below `M13`'s). Anything else is reported as measured, and
the capability claim is not made.

Measured on a scoped staleness-tier run (`--tier staleness --skip-scale`); the committed report
predates the arm and the suite and is not rewritten by this experiment.

### R12 result — recorded after the run (2026-10-08)

Command: `python3 benchmarks/run_bench.py --tier staleness --skip-scale` (exit 0; offline oracle
judge; 9 queries per arm — 3 + 3 + 3). Evidence: `benchmarks/results/raw-staleness.json`,
`benchmarks/RESULTS-staleness.md`.

Stale-leak / hit rate on `staleness (update)`:

| arm | leak | hit |
| :--- | ---: | ---: |
| `M9` myelinated + supersession *(auto-update on)* | 0.000 | 1.000 |
| `M13` myelinated + supersession (auto-update off) | 0.000 | 1.000 |
| `M8` myelinated + knapsack *(no supersession support)* | 0.000 | 1.000 |
| `M1` flat/FIFO, `M3`/`M3t`/`M3p`/`M3k` BM25, `M4` TF-IDF | 1.000 | 1.000 |

**Decision, by the frozen rule: NOT demonstrated.** `M9` ≤ 0.20 holds (0.000) but `M13` ≥ 0.80
fails (also 0.000), so the pair does not separate the auto-update switch from anything, and the
capability claim is not made. The negative result is kept.

**Why the rule failed, measured.** The pre-registration's reasoning sentence — that duplicate
collapsing "cannot masquerade as handling here" — was wrong, and the run is what shows it.
`refresh()`'s `_collapse_duplicates` merges **any** pair at Jaccard ≥ 0.90 with the newer wording
winning (`_merge_into`, D14 rule), regardless of `stale_retirement`/`auto_supersede`. On fixtures
built at Jaccard ≥ 0.90 by design, the stale entry is therefore gone after the first session
boundary for every engine arm — `M6` and `M8`, with no supersession support at all, included.
The add-time auto-supersede and the refresh-time collapse reach the same end state (old gone,
newer wording kept), and a day-55 leak metric cannot see the difference between them.

What the run does establish, as a measurement of the shipped engine: **a fact superseded by a
near-duplicate update stops surfacing — leak 0.000 for all eight engine configurations, with the
gold answer kept (hit 1.000) — while every non-engine baseline leaks 1.000.** The mechanism is
near-duplicate consolidation, not the auto-update switch; it is real, it is not attributable to
the switch by this pair, and separating the two would need a query in the add-to-refresh window
or a sub-0.90 pair with a caller signal — a different fixture, pre-registered before measured.

### W4/criterion-5 resolution — the decay-alone claim is formally retired

Applying W4's pre-registered bar (`leak < 0.50` with `hit_rate_decay_only ≥ 0.9 x flat`, negative
control `M0` failing) to the re-measured decay-only suite on this tree: the **bare `M6`
mechanism passes** (leak 0.000, hit 1.000; `M0` fails at hit 0.000) and **every configured
engine (`M8`–`M13`) fails** (leak 1.000). The claim as it applied to the shipped engine —
"superseded facts fade without an explicit retire signal" — is therefore **formally retired**:
the project no longer claims it, decision-rule row 5 stays a recorded FAIL, and what is claimed
instead is the narrower, measured statement above (near-duplicate updates are consolidated away;
explicit `retire`/`supersede` leaks 0.000). The `M6` nuance is recorded rather than promoted:
it is reproducible but does not survive the configuration the project ships.
