"""Task-level evaluation for the Myelinated Memory benchmark.

Pipeline: an arm retrieves a CONTEXT for a question -> a model answers from
that context only -> a judge grades the answer against the gold answer. This
module owns the last two steps.

Two judges exist, and the difference between them is the single most important
caveat in the whole report.

``OracleJudge`` is **a proxy for task success, not a language model.** It never
calls a network. It "answers" by returning the retrieved context line that
overlaps the question most, and it grades by checking whether the gold answer is
contained in (or numerically recoverable from) that answer or its context. So it
measures *evidence availability*: whether the right facts were placed inside the
budget at all. It cannot measure reasoning, paraphrase, or a model's willingness
to say "unknown".

``OpenAIJudge`` sends real chat completions requests and grades with an LLM.

Why the distinction must be reported: a system that wins under the oracle judge
has proven only that its retrievals contain the answer string. A system that
wins under the LLM judge has proven that a model could actually answer from its
context. These are different claims, and an oracle-only result advertised as
"task success" would overstate the evidence. Reports must therefore name which
judge produced every number.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

from common import jaccard, normalize, tokenize

_MONTHS = frozenset(
    """january february march april may june july august september october november
    december""".split()
)
_NUMBER_RE = re.compile(r"\d+")
_SCORE_RE = re.compile(r'"score"\s*:\s*([01])\b')
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


class JudgeError(RuntimeError):
    """Raised when a judge call fails; never swallowed into a fake score."""


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


class Judge:
    """Base class. ``mode`` is "oracle" or "openai"; ``is_llm`` says which."""

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


# ------------------------------------------------------------------ openai
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


class OpenAIJudge(Judge):
    """LLM judge over an OpenAI-compatible chat completions endpoint."""

    mode = "openai"
    is_llm = True

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 60.0, **kwargs) -> None:
        super().__init__()
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not key:
            raise JudgeError(
                "OPENAI_API_KEY is not set, so the LLM judge cannot run. "
                "Add it in Settings -> Environment, or use the oracle judge."
            )
        self.api_key = key
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        self.timeout = float(timeout)
        self._cache: Dict[str, str] = {}

    # --------------------------------------------------------------- transport
    def _chat(self, messages: List[Dict[str, str]]) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.0}
        cache_key = json.dumps(payload, sort_keys=True)
        if cache_key in self._cache:
            return self._cache[cache_key]

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
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise JudgeError("chat completions failed: HTTP %s: %s" % (exc.code, detail)) from exc
        except Exception as exc:  # URLError, timeout, ...
            raise JudgeError("chat completions failed: %s" % (exc,)) from exc

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
    """``openai`` forces the LLM judge, ``oracle`` the deterministic one,
    ``auto`` picks the LLM judge only when ``OPENAI_API_KEY`` is set."""
    if mode == "openai":
        return OpenAIJudge(**kwargs)
    if mode == "oracle":
        return OracleJudge(**kwargs)
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIJudge(**kwargs)
    return OracleJudge(**kwargs)


def describe(judge: Judge) -> str:
    """One-line description for the report header."""
    if getattr(judge, "is_llm", False):
        return "LLM judge (%s, %s)" % (judge.mode, judge.model)
    return ("Deterministic oracle judge (no API key set) - evidence-containment "
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

    print("judge ok")
