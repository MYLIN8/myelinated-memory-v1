# Round 3.5 — Bug-fix plan

**Owner:** head of staff (orchestrator). **Status:** proposed; every number below was produced by a
command in §6, offline, with **no API key set**.

> **Round 4 answers the question this list leaves open:** [`ROUND4-DESIGN.md`](ROUND4-DESIGN.md) shows
> that these eighteen defects reduce to **two root causes** (strength stored instead of derived, and a
> benchmark record with no declared shape), states the one mechanism that replaces them, and measures
> why the decay fix (D1) cannot land alone — it un-archives the whole store and costs 33% more
> characters per memory. Read it before implementing anything below.

This is a chief-of-staff pass: *verify first, then plan*. It re-ran everything the repo can prove
without a network, reproduced the twelve registered defects that are still live, and added
**six new ones** (D13–D18) that the previous reviews did not have. The plan below orders the fixes
by what they cost you if left alone — data loss first, harness honesty second, engine work third.

**Nothing in this pass changed engine or harness behaviour.** The two new defects that the engine
can be tested for (D13, D14) are registered in `benchmarks/test_engine.py`, so they cannot be
forgotten the way the compounding decay was.

**Round-4 status is recorded in §0 below.** The list that follows is kept as the
round-3.5 record, with its evidence; where a defect has since been repaired, the
entry says so.

---

## 0. Round-4 status — what has since been fixed

Round 4 landed Gate P0 and most of Gate P1 in **one** change set, because the
design ([`ROUND4-DESIGN.md`](ROUND4-DESIGN.md)) showed the defects reduce to two
root causes and cannot be fixed piecemeal.

* **Fixed in the engine** (`scripts/myelinate.py`): **D1** — a memory stores
  `(score, score_at)` and its strength is *derived*, so `realize()` is idempotent
  at a fixed `now` and `refresh()` folds only the decay not yet applied; **D9** —
  `retire()` and `pin()` invalidate the similarity index through one `_touch()`
  funnel; **D13** and **D14** — an update at 0.90+ Jaccard with a *different*
  token set is stored as a new memory and the older entry is retired with
  `superseded_by`, while an identical token set still collapses as a restatement
  (gated by `auto_supersede=True` so a later arm can attribute it); **D18's
  `pin()`** — pinning a retired memory now returns `False` instead of
  resurrecting it as a hidden 1.0. The store schema is version 3, with a migration
  that sets `score_at` from `last_access`, without which a migrated file would
  decay from the epoch.
* **Fixed in the harness** (`benchmarks/run_bench.py`, `benchmarks/metrics.py`):
  **D3** — the merged leak column is gone and the two staleness suites are
  reported separately per arm; **D4** — an unmeasured criterion reports
  `NOT MEASURED` and leaves the denominator; **D5 + D16** — every query carries a
  `judge_state`, and skipped and errored queries are excluded from `task_success`
  and counted in the report header; **D6** — the significance criterion now
  requires **both** flat baselines; **D8** — MRR and nDCG are scored on the arm
  rank order, with the packed order reported beside it (round 5 corrected this:
  the engine arm supplied no pre-packing order, so the harness fell back to the
  packed order and every engine arm's two nDCG columns were identical — see
  §0.1); **D12** — the `%%`
  artefact is gone; **D15** — only the full default run writes `RESULTS.md` and
  `results/raw.json`, while a scoped run writes `RESULTS-<tier>.md` and
  `raw-<tier>.json`.
* **Partly fixed — D2.** Warm recalls are separated from cold with p50/p95/p99
  plus a `cold_ms` mean, and the scale figures are the median of three timed
  passes. Wall-clock latency on this host is still noisier than the effect, though:
  two identical runs flipped the sign of the scale criterion (121.9 ms against
  101.3 ms, then 108.1 ms against 126.4 ms). That criterion is therefore reported
  as `INFORMATIONAL` and excluded from the scored denominator, and the decision
  rule is now **version 3**, printed in the report.
* **D7 is answered, not patched.** The three new allocator controls — **M3t**
  (BM25 + truncated text), **M3p** (BM25 + the engine ladder) and **M3k** (BM25 +
  the engine packer) — share BM25 ranking and vary only how the budget is filled.
  `M3k` reaches hit rate **0.893** against the best engine arm **0.835**, at
  **1296** characters per hit against **1572**, and **0.717** against **0.433** on
  LoCoMo. The engine budget win is its **packer**, not its **store**, and the
  report states that next to the verdict.
* **Still open:** **D10** (the skill invocation drift — not re-checked in this
  pass), **D17** (`QUERY_TERM_LIMIT` is still a literal inside
  `similarity_scores()`), and the remainder of **D18** (`_prune()` shortfall
  reporting, `recall` saving the store, the unused `w_minus` argument).

The published verdict moved with the instruments: **4 of 6 scored criteria
passed, 1 informational, 0 not measured**, and the two honest failures are
decay-alone and *significantly better than **both** flat baselines*.

### 0.1 Round-5 status — what round 5 then fixed

Round 5 was the attribution round: it took the open `PRIOR_WEIGHT` question to its
pre-registered test, and it fixed the instrument that had been unable to answer it.
The full account is [`ROUND5-STRATEGY.md`](ROUND5-STRATEGY.md).

