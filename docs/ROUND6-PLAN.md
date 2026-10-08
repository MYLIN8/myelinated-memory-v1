# Round 6 — Development & Polish Plan

**Produced by:** the myelinated council — Executive (coordinator) with the Architecture, QA, Evidence
and Docs & Positioning Heads.
**Baseline:** v5.2.1 (`a522f74`). **Date:** 2026-10-08.
**Status:** proposed. Every claim below was produced by a command run against this tree; nothing is
carried over from an earlier round without being re-checked.

> **Council discipline for this plan.** Every task was written with the delegation brief
> (TASK / WHY / SCOPE / DONE WHEN / CONSTRAINTS), every `DONE WHEN` is an objective check, and no
> task is "done" until the Executive has re-run its check. Behaviour-changing work is labelled; a
> refactor is not allowed to move a published number.

---

## 0. Verified baseline

| Check | Result |
| :--- | :--- |
| `python3 benchmarks/test_engine.py` | `engine ok: 181 checks`, exit 0 |
| `python3 benchmarks/test_harness.py` | `harness ok: 204 checks`, exit 0 (with the LoCoMo cache present) |
| `python3 benchmarks/test_adversarial.py` | `adversarial ok: 88 checks`, exit 0 |
| `python3 benchmarks/test_llm_judge.py` | `llm judge ok: 71 assertions`, exit 0 |
| `python3 scripts/myelinated_mcp.py --selftest` | `mcp ok: 10 checks`, exit 0 |
| `stats / engines / judge / stub_llm / synthetic`, `py_compile` | exit 0 |
| `python3 benchmarks/pool_probe.py` (R11) | reproduces: pool hit 1.000 on every tier; LoCoMo gap +0.317 |
| `benchmarks/RESULTS.md` vs the registry | **stale**: names `M0`–`M12` only; `M13` absent |
| README §7 quick start (isolated `$HOME`) | `refresh`, `add`, `access`, `recall`, `list`, `stats` all exit 0 |
| Licence | one active licence (`LICENSE.md`), source-available, no `MIT`/`POLYFORM` reference outside the changelog |

Two defects were **found and reproduced during this intake** (see §1); everything above is green.

---

## 0.1 Round-6 execution status (added after the work shipped in v5.2.2)

| Item | Outcome |
| :--- | :--- |
| **G0.1 (D21)** | **Fixed and verified at the CLI**: all four wrong-shape stores now print one `error:` line and exit 2 with no traceback, a corrupt store is unchanged at exit 2, valid v2/v3 stores still load, and a refused `load()` leaves the previous store intact. |
| **G0.2 (A1)** | **Fixed**: the version lives once in `myelinate.__version__ = "5.2.2"`, the MCP server reads it, and a check pins both to the newest `CHANGELOG.md` heading. |
| **G0.3 / G3.1 / G3.2** | **Shipped**: wrong-shape store at the engine and CLI, the empty-store CLI contract, unicode round trip, the stopword and version pins, and the 2,000-memory budget/determinism check. `test_engine.py` **209** checks, `test_adversarial.py` **103**. |
| **G2.1–G2.5** | **Shipped**: `_rank_by_shared_keys` replaces the two candidate scans, `add()` split into `_collapse_into`/`_insert_new`, sketch names disambiguated, `main()` became eight parser-bound `_cmd_*` handlers. |
| **G3.3 (Q4)** | **Still open** — a product decision, not a repair (see §4). |
| **G1.1 / G1.2** | **Still open**: the report still carries 15 arms (`M0`–`M12`) and the seed protocol is still three seeds. Both need a long run and the LoCoMo cache. |
| **G4.1–G4.4** | **Shipped**: CI badge, the PLAN.md/FIX-PLAN.md pointer, the round-4 status notes in TESTING.md, and the internal-tool reference removed from the state file. |
| **G4.5** | **No change needed**: `docs/POST.md` already carries an explicit unpublished-draft banner. |
| **G4.6** | **No change needed**: README §10 already states the source-available terms and links `LICENSE.md`. |
| **G5** | **Shipped** as v5.2.2 (patch: one bug fix, behaviour-preserving refactors, new checks). |

**Two findings the execution added to the record.**

1. **`ranked_ids` tie order is not reproducible across processes.** An A/B of a scoped curated run
   (new tree vs a `git worktree` of the previous commit) gives *identical* non-latency metrics for
   all 16 arms, but 28 of 1,792 records differ — and every one is a permutation of the same
   `ranked_ids` set. The control settles what that is: two runs of the **unchanged** engine differ in
   **32** records, and two runs of the new engine differ in 1. So it is pre-existing run-to-run
   tie-order noise (set iteration under `PYTHONHASHSEED`), not a behaviour change — but the engine is
   only metric-deterministic across processes, not record-deterministic, and the adversarial suite's
   determinism check (which runs in one process) cannot see it. Worth a follow-up: sort ties on a
   stable key so a replay is byte-identical.
