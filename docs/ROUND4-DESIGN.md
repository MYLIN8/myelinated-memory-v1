# Round 4 design — one mechanism, not twelve patches

**Owner:** head of staff (orchestrator). **Status:** proposed; nothing here is implemented yet.
**Changes no code.** Every number was produced by a command in §7.

The brief for this round was: *find a way the defects could be fixed or changed so that they work
together, increasing the efficiency of the memory* — rather than as twelve independent repairs.
Three analyst pods were run in parallel (engine internals, the benchmark instrument, the front page),
and the coordinator re-derived the load-bearing measurement itself (§3). The answer is below.

**The finding, in one paragraph.** The eighteen defects in [`FIX-PLAN.md`](FIX-PLAN.md) are not
eighteen problems. The engine ones (**D1, D9, D13, D14, D17**, plus the performance items W2/W3) are
symptoms of one design choice: *strength is an accumulator written by five unrelated code paths, and
"is this the same memory?" is answered twice, by two different rules*. The instrument ones
(**D2–D8, D15, D16**) are symptoms of a second: *a record has no declared shape and a run has no
declared scope*, so every metric and every criterion has to guess. Fix the two concepts and the
defects stop existing individually — and, critically, **the engine fix only becomes safe once the
instrument can attribute it**, because the honest measurement in §3 shows the decay repair makes
budget efficiency *worse* unless the tier policy moves with it.

---

## 1. Root cause A — strength as an accumulator, lifecycle answered twice

Today:

* `add()` seeds `score = 0.60`; `access()` multiplies it toward 1; `refresh()` multiplies it by
  `exp(−rate × days_since_last_access)` **again on every session** — because the thing it decays
  from, `last_access`, only moves on `access`. The exponent compounds as `rate × d(d+1)/2`. That is
  **D1**, and it is not a slip in one formula: it is what happens when a quantity is *stored* that
  should be *derived*.
* Five places write `mem.tier`, and one of them (`retire()`) forgets to tell the index it changed, so
  the TF-IDF vectors keep scoring a dead memory (**D9**). Same cause: mutation is scattered, so no
  single place owns consistency.
* Near-duplicate detection runs on two code paths with two different policies: `add()` absorbs a
  ≥0.9-similar memory into the existing entry (silently discarding the new text — **D14**), and
  `_collapse_duplicates()` picks a merge winner at the session boundary. Neither path asks whether
  the target is **retired**, so a correction can be absorbed by the dead fact it replaces and
  disappear from recall entirely (**D13** — probed: `used_ids == []`).
* `similarity_scores()` hard-codes `[:48]` query terms inside a function body (**D17**): a ranking
  parameter with no owner, which is the same disease at a smaller scale.

Two of the three cost problems share the cause as well: every recall scores **all n memories** (the
O(n) sweep, W2) and the index rebuild is lazy, so one unlucky query pays it; and `_cluster()` builds
a `mem.cluster` field that nothing reads, on the session-boundary hot path (W3).

## 2. The unified mechanism

Three rules replace the current behaviour. Each is a *concept*, and every defect in the list above
disappears as a consequence of one of them rather than being patched.

### Rule 1 — strength is derived, never accumulated

```python
def strength(self, mem, now) -> float        # mem.score * exp(-rate(mem.category) * days(mem.score_at, now))
def realize(self, mem, now) -> None          # idempotent: mem.score, mem.score_at = self.strength(mem, now), now
def tier_of(self, mem, now) -> str           # tier_for(self.strength(mem, now), mem.protected)
```

`Memory` keeps `score` **as of** a timestamp (`score_at`) instead of a value that is multiplied in
place; `tier` stops being a stored field. Realising twice at the same `now` is a no-op, so
"how often do we call `refresh`?" stops being a semantic question — which is the whole of D1. Every
reader (`recall`, `list`, `stats`, the packer) observes the same number because it computes it the
same way. **D1 dies by construction, not by a special case.**

### Rule 2 — one classifier, three policies

```python
def _classify(self, new_tokens, other, now) -> str   # "unrelated" | "duplicate" | "update"
```

One similarity function decides the *relationship* between two memories; the write path and the
boundary pass both use it, and each class has exactly one policy:

| Class | Condition | Policy (identical on both paths) |
| :--- | :--- | :--- |
| unrelated | Jaccard < 0.90 | keep both |
| duplicate | ≥ 0.90 similar **and** the value slots agree | merge into the stronger entry |
| update | ≥ 0.90 similar but a *value slot* differs (number, date, negation, ordinal, capitalised noun) | **keep the new text**, retire the old, record `superseded_by` |
| *any class* | the candidate is `retired` | **never a merge target** — the new memory is stored |

