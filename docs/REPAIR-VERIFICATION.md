# Memory safety repair: frozen verification protocol

Registered before running the repaired benchmark, 20 October 2026.
Base: d271a5fda2d179d594a9ed03473db7da9bf65bca (5.4.0).

## Scope and acceptance

- Distinct text must survive add, refresh and persistence without inferred
  merging or retirement. Identical text after outer trimming may collapse.
  Explicit supersession must work, and invalid targets must not mutate state.
- A writable engine owns its local store from load through close. A second
  owner must fail clearly. Clean exit and forced termination release ownership.
  Failed writes preserve the previous JSON; alternate paths cannot bypass locks.
- Invalid MCP input must not terminate the stdio process or alter the store.
  Subsequent valid requests must succeed and stdout remains protocol-only.

Run `benchmarks/test_repairs.py` and all existing CI suites. Test process-level
contention and real stdio transport, not only in-process calls. Windows locking
must run on Windows CI before claiming Windows verification. Network filesystem
and hard-link aliases are outside the local-store locking guarantee.

## Benchmark attribution

M6-M13 explicitly retain legacy identity through the harness-only configuration
`exact_identity=False`. M14 is M11 with exactly one change: `exact_identity=True`.
The application's default is conservative; legacy behaviour is unsafe for general
use and remains only to reproduce published experiments.

Run the full default workload with the offline oracle and seed 0, including the
public LoCoMo tier and 10,000-memory scale probe. Write to a separate report and
raw directory. Preserve the original RESULTS.md/raw.json as historical 5.4.0
evidence, and label the old README figures accordingly. Do not replace the old
pre-registered hero or edit its decision criteria.

Report M14 versus M11 and the strongest packing control: hit rate, ordering,
character cost, both staleness metrics and latency. Inspect the dedicated
staleness-update suite separately as well.
There is no requirement that a correctness fix improve retrieval; report losses.
The new safety checks are release gates. Historical metrics are not substitutes
for those checks, nor are oracle scores model-judged task success.

Record commands, runtime/platform, results and remaining limits in the PR.

Inspection clarification: the loader already includes the update suite in the
default workload, but the report's tier table omits its label. No unsupported
`--tier staleness-update` CLI flag is required; its three cases are recorded
under `by_source` in the complete raw evidence and summarised separately below.

## Local results (20 October 2026)

Python 3.11.16 on macOS. All eleven offline suites (including the new repair
suite) pass: engine 210 checks, harness 224, adversarial 112, repair suite 14
regression tests, MCP round trip 10 and LLM-stub judge 71 assertions, plus the
statistics, arm, oracle, HTTP-stub and synthetic suites. All Python modules
compile. The stdio regression drives a live server subprocess through invalid
input, valid requests, shutdown and persisted restart. A separate test kills an
owning process and proves a successor retains its saved facts. This is transport
verification, not a manual Claude Desktop/Code compatibility certification.

| Metric | M11 historical identity | M14 conservative identity |
|---|---:|---:|
| Aggregate evidence hit rate | 0.895 | 0.895 |
| Characters per evidence hit | 1293 | 1293 |
| nDCG@10 | 0.703 | 0.703 |
| Oracle task-success proxy | 0.545 | 0.545 |
| Explicit-supersession stale leak (3 cases) | 0.000 | 0.000 |
| Decay-only stale leak (3 cases) | 1.000 | 1.000 |
| Update without a signal stale leak (3 cases) | 0.000 | 1.000 |

The last row is an intentional loss: different wording no longer establishes
which statement is a correction. The caller must supply `supersedes` or `retire`.
The aggregate retrieval score does not penalise carrying both old and new facts;
that is why this separate stale-leak row matters. These tiny suites do not
establish general real-world accuracy, and the oracle is not a model judge.

The full 10,000-memory scale probe completed for all 17 offline arms, using
checkpointed passes. M14 retained 10,000 memories. Latency remains informational
and host-specific; the full report preserves every control and measurement.

Reproduction commands (change scratch paths as appropriate):

```bash
python3 benchmarks/run_bench.py --phase queries --state /tmp/repair-queries.json --out /tmp/repair-results --report docs/REPAIR-RESULTS.md
python3 benchmarks/run_bench.py --phase scale --state /tmp/repair-queries.json --out /tmp/repair-results --report docs/REPAIR-RESULTS.md --pass-budget 45
# Repeat the scale command if a pass checkpoints before every arm finishes.
```

The public LoCoMo cache must exist first (`python3 benchmarks/public_locomo.py`
downloads it). The first exploratory query pass omitted the missing cache and
was discarded; the published repair report uses the subsequent complete
36-scenario/209-query run. An attempted `--tier staleness-update` invocation was
rejected by the existing parser; no output from it is used as evidence.

The complete raw report is preserved losslessly as
`benchmarks/results/repair-5.5.0.json.xz`. Its original command and scratch paths
are kept in the JSON metadata. To inspect it:

```python
import json, lzma
with lzma.open("benchmarks/results/repair-5.5.0.json.xz", "rt", encoding="utf-8") as source:
    evidence = json.load(source)
print(evidence["summaries"]["M14 myelinated +exact identity"])
```

Uncompressed raw JSON SHA-256: `5e84799aaa98b6d85bd86344839edf752cb76445115dcfdb2be7fec341b1398b`.

Windows and Linux results are reported by the PR CI, not inferred from this local macOS run. No model API was called.