2. **The review's own D5 was already satisfied** and D2 was only partly accurate: `docs/TESTING.md`
   finding 1 already carried a round-4 status note (findings 2 and 3 did not, and now do). Both are
   recorded rather than quietly dropped.

---

## 1. Head findings

### Architecture Head — structure and simplicity

Map: constants + pure helpers → `Memory`/`RecallResult` dataclasses → one `MyelinatedMemory` class
whose every mutator funnels through `_touch()` (the D9 invariant); reads split into the derived
strength layer (`strength`/`realize`), the write/index layer, `refresh()` consolidation, and recall
(`_ordered_candidates` → `_recall_tiered` / `_recall_knapsack`). `myelinated_mcp.py` is a thin
JSON-RPC wrapper. The design is sound; the debt is concentrated and small.

- **A1 (correctness, user-visible).** `scripts/myelinated_mcp.py:37` advertises
  `"version": "5.1.0"` while the project is 5.2.1 — every MCP client is told the wrong version.
- **A2.** `_cluster()` (`scripts/myelinate.py`, ≈L832–880) writes `Memory.cluster`, but nothing in
  the tree reads it; `CLUSTER_JACCARD` feeds only `_cluster`. A Jaccard scan runs on every
  `refresh()` for an output nothing consumes. The README already calls the constant "dead".
- **A3.** The rarest-first candidate scan exists twice — in `_candidates` and inline in `_cluster` —
  with the same algorithm and different caps.
- **A4.** `_sketch_of(mem)` vs `_sketch_for(content)` and `_sk` (LSH band index) vs `_sketch`
  (per-memory sketch) are two-name pairs a reader must open to tell apart.
- **A5.** `add()` (≈L505–575) inlines four outcomes (restatement collapse, promote-to-protected,
  insert, auto-supersede); `_collapse_duplicates` (≈L780–830) and `main()` (≈L1190–1240) are also
  long.
- **A6.** `myelinate.INDEX_STOPWORDS` and `benchmarks/common.STOPWORDS` are byte-identical 53-word
  sets in two files (the split is deliberate — the engine must not import the harness) but nothing
  tests that they stay equal.

### QA Head — edge cases

Three of the council's seven cases are fully covered (repeated `refresh`; protected-never-decays;
unicode in recall), three partly (empty store; corrupt/missing file; unicode persistence), one not
at all (**concurrent runs**).

- **Q1 (live defect — the one that matters).** `load()` (`scripts/myelinate.py:1083-1084`) validates
  the store's *top level* but not the `memories` container or its entries, so a valid-JSON store of
  the wrong shape escapes as an uncaught exception. Reproduced this session:

  ```
  {"schema": 3, "memories": "abc"}   -> AttributeError: 'str' object has no attribute 'items'   (exit 1)
  {"schema": 3, "memories": [1,2]}   -> AttributeError: 'int' object has no attribute 'items'   (exit 1)
  {"schema": 3, "memories": [{"id":"x"}]} -> TypeError: missing 'content'                       (exit 1)
  not json                           -> error: Expecting value: line 1 ...                      (exit 2)  <- correct
  ```
  `SECURITY.md` and the 5.2.0 changelog say S3/S5 made corrupt and non-object stores clean. Only the
  top level is. This is a **correctness bug, not a missing test**. Council id: **D21**.
- **Q2.** An absent store path is unasserted (behaviour is a silent empty store; probably intended,
  unpinned). The empty-store CLI path is unasserted. Persistence round-trip is ASCII-only.
- **Q3.** The scale path is only measured by the benchmark's 10,000-memory probe (not run in CI);
  no check asserts the budget invariant or determinism once `RECALL_POOL`/`MAX_CANDIDATES` bind.
- **Q4.** Concurrent writers are undefined: `save()` is atomic but unlocked, so a second writer
  silently discards the first's memory. Lossy, undocumented, and a product decision.

### Evidence Head — proof

- **E1 (confirmed by command).** The committed report is stale: `benchmarks/RESULTS.md` names only
  `M0`–`M12` and **`M13` appears nowhere in it**, while the registry builds 16 engine arms; the
  committed `results/raw.json` still lists `tiers: [curated, synthetic, staleness, locomo]` with no
  `staleness (update)` suite (the three new update fixtures are what make the current query count 209
  rather than 206).
- **E2.** The report's header says "every number is produced by the command below", but the command
  is `run_bench.py --phase scale --state /tmp/fullstate.json` — the *resume half* of a two-phase
  run whose state file is gone.