* **F18 was attempted and it PASSES on both hold-out seeds.** The rule this plan
  wrote — *"passes → a labelled M11 ships and the prior is demoted to a
tie-breaker"* — is exactly what was executed, on the criterion frozen in the F18
  row above: hit rate **0.893/0.893** (≥ 0.874), nDCG@10 **0.702/0.697** (≥ 0.672),
  chars/hit **1276/1283** (≤ 1336), task success **0.539/0.539** (≥ 0.533), 206
  queries per arm, offline oracle judge. `PRIOR_WEIGHT` shipped at **0.0**, with
  `LEGACY_PRIOR_WEIGHT = 0.35` pinning `M6`–`M10` and `M12` so the round-4 rows
  stay reproducible; `M11` tracks the shipped default instead of the old constant.
* **The sweep instrument was broken and is now fixed.** `benchmarks/tune_probe.py`
  raised `TypeError: tuple indices must be integers` on every invocation, because
  it unpacked `evaluate()`'s `(records, ledgers)` return as a bare dict. The
  `PRIOR_WEIGHT` curve it reports had therefore never been produced; the re-run,
  its internal control holding, is what backs the F18 decision.
* **F21 (W5/D8) shipped.** `RecallResult.ranked_ids` is set at `recall()`'s single
  return point and `MyelinatedArm._recall` returns it, so `nDCG@10` scores the
  ranker while `nDCG@10 packed` scores the allocator. Both orders are in the
  report and the ranker is within 0.05 of the best semantic arm (`M11` 0.702
  against `M4`'s 0.732), which is this row's acceptance.
* **The candidate caps are measured out as the cause.** `M12` (unbounded
  `MAX_CANDIDATES`/`MAX_POSTINGS_SCAN`/`RECALL_POOL`) reproduces `M10` exactly on
  both seeds, so the ranking deficit was the prior term, not candidate generation.
* **A model judge was added beyond the F26 plan.** `NvidiaJudge` (`--judge nvidia`,
  env `NVIDIA_CLOUD_KEY`, default model `nvidia/nemotron-3.5-lightning-30b-a3b`)
  joins the Gemini and OpenAI providers, and the dense arm can now reach NVIDIA NIM
  embeddings (`nvidia/nemotron-3-embed-1b`, 2048 dimensions). Both paths were then
  verified live — a real answer and grade from the judge, and a real embedding
  retrieved through the arm's recall path — and `--judge nvidia` with no key exits
  2 with a message. **F26 remains unrun in the sense that matters:** no complete
  harness-level LLM-judged table exists, because judging 206 queries is
  network-bound and outran the command budget.
* **F16 / D17 is closed (round 5.1).** The `[:48]` query-vector cap inside
  `similarity_scores()` is now `QUERY_TERM_LIMIT = 48`: named, marked
  `ASSUMPTION`, and listed in `TESTING.md` §5, which is exactly what this row's
  acceptance asked for. The value is unchanged, so the top-10 order is unchanged
  for a fixed corpus.
* **D20 is new and fixed (round 5.1): the canonical full run could not be
  finished on hardware slower than the project's CI runner.** The 10,000-memory
  scale probe alone outlasts a short command timeout, and `M12`'s unbounded
  ceilings make that single row cost ~213 s (137.9 s ingest, 75.7 s refresh
  against ~5 s and ~3 s for every capped arm). The full default run is now
  producible in bounded passes: `--phase queries` replays the query tiers and
  writes a `--state` file, `--phase scale` resumes from it, measures the scale
  probe and renders the report. The state's identity — seed, tiers, budget, judge
  description, arm order, corpus size and the LoCoMo limits — is verified before
  the second phase will render, and a mismatch exits 2 and writes nothing, so two
  different runs cannot be spliced into one artifact; each scale row is persisted
  as it is measured, so a timeout costs only the arm in flight; and the report
  records the assembly in a `phases` field. The default is still a single
  process.
* **The committed report is regenerated (round 5.1).** `benchmarks/RESULTS.md` and
  `benchmarks/results/raw.json` now come from the round-5 code — a full default
  run at seed 0, 15 offline arms, the 10,000-memory scale tier — with verdict
  **4 of 6** scored criteria passed, 1 informational and 0 not measured, and
  `M11` at **0.893** hit rate, **0.699** nDCG@10 (0.689 packed) and **1279**
  characters per evidence hit.
* **Still open from this list:** **F5/F6** (not re-checked), **F12** (informational),
  **F14/F15** (the metric, verdict and harness fixture files), **F19/F20** (the
  scale latency and ingest targets), **F22** (the hold-out run is two seeds, not
  the ≥ 5 the protocol asks for), **F23** (decay-alone still leaks 1.000 for
  `M11`), **F24**, **F25**, and **F26** (no complete model-judged run — the NVIDIA
  judge answers and grades live, and a judged pass makes a real call per arm, but
  judging 206 queries is network-bound and no full run has been scored by a
  model).

---

## 1. Verification log — what was actually run

| Command | Exit | Observed |
| :--- | ---: | :--- |
| `python3 benchmarks/stats.py` | 0 | `stats ok` |
| `python3 benchmarks/engines.py` | 0 | `engines ok` (10 arms) — **round 4: 13 arms; round 5: 15 arms, with the expected list derived from `ARM_ORDER` instead of a literal** |
| `python3 benchmarks/judge.py` | 0 | `judge ok` |
| `python3 benchmarks/stub_llm.py` | 0 | `stub ok` |
| `python3 benchmarks/synthetic.py` | 0 | `synthetic ok: 14 scenarios, 33 queries, 900 scale memories; staleness ok: 3 + 3` |
| `python3 benchmarks/test_llm_judge.py` | 0 | `llm judge ok: 22 assertions` (protocol only, against the local stub) — **round 4: 60 assertions; round 5: 71 assertions, with NVIDIA selection and the offline-`auto` guarantee covered** |
| `python3 benchmarks/test_engine.py` | 0 | `engine ok: 69 checks` + `KNOWN DEFECT W0.1` + `KNOWN DEFECT W0.7` — **round 4: 139 checks, no `KNOWN DEFECT` line; round 5: 170 checks** (W0.1, W0.7, D13 and D14 are asserted for real; the registry stays in the file with zero entries so a new tracked defect can go back in) |
| `python3 -m py_compile scripts/myelinate.py benchmarks/*.py` | 0 | clean |
| CLI walk-through, verbatim from `SKILL.md` (§5) | 0 / 0 / 0 / **2** | the documented `recall` is query-blind; `--pure` before `recall` silently ignores `--query`; `recall --pure` is an argparse error |

Reproduced live defects (each re-derived here, not taken from the register):

| # | Reproduced? | Evidence from this pass |
| :--- | :--- | :--- |
| D1 compounding decay | **yes** | day 7 = `0.259026` (compounded) vs `0.486351` (documented per-day rule); matches `d(d+1)/2` for all of d = 1..30; **day 30 = 0.000001** |
| D3 mixed leak column | **yes** | 22 stale-bearing records = 14 curated + 2 synthetic `contradiction` + 3 + 3 staleness; M9 mixed = 19/22 = 0.8636 |
| D4 scoped run scores unmeasured criteria | **yes** | `--tier staleness --skip-scale` → `3 of 7`, including a `FAIL` on the scale criterion it never measured |
| D7 `downgrade()` unused | **yes** | one definition, **zero callers** |
| D9 stale similarity index | **yes** | retired id still scored after `retire()` (0.6027 before and after); a forced rebuild drops it |
| D10 interface drift | **yes** | see §5 |
| D11 network claims | **yes** | `urllib.request` in `engines.py:441`, `judge.py:185`, `public_locomo.py:68` vs `SECURITY.md` "No network calls" |
| D12 literal `%%` | **yes** | still generated at `run_bench.py:298`, still present in `benchmarks/RESULTS.md` and `results/raw.json` |
| D2 five-sample "p95" | **yes (by code)** | `scale_rows.pct(q)` = `latencies[min(len-1, int(q*len))]`; `scalability_corpus` emits `min(5, …)` queries, so p95 *is* the maximum |
| D5 `--judge-limit` fakes zeros | **yes (by code)** | `evaluate.on_result` writes `judge_score = 0.0`; `metrics.summarize` has no `judge_skipped` filter |
| D6 one-of-two flat arms | **yes (by code)** | `next((row for row in pairs if row["arm"] in FLAT_ARMS and row["significant"] and row["diff"] > 0), None)` |
| D8 nDCG/MRR on packed order | **yes (by code)** | `_recall_knapsack` returns `used_ids` in value-per-char order and the metrics read that order |

**Checked and found sound** (worth knowing before anyone "fixes" them): the budget invariant holds on
both recall paths, with and without a query, at budgets 0/50/500/2200/5000 — `chars <= budget` in all
10 combinations; `save()` is atomic via `os.replace`; `load()` filters unknown fields and marks the
similarity index dirty; `add()` does collapse on write; the probability reasoning behind
`SKETCH_SIZE`/`BAND_ROWS` is correct.

---

## 2. New defects (D13–D18)

### D13 — a corrected fact can be absorbed by the memory it replaces and disappear entirely (P0, data loss)

An update that is **≥ 0.9 Jaccard similar to a *retired* memory** is collapsed into that retired
entry. The new text is never stored, the entry stays retired, and recall returns **nothing at all**.

```
old = add(A)                       # A = "... the staging cluster owned by the platform team for nightly builds"
retire(old)
new = add(B)                       # B = same sentence with "staging" -> "production"  (Jaccard 0.900)
  new == old                      -> True    (the update was absorbed)
  memories[new].retired           -> True
  recall(query="where is the deploy target").used_ids -> []      chars: 0
```

Root cause: `retire()` marks the flag but leaves the memory in `_sk`, `_norm` and the sketch maps, so
`_find_duplicate()` can still return it; `add()` then takes the collapse path unconditionally and
`access()` only bumps a score on a retired entry. (`_collapse_duplicates()` has the same hole: it can
pick a retired memory as the merge winner.)

Why this is P0 and not a curiosity: *retire-then-restate* is the product's own correction workflow
(`retire`, or `add --supersedes`), and the benchmark **structurally cannot see it** — `synthetic.py`
deliberately keeps stale/evidence pairs **below** 0.9 Jaccard "so duplicate collapsing can never be
mistaken for staleness handling". So the suite that justifies the retirement story never exercises the
region where the retirement story destroys the correction.

**Fix.** Filter `retired` out of candidate generation — exclude retired memories in
`_find_duplicate()` *and* in `_collapse_duplicates()` (a one-line predicate each at the candidate/
match step), and treat "the duplicate is retired" as *not a duplicate* so the update is stored as a
new memory. Cheapest correct version: skip when `other.retired`.
**Files:** `scripts/myelinate.py`. **Acceptance:** the probe above ends with `new != old`,
`retired == False`, and `recall(...).used_ids == [new]`; `benchmarks/test_engine.py` asserts it (it
now registers D13 as a known defect, so it flips to `RESOLVED` on the day of the fix).
**Size:** S.
**Status (round 4): fixed.** Retired memories are excluded from candidate generation, so the update is
stored as a new memory and `recall` returns it; `test_engine.py` now asserts the probe above instead of
registering it.

### D14 — an update to a *live* memory is silently discarded (P1)

The same threshold, without retirement: adding a ≥ 0.9-similar sentence keeps the **older wording**
and drops the new one, with no signal to the caller beyond "the id you got back is the old id":

```
o = add(A); n = add(B)      # Jaccard 0.900
  n == o -> True | "production" in memories[n].content -> False
```

"Near-duplicates collapse" is intended, but the intended case is *the same fact restated*; the
contradiction case ("the value changed") lives in the same 0.9+ region, so the front door cannot tell
a restatement from an update. **Fix:** give collapse an explicit rule — when `supersedes` is given, or
when the new text disagrees on a token the old one has, keep the **new** content (recording
`superseded_by` on the old); at minimum, return a machine-readable marker (`{"id": …, "action":
"merged"|"created"|"updated"}`) instead of a bare id, and document the threshold in `SKILL.md`.
**Files:** `scripts/myelinate.py`, `SKILL.md`, README §7. **Acceptance:** the probe reports the newer
wording present after the second `add`; a distinct restatement still returns one entry, not two.
**Size:** S–M. **Note:** changes stored-entry counts (today's bench stores 9 998 of 10 000), so the
full report must be regenerated and labelled (BM-006 protocol) — see §3.
**Status (round 4): fixed.** A 0.90+ pair whose token set differs is stored as an update and the older
entry is retired with `superseded_by`; only an identical token set still collapses as a restatement.
`add` still returns a bare memory id, so the optional machine-readable action marker is **not**
implemented. The full report was regenerated and relabelled under the new decision-rule version.

### D15 — a scoped run overwrites the committed report (P0 for the published artefact)

`run_bench.py` writes `benchmarks/RESULTS.md` unconditionally, whatever `--tier`/`--skip-scale` were
used. Verified in a throwaway copy of the repo:

```
copy:  python3 benchmarks/run_bench.py --tier staleness --skip-scale   -> exit 0
       benchmarks/RESULTS.md rewritten: 152 lines (the committed full report is 172 lines)
       raw.json: 6 queries, 0 scale rows, verdict "3 of 7"
real repo: md5sum unchanged only because the test ran in the copy
```

So `--tier staleness` — a command the docs recommend for the staleness suite — **deletes the
published benchmark** and replaces it with a partial one. **Fix:** write the artefact under a
derived name unless the run is the full default one (`RESULTS.md` only for a full run; otherwise
`results/raw-<tier>.json` + `RESULTS-<tier>.md`), or require an explicit `--report PATH`. Optionally
refuse to overwrite a report produced by a *different* command line.
**Files:** `benchmarks/run_bench.py`. **Acceptance:** a scoped run leaves the committed report's md5
unchanged, and a full run still writes `RESULTS.md`. **Size:** S.
**Status (round 4): fixed.** The rule is as proposed: only a full default run writes `RESULTS.md` and
`results/raw.json`; a scoped run writes `RESULTS-<tier>.md` and `raw-<tier>.json`. The same data-loss
bug in the raw evidence file was closed at the same time.

### D16 — judge failures are scored as failures and reported nowhere (P1, same family as D5)

`evaluate.on_result` catches `JudgeError` and writes `record["judge_score"] = 0.0`. A run that loses
the API halfway through reports a **lower task success** rather than a partial run, and no
`judged_queries` count is published for either that path or the `--judge-limit` path.
**Fix:** fold into the D5 fix — count `judged` / `skipped` / `errored`, exclude them from
`task_success`, and print the counts in the report header.
**Files:** `benchmarks/run_bench.py`, `benchmarks/metrics.py`. **Acceptance:** a stub judge stubbed to
fail on half the queries reports `task_success` equal to the uncapped healthy value plus an explicit
`judge_errors` count. **Size:** S.
**Status (round 4): fixed.** Each query carries an explicit `judge_state` of judged, skipped or
errored; `metrics.summarize` averages only judged queries, and the report header prints
`judged / skipped / errored`. A real 429 during verification produced nine backoff retries and then a
`JudgeError` counted as `errored`, never scored as a wrong answer.

### D17 — a ranking parameter is hidden inside a function body (P2)

`similarity_scores()` truncates the query vector with a literal `[:48]`. It is not in the constants
block, not marked `ASSUMPTION`, not exposed on the CLI, and absent from the tuning inventory — yet it
decides which query terms can contribute to every similarity score, i.e. to ranking, nDCG and the whole
`PRIOR_WEIGHT` story. **Fix:** hoist to `QUERY_TERM_LIMIT = 48` with an `ASSUMPTION` comment, add it
to `docs/TESTING.md` §5 and to the sweep list (sweep 1's neighbours).
**Files:** `scripts/myelinate.py`, `docs/TESTING.md`. **Acceptance:** the constant appears in both
inventories; top-10 ids are unchanged for a fixed corpus (behaviour-preserving hoist). **Size:** S.
**Status (round 4): still open.** The literal is unchanged and the constant is still absent from both
inventories.

### D18 — small correctness and hygiene holes (P3)

* `pin()` will pin a **retired** memory (`reset`-style resurrection: score 1.0, tier active, still
  retired, so still hidden) — should return `False` or un-retire explicitly.
* `_prune(max_entries)` **cannot honour the ceiling** when too few archived entries exist: it removes
  only `archived` candidates and then stops, leaving the store over the limit with no signal.
  Return the shortfall.
* The CLI's `recall` calls `engine.save()` — a read mutates and persists the store (`last_access` is
  untouched, so it is mostly harmless, but it rewrites the file and its mtime on every context
  injection).
* `stats._exact_two_sided_p(ranks, w_plus, w_minus)` never reads `w_minus`.

**Acceptance:** one assertion each in `test_engine.py` / `stats.py`. **Size:** S total.
**Status (round 4): partly fixed.** `pin()` refuses a retired memory and returns `False`, and
`test_engine.py` asserts it. The `_prune()` shortfall, the CLI `recall` that saves, and the unused
`w_minus` argument are unchanged.

---

## 3. The fix plan

Sizes: **S** = under an hour, **M** = an afternoon, **L** = a day or more. Every acceptance criterion
is written before the work and is not adjusted after seeing numbers (BM-004).

### Gate P0 — stop losing data and published artefacts (do this first)

| ID | Fix | Files | Acceptance | Size |
| :--- | :--- | :--- | :--- | :--- |
| **F1** | **D15** — a scoped run must not clobber the committed report | `benchmarks/run_bench.py` | scoped run leaves `RESULTS.md`'s md5 unchanged; full run still writes it | S |
| **F2** | **D13** — retired memories are not duplicate candidates | `scripts/myelinate.py` | the D13 probe: update stored, not retired, `recall` returns it; registry line flips to `RESOLVED` | S |
| **F3** | **D14** — collapse keeps an update, and reports what it did | `scripts/myelinate.py`, `SKILL.md` | newer wording survives; a pure restatement still collapses to one entry; `add` returns an action | M |
| **F4** | **D12** — the `%%` literal | `benchmarks/run_bench.py` | `grep -c '20%%'` is 0 after a full regeneration | S |
| **F5** | **D10** — the interface tells the truth | `SKILL.md`, README §7, `scripts/myelinate.py` | every `SKILL.md` command runs verbatim; the documented `recall` passes the question; `--category` documented; `--pure --query` **errors** instead of silently ignoring the query; `list`/`stats` documented | M |
| **F6** | **D11** — the two false claims | `SECURITY.md` | no "no network calls" / "sanitized inputs"; the three network call sites named and the opt-in flags documented | S |

**Round-4 status.** **F1** (D15), **F2** (D13) and **F4** (D12) landed, so no published artefact is
destroyed and no correction is lost. **F3** (D14) landed as the update/restatement split; the optional
machine-readable action marker was not implemented, so `add` still returns a bare id. **F5** (D10) and
**F6** (D11) were not re-checked in this pass.

Because F2/F3 change how many entries the store keeps, the full benchmark must be re-run **after**
them and every affected table relabelled: stored counts and any dedupe-dependent number are
superseded, never compared across the change (the BM-006 protocol). F1, F4, F5 and F6 are
artefact/doc-only and can land immediately.

### Gate P1 — measurement integrity (this is round 3's Gate 0; blocks all engine claims)

| ID | Fix | Files | Acceptance | Size |
| :--- | :--- | :--- | :--- | :--- |
| **F7** | **D1** — decay the delta since the last decay (`score_at_access`), or make the score a pure function of `days_dormant` | `scripts/myelinate.py`, `docs/HOW-IT-WORKS.md` | `score(N days) == INITIAL_SCORE × exp(-rate × N)` within 1e-9 for N = 1..30; the doc states the same formula; rounds 1–2 decay numbers marked superseded | S |
| **F8** | **D9** — invalidate the similarity index on `retire()` (and on `pin()`) | `scripts/myelinate.py` | incremental scores == a full rebuild after a retire; registry line flips to `RESOLVED` | S |
| **F9** | **D4** — score only what was measured | `benchmarks/run_bench.py` | a scoped run prints `n of m measured` and excludes unmeasured criteria from the denominator | S |
| **F10** | **D5 + D16** — skipped and errored judge calls are not zeros | `benchmarks/run_bench.py`, `benchmarks/metrics.py` | constant-1.0 stub + `--judge-limit 20` gives the uncapped task success; `judged`/`skipped`/`errored` printed | S |
| **F11** | **D6** — both flat arms, both diffs, both p-values | `benchmarks/run_bench.py` | the criterion passes only if M1 *and* M2 are significantly beaten; the report shows both | S |
| **F12** | **D2** — honest latency | `benchmarks/run_bench.py`, `benchmarks/synthetic.py` | ≥ 50 warm queries per arm, cold start reported separately, p50/p95/p99; p95 stable within ±10% over three identical runs | M |
| **F13** | **D3** — un-mix the leak column; assert it | `benchmarks/run_bench.py`, `benchmarks/metrics.py` | arm-table leak == staleness-suite leak per arm, asserted; `contradiction_leak_rate` reported separately (16 records, today 1.000 for every arm) | M |
| **F14** | **W0.9** — metric and verdict fixtures | `benchmarks/test_metrics.py`, `benchmarks/test_verdict.py` (new) | hand-computed nDCG/MRR/hit/leak; F9–F11's bugs are caught by the tests | M |
| **F15** | **W0.10** — harness invariants | `benchmarks/test_harness.py` (new) | same seed → identical records; every one of the 10 arms respects the budget; `downgrade()` is ≤ limit and idempotent | S |
| **F16** | **D17** — hoist `QUERY_TERM_LIMIT` | `scripts/myelinate.py`, `docs/TESTING.md` | constant in both inventories; top-10 ids unchanged | S |

**Round-4 status.** **F7** (D1), **F8** (D9), **F9** (D4), **F10** (D5 + D16), **F11** (D6) and
**F13** (D3) landed and are asserted by the suites. **F12** (D2) landed only in part: cold recalls are
reported separately and the scale figures are a median of three timed passes, but the criterion is still
too noisy to score on this host, so it is reported as `INFORMATIONAL` rather than PASS or FAIL. **F14**
and **F15** (the metric, verdict and harness fixture files) and **F16** (D17, `QUERY_TERM_LIMIT`) are
**still open**.

### Gate P2 — attribution, then engine work (only after P1 is green)

| ID | Fix | Files | Acceptance | Size |
| :--- | :--- | :--- | :--- | :--- |
| **F17** | **W1.1 / D7** — control arm **M3p**: BM25 ranked, rendered and packed by the engine's own allocator (the unused `common.downgrade()` is the starting point) | `benchmarks/engines.py` | Outcome A: M3p ≥ 0.825 hit rate **and** ≤ 1589 chars/hit → the claim becomes *allocation*; Outcome B: M8 still ahead → the score contributes | M |
| **F18** | **W1.2** — control arm **M11** (`PRIOR_WEIGHT = 0`) on **hold-out seeds 3–4**, criterion already frozen: hit rate ≥ 0.874, nDCG@10 ≥ 0.672, chars/hit ≤ 1336, task success ≥ 0.533 | `scripts/myelinate.py`, `benchmarks/engines.py` | passes → a labelled M11 ships and the prior is demoted to a tie-breaker; fails → the existing sweep stays labelled exploration and nothing changes | M |
| **F19** | **W2 / R9** — inverted postings for similarity, rebuild moved into `refresh()` | `scripts/myelinate.py` | scale p50 ≤ 8 ms and p95 < BM25 at 10k; hit rate ≥ 0.825; top-10 ids identical to the exhaustive implementation on 200 queries | L |
| **F20** | **W3 / R8b** — lazy closed-form decay, `_cluster()` off the refresh path, tokenise once per `add` | `scripts/myelinate.py` | ingest < 1.0 s per 10k (from 4.6–5.4 s), `refresh_total_s` < 0.1, **zero** metric change | M |
| **F21** | **W5 / D8** — record pre-packing rank order; report nDCG both ways | `benchmarks/common.py`, `benchmarks/metrics.py` | both orders in the report; a criterion within 0.05 of the best semantic arm | M |
| **F22** | **W6 + D2's power** — `--seeds a,b,c`; parameterise the staleness generator (≥ 20 stale-bearing queries); print `n` and the `2/2ⁿ` floor beside every statistic, `NOT TESTABLE` below the Holm floor | `benchmarks/run_bench.py`, `benchmarks/synthetic.py`, `benchmarks/stats.py` | staleness decided on a pooled mean over ≥ 5 seeds with the per-seed range shown | M |
| **F23** | **W4 / BM-009 + R3** — resolve the decay claim: leak < 0.50 **with** `hit_rate_decay_only ≥ 0.9 × flat`, and the negative control (M0) must **fail**; otherwise retire the claim formally and ship contradiction-detection (auto-retire on a conflicting same-subject update) as its own labelled arm | `scripts/myelinate.py`, `benchmarks/synthetic.py`, `docs/REMEDIATION.md` | as stated; today M0/M2/M6 "pass" the decay criterion only by retrieving nothing | L |
| **F24** | **D18** — the small holes | `scripts/myelinate.py`, `benchmarks/stats.py` | one assertion each | S |
| **F25** | **W8 / D11** — a scripted guard that every quantitative README claim matches a report row | `README.md`, `benchmarks/run_bench.py` | the claims register is empty of UNSUPPORTED | M |

**Round-4 status.** **F17** (D7) shipped — as three controls rather than one, so the allocator can be
separated from the ranking: **M3t** BM25 + truncated text, **M3p** BM25 + the engine ladder, and **M3k**
BM25 + the engine packer. The outcome is Outcome A, cleanly: `M3k` beats every engine arm on hit rate
(0.893 against 0.835) and on characters per hit (1296 against 1572). **F18–F25 remain open**; nothing in
them was attempted in this pass.

**Round-5 status.** **F18** was attempted on the criterion already frozen above and **passed on both
hold-out seeds**, so the rule it wrote — a labelled `M11` ships and the prior is demoted to a tie-breaker —
was executed: `PRIOR_WEIGHT` is now **0.0**, `LEGACY_PRIOR_WEIGHT = 0.35` pins `M6`–`M10`/`M12`, and `M11`
(`0.893` hit rate, `0.702` nDCG, `1276` chars/hit, `0.539` task success on seed 3) becomes the best engine
arm, level with `M3k` on hit rate and below it on characters per hit. **F21** (W5/D8) shipped with it: the
engine's pre-packing order is recorded and reported, so `nDCG@10` and `nDCG@10 packed` finally differ for
the engine arms. **F17's** controls were extended by **M12** (unbounded candidate caps), which reproduces
`M10` exactly — the caps are measured out as the cause. **F19, F20, F22–F25 remain open**, and the sweep
instrument (`benchmarks/tune_probe.py`) itself had to be repaired first: it raised
`TypeError: tuple indices must be integers` on every run. See §0.1 and
[`ROUND5-STRATEGY.md`](ROUND5-STRATEGY.md).

### Gate P3 — blocked on a key (see §4)

| ID | Work | Blocked by |
| :--- | :--- | :--- |
| **F26** | **W9** — the LLM-judged run (curated + synthetic tiers, cost-capped, `judged_queries` reported, numbers labelled LLM-judged) | no longer blocked on a key: the Gemini judge is integrated and a free-tier `GEMINI_KEY` is configured. Still unrun — the free-tier quota was exhausted during verification, so no harness-level LLM-judged table exists |
| **F27** | **M5 dense-embeddings arm** (`--network`) | still `OPENAI_API_KEY` (the embeddings arm is separate from the judge and remains absent from every table) |

---

## 4. The Gemini key: what it unblocks, and what it does not

Round 4 added the **judge integration and a free-tier Gemini key** (`GEMINI_KEY`), so the LLM path is
no longer blocked on the OpenAI key. Two things are nevertheless **not proven** and must not be
claimed:

1. **Task success with a language model.** Every `task_success` figure on the page is the
   deterministic oracle's, which rewards *surfacing* the evidence, not *reasoning* over it. What *is*
   proven is the whole LLM path **up to the model**: `test_llm_judge.py` drives the real
   OpenAI-protocol code against a local stub — 22 assertions covering request shape, JSON parsing,
   response caching, the HTTP-500 path and judge selection — with no key. So the integration is
   verified; the model's judgement is not.
2. **The dense-embeddings arm (M5).** It is absent from every table; `build_notes()` already says so
   and the report's own header repeats it. TF-IDF cosine stands in as the vector-space baseline.

The consequence is a **project-level constraint, not a task**: the top two arms differ by 0.010 judged
task success (TF-IDF 0.563 vs M8/M10 0.553), a gap inside the oracle's noise floor, so per BM-010
**no task-success claim may ship** until a model judges it. Everything in §3 is offline, free and
proven by the commands in §6.

With `GEMINI_KEY` set, the model-judged path is one flag away:

```bash
python3 benchmarks/run_bench.py --judge gemini --tier curated     # real task success, scoped
python3 benchmarks/run_bench.py --judge gemini --judge-limit 200  # capped, and the cap is honest now
python3 benchmarks/run_bench.py --judge llm                       # first available model judge
python3 benchmarks/run_bench.py --network                         # still needs OPENAI_API_KEY
```

Three things the round-4 integration guarantees, all verified live: `auto` and `oracle` stay the
**offline** judge, so a key in the environment can never silently turn the reproducible default run
into a network run; a free-tier key is paced (4 s per request by default, `JUDGE_MIN_INTERVAL` to
change it) with 429/502/503/504 retried under exponential backoff that honours `Retry-After`, and a
hard `JUDGE_MAX_CALLS` budget (400) that raises instead of scoring; and a call that fails is counted
as `errored` rather than averaged in as a wrong answer, so `--judge-limit` is safe now that **F10**
has landed. What is *not* proven is any task-success number under a model judge: two live calls
succeeded (answer, then a 1.0 grade) and the quota was then exhausted, which is why no table in
`RESULTS.md` is labelled LLM-judged.

---

## 5. The interface, as the skill documents it (D10 in full)

Run verbatim against a temp store, `add` twice, then recall:

| Documented / attempted | Observed |
| :--- | :--- |
| `python3 scripts/myelinate.py add --content "…"` | works (prints the id) |
| `python3 scripts/myelinate.py recall --budget 2200` — the command `SKILL.md` gives | works, but **query-blind**: keeps insertion order (`user preference` line first, deploy fact second) |
| `… recall --query "where do we deploy?"` | deploy fact first — the measured configuration, documented only in the README |
| `… --pure recall --query "where do we deploy?"` | **exit 0, query silently ignored** — byte-identical to the query-blind output |
| `… recall --pure` | **exit 2**, `error: unrecognized arguments: --pure` (the flag is global) |
| `--category` | exists, but `SKILL.md` never sets it, so every skill-written memory decays at the `general` rate (0.030/day), not the advertised `preference` rate (0.010) |

Acceptance for F5: all six rows become truthful — the documented call passes the question,
`--pure --query` is an error rather than a silent no-op, and the skill's own examples set `--category`.

---

## 6. Reproduce / verification log

Everything in this document, in order, from the repo root (Python 3.10.12, no network, no key). This is
the **round-3.5 record**; three of the commands now behave differently, because the fixes landed:
`test_engine.py` prints 139 checks and no `KNOWN DEFECT` line (170 after round 5), a scoped
`run_bench.py` writes a derived artefact instead of overwriting the report, and a capped judge run
counts skips instead of scoring them as zeros. One more command behaved differently after round 5:
`benchmarks/tune_probe.py` used to abort with `TypeError: tuple indices must be integers` before it
printed anything, and now produces the curve.

```bash
# the whole offline battery — every one must exit 0
for m in stats engines judge stub_llm synthetic test_llm_judge test_engine; do python3 benchmarks/$m.py; done
python3 -m py_compile scripts/myelinate.py benchmarks/*.py

# D1 — day 7 observed 0.259026 == compounded d(d+1)/2 (!= documented 0.486351); day 30 == 0.000001
python3 - <<'PY'
import sys, math; sys.path.insert(0, "scripts")
from myelinate import MyelinatedMemory, CATEGORY_DECAY_PER_DAY as R, INITIAL_SCORE as S
m = MyelinatedMemory(in_memory=True); t0 = 1700000000.0
m.add("The deploy target for the web service is the staging cluster in eu-west.", now=t0)
mid = list(m.memories)[0]
for d in range(1, 31):
    m.refresh(now=t0 + d*86400)
    print(d, m.memories[mid].score, S*math.exp(-R["general"]*d), S*math.exp(-R["general"]*d*(d+1)/2))
PY

# D9 — retired id still scored; a forced rebuild drops it
#      before/after 0.6027, then engine._sim_dirty = True -> absent

# D13/D14 — the >=0.9 update: absorbed by a retired entry (recall == []), or silently dropped when live
#   A = "...staging cluster owned by the platform team for nightly builds" (23 words, 19 unique tokens)
#   B = same sentence with production            -> Jaccard 0.900

# D3 — the 22 stale records
python3 - <<'PY'
import json, collections
d = json.load(open("benchmarks/results/raw.json"))
st = [r for r in d["records_by_arm"]["M9 myelinated +supersession"] if r.get("stale_ids")]
print(len(st), collections.Counter((r["source"], r["kind"]) for r in st))
PY

# D4 + D15 — in a THROWAWAY COPY, because a scoped run overwrites benchmarks/RESULTS.md (172 -> 152 lines)
T=$(mktemp -d); mkdir -p $T/repo && cp -r scripts benchmarks docs README.md SKILL.md $T/repo/
(cd $T/repo && python3 benchmarks/run_bench.py --tier staleness --skip-scale)   # prints "3 of 7"

# D7 / D11 / D12
grep -rn "downgrade" benchmarks/*.py scripts/*.py      # one definition, no callers
grep -rn "urlopen" benchmarks/*.py                     # engines.py:441, judge.py:185, public_locomo.py:68
grep -n "20%%" benchmarks/run_bench.py                 # still generated at :298

# budget invariant — 10/10 combinations respect the budget (0/50/500/2200/5000 x both recall paths)
```

**What this pass changed:** `docs/FIX-PLAN.md` (new), the D13/D14 registry entries in
`benchmarks/test_engine.py`, and the cross-references in `docs/PLAN.md`, `README.md` and
`docs/project-state.json`. No engine or harness behaviour changed; no committed result was regenerated.

**What round 4 then changed:** the engine (decay, index invalidation, the update/restatement split,
`pin()`), the harness (the judge-state ledger, rank-order metrics, warm/cold latency, the leak split,
the report guard, the informational scale criterion), three new allocator control arms, the Gemini
judge, and the regenerated report under decision-rule version 3. §0 is the summary.

---

## 7. Definition of done

* **P0 closed:** the engine cannot lose a correction (F2/F3), a scoped run cannot overwrite the
  published report (F1), and the skill's documented invocation is the measured one (F5).
* **P1 closed:** every Gate-0 instrument is repaired (F7–F13), the metric and verdict fixtures exist
  (F14/F15), and the two tracked engine defects read `RESOLVED` in an otherwise green
  `benchmarks/test_engine.py`.
* **P2 reported:** the M3p and M11 attribution experiments answered, and the decay claim either proven
  with a retrieval floor or formally retired.
* **No README sentence without a supporting row**, and no criterion edited after its run.

**Do these six first** (an afternoon, all S): **F1, F2, F4, F9, F10, F11** — they need no board
ruling, they stop an artefact being destroyed and a fact being lost, and three of them can be proven
by a command in this document.

**Round-4 outcome against this list.** **P0 is closed**: F1, F2 and F4 landed, F3 landed without the
action marker, and F5/F6 were not re-checked here. **P1 is closed except F12, F14, F15 and F16**: F12
is partial by measurement — the scale criterion is informational, not scored — and the missing fixture
files are still missing. **P2 is reported, not finished**: F17 answered the attribution question in
favour of the allocator, and nothing else in the gate was attempted. The definition of done is
therefore met for the instrument, and still open for the claims: no task-success number under a
language model exists, and the decay claim is still unproven.
