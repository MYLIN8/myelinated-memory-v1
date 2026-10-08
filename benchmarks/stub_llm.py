"""A local stub of the OpenAI chat-completions endpoint, for testing judge.py.

Why this exists: the judge code path must be testable without spending quota, so
this stub stands in for whichever hosted endpoint would otherwise be called.
This module lets that *code path* be exercised end to end anyway - request
construction, JSON parsing, the response cache, and the HTTP error path -
against a server that speaks the same wire protocol.

What this proves and what it does not:

  proves   the judge sends a well-formed request, reads the documented
           response shape, caches identical calls, raises JudgeError with the
           HTTP status instead of inventing a score, and - via STUB_429_ONCE -
           that the rate-limit path retries and then succeeds;
  does not prove anything about model quality. Real LLM-judged numbers need a
  real endpoint and a key (GEMINI_KEY, OPENAI_API_KEY or NVIDIA_CLOUD_KEY in
  Settings -> Environment); this fixture never proves a model's judgement.

This is a short-lived in-process test fixture. It is started by
`benchmarks/test_llm_judge.py` inside a single command and shut down in a
`finally` block; it must never be run as a service.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Tuple

# Markers a caller can embed in a request to steer the stub.
FAIL_MARKER = "STUB_FAIL"          # -> HTTP 500 (terminal, not retried)
ZERO_MARKER = "STUB_SCORE_0"       # -> grading verdict of 0 instead of 1
# -> HTTP 429 on the FIRST request carrying it, then a normal 200. That is the
# only stateful marker here, and it exists so the judge's retry-with-backoff
# path can be tested without a real rate limit and without sleeping for real.
THROTTLE_MARKER = "STUB_429_ONCE"

# Counts of throttle answers handed out, keyed by marker.
_THROTTLE_COUNTS: Dict[str, int] = {}
_THROTTLE_LOCK = threading.Lock()


def reset_throttle() -> None:
    """Forget throttle history (called by ``serve_in_thread`` and by tests)."""
    with _THROTTLE_LOCK:
        _THROTTLE_COUNTS.clear()


def _first_throttle(marker: str) -> bool:
    """True exactly once per process for ``marker``, False from then on."""
    with _THROTTLE_LOCK:
        seen = _THROTTLE_COUNTS.get(marker, 0)
        _THROTTLE_COUNTS[marker] = seen + 1
    return seen == 0

# The grading call is identified by its system prompt, not by the user content,
# so a context that happens to contain the word "score" cannot be mistaken for a
# grading request.
_GRADE_MARKERS = ("grader", "strict json")

_CONTEXT_MARKER = "Context:"


def _split_messages(payload: Dict) -> Tuple[str, str]:
    system_parts = []
    user_parts = []
    for message in payload.get("messages") or []:
        content = message.get("content") or ""
        if message.get("role") == "system":
            system_parts.append(content)
        elif message.get("role") == "user":
            user_parts.append(content)
    return "\n".join(system_parts), "\n".join(user_parts)


def _is_grading_request(system: str) -> bool:
    lowered = system.lower()
    return any(marker in lowered for marker in _GRADE_MARKERS)


def _answer_from_context(user: str) -> str:
    """Derive a short answer from the context block, like a terse model would."""
    tail = user
    index = user.find(_CONTEXT_MARKER)
    if index != -1:
        tail = user[index + len(_CONTEXT_MARKER):]
        stop = tail.find("\n\n")
        if stop != -1:
            tail = tail[:stop]
    for line in tail.splitlines():
        if line.strip():
            return line.strip()
    return ""


def build_response(payload: Dict) -> Tuple[int, Dict]:
    """Pure request -> (status, body) mapping. Kept separate so it is testable."""
    system, user = _split_messages(payload)
    blob = system + "\n" + user

    if FAIL_MARKER in blob:
        return 500, {"error": {"message": "stub forced failure", "type": "stub_error"}}

    if THROTTLE_MARKER in blob and _first_throttle(THROTTLE_MARKER):
        return 429, {"error": {"message": "stub rate limit",
                               "type": "rate_limit_exceeded"}}

    if _is_grading_request(system):
        score = 0 if ZERO_MARKER in user else 1
        content = json.dumps({"score": score, "reason": "stub verdict"})
    else:
        content = _answer_from_context(user) or "unknown"

    return 200, {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "model": payload.get("model", "stub-model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


class StubHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-protocol handler. Logging is suppressed on purpose."""

    server_version = "StubLLM/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - signature fixed by base class
        return

    def _respond(self, status: int, body: Dict) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if status == 429:
            # A real limiter sends this; the judge must honour it instead of
            # guessing, so the stub sends it too (0 s keeps the test fast).
            self.send_header("Retry-After", "0")
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802 - name fixed by base class
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._respond(404, {"error": {"message": "unknown path %s" % self.path}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            self._respond(400, {"error": {"message": "bad json: %s" % exc}})
            return
        status, body = build_response(payload if isinstance(payload, dict) else {})
        self._respond(status, body)


def serve_in_thread(port: int = 0):
    """Start the stub on a background thread.

    ``port=0`` binds an ephemeral free port. Returns
    ``(server, base_url, shutdown)`` where ``base_url`` is the ``/v1`` prefix
    that ``judge.OpenAIJudge`` expects.
    """
    reset_throttle()
    server = ThreadingHTTPServer(("127.0.0.1", port), StubHandler)
    actual_port = server.server_address[1]
    base_url = "http://127.0.0.1:%d/v1" % actual_port
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True, name="stub-llm")
    thread.start()

    def shutdown() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)

    return server, base_url, shutdown


if __name__ == "__main__":
    _server, _url, _shutdown = serve_in_thread()
    try:
        print("stub listening on %s" % _url)
        status, body = build_response({
            "messages": [
                {"role": "system", "content": "You answer questions using ONLY the context."},
                {"role": "user", "content": "Context:\nThe deploy target is staging.\n\nQuestion: which?"},
            ]
        })
        assert status == 200 and body["choices"][0]["message"]["content"] == "The deploy target is staging."
        status, body = build_response({
            "messages": [
                {"role": "system", "content": "You are a strict grader. Reply with STRICT JSON."},
                {"role": "user", "content": "Candidate answer: STUB_SCORE_0"},
            ]
        })
        assert status == 200 and '"score": 0' in body["choices"][0]["message"]["content"]
        status, _body = build_response({"messages": [{"role": "user", "content": "STUB_FAIL"}]})
        assert status == 500

        # The throttle marker answers exactly once, then behaves normally.
        reset_throttle()
        throttled = {"messages": [{"role": "user", "content": "STUB_429_ONCE"}]}
        assert build_response(throttled)[0] == 429
        assert build_response(throttled)[0] == 200
        reset_throttle()
        assert build_response(throttled)[0] == 429, "reset must re-arm the throttle"
        print("stub ok")
    finally:
        _shutdown()