That is **D13** (retired memories leave candidate generation), **D14** (an update keeps its own
wording instead of being silently absorbed), and — the strategic part — it makes decay-free
supersession *detectable*: a value-changing update can be retired at write time with **no retire
signal from the caller**, which is the only mechanism proposed anywhere in this repo that can move
the decay-only leak off 1.000 (criterion 5, currently FAIL by construction).

### Rule 3 — one index version, one pass

```python
def _touch(self, *ids) -> None       # every mutator: dirty set + index_version += 1
def _ensure_index(self) -> None      # every reader: rebuild only what is dirty
def consolidate(self, now) -> Dict   # replaces refresh(): realize -> classify/merge/retire -> prune
```

`retire()`, `pin()`, `access()` and `add()` all call `_touch`; nothing else is allowed to mutate
index state. **D9** stops being possible. Consolidation becomes one ordered pass over the *dirty
set* — decay (Rule 1) → classify/merge/retire (Rule 2) → prune — which means:

* the index rebuild happens **at the session boundary**, so no query pays it (W2's latency fix),
* `_cluster()` and its write-only `mem.cluster` field are **deleted** (W3), and the cluster idea
  returns only if the packer uses it: a diversity term so the budget does not fill with four
  paraphrases of one fact (this is where `evidence_precision` — engine 0.084 vs BM25 0.215 — could
  actually improve),
* retrieval scores **candidates from postings** (rarest-key-first, capped) instead of sweeping all
  n memories; the exhaustive O(n) scan stays available behind a flag as the **equivalence oracle**
  the plan already demands (top-10 identical on 200 queries).

### How fixing one defect pays for another

| Dimension | Direction | Because of |
| :--- | :--- | :--- |
| Ingest cost | **down** (4.6–5.4 s → target < 1 s / 10k) | one tokenisation per `add` (today `_index_insert` tokenises twice), consolidation visits only *dirty* memories, and `_cluster()`'s second candidate scan leaves the boundary |
| Recall latency at 10k | **down** (p50 19.7 ms, p95 115.8 ms) | candidate-set scoring replaces the full sweep; the rebuild moves to the boundary |
| Stored-entry correctness | **up** | D13 gone; D14 becomes a labelled action, not a silent delete |
| Staleness **without** a retire signal | **up — the strategic win** | Rule 2's `update` class |
| Ranking quality | **mixed** | candidate restriction can drop evidence if the candidate budget is too small; only the top-10 equivalence test settles it |
| **Context cost** | **worse before it is better** | measured in §3: a correct decay un-archives the store, so more full text competes for the same 2,200 characters |

## 3. The measured coupling (new evidence, this pass)

This is the part that makes the answer "one mechanism" instead of a wish list. The decay repair
(D1) is not local: it changes how many memories are *rendered at full length*, which is the input to
the budget-efficiency claim.

A model of the engine's own scoring was built from the events (boosts at access times, decay between
them), validated against the shipped engine, then re-run with the **fixed** decay rule. On the
900-memory scale corpus (seed 0, 60 virtual days, all tiers):

| | shipped (compounding) | fixed decay (`exp(−rate × d)`) |
| :--- | ---: | ---: |
| model error against the engine | **1.67e-16** over 900 memories (0 collapsed ids) | — |
| Active (`> 0.5`) | 129 | **247** |
| Latent (0.1 – 0.5) | 135 | **653** |
| Archived (≤ 0.1) | **636** | **0** |
| mean rendered characters per memory | 72.9 | **96.8 (+33%)** |
| memories a 2,200-char budget can hold (rough) | ≈ 30 | ≈ **23** |
| mean score | 0.146 | 0.385 |

The model reproducing the shipped score to 1.7e-16 is the control: it means the fixed-rule column is
the same code path with one formula changed, not a different simulation.

**The reading.** Fixing D1 removes the entire archived tier on this workload: nothing decays to a
gist any more, so every memory contributes more text and **+33% characters for the same budget**.
Criterion 1 (budget efficiency, today PASS at 1589 vs flat 1734) is therefore *at risk from the
decay fix alone* — and so is `chars_per_hit = mean(chars) / mean(hit_rate)`, whose numerator rises.
The fix only stays a win if it lands **together with** the tier policy that was fitted under the
buggy curve: `ACTIVE_THRESHOLD`/`LATENT_THRESHOLD`, `SUMMARY_MAX_CHARS`/`GIST_MAX_CHARS` and
`DETAIL_VALUE` must be re-swept as a group (they trade off against each other), and the diversity
term from Rule 3 has to stop the packer spending its budget on near-paraphrases. That is the concrete
sense in which these defects have to be fixed *together*.

**Pre-registered acceptance for the unified configuration** (written now, before any run):

1. `score(N days) == INITIAL_SCORE × exp(−rate × N)` within 1e-9 for N = 1..30, and `realize()` is
   idempotent;
2. after any sequence of add/access/retire/pin, incremental index state equals a full rebuild
   (D9 closed), and `retire`-then-restate stores and recalls the correction (D13 closed);
