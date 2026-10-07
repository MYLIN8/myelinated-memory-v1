"""Verification that the LLM judge code path works, without touching a real model.

There is no OpenAI key in this environment. Where keys *do* exist (this
workspace carries a free-tier ``GEMINI_KEY`` and an ``NVIDIA_CLOUD_KEY``), these
tests still never call them: every request goes to `benchmarks/stub_llm.py`, a
local server that speaks the same chat-completions wire protocol. That matters
more than it sounds - a test suite that quietly spends the user's quota is worse
than no test suite, so the key names are cleared and restored around every
selection check.

Scope, stated plainly: it verifies REQUEST SHAPE, RESPONSE PARSING, the
per-instance CACHE, the HTTP ERROR path, the RATE-LIMIT pacing, the RETRY path
and the CALL BUDGET. It measures nothing about model quality. Real LLM-judged
benchmark numbers require a key and a real endpoint.

Run it as a script so `benchmarks/` is the sys.path root:

    python3 benchmarks/test_llm_judge.py     # from the repo root
    cd benchmarks && python3 test_llm_judge.py

Any failure raises AssertionError, so the process exits non-zero.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict

from judge import (  # noqa: E402 - sys.path root is benchmarks/
    DEFAULT_MAX_CALLS,
    GeminiJudge,
    JudgeError,
    NvidiaJudge,
    OpenAIJudge,
    OracleJudge,
    describe,
    make_judge,
)
from stub_llm import reset_throttle, serve_in_thread  # noqa: E402

# Every environment name any judge will look at. Tests clear all of them, so the
# result never depends on which keys the machine running them happens to have.
_KEY_NAMES = ("OPENAI_API_KEY", "GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "NVIDIA_CLOUD_KEY", "NVIDIA_API_KEY")


@contextmanager
def keys(**overrides: str):
    """Run a block with exactly ``overrides`` set and every other key absent."""
    saved: Dict[str, str] = {name: os.environ[name] for name in _KEY_NAMES
                             if name in os.environ}
    try:
        for name in _KEY_NAMES:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name in _KEY_NAMES:
            os.environ.pop(name, None)
        os.environ.update(saved)


def main() -> int:
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        assert condition, "check %d failed: %s" % (checks, message)

    _server, base_url, shutdown = serve_in_thread()
    try:
        judge = OpenAIJudge(api_key="stub-key", base_url=base_url, model="stub-model",
                            min_interval=0.0)
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
        check(judge.cache_hits >= 1, "a cache hit must be counted for the report")

        zero = judge.grade("Which cluster?", "staging cluster", "STUB_SCORE_0 candidate", context)
        check(zero == 0.0, "a stub verdict of 0 must grade as 0.0, got %r" % zero)
        check(judge.calls == before + 2, "the forced-zero call is a distinct request")

        # The error path must raise, never silently score. A 500 is terminal: it
        # is a server defect, not a rate limit, so it must not be retried.
        raised = None
        try:
            judge.grade("forced", "gold", "STUB_FAIL please fail", context)
        except JudgeError as exc:
            raised = exc
        check(raised is not None, "an HTTP 500 must raise JudgeError")
        check("500" in str(raised), "the error must carry the HTTP status, got %r" % str(raised))
        check(judge.retries == 0, "a 500 must not be retried, got %d retries" % judge.retries)

        # ------------------------------------------------------------------ pacing
        # The free tier is the binding constraint, so the limiter is tested with
        # an injected sleeper: the assertions are about *whether and how much*
        # the judge waits, not about burning wall-clock seconds.
        sleeps = []
        paced = GeminiJudge(api_key="stub-key", base_url=base_url, model="stub-model",
                            min_interval=0.25, sleep=sleeps.append, jitter=lambda: 0.0)
        check(paced.mode == "gemini", "the paced judge must report mode gemini")
        paced.answer("Which cluster do we deploy to?", context)
        check(sleeps == [], "the first request must not wait: pacing is between calls")
        paced.answer("What is the rollback window?", "The rollback window is 30 minutes.")
        check(len(sleeps) == 1, "a second distinct request must be paced, slept %r" % sleeps)
        # The limiter subtracts the time the previous request already spent, so
        # the invariant is the *spacing* between request starts (>= interval),
        # not the length of the sleep on its own; a local stub round trip is
        # under a millisecond and a loaded machine is not much more, so a small
        # tolerance is the honest assertion here.
        check(sleeps[0] > 0.0, "a second request must actually pause, got %r" % sleeps[0])
        check(sleeps[0] >= 0.25 - 0.05,
              "request starts must stay at least the interval apart, got %r" % sleeps[0])
        check(paced.waited_s >= sleeps[0], "waited_s must accumulate for the report")
        hits_before = paced.cache_hits
        paced.answer("Which cluster do we deploy to?", context)
        check(paced.cache_hits == hits_before + 1, "a repeated payload must hit the cache")
        check(len(sleeps) == 1, "a cached payload must never be paced again")

        # Free-tier defaults, checked without a request. Compared against the
        # module constants rather than literals, so a deliberate override in the
        # environment cannot make the suite flaky.
        default_gem = GeminiJudge(api_key="stub-key")
        check(GeminiJudge.default_min_interval_s == 4.0,
              "the documented Gemini pace is one request every 4 s (about 15 RPM)")
        check(default_gem.min_interval >= GeminiJudge.default_min_interval_s,
              "the default pace must not be faster than the documented one, got %r"
              % default_gem.min_interval)
        if os.environ.get("JUDGE_MAX_CALLS") is None:
            check(default_gem.max_calls == DEFAULT_MAX_CALLS,
                  "the default call budget must be the documented one, got %r"
                  % default_gem.max_calls)
        check(default_gem.max_calls > 0, "a call budget of zero would forbid every request")
        check(default_gem.base_url.endswith("generativelanguage.googleapis.com/v1beta/openai"),
              "the default endpoint must be Gemini's OpenAI-compatible one, got %r"
              % default_gem.base_url)

        # ------------------------------------------------------------------ retry
        backoff = GeminiJudge(api_key="stub-key", base_url=base_url, model="stub-model",
                              min_interval=0.0, sleep=lambda _s: None, jitter=lambda: 0.0)
        check(backoff._backoff(1, None) == 0.5, "the first backoff must be 0.5 s")
        check(backoff._backoff(2, None) == 1.0, "the backoff must double")
        check(backoff._backoff(3, 7.0) == 7.0, "Retry-After must win when the server sends one")

        retry_sleeps = []
        flaky = GeminiJudge(api_key="stub-key", base_url=base_url, model="stub-model",
                            min_interval=0.0, sleep=retry_sleeps.append, jitter=lambda: 0.0)
        reset_throttle()
        score = flaky.grade("Which cluster?", "staging cluster",
                            "STUB_429_ONCE candidate", context)
        check(score == 1.0, "a 429 must be retried and the retry must be graded")
        check(flaky.retries == 1, "exactly one retry must be recorded, got %d" % flaky.retries)
        check(flaky.calls == 1, "a throttled attempt is not a completed call, got %d" % flaky.calls)
        check(len(retry_sleeps) == 1,
              "the retry must wait once (the stub sends Retry-After: 0), got %r" % retry_sleeps)

        # ------------------------------------------------------------------ budget
        capped = GeminiJudge(api_key="stub-key", base_url=base_url, model="stub-model",
                             min_interval=0.0, max_calls=1, sleep=lambda _s: None)
        capped.answer("Which cluster do we deploy to?", context)
        exhausted = None
        try:
            capped.answer("What is the rollback window?", "The rollback window is 30 minutes.")
        except JudgeError as exc:
            exhausted = exc
        check(exhausted is not None, "an exhausted budget must raise, never score a wrong answer")
        check("JUDGE_MAX_CALLS" in str(exhausted),
              "the error must name the budget env var, got %r" % str(exhausted))
        check(capped.calls == 1, "no request may be sent after the budget is spent")

        counters = capped.counters()
        check(counters["calls"] == 1 and counters["max_calls"] == 1,
              "counters() must report what the report prints, got %r" % counters)

        # ------------------------------------------------------- judge selection
        with keys():
            auto = make_judge("auto")
            check(isinstance(auto, OracleJudge), "auto without any key must pick the oracle judge")
            check(auto.is_llm is False, "the oracle judge is not an LLM")
            no_llm = None
            try:
                make_judge("llm")
            except JudgeError as exc:
                no_llm = exc
            check(no_llm is not None, "llm mode with no key must raise JudgeError")
            check("GEMINI_KEY" in str(no_llm),
                  "the error must name the key to set, got %r" % str(no_llm))

            for mode in ("openai", "gemini", "nvidia"):
                forced = None
                try:
                    make_judge(mode)
                except JudgeError as exc:
                    forced = exc
                check(forced is not None, "%s mode without a key must raise JudgeError" % mode)
                check("is not set" in str(forced),
                      "the error must explain the missing key, got %r" % str(forced))

        with keys(GEMINI_KEY="stub-key"):
            # A key in the environment must NOT redirect the default run: the
            # canonical command stays offline, deterministic and free (D16 and
            # README 8), and the model judge is one flag away.
            check(isinstance(make_judge("auto"), OracleJudge),
                  "auto must stay the offline judge even when a key is present")
            picked = make_judge("llm")
            check(isinstance(picked, GeminiJudge),
                  "llm with only GEMINI_KEY set must pick the Gemini judge")
            check("GEMINI_KEY" not in str(picked.model), "a key must never appear in a label")

        with keys(GEMINI_KEY="stub-key", OPENAI_API_KEY="other-key"):
            check(isinstance(make_judge("llm"), GeminiJudge),
                  "Gemini is preferred while it is the key this workspace has")

        with keys(OPENAI_API_KEY="other-key"):
            check(isinstance(make_judge("llm"), OpenAIJudge),
                  "with no Gemini key, llm must fall back to the OpenAI judge")
            check(isinstance(make_judge("auto"), OracleJudge),
                  "auto must stay the offline judge with only an OpenAI key too")

        with keys(NVIDIA_CLOUD_KEY="nv-key"):
            check(isinstance(make_judge("llm"), NvidiaJudge),
                  "with only an NVIDIA key, llm must fall back to the NVIDIA judge")
            check(isinstance(make_judge("auto"), OracleJudge),
                  "auto must stay the offline judge with only an NVIDIA key too")

        # NVIDIA is the last resort in the llm order: a Gemini or OpenAI key must
        # still win, so adding a provider cannot silently change which model
        # judged an existing run.
        with keys(GEMINI_KEY="stub-key", NVIDIA_CLOUD_KEY="nv-key"):
            check(isinstance(make_judge("llm"), GeminiJudge),
                  "Gemini must keep priority over NVIDIA")
        with keys(OPENAI_API_KEY="other-key", NVIDIA_CLOUD_KEY="nv-key"):
            check(isinstance(make_judge("llm"), OpenAIJudge),
                  "OpenAI must keep priority over NVIDIA")

        oracle_text = describe(OracleJudge())
        llm_text = describe(judge)
        check("not a language model" in oracle_text,
              "the oracle description must refuse the LLM label, got %r" % oracle_text)
        check("stub-model" in llm_text, "the LLM description must name the model, got %r" % llm_text)
        check(llm_text.startswith("LLM judge"), "the LLM description must say LLM judge")
        gem_text = describe(GeminiJudge(api_key="stub-key"))
        # Compared against the constant, not a literal: a retired model name is
        # an HTTP 404 from the live endpoint, so the default does move.
        check(gem_text == "LLM judge (gemini, %s)" % GeminiJudge.default_model,
              "a Gemini run must be unmistakable in the report, got %r" % gem_text)

        # The NVIDIA provider is only wired, never called here: the assertions are
        # about the request target and the report label, because a live call needs
        # a key and a network this suite deliberately does not use.
        nv_default = NvidiaJudge(api_key="stub-key")
        check(nv_default.mode == "nvidia" and nv_default.is_llm is True,
              "the NVIDIA judge must be a model judge")
        check(nv_default.model == NvidiaJudge.default_model,
              "the NVIDIA model id must come from the constant, got %r" % nv_default.model)
        check(nv_default.base_url.endswith("integrate.api.nvidia.com/v1"),
              "the NVIDIA endpoint must be the OpenAI-compatible NIM base, got %r"
              % nv_default.base_url)
        check(nv_default.calls == 0, "constructing a judge must not spend a request")
        nv_text = describe(NvidiaJudge(api_key="stub-key"))
        check(nv_text == "LLM judge (nvidia, %s)" % NvidiaJudge.default_model,
              "an NVIDIA run must be unmistakable in the report, got %r" % nv_text)
    finally:
        shutdown()

    print("llm judge ok: %d assertions" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