- **E3.** R11 was re-verified end-to-end this session (pool hit 1.000 every tier; LoCoMo +0.317;
  overall +0.105, which is the published +0.107 once the three new `update` fixtures are excluded).
- **E4.** Open and unclaimed: the five-seed pooled protocol (F22), a complete model-judged run, and
  R12's mechanism separation.
- **E5.** Round 4's *not-met* acceptance items stand: ingest ≈5–6 s and refresh ≈3.5–3.8 s per
  10,000 against the `< 1 s` / `< 0.1 s` targets, and query p50 ≈27 ms against a `≤ 8 ms` gate.

### Docs & Positioning Head — polish

The README, `SECURITY.md`, `CONTRIBUTING.md`, `docs/TESTING.md`, `docs/DISTRIBUTION.md`,
`docs/POST.md`, `docs/REMEDIATION.md` and `docs/project-state.json` were reconciled in 5.2.1.
Remaining:

- **D1.** The README's **CI badge** (`README.md:9`) points at
  `TWS07-gif/myelinated-memory/actions/workflows/checks.yml`, but `git remote -v` is
  `MYLIN8/myelinated-memory-v1` — so the badge resolves to a workflow that does not exist and renders
  as "no status". (The other five badges are fine: they link to in-repo files and carry no repo slug.)