3. hit rate **≥ 0.825** and chars/hit **≤ 1589** on the same tiers and seed — i.e. the unified
   configuration does not lose the budget-efficiency criterion that the decay fix would otherwise
   cost it;
4. scale p50 ≤ 8 ms and p95 below BM25's at 10k, with top-10 ids identical to the exhaustive scan on
   200 queries via the equivalence oracle;
5. ingest < 1.0 s per 10k and `refresh_total_s` < 0.1, with **zero** metric movement attributable to
   the performance change alone;
6. decay-only leakage < 0.50 **with** `hit_rate_decay_only ≥ 0.9 × flat`, and the negative controls
   (M0, M2, M6) must now **fail** that criterion instead of passing it by retrieving nothing.

## 4. Root cause B — a record has no declared shape, a run has no scope

Every instrument defect is the same missing concept: `used_ids` is the **packed** order, so nDCG and
MRR score the allocator as if it were the retriever (D8); `verdict()` guesses what a run measured,
so a scoped run invents a FAIL (D4); "unjudged" is not a state, so `--judge-limit` and judge errors
are averaged in as `0.0` (D5, D16); the leak column averages whatever carries `stale_ids`, mixing 22
records of which 16 have no retire signal (D3); and the report path is hard-coded, so a scoped run
overwrites the committed report (D15). One change of concept fixes all six:

```python
# a record declares what was produced ...
ranked_ids   # the arm's own order, pre-packing
used_ids     # what actually went into the context (the packing order)
details      # id -> "full" | "summary" | "gist"
dropped_ids  # ranked but not packed
judge_state  # "judged" | "skipped" | "errored"

# ... and a run declares what it measured
measured = {"quality": True, "scale": False, "staleness": True, "significance": True, ...}
verdict(summaries, by_source, scale, pairs, measured)   # each criterion -> PASS | FAIL | NOT MEASURED
report_path(args, tiers)                                # RESULTS.md only for a full run
```

Then: order is a parameter (`ndcg(record, order="ranked")`), the leak column is **removed** rather
than filtered and replaced by one column per suite, `task_success` excludes `judge_state != "judged"`
and reports the counts, the significance criterion requires **both** flat arms, and a scoped run
writes `RESULTS-<tier>.md` and leaves the committed report byte-identical.

### The attribution matrix (why the instrument has to be fixed first)

The ablation ladder becomes a 2×2 — {BM25 rank, engine cosine rank} × {truncate at full text, engine
detail ladder} — so the budget claim can finally be attributed to the *store* or to the *packer*:

| Arm | Ranker | Allocator | Decides |
| :--- | :--- | :--- | :--- |
| M3 (`BM25`) | BM25 | truncate at full text | today's baseline |
| M3t | BM25 | `common.downgrade()` (the currently-unused function) | the *first* honest truncation control |
| M3p | BM25 | engine detail ladder (`TIER_DETAILS`, 160/64 caps) | **is the ladder the win?** |
| M3k | BM25 | engine value-per-char packer, utility = rank position (not the engine's score) | **is the packer the win?** |
| M12 | engine cosine (derived strength, Rule 1/2/3) | engine packer | does the *store* still add anything? |
| M11 | engine cosine with `PRIOR_WEIGHT = 0`, hold-out seeds | engine packer | is the retrieval-strength prior a cost to ranking? |

Outcomes, pre-registered: if **M3p ≈ M8**, the win is the **allocator** and every published
"budget efficiency" claim is restated as *allocation* rather than *store*. If **M3k > M3p**, the win
is the **packer** specifically — a stronger and more useful claim than "the ranker is better". If
**M11** (hold-out) reaches hit rate ≥ 0.874 / nDCG ≥ 0.672 / chars-per-hit ≤ 1336, the prior is
demoted to a tie-breaker and criterion 3 passes with margin instead of by 0.049. And **M12** is the
only arm that can move criterion 5 at all, via Rule 2's update class.

## 5. Sequencing

```
Rule 3 index discipline ─┐
Rule 1 derived strength ─┼─► M12 arm ─► re-measure decay/tier/scale tables ─► re-sweep thresholds+caps ─┐
Rule 2 classifier ───────┘                                                                              ├─► claims
instrument: record shape + measured set + report guard + judge ledger ──► M3p/M3k/M3t/M11 ──────────────┘
```

**Smallest independently testable first slice (no metric surface, ~30 lines, `test_engine.py` alone):**
`realize()` + `_touch`/`_ensure_index`. Acceptance: `realize()` twice at the same `now` is a no-op;
N daily realisations equal `INITIAL_SCORE × exp(−rate × N)` to 1e-9; incremental state equals a full
rebuild after any add/access/retire/pin sequence. Then `_classify()` (D13/D14), whose fixtures must
include the ≥0.9 contradiction pair the staleness suites deliberately avoid — worth adding as a
scenario, because today **no scenario in the benchmark exercises it**.

**Migration and attribution.** Land the whole mechanism as its own labelled arm (`M12 myelinated
(unified)`, an 11th arm; `engines.ARM_ORDER` and its `assert len(arms) == 10` must move with it) so
all ten committed arms stay reproducible and no published number becomes unreproducible by a
refactor. Two things cannot be arm-scoped and are therefore **protocol changes that must be
labelled**: the `(score, score_at)` migration (a `load()` shim sets `score_at = last_access` for v2
files, and rounds 1–2 decay figures are marked superseded — already promised by W0.1/BM-006), and
D13's change in stored counts (today's 9,998 becomes *superseded*, never compared across the change).
Re-measure: the whole decay/tier family, the scale table, the per-tier hit-rate table. Decision-rule
rows 1, 3, 5 and 6 move; rows 2, 4 and 7 do not.

