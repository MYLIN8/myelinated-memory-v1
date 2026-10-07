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
- `benchmarks/judge.py` posts to a model API only when a judge is given a key. Two
  endpoints are supported: OpenAI (`--judge openai`) and Google's
  OpenAI-compatible Gemini endpoint (`--judge gemini`), or `--judge llm` for the
  first of the two that has a key, Gemini first. The key is read from the
  environment by name only (`GEMINI_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`,
  `OPENAI_API_KEY`), is never logged and never written into a report, and the
  judge that ran is named in the report header. Gemini runs are paced at 4 s per
  request by default and retry `429`/`502`/`503`/`504` with backoff, because a
  free-tier key is the binding constraint.
- The **default judge is not a model.** `python3 benchmarks/run_bench.py` uses the
  deterministic local oracle, and `--judge auto` (the default) stays on it even
  when an API key is present in the environment: an offline, reproducible run
  must not become a network run by accident.
- `benchmarks/engines.py` calls the embeddings API only with `--network`.

None of the network paths is the default. The offline self-checks listed in
[README §4](README.md#4-how-it-is-tested) never open a socket either, and the
judge protocol is tested against a local stub, so no key is needed to run them.
Memory text is not sanitised anywhere in this project: the harness passes
retrieved content to the judge verbatim, exactly as the engine does, so a store
that contains untrusted text is an injection surface into the answering model as
well.

## Supported versions

The engine targets Python 3.10 and 3.11 and has no dependencies to keep patched.

## License

MIT — see [LICENSE.md](LICENSE.md).
