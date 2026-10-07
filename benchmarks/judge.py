"""Task-level evaluation for the Myelinated Memory benchmark.

Pipeline: an arm retrieves a CONTEXT for a question -> a model answers from
that context only -> a judge grades the answer against the gold answer. This
module owns the last two steps.

Three judges exist, and the difference between them is the single most
important caveat in the whole report.

``OracleJudge`` is **a proxy for task success, not a language model.** It never
calls a network. It "answers" by returning the retrieved context line that
overlaps the question most, and it grades by checking whether the gold answer is
contained in (or numerically recoverable from) that answer or its context. So it
measures *evidence availability*: whether the right facts were placed inside the
budget at all. It cannot measure reasoning, paraphrase, or a model's willingness
to say "unknown".

``OpenAIJudge`` and ``GeminiJudge`` send real chat completions requests and grade
with an LLM. They share one transport (``_ChatJudge``) because they speak the
same wire protocol: Google exposes an OpenAI-compatible endpoint for Gemini, so
the request shape, the parsing, the cache and the error path are verified once
and used by both. ``GeminiJudge`` exists because this workspace has a free-tier
Gemini key and no OpenAI key - and a free tier is exactly the situation where a
careless client turns a benchmark run into a 429 storm, which is why it carries
a client-side rate limiter (see below).

Why the distinction must be reported: a system that wins under the oracle judge
has proven only that its retrievals contain the answer string. A system that
wins under the LLM judge has proven that a model could actually answer from its
context. These are different claims, and an oracle-only result advertised as
"task success" would overstate the evidence. Reports must therefore name which
judge produced every number.

Rate limiting, and its defaults
-------------------------------
A benchmark query costs **two** LLM calls (answer, then grade), so the free tier
is the binding constraint, not the model. ``GeminiJudge`` therefore defaults to
one request every ``JUDGE_MIN_INTERVAL`` seconds (4.0 s, about 15 requests per
minute), and every network call is preceded by that check; a cached payload
never waits at all, because paying latency for a response already in memory is
pure waste.

Three further guards, all deliberate:

* a process-wide **call budget** (``JUDGE_MAX_CALLS``, default 400) that raises
  ``JudgeError`` when exhausted. Hitting a budget must be *reported*, never
  silently scored as a wrong answer - a judge that returns 0.0 because it ran
  out of quota would corrupt every downstream average;
* **retry with exponential backoff plus jitter** on the statuses that mean "ask
  again later" (429, 502, 503, 504), honouring ``Retry-After`` when the server
  sends one. A plain 500 is *not* retried: a server-side bug is not a rate
  limit, and retrying it only burns quota;
* the per-payload cache, which both saves quota and makes a re-run of the same
  probe cheap and deterministic.

Nothing here raises the limits on its own, and no key value is ever printed.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from common import jaccard, normalize, tokenize

_MONTHS = frozenset(
    """january february march april may june july august september october november
    december""".split()
)
_NUMBER_RE = re.compile(r"\d+")
_SCORE_RE = re.compile(r'"score"\s*:\s*([01])\b')
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")

# Statuses that mean "the request was fine, try again shortly". 500 is absent on
# purpose: a server error is a defect, not a rate limit.
RETRYABLE_STATUSES = (429, 502, 503, 504)
DEFAULT_MAX_CALLS = 400
DEFAULT_MAX_RETRIES = 3
MAX_BACKOFF_S = 60.0


class JudgeError(RuntimeError):
    """Raised when a judge call fails; never swallowed into a fake score."""


class RetryableJudgeError(JudgeError):
    """A call that failed for a reason the caller may retry (429 and friends)."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# ------------------------------------------------------------------ helpers
def _numbers(text: str) -> set:
    return set(_NUMBER_RE.findall(text or ""))


def _month_words(text: str) -> set:
    return {w for w in tokenize(text) if w in _MONTHS}


def _gold_in(gold: str, haystack: str) -> bool:
    """Containment, numeric, and date-tolerant matching of the gold answer."""
    gold_norm = normalize(gold)
    if not gold_norm:
        return False
    if gold_norm in haystack:
        return True

    gold_nums = _numbers(gold)
    if gold_nums and gold_nums & _numbers(haystack):
        return True

    gold_months = _month_words(gold)
    if gold_months and _month_words(haystack) & gold_months:
        return True
    return False


