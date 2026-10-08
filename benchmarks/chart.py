#!/usr/bin/env python3
"""The honest chart, in one command.

    python3 benchmarks/chart.py

Reads the committed ``benchmarks/results/raw.json`` - the evidence for every
number in ``README.md`` and ``docs/POST.md`` - and renders the comparison the
project leads with: **evidence hit rate vs characters per evidence hit**, where
up-and-to-the-left is better. It also verifies that the headline figures quoted
in the docs still match the raw evidence, so a chart and a README can never
drift apart silently again (the failure mode found in docs/POST.md).

Stdlib only. It never re-runs the benchmark and never writes anything: the
benchmark itself is ``python3 benchmarks/run_bench.py``.

Lost draws are printed as prominently as wins - that is the point of the file.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "results", "raw.json")

# The rows of the chart, in display order: (arm key in raw.json, label).
CHART_ARMS = [
    ("M0 no-memory", "No memory (control)"),
    ("M2 recency/LRU", "Recency / LRU"),
    ("M1 flat/FIFO", "Flat / FIFO store"),
    ("M4 semantic/TF-IDF", "Semantic / TF-IDF"),
    ("M3 semantic/BM25", "Semantic / BM25"),
    ("M6 myelinated (pure)", "Myelinated - as specified"),
    ("M7 myelinated +similarity", "Myelinated + similarity"),
    ("M8 myelinated +knapsack", "Myelinated + packing"),
    ("M11 myelinated +lexical ranking", "Myelinated - SHIPPED DEFAULT"),
    ("M3k BM25 +engine packer", "BM25 + engine packer (control)"),
]

# Claims made in README.md / docs/POST.md, checked against the raw evidence so
# the prose cannot drift from the data. Each is (arm, metric, expected, quoted-as).
CLAIMS = [
    ("M11 myelinated +lexical ranking", "hit_rate", 0.893, "shipped hit rate"),
    ("M11 myelinated +lexical ranking", "chars_per_hit", 1279, "shipped chars/hit"),
    ("M3k BM25 +engine packer", "hit_rate", 0.893, "control hit rate"),
    ("M3k BM25 +engine packer", "chars_per_hit", 1296, "control chars/hit"),
    ("M3 semantic/BM25", "ndcg@10", 0.732, "BM25 ranking"),
    ("M1 flat/FIFO", "chars_per_hit", 1734, "flat chars/hit"),
    ("M2 recency/LRU", "chars_per_hit", 2201, "recency chars/hit"),
]


def load() -> dict:
    if not os.path.exists(RAW):
        sys.stderr.write("missing %s - run: python3 benchmarks/run_bench.py\n" % RAW)
        raise SystemExit(2)
    with open(RAW, "r", encoding="utf-8") as handle:
        return json.load(handle)


def summaries(payload: dict) -> dict:
    """Per-arm metric summaries. The committed raw.json keys them ``summaries``."""
    arms = payload.get("summaries") or payload.get("arms") or {}
    if not isinstance(arms, dict) or not arms:
        sys.stderr.write("raw.json has no per-arm summaries\n")
        raise SystemExit(2)
    return arms


def scatter(rows) -> str:
    """A tiny text scatter: x = chars/hit (cost), y = hit rate (quality).

    40 columns x 12 rows is enough to show the frontier; the exact numbers are
    in the table below it, which is what anyone should quote.
    """
    width, height = 40, 12
    # M0 (no memory) has chars_per_hit = inf - no hits, no cost - which cannot
    # scale an axis. It is plotted only when finite; the table below shows every row.
    finite = [row for row in rows if row[3] == row[3] and row[3] != float("inf")]
    xs = [chars for _, _, _, chars in finite]
    ys = [hit for _, _, hit, _ in finite]
    lo_x, hi_x = min(xs), max(xs)
    lo_y, hi_y = min(ys), max(ys)
    grid = [[" "] * width for _ in range(height)]
    marks = {}
    for index, (key, label, hit, chars) in enumerate(rows):
        if chars != chars or chars == float("inf"):
            continue
        col = 0 if hi_x == lo_x else int(round((chars - lo_x) / (hi_x - lo_x) * (width - 1)))
        row = height - 1 if hi_y == lo_y else int(round((hit - lo_y) / (hi_y - lo_y) * (height - 1)))
        # grid[0] is the TOP line of the display (highest hit rate).
        marks[(height - 1 - row, col)] = str(index)
    for (row, col), glyph in marks.items():
        grid[row][col] = glyph
    lines = ["  hit rate ^   (0-9 = rows in the table below; up-and-left is better)"]
    # grid[0] is the top line: the highest hit rate.
    lines += ["  %6.3f |%s" % (hi_y - (hi_y - lo_y) * r / (height - 1), "".join(grid[r]))
              for r in range(height)]
    lines.append("          +" + "-" * width)
    lines.append("           %6.0f chars/evidence-hit %6.0f" % (lo_x, hi_x))
    return "\n".join(lines)


def main() -> int:
    payload = load()
    arms = summaries(payload)
    rows = []
    for key, label in CHART_ARMS:
        metrics = arms.get(key)
        if metrics is None:
            sys.stderr.write("arm missing from raw.json: %s\n" % key)
            return 2
        rows.append((key, label, metrics["hit_rate"], metrics["chars_per_hit"]))

    print("Myelinated Memory - evidence hit rate vs characters per evidence hit")
    print("source: benchmarks/results/raw.json (committed report; seed 0; offline oracle judge)")
    print()
    print(scatter(rows))
    print()
    print("  #  arm                               hit rate   chars/hit    nDCG@10  stale leak")
    for index, (key, label, hit, chars) in enumerate(rows):
        m = arms[key]
        chars_text = "%10.0f" % chars if chars == chars and chars != float("inf") else "         -"
        ndcg = m.get("ndcg@10", 0.0)
        leak = m.get("stale_leak_rate", 0.0)
        print("  %d  %-33s %9.3f %s %10.3f %12.3f" % (index, label, hit, chars_text, ndcg, leak))
    print()
    print("Read the top rows as the honest picture: the shipped engine reaches the best")
    print("hit rate at the lowest characters per hit of any ranked arm, while plain BM25")
    print("still orders evidence better (nDCG). Wins and losses are from the same run.")
    print("stale leak above is the mixed column; the split supersession/decay-only suites")
    print("are in RESULTS.md - supersession leaks 0.000, decay alone still leaks 1.000.")

    print()
    print("Checking quoted claims against the evidence:")
    failures = 0
    for key, metric, expected, quoted_as in CLAIMS:
        actual = arms.get(key, {}).get(metric)
        rounded = round(actual) if isinstance(actual, float) and actual == actual else actual
        # Published numbers are quoted at the precision of the report: 3dp for
        # rates, whole characters for costs.
        if metric == "hit_rate" or metric == "ndcg@10":
            match = abs(actual - expected) < 0.0005
            shown = "%.3f" % actual
        else:
            match = rounded == expected
            shown = "%.0f" % actual
        status = "ok " if match else "MISMATCH"
        failures += 0 if match else 1
        print("  %s %-24s quoted %-6s evidence %s" % (status, quoted_as, expected, shown))
    if failures:
        print()
        print("%d claim(s) no longer match raw.json - fix the prose or re-run the benchmark." % failures)
        return 1
    print()
    print("all quoted claims match the committed evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
