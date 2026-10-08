# Contributing to Myelinated Memory

Thanks for considering a contribution. This repository has an unusually strict protocol, because
its whole value is that its numbers are trustworthy. The rules below are the ones that already
govern the project.

## Before you start

- **Zero dependencies, standard library only.** The engine (`scripts/myelinate.py`) and the harness
  (`benchmarks/`) import nothing outside the Python standard library. A contribution that adds a
  dependency will be declined, however good it is. The project targets **Python 3.10 and 3.11**.
- **Read the honest headline first.** `README.md` publishes the project's own negative verdict, and
  `benchmarks/RESULTS.md` is the only source of quantitative claims. Do not add a claim to any
  document that no row in the report supports.

## The checks (all must be green)

Run from the repository root, offline, no API key:

```bash
python3 benchmarks/test_engine.py     # the engine: 209 checks, exits 0
python3 benchmarks/stats.py
python3 benchmarks/engines.py
python3 benchmarks/judge.py
python3 benchmarks/stub_llm.py
python3 benchmarks/synthetic.py
python3 benchmarks/test_llm_judge.py  # the LLM judge protocol, against a local stub
python3 benchmarks/test_harness.py    # the harness: 204 checks, exits 0
python3 benchmarks/test_adversarial.py # the council's adversarial suite: 103 checks, exits 0
python3 scripts/myelinated_mcp.py --selftest   # the MCP server, in-process, no client needed
python3 -m py_compile scripts/myelinate.py scripts/myelinated_mcp.py benchmarks/*.py
```

`benchmarks/test_engine.py` supports a **known-defect registry**: behaviour that is wrong but
tracked and scheduled prints as `KNOWN DEFECT <id> …` instead of failing, so the suite is green
before and after a fix and a tracked defect cannot be forgotten. **The registry is empty today** —
the defects it used to carry are real assertions now that fail if the behaviour regresses — but
adding a new `KNOWN DEFECT` line is a legitimate contribution; **a failing assertion is not.**
[`docs/FIX-PLAN.md`](docs/FIX-PLAN.md) is the list.

Do **not** run `python3 benchmarks/run_bench.py` to "check" a change: it rewrites the committed
`benchmarks/RESULTS.md` and `benchmarks/results/raw.json`. Run it deliberately when you mean to
publish a new measurement — a full run, not a scoped one.

## The measurement protocol

1. **One change per ablation arm.** A tuned constant or a new capability ships as its own labelled
   arm (`benchmarks/engines.py`), never folded into an existing one, so any improvement stays
   attributable.
2. **Pre-register the criterion** in `docs/PLAN.md` before the run. Criteria are never edited after
   seeing numbers, and the pre-registered hero arm is never swapped for whichever arm won.
3. **Sweeps are reported as curves**, not winners: these constants are pseudo-parameters with no
   physical meaning, and the sensitivity is the finding. `benchmarks/tune_probe.py` runs a sweep
   without touching the committed report.
4. **Negative results are kept.** R2 (reinforcement) measured worse than the arm it was added to and
   is still in the report. Do not delete a loss.
5. **A change that moves published numbers is labelled a protocol change** and re-measures every
   table it touches; the old figures are marked superseded, never silently replaced.

## Code style

- Match the existing style: module docstrings that state the assumptions, `ASSUMPTION` comments on
  every value chosen where the specification was silent, type hints, no prints outside the CLI.
- Keep the engine's constructor flags honest: a capability that a benchmark needs to attribute is
  opt-in (`similarity=`, `knapsack=`, `stale_retirement=`).
- New behaviour needs a check in `benchmarks/test_engine.py` (or the matching suite); the review
  will ask for one.

## Pull requests

Use the pull-request template. Small, focused PRs are reviewed fastest. If your change affects a
number in `README.md` or `benchmarks/RESULTS.md`, say which row and paste the command output that
produced it.