def _first_env(names: Tuple[str, ...]) -> Optional[str]:
    """The first of ``names`` that is set and non-empty. Names only, never values."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Judge:
    """Base class. ``mode`` names the judge; ``is_llm`` says whether it is one."""

    mode = "base"
    is_llm = False
    model = ""

    def __init__(self) -> None:
        self.calls = 0

    def answer(self, question: str, context: str) -> str:
        raise NotImplementedError

    def grade(self, question: str, gold: str, model_answer: str,
              context: str = "", check: str = "contains") -> float:
        raise NotImplementedError

    def counters(self) -> Dict[str, float]:
        """Counters the report prints beside an LLM-judged table."""
        return {
            "calls": float(getattr(self, "calls", 0)),
            "retries": float(getattr(self, "retries", 0)),
            "cache_hits": float(getattr(self, "cache_hits", 0)),
            "waited_s": round(float(getattr(self, "waited_s", 0.0)), 3),
            "max_calls": float(getattr(self, "max_calls", 0)),
        }


# ------------------------------------------------------------------- oracle
class OracleJudge(Judge):
    """Deterministic, offline proxy for task success. Not a language model."""

    mode = "oracle"
    is_llm = False

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.model = "evidence-containment"

    def answer(self, question: str, context: str) -> str:
        lines = [line.strip() for line in (context or "").splitlines() if line.strip()]
        if not lines:
            return ""
        q_tokens = tokenize(question)
        best: Optional[str] = None
        best_key: Tuple[float, int] = (-1.0, -1)
        for line in lines:
            score = jaccard(q_tokens, tokenize(line))
            key = (score, len(line))
            if key > best_key:
                best_key = key
                best = line
        return best or ""

    def grade(self, question: str, gold: str, model_answer: str,
              context: str = "", check: str = "contains") -> float:
        try:
            if not gold:
                return 0.0
            if check == "exact":
                if normalize(model_answer) and normalize(model_answer) == normalize(gold):
                    return 1.0
                return 0.0
            haystack = normalize(model_answer) + " " + normalize(context)
            if _gold_in(gold, haystack):
                return 1.0
            return 0.0
        except Exception:
            # A grader must never crash a benchmark run.
            return 0.0


# ------------------------------------------------------------------- chat llm
_ANSWER_SYSTEM = (
    "You answer questions using ONLY the provided context. "
    "Be terse: reply with the answer itself, no preamble. "
    'If the context does not contain the answer, reply exactly "unknown".'
)

_GRADE_SYSTEM = (
    "You are a strict grader. Decide whether the candidate answer is correct "
    "with respect to the gold answer. Accept paraphrase, dates and numbers that "
    "refer to the same fact. Reply with STRICT JSON only, no prose, no code "
    'fences: {"score": 0 or 1, "reason": "short reason"}.'
)


class _ChatJudge(Judge):
    """LLM judge over an OpenAI-compatible chat-completions endpoint.

    Subclasses supply where the key and the defaults come from; the request
    shape, the cache, the rate limiter and the retry policy live here so every
    provider behaves identically under load.
    """

    mode = "chat"
    is_llm = True
    # Environment variable names that may hold this provider's key, in order.
    env_keys: Tuple[str, ...] = ()
    default_base_url = ""
    default_model = ""
    base_url_env = "OPENAI_BASE_URL"
    model_env = "OPENAI_MODEL"
    # 0.0 = no client-side pacing (a paid endpoint does not need it).
    default_min_interval_s = 0.0

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 60.0,
                 min_interval: Optional[float] = None, max_calls: Optional[int] = None,
                 max_retries: Optional[int] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 jitter: Optional[Callable[[], float]] = None, **kwargs) -> None:
        super().__init__()
        key = api_key if api_key is not None else _first_env(self.env_keys)
        if not key:
            raise JudgeError(
                "%s is not set, so the %s judge cannot run. Add it in "
                "Settings -> Environment, or use the oracle judge."
                % (" / ".join(self.env_keys) or "no API key", self.mode)
            )
        self.api_key = key
        self.base_url = (base_url or os.environ.get(self.base_url_env)
                         or self.default_base_url).rstrip("/")
        self.model = model or os.environ.get(self.model_env) or self.default_model
        self.timeout = float(timeout)
        self.min_interval = (self.default_min_interval_s if min_interval is None
                             else float(min_interval))
        if min_interval is None:
            self.min_interval = _env_float("JUDGE_MIN_INTERVAL", self.min_interval)
        self.max_calls = (DEFAULT_MAX_CALLS if max_calls is None else int(max_calls))
        if max_calls is None:
            self.max_calls = int(_env_float("JUDGE_MAX_CALLS", self.max_calls))
        self.max_retries = (DEFAULT_MAX_RETRIES if max_retries is None else int(max_retries))
        if max_retries is None:
            self.max_retries = int(_env_float("JUDGE_MAX_RETRIES", self.max_retries))
        # Injectable so tests can run the limiter and the backoff without
        # spending real seconds, and so a caller can swap in a smarter sleeper.
        self._sleep = sleep if sleep is not None else time.sleep
        self._jitter = jitter if jitter is not None else random.random
        self._last_request_at: Optional[float] = None
        self._cache: Dict[str, str] = {}
        self.retries = 0
        self.cache_hits = 0
        self.waited_s = 0.0

    # ------------------------------------------------------------- pacing
    def _pace(self) -> None:
        """Wait out the minimum interval. Never called for a cached payload."""
        if self.min_interval <= 0.0:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            wait = self.min_interval - (now - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
                self.waited_s += wait
        self._last_request_at = time.monotonic()

    def _backoff(self, attempt: int, retry_after: Optional[float]) -> float:
        """Seconds to wait before retry ``attempt`` (1-based), with jitter."""
        if retry_after is not None and retry_after >= 0:
            delay = retry_after
        else:
            delay = 0.5 * (2 ** (attempt - 1))
        return min(delay + self._jitter() * 0.25, MAX_BACKOFF_S)

    # --------------------------------------------------------------- transport
    def _post(self, payload: Dict) -> str:
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            message = "chat completions failed: HTTP %s: %s" % (exc.code, detail)
            if exc.code in RETRYABLE_STATUSES:
                raise RetryableJudgeError(message, retry_after=_retry_after(exc)) from exc
            raise JudgeError(message) from exc
        except Exception as exc:  # URLError, timeout, ...
            raise JudgeError("chat completions failed: %s" % (exc,)) from exc

    def _chat(self, messages: List[Dict[str, str]]) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.0}
        cache_key = json.dumps(payload, sort_keys=True)
        if cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]

        if self.calls >= self.max_calls:
            raise JudgeError(
                "judge call budget exhausted (JUDGE_MAX_CALLS=%d after %d calls): "
                "refusing to send another request rather than scoring it as a "
                "failure. Raise JUDGE_MAX_CALLS, or cap the run with --judge-limit."
                % (self.max_calls, self.calls)
            )

        attempt = 0
        while True:
            self._pace()
            try:
                body = self._post(payload)
                break
            except RetryableJudgeError as exc:
                if attempt >= self.max_retries:
                    raise JudgeError(
                        "%s (gave up after %d retries)" % (exc, attempt)
                    ) from exc
                attempt += 1
                self.retries += 1
                delay = self._backoff(attempt, exc.retry_after)
                self._sleep(delay)
                self.waited_s += delay

        self.calls += 1
        try:
            parsed = json.loads(body)
            text = parsed["choices"][0]["message"]["content"]
        except Exception as exc:
            raise JudgeError("could not parse chat completion: %s" % (body[:300],)) from exc
        self._cache[cache_key] = text
        return text

    # -------------------------------------------------------------- interface
    def answer(self, question: str, context: str) -> str:
        text = self._chat([
            {"role": "system", "content": _ANSWER_SYSTEM},
            {"role": "user", "content": "Context:\n%s\n\nQuestion: %s" % (context, question)},
        ])
        return (text or "").strip()

    def grade(self, question: str, gold: str, model_answer: str,
              context: str = "", check: str = "contains") -> float:
        raw = self._chat([
            {"role": "system", "content": _GRADE_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Question: %s\nGold answer: %s\nCandidate answer: %s\n\n"
                    "Is the candidate answer correct?" % (question, gold, model_answer)
                ),
            },
        ])
        score, _reason = _parse_score(raw)
        return score


def _retry_after(exc: "urllib.error.HTTPError") -> Optional[float]:
    """Seconds from a Retry-After header, when the server sends one."""
    raw = (exc.headers.get("Retry-After") if exc.headers else None)
    if not raw:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


class OpenAIJudge(_ChatJudge):
    """LLM judge over any OpenAI-compatible endpoint (the original provider)."""

    mode = "openai"
    env_keys = ("OPENAI_API_KEY",)
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4o-mini"
    base_url_env = "OPENAI_BASE_URL"
    model_env = "OPENAI_MODEL"


class GeminiJudge(_ChatJudge):
    """Gemini over Google's OpenAI-compatible endpoint.

    Paced by default because the key in this workspace is free-tier: a 206-query
    run is over 400 calls, and the free tier is measured in requests per minute.
    """

    mode = "gemini"
    # GEMINI_KEY is the name this workspace's .env.local actually uses; the other
    # two are accepted so a differently-named key still works.
    env_keys = ("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Retired model names are answered with HTTP 404, so this is checked against
    # the live endpoint rather than trusted: the earlier default
    # (gemini-2.0-flash) came back "no longer available, use gemini-3.8-flash".
    # Override with GEMINI_MODEL when Google moves again.
    default_model = "gemini-3.8-flash"
    base_url_env = "GEMINI_BASE_URL"
    model_env = "GEMINI_MODEL"
    default_min_interval_s = 4.0


def _parse_score(raw: str) -> Tuple[float, str]:
    """Extract a 0/1 score from a judge reply, tolerating fences and prose."""
    text = _FENCE_RE.sub("", (raw or "").strip()).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "score" in parsed:
            return float(parsed["score"]), str(parsed.get("reason", ""))
        if isinstance(parsed, (int, float)):
            return float(parsed), ""
    except Exception:
        pass
    match = _SCORE_RE.search(text)
    if match:
        return float(match.group(1)), text[:200]
    match = re.search(r"\b([01])\b", text)
    if match:
        return float(match.group(1)), text[:200]
    raise JudgeError("could not parse a 0/1 score from judge reply: %r" % (raw or "")[:300])


# ------------------------------------------------------------------ factory
def make_judge(mode: str = "auto", **kwargs) -> Judge:
    """``openai``/``gemini`` pin a model judge, ``oracle`` the deterministic one.

    ``auto`` is the **local, offline** judge, and that is deliberate: the
    canonical command in the README has to stay deterministic and free, so the
    mere presence of an API key in the environment must never turn a
    reproducible run into a network run. ``llm`` is the opt-in "best available
    model judge" (Gemini first, because this workspace's key is free-tier, then
    OpenAI), and ``gemini``/``openai`` force one provider.
    """
    if mode == "openai":
        return OpenAIJudge(**kwargs)
    if mode == "gemini":
        return GeminiJudge(**kwargs)
    if mode == "llm":
        if _first_env(GeminiJudge.env_keys):
            return GeminiJudge(**kwargs)
        if os.environ.get("OPENAI_API_KEY"):
            return OpenAIJudge(**kwargs)
        raise JudgeError(
            "no model judge key is set. Add GEMINI_KEY or OPENAI_API_KEY, or run "
            "the offline judge with --judge auto (the default).")
    return OracleJudge(**kwargs)


def describe(judge: Judge) -> str:
    """One-line description for the report header."""
    if getattr(judge, "is_llm", False):
        return "LLM judge (%s, %s)" % (judge.mode, judge.model)
    return ("Deterministic oracle judge (offline) - evidence-containment "
            "proxy, not a language model")


if __name__ == "__main__":
    oracle = OracleJudge()
    context = "User prefers concise replies.\nThe deploy target is the staging cluster."

    extracted = oracle.answer("Which cluster do we deploy to?", context)
    assert extracted, "oracle should extract a line from a non-empty context"
    assert oracle.grade("Which cluster?", "staging cluster", extracted, context) == 1.0
    assert oracle.grade("Which cluster?", "production cluster", "unknown", context) == 0.0
    assert oracle.grade("When?", "7 May 2023", "unknown", "Caroline went in 2023") == 1.0
    assert oracle.grade("When?", "7 May 2023", "unknown", "") == 0.0
    assert oracle.grade("Which?", "staging", "staging", context, check="exact") == 1.0
    assert oracle.answer("anything", "") == ""

    forced = make_judge("oracle")
    assert forced.mode == "oracle" and forced.is_llm is False
    assert "not a language model" in describe(forced)

    # Provider wiring is checked without touching the network: a constructed
    # judge makes no request until it is asked a question.
    gem = GeminiJudge(api_key="not-a-real-key")
    assert gem.mode == "gemini" and gem.is_llm is True
    assert gem.model == GeminiJudge.default_model
    assert gem.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert gem.min_interval == 4.0, "the free-tier default must pace requests"
    assert gem.calls == 0 and gem.retries == 0
    assert describe(gem) == "LLM judge (gemini, %s)" % GeminiJudge.default_model
    assert OpenAIJudge(api_key="x").min_interval == 0.0, "a paid endpoint is not paced"

    print("judge ok")