## 6. Risks, and what stays unproven

| Risk | Mitigation |
| :--- | :--- |
| The decay fix raises chars/hit (measured, §3) | acceptance 3 above; the thresholds/caps sweep lands in the *same* change, not after it |
| Candidate-first retrieval drops evidence | the equivalence oracle (top-10 identical on 200 queries) is a gate, not a check |
| Adding an 11th arm breaks the `len(arms) == 10` assertions in `engines.py` | update them in the same commit; they are self-checks, not published numbers |
| The unified classifier stores *more* entries (no silent absorption), lowering `evidence_precision` | the diversity term in Rule 3 targets exactly that; if it fails, the arm is reported as such |
| `_cluster()` deletion removes the hook a future diversity term needs | it is deleted as a *write-only field*, and re-introduced only when the packer reads it |

**Unproven without an API key.** The LLM-judged task success and the dense-embedding arm (M5) do not
exist in any table, so no unification here can claim "answers more questions correctly". The judge
*protocol* is verified against the local stub; the model's judgement is not.

## 7. Reproduce

```bash
# every offline check (all exit 0 today)
for m in stats engines judge stub_llm synthetic test_llm_judge test_engine; do python3 benchmarks/$m.py; done

# the measurement in §3 (model validated against the engine, then re-run with the fixed rule)
python3 - <<'PY'
import sys, math
sys.path.insert(0, "benchmarks"); sys.path.insert(0, "scripts")
import synthetic
from myelinate import (MyelinatedMemory, CATEGORY_DECAY_PER_DAY as R, INITIAL_SCORE as S,
                       BOOST_ALPHA, ACTIVE_THRESHOLD, LATENT_THRESHOLD, make_summary,
                       GIST_MAX_CHARS, SUMMARY_MAX_CHARS)
DAY = 86400.0
events, _ = synthetic.scalability_corpus(seed=0, n_memories=900, days=60)
eng = MyelinatedMemory(in_memory=True); hist = {}; last_day = None
for e in events:
    if last_day is None or e.day != last_day:
        eng.refresh(e.timestamp()); last_day = e.day
    if e.op == "add":
        eng.add(e.content, memory_id=e.id or None, category=e.category,
                protected=e.protected, now=e.timestamp()); hist.setdefault(e.id, []).append((e.day, "add"))
    elif e.op == "access":
        eng.access(e.id, e.timestamp()); hist.setdefault(e.id, []).append((e.day, "access"))
def model(compound):
    out = {}
    for mid, h in hist.items():
        s, last = S, h[0][0]
        for d, kind in h:
            gap = d - last
            if gap > 0:
                s *= math.exp(-R["general"] * (gap*(gap+1)/2.0 if compound else gap))
            if kind == "access":
                s += BOOST_ALPHA * (1.0 - s)
            last = d
        gap = 59 - last
        s *= math.exp(-R["general"] * (gap*(gap+1)/2.0 if compound else gap)) if gap > 0 else 1.0
        out[mid] = s
    return out
shipped, derived = model(True), model(False)
common = [m for m in hist if m in eng.memories]
print("model error vs engine:", max(abs(shipped[m]-eng.memories[m].score) for m in common))
tier = lambda s: "active" if s > ACTIVE_THRESHOLD else ("latent" if s > LATENT_THRESHOLD else "archived")
from collections import Counter
print(Counter(tier(shipped[m]) for m in common), Counter(tier(derived[m]) for m in common))
PY
```

**Design inputs.** Three analyst pods (engine internals; the benchmark instrument; the front page),
whose reports the coordinator re-derived where they were load-bearing — the compounding model above
is that re-derivation. The engine pod's own warning that a per-arm `unified` flag changes
`engines.py`'s arm-count self-checks, and the instrument pod's warning that the scale corpus may not
support ≥50 warm queries, are both carried into §5/§6 rather than dropped.
