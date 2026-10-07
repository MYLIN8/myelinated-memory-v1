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

---

## 1. Verification log — what was actually run

| Command | Exit | Observed |
| :--- | ---: | :--- |
| `python3 benchmarks/stats.py` | 0 | `stats ok` |
| `python3 benchmarks/engines.py` | 0 | `engines ok` (10 arms) |
| `python3 benchmarks/judge.py` | 0 | `judge ok` |
| `python3 benchmarks/stub_llm.py` | 0 | `stub ok` |
| `python3 benchmarks/synthetic.py` | 0 | `synthetic ok: 14 scenarios, 33 queries, 900 scale memories; staleness ok: 3 + 3` |
| `python3 benchmarks/test_llm_judge.py` | 0 | `llm judge ok: 22 assertions` (protocol only, against the local stub) |
| `python3 benchmarks/test_engine.py` | 0 | `engine ok: 69 checks` + `KNOWN DEFECT W0.1` + `KNOWN DEFECT W0.7` |
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

### D16 — judge failures are scored as failures and reported nowhere (P1, same family as D5)

`evaluate.on_result` catches `JudgeError` and writes `record["judge_score"] = 0.0`. A run that loses
the API halfway through reports a **lower task success** rather than a partial run, and no
`judged_queries` count is published for either that path or the `--judge-limit` path.
**Fix:** fold into the D5 fix — count `judged` / `skipped` / `errored`, exclude them from
`task_success`, and print the counts in the report header.
**Files:** `benchmarks/run_bench.py`, `benchmarks/metrics.py`. **Acceptance:** a stub judge stubbed to
fail on half the queries reports `task_success` equal to the uncapped healthy value plus an explicit
`judge_errors` count. **Size:** S.

### D17 — a ranking parameter is hidden inside a function body (P2)

`similarity_scores()` truncates the query vector with a literal `[:48]`. It is not in the constants
block, not marked `ASSUMPTION`, not exposed on the CLI, and absent from the tuning inventory — yet it
decides which query terms can contribute to every similarity score, i.e. to ranking, nDCG and the whole
`PRIOR_WEIGHT` story. **Fix:** hoist to `QUERY_TERM_LIMIT = 48` with an `ASSUMPTION` comment, add it
to `docs/TESTING.md` §5 and to the sweep list (sweep 1's neighbours).
**Files:** `scripts/myelinate.py`, `docs/TESTING.md`. **Acceptance:** the constant appears in both
inventories; top-10 ids are unchanged for a fixed corpus (behaviour-preserving hoist). **Size:** S.

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

### Gate P3 — blocked on a key (see §4)

| ID | Work | Blocked by |
| :--- | :--- | :--- |
| **F26** | **W9** — the LLM-judged run (curated + synthetic tiers, cost-capped, `judged_queries` reported, numbers labelled LLM-judged) | `OPENAI_API_KEY` |
| **F27** | **M5 dense-embeddings arm** (`--network`) | `OPENAI_API_KEY` |

---

## 4. The missing API key: what it blocks, and what it does not

The key is missing, so two things are **not proven** and must not be claimed:

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

The day a key exists: put `OPENAI_API_KEY` in the environment (Settings → Environment / Keys), then

```bash
python3 benchmarks/run_bench.py --judge openai                    # real task success
python3 benchmarks/run_bench.py --judge openai --tier curated     # cheaper, scoped
python3 benchmarks/run_bench.py --network                         # + the dense arm
```

Do **not** use `--judge-limit` for those runs until **F10** lands: today a capped run silently scores
every skipped query as 0.0.

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

Everything in this document, in order, from the repo root (Python 3.10.12, no network, no key):

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
