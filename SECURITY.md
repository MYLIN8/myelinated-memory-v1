# Security Policy

## Reporting a vulnerability

Open a GitHub issue, or use GitHub's private vulnerability reporting if the finding is
sensitive. Please do not publish working exploit details before a fix is available.

## What this project is

**Myelinated Memory is a local memory store plus a benchmark harness.** They have different
security properties, and the earlier version of this file conflated them.

### The engine (`scripts/myelinate.py`)

- **Network:** none. It opens no socket and imports nothing outside the standard library.
- **Data access:** one local JSON file. The path is `--store PATH`, else `$HERMES_MEMORY_STORE`,
  else `~/.hermes/memory/myelinated.json`. `save()` writes a temporary file and renames it, so an
  interrupted write cannot leave a half-written store.
- **Untrusted input:** **memory content is data, never instructions, and nothing sanitises it.**
  The engine does not escape, validate or length-limit stored text, and it renders a memory into
  the context budget verbatim. If you inject third-party text (a web page, an email, a tool
  result) into the store, that text can attempt to steer whatever model later reads the context.
  Treat the store as an injection surface: keep untrusted content out, or filter it upstream.
- **File permissions:** the store is created with the process umask; the engine does not set a mode.

### The benchmark harness (`benchmarks/`)

The harness is not part of the engine and **does** make network calls. All of them are opt-in:

- `benchmarks/public_locomo.py` downloads the public LoCoMo dataset once, on demand, from
  `raw.githubusercontent.com`. `benchmarks/data/` is git-ignored.
- `benchmarks/judge.py` posts to the OpenAI API only when a judge is given a key
  (`--judge openai`).
- `benchmarks/engines.py` calls the embeddings API only with `--network`.

The offline default (`python3 benchmarks/run_bench.py`) uses the deterministic local judge and
touches no network. The seven offline self-checks listed in
[README §4](README.md#4-how-it-is-tested) never do either.

## Supported versions

The engine targets Python 3.10 and 3.11 and has no dependencies to keep patched.

## License

MIT — see [LICENSE.md](LICENSE.md).