- **D2.** `docs/TESTING.md` still carries round-3 headline framing ("the engine has no automated
  checks") without a per-finding "round-4 status: closed" note.
- **D3.** `docs/project-state.json`'s `verification_trace` mixes stale pre-round-4 entries
  (self-labelled) with one internal tool reference (`freebuff-env list`).
- **D4.** `docs/HOW-IT-WORKS.md` links to `PLAN.md` calling it "the defect registry", but
  `docs/PLAN.md` is the 601-line **round-3 plan**, not a registry; the defect registry is
  `docs/FIX-PLAN.md` (which `REMEDIATION.md` already cites correctly).
- **D5.** `docs/POST.md` is a committed draft; it should be finalised or moved to an explicit
  draft corner.

---

## 2. The plan

### Gate 0 — correctness (blocker; nothing else starts until this is green)

**G0.1 — D21: reject a wrong-shape store.**
- **WHY:** a shipped, user-reachable traceback contradicts `SECURITY.md`; it is the only finding
  that is a bug rather than debt.
- **SCOPE:** `scripts/myelinate.py` → `load()` only (validate `memories` is a list, each entry a
  dict with the required fields; raise `ValueError`). Plus `benchmarks/test_adversarial.py`
  (`store_fixtures`) and `benchmarks/test_engine.py`.
- **DONE WHEN:** the three wrong-shape stores above each raise `ValueError`, the CLI prints
  `error: …` and exits **2** for each (no `Traceback` in stderr), and every suite stays green.
- **CONSTRAINTS:** no schema change; valid v2/v3 stores must still load; no new dependency.

**G0.2 — MCP advertises the right version (A1).**
- **DONE WHEN:** `python3 scripts/myelinated_mcp.py --selftest` prints `mcp ok: 10 checks` and the
  advertised `SERVER_INFO["version"]` equals the released version.

**G0.3 — close the QA gaps for the above (Q1/Q2).**
- **DONE WHEN:** new assertions pin wrong-shape → `ValueError`, absent store → clean empty engine
  (`load()==0`, `stats()["total"]==0`, `recall().chars==0`), and empty-store CLI `stats`/`list`/
  `recall` exit 0.

### Gate 1 — evidence honesty

**G1.1 — regenerate the committed report over the 16-arm registry (E1/E2).**
- **SCOPE:** `benchmarks/RESULTS.md`, `benchmarks/results/raw.json` (produced, not hand-edited).
- **DONE WHEN:** the report lists `M13` and the `staleness (update)` tier; `benchmarks/chart.py`
  still reports "all quoted claims match"; the header command names **both** phases.
- **CONSTRAINTS:** full default run only (a scoped run must not touch the committed artefact);
  needs the public LoCoMo cache (network once).

**G1.2 — settle the seed protocol (E4).**
- **DONE WHEN:** either seeds 6–9 are run to a pooled F22 result, **or** the claim is formally
  reduced to the three seeds measured, recorded in `docs/ROUND5-STRATEGY.md` and the state file.
  Pre-register before running.

### Gate 2 — simplicity (behaviour-preserving; each gated on unchanged numbers)

- **G2.1 (A4)** rename `_sketch_of`/`_sketch_for` and disambiguate `_sk`/`_sketch`.
- **G2.2 (A5)** split `add()` into `_collapse_into(...)` + `_insert_new(...)`.
- **G2.3 (A3)** extract the shared rarest-first candidate scan; call it from `_candidates` and
  `_cluster`.
- **G2.4 (A6)** add the one assertion `myelinate.INDEX_STOPWORDS == common.STOPWORDS`.
- **G2.5 (A5)** give `main()` one handler per subcommand.
- **DONE WHEN (all of Gate 2):** `test_engine.py` stays at 181 checks (or +N for G2.4), and M11's
  hit rate / nDCG / chars-per-hit are byte-identical on a fixed scoped run.
- **CONSTRAINTS:** no behaviour change; no rename of a public/CLI surface.

### Gate 3 — QA hardening

- **G3.1 (Q2)** unicode persistence round-trip (CJK + emoji survives `save()`/reload, file is valid
  UTF-8).
- **G3.2 (Q3)** budget invariant + determinism with ~2,000 memories, where the pool cap binds.
- **G3.3 (Q4)** the concurrency contract — **needs the user's decision** (see §4): either refuse a
  second writer (lockfile) or document last-write-wins and assert no torn file is ever readable.
- **DONE WHEN:** each new check fails against a deliberately broken build and passes against this
  one (a check that cannot fail is not a check).

### Gate 4 — polish

- **G4.1 (D1)** point the CI badge at `MYLIN8/myelinated-memory-v1/actions/workflows/checks.yml`
  (source of truth: `git remote -v`).
- **G4.2 (D2)** add "round-4 status: closed" notes to TESTING.md's round-3 findings.
- **G4.3 (D3)** prune `verification_trace` of superseded entries and the internal tool reference.
- **G4.4 (D4)** fix the "defect registry" pointers to `docs/FIX-PLAN.md`.
- **G4.5 (D5)** finalise `docs/POST.md` or mark it explicitly as an unpublished draft.
- **G4.6** add a short "Licence" line to the README §10 that links `LICENSE.md` (one sentence, no
  restatement of terms).
- **DONE WHEN:** no broken intra-repo link; no reference to a file that does not exist; the README
  renders with all badge anchors resolving.

### Gate 5 — release gate (Executive)

- Bump the version and add the changelog entry (patch if only Gates 0/4 ship; **minor** if Gate 2
  refactors or Gate 1's regeneration ship).
- Walk the definition-of-done checklist below; anything unchecked goes back down the chain.

---

## 3. Definition of done — status at intake (see §0.1 for what has since shipped)

| Criterion | Status |
| :--- | :--- |
| All tests pass from a clean checkout, output shown | ✅ 181 / 204 / 88 / 71 / 10 + py_compile |
| Edge cases: empty store, large store, corrupt/missing file, unicode, repeated `refresh`, protected never decay | ⚠️ **D21** wrong-shape store tracebacks; large store and concurrency unpinned |
| No new dependencies; still a single file | ✅ |
| Functions short and named for what they do | ⚠️ A3/A4/A5 targets |
| Benchmark runs with one command and reproduces the published numbers | ⚠️ report is 15 of 16 arms; header command is a resume half |
| Every README claim backed by a measurement or removed | ✅ (after 5.2.1), with E1's caveat |
| Quick start works verbatim on a fresh machine | ✅ verified this session |
| Licence, copyright and contribution text consistent | ✅ (after 5.2.1) |
| Known limitations stated plainly | ✅, and the research backlog is now recorded |

---

## 4. Decisions needed from the user (council cannot decide these)

1. **Concurrency contract (G3.3 / Q4).** Refuse a second writer, or document last-write-wins? The
   current behaviour silently loses a writer's memory.
2. **Dead code (A2).** May `_cluster` / `CLUSTER_JACCARD` / `Memory.cluster` and the unreachable
   `render(..., "stub")` branch be deleted? It changes `refresh()`'s returned keys and removes a
   documented method, so it is a labelled protocol change.
3. **Report scope (G1.1).** Regenerate now (needs the LoCoMo cache and a long scale probe), or keep
   the "15 of 16 arms, predates M13" note and regenerate at the next release?
4. **Seeds (G1.2).** Fund the five-seed pooled run, or formally reduce the claim to three seeds?
5. **Publication (G4.5).** Finalise and post `docs/POST.md`, or archive it as a draft?

## 5. Sequencing

Gate 0 is a hard blocker and is small — it should land alone as a patch. Gates 2 and 4 are
independent of Gate 1 and of each other, so they can run in parallel. Gate 1 is the long pole
(network + a scale probe) and should start early in the background of the rest. Gate 3's
concurrency item waits on decision #1; the other two QA items can ship with Gate 2.

## 6. Explicitly out of scope

- Any change to the store schema or to the shipped engine **configuration** (those re-measure
  published numbers and need their own pre-registered round).
- The licence text (settled at 5.2.1) and public repository settings.
- Model-judged runs and the engine-side research items (R3/R6/R9, R12) beyond recording them; each
  needs a pre-registered criterion and a run and is tracked in `docs/project-state.json`'s
  `research_backlog`.
