# Distribution drafts

**Everything in this file is a draft. Nothing here has been published.** Posting to Show HN,
Reddit, or any registry is the maintainer's action; this file only stages the text. Every number
below is traceable to `benchmarks/RESULTS.md` (committed report, seed 0), the round-5 hold-out run
(seeds 3–4), or criterion R10 in `docs/PLAN.md`, and each post says which.

---

## 1. GitHub repository description (one line, ≤ 350 chars)

> A Hebbian retrieval-strength memory engine for AI agents — one Python file, zero dependencies.
> Matches a BM25 index on evidence retrieval (0.893 hit rate) while spending fewer context
> characters per hit (1276 vs 1294), with supersession, pinning, per-category decay and a
> persisted store. Ships its own benchmark and reports its own losses.

## 2. Repository topics

`ai-agents` · `memory` · `context-management` · `retrieval` · `llm` · `agent-memory` ·
`benchmark` · `python` · `stdlib-only` · `mcp` · `rag` · `hebbian-learning`

## 3. Release notes draft — v5.2.0 (unreleased)

The canonical record is now the `[5.2.0]` entry in [`CHANGELOG.md`](../CHANGELOG.md) (which also
covers R11, R12, the harness fixtures and the F23 resolution); use that entry when drafting the
GitHub release. The condensed version below is kept as the short form:

```markdown
## 5.2.0 — the CLI repair, the MCP server, and the first external comparison

### Fixed
- The CLI's `--force-similarity`/`--no-similarity` flags were parsed but never reached
  `recall()`; they now map to `recall(force_similarity=...)`. The recall path's duplicated
  dead code block is gone, and the phantom `init`/`save`/`load` subcommands and `--summary`
  flag (parsed, never handled) are removed rather than left to exit 2 on users.
- `docs/POST.md` mixed hold-out-seed figures with committed-report figures in two chart cells;
  every number is now labelled with the run that produced it.

### Added
- `scripts/myelinated_mcp.py` — the engine behind a stdio MCP server (JSON-RPC 2.0,
  zero dependencies): `memory_add`, `memory_access`, `memory_retire`, `memory_pin`,
  `memory_recall`, `memory_refresh`, `memory_stats`, with `--selftest` for an offline
  round trip. Verified over real pipes, not just in-process.
- `docs/PORTABILITY.md` — library API, MCP configuration, a LangChain-style retriever,
  the session protocol, and the known limits.
- `benchmarks/chart.py` — one command that turns `results/raw.json` into the cost-vs-hit-rate
  chart and table, reproducing the published numbers from the committed evidence.
- Engine suite: 181 checks (was 170). The new checks cover the CLI wiring — the gap that let
  the flags above stay non-functional while every suite stayed green.

### Measured
- **External comparison, criterion R10 (pre-registered in `docs/PLAN.md` before the run).**
  On the public LoCoMo tier, a dense embedding arm (`nvidia/nemotron-3-embed-1b`, scoped run,
  60 queries, seed 0) reaches **0.833** evidence-hit rate against the shipped engine's 0.683
  and the BM25 control's 0.717, at fewer characters per hit than both. By the frozen rule this
  **closes the tier**: the engine's remaining retrieval deficit on paraphrase-heavy transcripts
  is term-overlap retrieval, and semantic recall closes it for the price of an API and a key.
  The engine is unchanged; this compares retrievers, not engine settings.
```

## 4. Show HN draft

> **Show HN: Myelinated Memory – a context-budget memory engine for agents, with its own losses
> published**
>
> One Python file, zero dependencies, one JSON store. Memories carry a retrieval-strength score
> that rises when used and decays when ignored; the score decides how much of each memory's text
> earns a fixed context budget (full text → summary → 64-char gist, allocated by value per
> character). Supersession, pinning, per-category decay rates, duplicate collapsing.
>
> The interesting part is the measurement, not the metaphor. We built a benchmark harness with
> 15 ablation arms and a pre-registered decision rule, and the engine's own hypothesis lost: the
> strength prior it is named after measured as a *cost* to ranking and was demoted to a
> tie-breaker (nDCG 0.702 → better ordering when the weight hits 0.0). What survives is real:
> it ties a BM25 index on evidence retrieval (0.893 hit rate) while spending fewer context
> characters per hit (1276 vs 1294), stale facts leak 0.000 with an explicit retire signal, and
> it needs no service at all.
>
> What it is not: the best pure ranker (BM25 orders evidence better), and decay-alone retirement
> is still unproven (leak 1.000 without a signal). Both are on the front page, not in a footnote.
>
> New in this release: an MCP server (zero-dep stdio) so Claude Code/Desktop can use it as memory
> tools, and a dense-embedding comparison on LoCoMo that a dense arm wins decisively (0.833 vs
> 0.683) — which is the honest answer for paraphrase-heavy workloads.
>
> 181 offline checks, everything reproducible with one command. Curious what HN thinks about
> publishing negative results by default.

## 5. Reddit draft (r/LocalLLaMA or r/MachineLearning, "discussion")

Title: *We pre-registered our memory engine's hypothesis, and it failed — here is the whole record*

Body: same substance as the Show HN post, with the tables from README §2 pasted in and the
emphasis on the protocol: one change per arm, criteria frozen before runs, negative results
kept, hold-out seeds, and the scoped-run provenance rules. End with the two open questions
(pool recall@k to split candidate generation from ordering; dense ranking inside the engine).

## 6. Hermes registry draft (skill listing)

> **Myelinated Memory** — session memory for Hermes agents: `refresh` → `recall` → `access` →
> `add`, with budgets, supersession and decay. Stdlib-only, one JSON store, MCP server included.
> The skill protocol is `SKILL.md`; the engine is `scripts/myelinate.py`.

---

### Numbers cheat-sheet for any post (do not round up)

| Claim | Number | Source |
| :--- | :--- | :--- |
| Engine hit rate, ties BM25+packer control | 0.893 vs 0.893 | hold-out seeds 3–4 (`docs/ROUND5-STRATEGY.md`) |
| Characters per evidence hit | 1276/1283 vs 1294/1301 | hold-out seeds 3–4 |
| Committed report figures (if quoting one run) | 0.893 hit, 1279 chars/hit, nDCG 0.699 | `benchmarks/RESULTS.md`, seed 0 |
| nDCG@10, behind the control | 0.702 vs 0.734 | hold-out seeds 3–4 |
| LoCoMo, engine vs control | 0.683 vs 0.717 | hold-out seeds 3–4 |
| LoCoMo, dense arm closes the tier | 0.833 vs 0.683 / 0.717 | R10 scoped run, seed 0 |
| Supersession leak with signal / without | 0.000 / 1.000 | committed report |
| Decision rule | 4 of 6 scored criteria pass | committed report |
| Checks | 181 engine checks, 204 harness checks, 88 adversarial checks, 71 judge assertions, 10 offline suites | this tree |
