"""LoCoMo public-benchmark adapter for the Myelinated Memory harness.

LoCoMo is a long-conversation memory benchmark: multi-session dialogues between
two speakers, plus questions whose answers require specific dialogue turns.
Upstream: https://github.com/snap-research/locomo (see that repository for its
licence; the dataset file itself is fetched at runtime and cached locally).

This module converts one LoCoMo conversation into one ``Scenario`` by turning
every dialogue turn into an ``add`` event whose id IS the turn's ``dia_id``
(``"D1:3"``), so a question's ``evidence`` ids line up exactly with the memory
ids the arms store.

Two deliberate choices, both worth knowing when reading results:

1. Only real turn lists are read. Conversation keys include
   ``session_7_date_time``, ``session_7_observation`` and ``session_7_summary``;
   only ``session_<n>`` keys become memories, so summaries never leak into the
   store.
2. Every query in a conversation is asked at the conversation's LAST session.
   LoCoMo asks its questions after the whole conversation, and the shared
   ``common.replay`` cursor only moves forward, so a per-query session stamp
   would replay the wrong history. Asking at the end gives every arm the full
   transcript and keeps the comparison identical across arms.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from common import Event, Query, Scenario

DATASET_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"

_SESSION_RE = re.compile(r"^session_(\d+)$")
_DIA_RE = re.compile(r"D(\d+):")

CATEGORY_KINDS = {
    1: "lookup",        # single-hop
    2: "lookup",        # temporal
    3: "continuity",    # multi-hop
    4: "lookup",        # open-domain
    5: "adversarial",   # adversarial
}


# ------------------------------------------------------------------- files
def cache_path_default() -> str:
    """``benchmarks/data/locomo10.json``, resolved next to this file."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "data", "locomo10.json")


def available(cache_path: Optional[str] = None) -> bool:
    return os.path.exists(cache_path or cache_path_default())


def download(cache_path: Optional[str] = None, url: str = DATASET_URL) -> str:
    """Fetch the dataset and cache it locally; returns the local path."""
    path = cache_path or cache_path_default()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
    except Exception as exc:  # noqa: BLE001 - surfaced with context below
        raise RuntimeError("could not download LoCoMo from %s: %s" % (url, exc))
    tmp = path + ".part"
    with open(tmp, "wb") as handle:
        handle.write(payload)
    os.replace(tmp, path)
    return path


# -------------------------------------------------------------- conversion
def _day_of(dia_id: str) -> int:
    match = _DIA_RE.search(dia_id or "")
    return int(match.group(1)) if match else 0


def _iter_turns(session: Any) -> Iterator[Tuple[str, str, str]]:
    """Yield ``(speaker, text, dia_id)`` from a session, tolerating shape drift."""
    if not isinstance(session, list):
        return
    for turn in session:
        if isinstance(turn, dict):
            text = turn.get("text") or turn.get("blip_caption") or ""
            dia = str(turn.get("dia_id") or "")
            speaker = str(turn.get("speaker") or "")
        elif isinstance(turn, (list, tuple)) and len(turn) >= 2:
            text, dia, speaker = str(turn[0]), str(turn[1]), ""
        else:
            continue
        text = str(text).strip()
        if dia.strip() and text:
            yield speaker, text, dia.strip()


def _iter_sessions(conversation: Dict[str, Any]) -> Iterable[Tuple[int, Any]]:
    """Only ``session_<n>`` keys, ordered by n."""
    found = []
    for key, value in conversation.items():
        match = _SESSION_RE.match(key)
        if match:
            found.append((int(match.group(1)), value))
    return sorted(found, key=lambda pair: pair[0])


def _kind(category: Any) -> str:
    try:
        return CATEGORY_KINDS.get(int(category), "lookup")
    except (TypeError, ValueError):
        return "lookup"


