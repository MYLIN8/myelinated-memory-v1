"""Verification that the LLM judge code path works, without an API key.

There is no `OPENAI_API_KEY` in this environment, so a real model cannot be
called. This test therefore runs `judge.OpenAIJudge` against
`benchmarks/stub_llm.py`, a local server that speaks the OpenAI
chat-completions wire protocol.

Scope, stated plainly: it verifies REQUEST SHAPE, RESPONSE PARSING, the
per-instance CACHE and the HTTP ERROR path. It measures nothing about model
quality. Real LLM-judged benchmark numbers require OPENAI_API_KEY in
Settings -> Environment plus a real endpoint.

Run it as a script so `benchmarks/` is the sys.path root:

    python3 benchmarks/test_llm_judge.py     # from the repo root
    cd benchmarks && python3 test_llm_judge.py

Any failure raises AssertionError, so the process exits non-zero.
"""

from __future__ import annotations

import os

from judge import JudgeError, OpenAIJudge, OracleJudge, describe, make_judge
from stub_llm import serve_in_thread


def main() -> int:
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        assert condition, "check %d failed: %s" % (checks, message)

    _server, base_url, shutdown = serve_in_thread()
    try:
        judge = OpenAIJudge(api_key="stub-key", base_url=base_url, model="stub-model")
        check(judge.mode == "openai", "mode should be openai")
        check(judge.is_llm is True, "the LLM judge must report is_llm True")
        check(judge.base_url == base_url, "base_url should be the stub, got %r" % judge.base_url)
        check(judge.model == "stub-model", "model should be taken from the argument")
        check(judge.calls == 0, "no requests should have been made yet")

        context = ("The deploy target is the staging cluster.\n"
                   "The rollback window is 30 minutes.")
        answer = judge.answer("Which cluster do we deploy to?", context)
        check(bool(answer), "answer() must return a non-empty string")
        check("staging cluster" in answer,
              "answer() must be derived from the supplied context, got %r" % answer)
        check(judge.calls == 1, "one request should have been sent, got %d" % judge.calls)

        # Grading: the stub returns {"score": 1} unless the candidate carries
        # the forced-zero marker.
        before = judge.calls
        score = judge.grade("Which cluster?", "staging cluster", answer, context)
        check(score == 1.0, "a stub verdict of 1 must grade as 1.0, got %r" % score)
        check(judge.calls == before + 1, "grading should send exactly one request")

        repeat = judge.grade("Which cluster?", "staging cluster", answer, context)
        check(repeat == 1.0, "the cached verdict must be unchanged")
        check(judge.calls == before + 1,
              "an identical grade call must be served from cache, got %d calls" % judge.calls)

        zero = judge.grade("Which cluster?", "staging cluster", "STUB_SCORE_0 candidate", context)
        check(zero == 0.0, "a stub verdict of 0 must grade as 0.0, got %r" % zero)
        check(judge.calls == before + 2, "the forced-zero call is a distinct request")

        # The error path must raise, never silently score.
        raised = None
        try:
            judge.grade("forced", "gold", "STUB_FAIL please fail", context)
        except JudgeError as exc:
            raised = exc
        check(raised is not None, "an HTTP 500 must raise JudgeError")
        check("500" in str(raised), "the error must carry the HTTP status, got %r" % str(raised))

        # Judge selection must follow the presence of the key, not the caller.
        saved = os.environ.pop("OPENAI_API_KEY", None)
        try:
            auto = make_judge("auto")
            check(isinstance(auto, OracleJudge), "auto without a key must pick the oracle judge")
            check(auto.is_llm is False, "the oracle judge is not an LLM")

            forced = None
            try:
                make_judge("openai")
            except JudgeError as exc:
                forced = exc
            check(forced is not None, "openai mode without a key must raise JudgeError")
        finally:
            if saved is not None:
                os.environ["OPENAI_API_KEY"] = saved

        oracle_text = describe(OracleJudge())
        llm_text = describe(judge)
        check("not a language model" in oracle_text,
              "the oracle description must refuse the LLM label, got %r" % oracle_text)
        check("stub-model" in llm_text, "the LLM description must name the model, got %r" % llm_text)
        check(llm_text.startswith("LLM judge"), "the LLM description must say LLM judge")
    finally:
        shutdown()

    print("llm judge ok: %d assertions" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
