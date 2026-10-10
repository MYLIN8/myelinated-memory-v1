<!-- 
  NOTE: This is a committed draft artifact for a public post + community crosspost.
  It is intentionally written as a single, honest explainer with a short demo and one chart.
  Promotional posting is the maintainer's action; nothing here has been published.
-->

# Myelinated Memory — a compact, budget-aware recall layer for LLM agents

> **Unpublished draft.** This post has not been published anywhere; the figures below are
> pinned to the committed benchmark report (`benchmarks/RESULTS.md`, seed 0) and the
> labelled hold-out runs, and the draft stays in the tree so those pins can be checked.

A short post about a small Python memory engine, what it does, what it does not do,
and the one chart that matters.

## The problem it tries to solve

An agent with a long-lived memory still has a *short* context window. Every recall that
pastes a memory into the prompt spends scarce tokens, so a memory system has two jobs that
are easy to confuse:

1. Decide **what** to recall.
2. Decide **how much** of each memory to paste — full text, a one-line summary, or a
   short gist.

Most memory prototypes I have seen optimize the first job and treat the second as an
afterthought. That is the wrong split. If you have 2200 characters of budget and ten
candidate memories, the interesting question is often not "which memory is most relevant?"
but "which slice of which memory gives me the most evidence per character?"

## What this is

Myelinated Memory is a single stdlib-only Python file that stores memories as JSON and
assigns each one a retrieval-strength score in `[0,1]`. The score rises when the memory is
used and decays with dormancy, with different categories decaying at different rates. The
score maps to a rendering tier:

- **Active** — full text
- **Latent** — a one-line summary
- **Archived** — a short content gist

The recall path fills a character budget by expected value per character, so a cheap
summary can outrank an expensive full text. There is no vector database, no embedding API,
no service to run. One file, one JSON store.

That is the pitch. The honest part comes next.

## What the numbers actually say

The project includes its own offline benchmark: 36 scenarios, 209 queries, a 2200-character
budget, replayed on a virtual clock so decay and access patterns are reproducible. The judge
is a deterministic evidence-containment oracle — it rewards surfacing the right line, not
reasoned answers. That is intentional: it makes the run free, offline, and repeatable.

Headline, quoted directly from the repo's own report:

Every figure below is from the **committed report** (`benchmarks/RESULTS.md`, full run at
seed 0, offline oracle judge); a separate hold-out confirmation on seeds 3–4 is quoted where
it exists and is labelled as such.

- **Evidence hit rate:** 0.895 — level with the strongest semantic baseline tested in the
  suite (the BM25+packer control also scores 0.895).
- **Characters per evidence hit:** 1293 — cheaper than the BM25+packer control's 1310 and far
  cheaper than a flat FIFO store's 1742 or a recency cache's 2253. (Hold-out seeds 3–4:
  1276–1283 against 1294–1301 — same ordering.)
- **nDCG@10:** 0.703 rank / 0.693 packed — behind plain BM25's 0.736. (Hold-out seeds 3–4:
  0.697–0.702 rank; BM25's control arm 0.731–0.734.)
- **Public LoCoMo tier:** 0.683 hit rate — behind the allocator-control arm that pairs BM25
  ranking with this engine's packer, which scored 0.717. (Same on both hold-out seeds.)

So the engine's own reading is:

- Competitive with a lexical index on retrieval, at lower context cost.
- Clearly ahead of flat and recency stores on both metrics at once.
- Still short of being the best pure ranker. BM25 still orders evidence better on this
  workload.
- The "strength and decay" mechanism the project is named after has **not** been proven to
  earn its keep. In round 5 the retrieval-strength prior was demoted to a tie-breaker after
  it measured as a *cost* on ranking.

That last point is not a footnote I added for drama. It is the most interesting finding in the
repo: the store won on budget efficiency, the packer won on allocation, and the strength prior
itself lost on ordering. A project that says so in its own README is doing something right.

## The one chart

If you only look at one comparison, make it this one: **evidence hit rate vs characters per
evidence hit** on the project's own offline suite.

| Configuration | Evidence hit rate | Chars per evidence hit |
| --- | ---: | ---: |
| No memory | 0.000 | — |
| Flat / FIFO store | 0.780 | 1742 |
| Recency / LRU | 0.603 | 2253 |
| Semantic / BM25 | 0.866 | 1350 |
| Semantic / TF-IDF | 0.876 | 1551 |
| Myelinated — as specified | 0.684 | 1940 |
| Myelinated + similarity | 0.818 | 1623 |
| Myelinated + value-per-char packing | 0.837 | 1582 |
| **Myelinated — shipped default** | **0.895** | **1293** |

Read it as a frontier, not a leaderboard. The interesting cells are the upper-left: high
recall, low cost. The shipped engine is not the top-right object; it is one of the few points
that gets close to the semantic baseline while spending less context to do it.

The project's own caveat belongs right under this chart: **BM25 still ranks better**, and the
project says so. If you only care about ordering evidence and you can already run BM25, use
BM25. The engine's case is cost, store semantics, and zero infrastructure.