def convert(record: Dict[str, Any], index: int, max_queries: int = 20) -> Scenario:
    """One LoCoMo record -> one Scenario."""
    conversation = record.get("conversation") or {}
    sample_id = str(record.get("sample_id") or "conv-%d" % index)

    events: List[Event] = []
    known_ids = set()
    last_day = 0
    for _session_index, turns in _iter_sessions(conversation):
        for speaker, text, dia_id in _iter_turns(turns):
            day = _day_of(dia_id)
            last_day = max(last_day, day)
            content = "%s: %s" % (speaker, text) if speaker else text
            events.append(
                Event(op="add", day=float(day), content=content, id=dia_id, category="ephemeral")
            )
            known_ids.add(dia_id)
    events.sort(key=lambda event: (event.day, event.id))

    ask_day = last_day
    queries: List[Query] = []
    for qa_index, item in enumerate(record.get("qa") or []):
        if len(queries) >= max_queries:
            break
        if not isinstance(item, dict):
            continue
        evidence = [str(entry) for entry in (item.get("evidence") or [])]
        evidence = [entry for entry in evidence if entry in known_ids]
        if not evidence:
            continue
        answer = item.get("answer")
        queries.append(
            Query(
                id="%s-q%d" % (sample_id, qa_index),
                query=str(item.get("question") or "").strip(),
                answer="" if answer is None else str(answer).strip(),
                evidence_ids=evidence,
                stale_ids=[],
                session=int(ask_day),
                kind=_kind(item.get("category")),
                answer_check="contains",
            )
        )

    return Scenario(
        id="locomo-%s" % sample_id,
        kind="locomo",
        description="LoCoMo %s: %d turns across %d sessions, %d questions"
        % (sample_id, len(events), len(list(_iter_sessions(conversation))), len(queries)),
        events=events,
        queries=queries,
        source="locomo",
    )


def load_locomo(
    cache_path: Optional[str] = None,
    limit: int = 3,
    max_queries_per_conversation: int = 20,
) -> List[Scenario]:
    """Load the first ``limit`` conversations as Scenarios."""
    path = cache_path or cache_path_default()
    if not os.path.exists(path):
        raise FileNotFoundError(
            "LoCoMo cache not found at %s - run download() first (needs network once)" % path
        )
    with open(path, "r", encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise RuntimeError("unexpected LoCoMo payload: top level is %s" % type(records).__name__)
    scenarios = []
    for index, record in enumerate(records[: max(0, limit)]):
        if isinstance(record, dict):
            scenario = convert(record, index, max_queries_per_conversation)
            if scenario.queries:
                scenarios.append(scenario)
    return scenarios


# ------------------------------------------------------------ validation
def validate(scenarios: Sequence[Scenario]) -> List[str]:
    """Check the two invariants the harness silently depends on.

    1. Every ``evidence_ids`` entry names a turn the same Scenario really adds.
       ``convert`` filters on this, and if it ever stopped doing so the hit rate
       would quietly fall while every arm looked equally bad.
    2. Every query is asked at the conversation's last session, which is what
       makes the shared ``common.replay`` cursor give each arm the whole
       transcript (see the module docstring).
    """
    problems: List[str] = []
    for scenario in scenarios:
        known = {event.id for event in scenario.events if event.op == "add"}
        last_day = max((event.day for event in scenario.events), default=0.0)
        for query in scenario.queries:
            if not query.evidence_ids:
                problems.append("%s/%s: no evidence" % (scenario.id, query.id))
            for mem_id in query.evidence_ids:
                if mem_id not in known:
                    problems.append("%s/%s: evidence %s is not a turn of this conversation"
                                    % (scenario.id, query.id, mem_id))
            if query.session != int(last_day):
                problems.append("%s/%s: asked at session %s, not the last session (%d)"
                                % (scenario.id, query.id, query.session, int(last_day)))
    return problems


if __name__ == "__main__":
    if not available():
        print("locomo cache missing; downloading via download()")
        download()
    scenarios = load_locomo()
    problems = validate(scenarios)
    if problems:
        for problem in problems[:20]:
            print("FAIL", problem)
        raise SystemExit(1)
    print(
        "locomo ok: %d scenarios, %d queries, %d turns"
        % (
            len(scenarios),
            sum(len(s.queries) for s in scenarios),
            sum(len(s.events) for s in scenarios),
        )
    )
