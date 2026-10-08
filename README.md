# Myelinated Memory

> **A Hebbian retrieval-strength memory engine for LLM agents.**

[![License: Source-Available](https://img.shields.io/badge/license-Source--Available-red.svg)](LICENSE.md)
[![Python 3.10 | 3.11](https://img.shields.io/badge/python-3.10%20%7C%203.11-3776ab.svg)](CONTRIBUTING.md)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](SECURITY.md)
[![Checks](https://img.shields.io/badge/checks-10%20offline%20suites%20%7C%20181%20engine%20%7C%20204%20harness%20%7C%2088%20adversarial%20%7C%2071%20judge-brightgreen.svg)](#4-how-it-is-tested)
[![CI](https://github.com/TWS07-gif/myelinated-memory/actions/workflows/checks.yml/badge.svg)](https://github.com/TWS07-gif/myelinated-memory/actions/workflows/checks.yml)
[![Benchmark](https://img.shields.io/badge/benchmark-results-orange.svg)](benchmarks/RESULTS.md)

Myelinated Memory is a context-management system that simulates neural consolidation. Each memory carries a retrieval-strength score that rises when it is used and decays when it is ignored; the store keeps explicit state, can retire stale facts, and budgets the context it returns.

This project is a personal research and hobby project maintained by Tom Sturgeon.

## Licensing

This repository uses one active license only:

- Myelinated Memory Source-Available Licence

The complete text is in [LICENSE.md](LICENSE.md).

This is a single, clear licensing model. It keeps the rights with Tom Sturgeon while allowing personal, educational, research, and evaluation use. Commercial use, redistribution, hosting, modification, and publication without permission are restricted.

This is intentional. The project is shared for study and experimentation, but it is not intended to become a free-for-all commercial product without permission.

## Maintainer

This project is maintained by Tom Sturgeon.

I am keeping the rights to this project because it is a hobby and research project, but I still want clear control over how it is used, distributed, and commercialised.

## Why a single license is the cleanest choice

A single license is simpler, clearer, and easier to defend legally.

- One active license means no ambiguity about what is allowed
- The project has a clear legal stance without mixing standards
- It avoids inconsistent messaging between repo files
- It keeps the project honest and readable for anyone evaluating it

A project does not need multiple licenses if one custom license already covers the intended use case.

## Project status

This is a hobby research project, not a company project or a public unrestricted open-source release.

It is provided for learning, evaluation, and research under the terms of the project license, with attribution to Tom Sturgeon.

---

© 2026 Tom Sturgeon. All rights reserved.