## A short demo

Install nothing. Run it against an in-memory store:

```bash
python3 scripts/myelinate.py add --content "User prefers concise replies." --category preference
python3 scripts/myelinate.py recall --query "What does the user prefer?" --budget 2200
```

Or use it as a library:

```python
from myelinate import MyelinatedMemory

m = MyelinatedMemory(in_memory=True)
m.add("User prefers concise replies.", category="preference")
r = m.recall(query="What does the user prefer?", budget=2200)
print(r.text)
print(r.chars, r.used_ids)
```

The recall call returns a `RecallResult` with the packed text, the ids used, the tier counts,
the character spend, and — since round 5 — the pre-packing rank order so the harness can score
ranking and allocation separately. That last bit matters more than it looks: before it existed,
the report was accidentally scoring the allocator as if it were the ranker.

## What it is good for

- **Zero infrastructure.** One file, one readable JSON store. You can diff your memory.
- **Budget discipline.** A fixed context budget that gets spent by expected value per
  character, not by "paste the top N memories in full."
- **Store semantics a lexical index does not have.** Pinning, supersession with a 0.000 stale
  leak under an explicit retire signal, per-category decay, tiered rendering, and duplicate
  collapsing on write.
- **A benchmark that reports its own losses.** The README leads with the failures. That is
  rare enough to be worth noticing.

## What it is not

- Not the best pure ranker. BM25 still orders evidence better on this benchmark.
- Not proven to retire stale facts by decay alone at the configured default. With an explicit
  retire signal it leaks 0.000; without one, the default arm still leaks 1.000. The pure spec
  now leaks 0.000 on a tiny three-scenario suite, which is a lead, not a capability.
- Not a drop-in replacement for a vector database if your recall problem is genuinely semantic
  and you are already comfortable running one.

## Portability

The engine is already a plain Python class with an `in_memory=True` mode and a clock callback,
so it can be embedded without touching a store file. The CLI is thin and the recall path takes
a query, a budget, and optional similarity overrides, which makes it usable from other tooling
without adopting the whole Hermes session protocol.

If you want it as an MCP server or a generic library with a stable public API, the natural
shape is:

- `add(content, category, protected, supersedes)` → memory id
- `access(id)`
- `retire(id, superseded_by)`
- `pin(id)`
- `recall(budget, query, use_similarity, force_similarity)` → `{text, used_ids, ranked_ids, tiers, chars}`
- `refresh()` → consolidation stats
- `save()` / `load()`

That is enough to build an MCP tool wrapper or a LangChain-style retriever around it without
coupling to any agent runtime. If I were shipping this for general use, I would add a small
public wrapper with that surface and leave the CLI as a demo of it.

## How to reproduce the chart yourself

```bash
python3 benchmarks/run_bench.py
```

Or, for the head-to-head recall-vs-cost view without the full scale run:

```bash
python3 benchmarks/run_bench.py --tier curated --skip-scale
```

The first command regenerates the committed report. The second writes a scoped report under a
derived name so it does not clobber the published one — a defect the project found and fixed,
and one worth mentioning because it is exactly the kind of thing a benchmark repo should guard
against.

## The honest “where it wins” summary

If you want a one-liner that does not oversell:

- **Cost and budget efficiency:** yes, and by a visible margin on this suite.
- **Retrieval quality vs a lexical index:** level on hit rate, cheaper per hit, behind on
  ordering.
- **Simplicity and ops:** the strongest argument. No service, no embedding key, no vector
  store.
- **Store semantics:** real, and not available from a pure index.
- **The biological metaphor:** unproven. The decay fix was necessary just to make the store
  stop lying to itself; the strength prior was demoted because it hurt ranking.

## Monetization, if anyone asks

Do not start there. The realistic routes, in order, are:

1. **Consulting / integration work.** There is more value in wiring a disciplined, zero-infra
   recall layer into a real agent than in selling the idea of a recall layer.
2. **A hosted version, only if it earns it.** Hosted is justified if someone genuinely wants
   multi-process, multi-user, multi-device memory with the same budget discipline and they do
   not want to run the store themselves. Until then, hosting a single JSON file is not a
   business.
3. **Portfolio piece.** If this gets traction, the credibility is worth more than a sale price.
   A project that publishes its own negative results, fixes its own benchmark bugs, and still
   has a coherent story is a strong signal.

The order matters. Prove the thing is good and portable first. Then decide whether money should
come from services, hosting, or credibility. The repo basics — a decent description, a few
sensible topics, a release tag, and a comparison table that does not overclaim — are the
cheapest part of that and should be done before any of the above.

## One sentence version

A compact, budget-aware recall layer that is competitive with a lexical index on retrieval,
cheaper per evidence hit, ahead of flat and recency stores, and honest about the places it is
still worse — with no infrastructure to run.

If you want, I can turn this into the repo's public-facing post and tighten the README
comparison table around exactly these claims instead of leaving it as a draft file.
