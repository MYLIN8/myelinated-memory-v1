"""Curated scenario suite for the Myelinated Memory benchmark harness.

This is the *realistic* tier: hand-authored session histories whose prose is
good enough to hand to an LLM judge. Each scenario is a replayable event log
(``add`` / ``access`` with a virtual day) plus questions with ground truth.

Ground-truth invariants, all asserted in ``__main__``:

1. Every ``evidence_ids`` / ``stale_ids`` entry names an ``add`` event that the
   same scenario really creates.
2. A query's ``session`` is on or after the day of every memory it depends on,
   so the memory exists by the time the question is asked.
3. Memory ids are unique within a scenario.
4. ``stale_ids`` only appears where a value was superseded: an ``add`` after the
   superseded memory's day carries the replacement.
5. Every ``kind`` is one of ``common.QUERY_KINDS``.

Coverage: all seven query kinds across ten scenarios, two multi-session
``continuity`` scenarios, two ``contradiction`` scenarios, a ``budget`` scenario
that overflows the 2,200-char context, and ``dormancy-120d``, which spans 120
virtual days with pinned identity/safety memories that must survive to the end.
"""

from __future__ import annotations

from typing import List, Sequence

from common import Event, Query, Scenario

_ISO = "ISO-8601"


# ------------------------------------------------------------------ helpers
def _add(day_: float, memory_id: str, content: str, category: str = "general",
         protected: bool = False) -> Event:
    return Event(op="add", day=float(day_), content=content, id=memory_id,
                 category=category, protected=protected)


def _access(day_: float, memory_id: str) -> Event:
    return Event(op="access", day=float(day_), id=memory_id)


def _q(session: int, query_id: str, kind: str, question: str, answer: str,
       evidence: Sequence[str], stale: Sequence[str] = (), check: str = "contains") -> Query:
    return Query(id=query_id, query=question, answer=answer, evidence_ids=list(evidence),
                 stale_ids=list(stale), session=int(session), kind=kind, answer_check=check)


# --------------------------------------------------------- 1. preference
def _preference_tooling() -> Scenario:
    events = [
        _add(0, "p-pkg", "User prefers bun over npm and yarn for JavaScript projects.", "preference"),
        _add(0, "p-shell", "User's shell is zsh with oh-my-zsh.", "preference"),
        _add(0, "p-editor", "User edits code in Neovim, not VS Code.", "preference"),
        _add(0, "p-tests", "User writes Python tests with pytest rather than unittest.", "preference"),
        _add(0, "p-pyver", "User targets Python 3.11 for new projects.", "preference"),
        _add(0, "p-style", "User wants no trailing periods in commit subject lines.", "preference"),
        _add(0, "p-commits", "User uses Conventional Commits (feat:, fix:, chore:).", "preference"),
        _add(0, "p-quiet", "User prefers concise answers without preamble.", "preference"),
        _add(0, "p-indent", "User indents with 4 spaces, never tabs.", "preference"),
        _add(0, "p-hints", "User wants full type hints on public functions.", "preference"),
        _access(1, "p-pkg"), _access(1, "p-quiet"),
    ]
    queries = [
        _q(1, "pref-01", "preference", "Which package manager should I use for this project?", "bun", ["p-pkg"]),
        _q(1, "pref-02", "preference", "What shell does the user use?", "zsh", ["p-shell"]),
        _q(1, "pref-03", "preference", "Which editor does the user prefer?", "Neovim", ["p-editor"]),
        _q(1, "pref-04", "preference", "Which test framework for Python?", "pytest", ["p-tests"]),
        _q(1, "pref-05", "preference", "What Python version should new projects target?", "3.11", ["p-pyver"]),
        _q(1, "pref-06", "preference", "How should commit subject lines be punctuated?", "no trailing period", ["p-style"]),
        _q(1, "pref-07", "preference", "What commit message convention does the user follow?", "Conventional Commits", ["p-commits"]),
        _q(1, "pref-08", "preference", "How verbose should answers be?", "concise", ["p-quiet"]),
        _q(2, "pref-09", "preference", "Spaces or tabs for indentation?", "4 spaces", ["p-indent"]),
        _q(2, "pref-10", "preference", "Should public functions have type hints?", "yes, full type hints", ["p-hints"]),
        _q(2, "pref-11", "preference", "Which package managers should be avoided?", "npm and yarn", ["p-pkg"]),
        _q(2, "pref-12", "preference", "How many spaces per indent level?", "4", ["p-indent"]),
    ]
    return Scenario("preference-tooling", "preference",
                    "Durable user preferences for language tooling and workflow.",
                    events, queries, source="curated")


# -------------------------------------------------------------- 2. lookup
def _infra_facts() -> Scenario:
    events = [
        _add(0, "i-region", "Production runs in the eu-west-1 region.", "general"),
        _add(0, "i-db", "Production Postgres is version 14.9.", "general"),
        _add(0, "i-cache", "The shared Redis cluster is cache-prod.internal:6379.", "general"),
        _add(0, "i-cdn", "Static assets are served from the freebuff-cdn bucket.", "general"),
        _add(0, "i-ci", "CI runs on GitHub Actions, workflow file .github/workflows/ci.yml.", "general"),
        _add(0, "i-mon", "Errors are reported to the Sentry project named backend.", "general"),
        _add(0, "i-queue", "Background jobs run on the RabbitMQ exchange named tasks.", "general"),
        _add(0, "i-vault", "Secrets live in AWS Secrets Manager under /prod/app.", "general"),
        _add(0, "i-dns", "The API is served at api.example.com behind Cloudflare.", "general"),
        _add(0, "i-oncall", "The on-call rotation is the PagerDuty schedule named core.", "general"),
    ]
    queries = [
        _q(1, "infra-01", "lookup", "Which region does production run in?", "eu-west-1", ["i-region"]),
        _q(1, "infra-02", "lookup", "Which Postgres version does production use?", "14.9", ["i-db"]),
        _q(1, "infra-03", "lookup", "What is the shared Redis host?", "cache-prod.internal:6379", ["i-cache"]),
        _q(1, "infra-04", "lookup", "Where are static assets served from?", "freebuff-cdn", ["i-cdn"]),
        _q(1, "infra-05", "lookup", "Which CI system do we use?", "GitHub Actions", ["i-ci"]),
        _q(1, "infra-06", "lookup", "Where do errors get reported?", "Sentry project backend", ["i-mon"]),
        _q(1, "infra-07", "lookup", "What broker do background jobs use?", "RabbitMQ exchange tasks", ["i-queue"]),
        _q(1, "infra-08", "lookup", "Where are production secrets stored?", "AWS Secrets Manager /prod/app", ["i-vault"]),
        _q(1, "infra-09", "lookup", "What is the API hostname?", "api.example.com", ["i-dns"]),
        _q(1, "infra-10", "lookup", "Which PagerDuty schedule covers on-call?", "core", ["i-oncall"]),
    ]
    return Scenario("infra-facts", "lookup",
                    "Flat inventory of infrastructure facts, one question each.",
                    events, queries, source="curated")


# ---------------------------------------------------------- 3. continuity
def _flask_fastapi() -> Scenario:
    events = [
        _add(0, "c-branch", "The FastAPI migration happens on branch feat/fastapi-migration.", "task"),
        _add(0, "c-scope", "The migration covers the public API only; the admin UI stays on Flask.", "task"),
        _add(1, "c-auth", "The auth blueprint was ported to FastAPI routers on day 1.", "task"),
        _add(2, "c-tests", "Auth router tests now pass under pytest with 96% coverage.", "task"),
        _add(3, "c-blocker", "Blocked on Flask sessions versus FastAPI dependency injection for the legacy login flow.", "task"),
        _add(5, "c-unblock", "Resolved the session blocker with a custom dependency that reads the Flask session cookie.", "task"),
        _add(7, "c-orders", "The orders blueprint is ported; 41 endpoints migrated so far.", "task"),
        _add(9, "c-openapi", "The OpenAPI schema is now generated by FastAPI and published at /openapi.json.", "task"),
        _add(10, "c-perf", "p95 latency improved from 180ms to 120ms after the orders port.", "task"),
        _add(12, "c-remaining", "Remaining work: the webhooks blueprint and deleting the Flask app factory.", "task"),
        _access(2, "c-branch"), _access(8, "c-scope"),
    ]
    queries = [
        _q(1, "cont-01", "continuity", "What branch is the FastAPI migration on?", "feat/fastapi-migration", ["c-branch"]),
        _q(1, "cont-02", "continuity", "Does the migration cover the admin UI?", "no, only the public API", ["c-scope"]),
        _q(2, "cont-03", "continuity", "Which blueprint was ported first?", "auth", ["c-auth"]),
        _q(3, "cont-04", "continuity", "What is the coverage on the auth router tests?", "96%", ["c-tests"]),
        _q(3, "cont-05", "continuity", "What is currently blocking the migration?", "Flask sessions versus FastAPI dependency injection", ["c-blocker"]),
        _q(4, "cont-06", "continuity", "Is the migration blocked right now?", "yes, on session handling", ["c-blocker"]),
        _q(5, "cont-07", "continuity", "How was the session blocker resolved?", "a custom dependency reading the Flask session cookie", ["c-unblock"]),
        _q(6, "cont-08", "continuity", "Is the session blocker still open?", "no, it was resolved", ["c-unblock"]),
        _q(7, "cont-09", "continuity", "How many endpoints have been migrated?", "41", ["c-orders"]),
        _q(8, "cont-10", "continuity", "Which blueprint was ported after auth?", "orders", ["c-orders"]),
        _q(9, "cont-11", "continuity", "How is the OpenAPI schema generated now?", "by FastAPI and served at /openapi.json", ["c-openapi"]),
        _q(10, "cont-12", "continuity", "What is the new p95 latency?", "120ms", ["c-perf"]),
        _q(12, "cont-13", "continuity", "What work remains on the migration?", "the webhooks blueprint and removing the Flask app factory", ["c-remaining"]),
        _q(12, "cont-14", "continuity", "Which components still need porting?", "webhooks", ["c-remaining"]),
    ]
    return Scenario("flask-fastapi-migration", "continuity",
                    "Twelve-day migration whose state changes every few sessions.",
                    events, queries, source="curated")


def _postgres_upgrade() -> Scenario:
    events = [
        _add(0, "pg-plan", "Plan: upgrade the primary Postgres cluster from 14.9 to 16.3.", "task"),
        _add(0, "pg-window", "The upgrade window is Saturday 02:00-05:00 UTC.", "task"),
        _add(1, "pg-snapshot", "A logical pre-upgrade snapshot was taken to s3://backups/pg-pre-16.", "task"),
        _add(2, "pg-staging", "pg16 was validated on staging for a full week.", "task"),
        _add(4, "pg-ext", "pg_stat_statements and pgcrypto were verified compatible.", "task"),
        _add(6, "pg-analytics", "The analytics service moved to the new cluster first.", "task"),
        _add(8, "pg-remaining", "Services still to move: billing and notifications.", "task"),
        _add(10, "pg-rollback", "Rollback plan is a restore from the logical snapshot plus DNS flip.", "task"),
    ]
    queries = [
        _q(1, "pgup-01", "continuity", "Which Postgres version are we upgrading to?", "16.3", ["pg-plan"]),
        _q(1, "pgup-02", "continuity", "Which version are we coming from?", "14.9", ["pg-plan"]),
        _q(3, "pgup-03", "continuity", "When is the upgrade window?", "Saturday 02:00-05:00 UTC", ["pg-window"]),
        _q(5, "pgup-04", "continuity", "Where did we put the pre-upgrade snapshot?", "s3://backups/pg-pre-16", ["pg-snapshot"]),
        _q(7, "pgup-05", "continuity", "How long did we validate pg16 on staging?", "a full week", ["pg-staging"]),
        _q(9, "pgup-06", "continuity", "Which extensions were verified?", "pg_stat_statements and pgcrypto", ["pg-ext"]),
        _q(11, "pgup-07", "continuity", "Which service moved to the new cluster first?", "analytics", ["pg-analytics"]),
        _q(14, "pgup-08", "continuity", "Which services still need to move?", "billing and notifications", ["pg-remaining"]),
        _q(14, "pgup-09", "continuity", "What is the rollback plan?", "restore the logical snapshot and flip DNS", ["pg-rollback"]),
        _q(20, "pgup-10", "continuity", "Is the Postgres 16.3 upgrade finished?", "no, billing and notifications still remain", ["pg-remaining"]),
    ]
    return Scenario("postgres-upgrade", "continuity",
                    "Three-week database upgrade tracked across planning and execution.",
                    events, queries, source="curated")


# ------------------------------------------------------- 4. contradiction
def _config_updates() -> Scenario:
    events = [
        _add(0, "dc-region-old", "Deploys go to the us-east-1 region.", "general"),
        _add(0, "dc-cache-old", "The cache layer runs Redis 6.", "general"),
        _add(0, "dc-sdk-old", "The mobile client pins SDK 3.4.", "general"),
        _add(0, "dc-oncall-old", "Primary on-call is Priya.", "general"),
        _add(0, "dc-noise", "The office espresso machine is on the fourth floor.", "ephemeral"),
        _add(14, "dc-cache-new", "The cache layer was upgraded to Redis 7.2.", "general"),
        _add(20, "dc-region-new", "Deploys now go to the eu-central-1 region.", "general"),
        _add(25, "dc-sdk-new", "The mobile client now pins SDK 5.0.", "general"),
        _add(30, "dc-oncall-new", "Primary on-call is now Marcus.", "general"),
    ]
    queries = [
        _q(3, "upd-01", "lookup", "Which region do deploys go to?", "us-east-1", ["dc-region-old"]),
        _q(5, "upd-02", "lookup", "What Redis version is the cache layer on?", "Redis 6", ["dc-cache-old"]),
        _q(12, "upd-03", "lookup", "Which mobile SDK is pinned?", "3.4", ["dc-sdk-old"]),
        _q(16, "upd-04", "contradiction", "What Redis version is the cache layer on?", "Redis 7.2", ["dc-cache-new"], ["dc-cache-old"]),
        _q(18, "upd-05", "contradiction", "What cache version do we run now?", "Redis 7.2", ["dc-cache-new"], ["dc-cache-old"]),
        _q(22, "upd-06", "contradiction", "Which region do deploys go to?", "eu-central-1", ["dc-region-new"], ["dc-region-old"]),
        _q(24, "upd-07", "contradiction", "Which region do deploys target?", "eu-central-1", ["dc-region-new"], ["dc-region-old"]),
        _q(27, "upd-08", "contradiction", "Which mobile SDK is pinned?", "5.0", ["dc-sdk-new"], ["dc-sdk-old"]),
        _q(29, "upd-09", "contradiction", "Which SDK version does the mobile client pin?", "5.0", ["dc-sdk-new"], ["dc-sdk-old"]),
        _q(32, "upd-10", "contradiction", "Who is primary on-call?", "Marcus", ["dc-oncall-new"], ["dc-oncall-old"]),
        _q(34, "upd-11", "contradiction", "Who is on primary on-call now?", "Marcus", ["dc-oncall-new"], ["dc-oncall-old"]),
        _q(36, "upd-12", "contradiction", "Which region is production deployed to?", "eu-central-1", ["dc-region-new"], ["dc-region-old"]),
    ]
    return Scenario("config-updates", "contradiction",
                    "Four config values change over 36 days; stale values must not resurface.",
                    events, queries, source="curated")


def _secret_rotation() -> Scenario:
    events = [
        _add(0, "r-key-old", "The staging API key is sk-staging-legacy.", "general"),
        _add(0, "r-token-old", "The webhook signing token is whsec_old.", "general"),
        _add(3, "r-key-new", "The staging API key was rotated to sk-staging-2026-04.", "general"),
        _add(6, "r-token-new", "The webhook signing token was rotated to whsec_new_04.", "general"),
    ]
    queries = [
        _q(1, "rot-01", "lookup", "What is the staging API key?", "sk-staging-legacy", ["r-key-old"]),
        _q(2, "rot-02", "lookup", "What webhook signing token are we using?", "whsec_old", ["r-token-old"]),
        _q(4, "rot-03", "lookup", "What was the webhook signing token before rotation?", "whsec_old", ["r-token-old"]),
        _q(5, "rot-04", "contradiction", "What is the staging API key now?", "sk-staging-2026-04", ["r-key-new"], ["r-key-old"]),
        _q(6, "rot-05", "contradiction", "Has the staging API key been rotated?", "yes, to sk-staging-2026-04", ["r-key-new"], ["r-key-old"]),
        _q(8, "rot-06", "contradiction", "What is the webhook signing token?", "whsec_new_04", ["r-token-new"], ["r-token-old"]),
        _q(9, "rot-07", "contradiction", "What is the current webhook signing token?", "whsec_new_04", ["r-token-new"], ["r-token-old"]),
        _q(10, "rot-08", "contradiction", "Which staging API key should I use?", "sk-staging-2026-04", ["r-key-new"], ["r-key-old"]),
    ]
    return Scenario("secret-rotation", "contradiction",
                    "Credentials rotate mid-stream; only the current values may be surfaced.",
                    events, queries, source="curated")


# -------------------------------------------------------------- 5. budget
# 56 low-value memories plus 8 dated commitments, ~3,000 chars of content into
# a 2,200-char budget. The keys are interleaved with the filler and are the only
# entries that get used, so *usage* is the only signal that can find them: FIFO
# and recency both get an arbitrary slice of the noise.
_FILLER = [
    "The espresso machine is on the third floor.", "Standup is at 09:15 every weekday.",
    "The design doc lives in Notion.", "The team channel is #eng-platform.",
    "Lunch is usually at 12:30.", "The office printer needs a badge swipe.",
    "The wiki moved to Confluence last year.", "The meeting room TV takes HDMI.",
    "The bike rack is in the basement.", "The build cache is on the CI runner volume.",
    "The team uses Jira for tickets.", "The retro is every second Friday.",
    "The coffee beans are ordered monthly.", "The VPN client is WireGuard.",
    "The standing desk settings are saved in the app.", "The plant in the corner needs water weekly.",
    "The whiteboard markers live in the drawer.", "The badge reader was replaced in March.",
    "The printer queue is named HQ-2F.", "The parking code changes quarterly.",
    "The team photo is from the 2025 offsite.", "The kitchen has a filtered water tap.",
    "The monitor arms are VESA 100.", "The spare keyboards are in the storage closet.",
    "The meeting invites come from the shared calendar.", "The AV remote is kept in the top drawer.",
    "The lab machines run Ubuntu 22.04.", "The shared drive is mounted at /mnt/team.",
    "Software licenses are tracked in the finance sheet.", "The onboarding checklist lives in the wiki.",
    "The recycling bins are collected on Tuesdays.", "The air conditioning is controlled by facilities.",
    "The guest wifi is separate from the staff network.", "The stationery order goes in every quarter.",
    "The team offsite is usually in September.", "The demo laptop is the grey ThinkPad.",
    "The whiteboard in room 4 is magnetic.", "The code of conduct is in the handbook.",
    "The holiday calendar is published in January.", "The expense tool is Concur.",
    "The travel policy caps hotels at 150 per night.", "The team Slack has a #random channel.",
    "The fire drill happens twice a year.", "The first aid kit is by the kitchen door.",
    "The badges are programmed by reception.", "The server room is on the ground floor.",
    "The spare monitors are in the loft.", "The desk phones are rarely used.",
    "Meeting rooms are booked through the calendar app.", "The kitchen rota is pinned by the fridge.",
    "The bike lockers need a deposit.", "The stationery cupboard is restocked monthly.",
    "The team mascot is a heron.", "The company hoodies come in navy only.",
    "The photocopier needs a code.", "The window blinds are automated.",
]

_KEYS = [
    ("bp-audit", "The security audit deliverable is due Friday 2026-04-17."),
    ("bp-rotation", "Production database password rotation is due 2026-05-01."),
    ("bp-vendor", "The vendor contract renewal date is 2026-06-30."),
    ("bp-soc2", "The SOC2 evidence pack lives in s3://compliance/soc2-2026."),
    ("bp-pentest", "The pen-test retest is booked for 2026-05-12."),
    ("bp-freeze", "Billing service deploys are frozen until 2026-04-20."),
    ("bp-postmortem", "The incident postmortem for INC-4412 is due 2026-04-15."),
    ("bp-release", "The user is release manager for release/2026.4."),
]


def _budget_pressure() -> Scenario:
    # Interleave: a key every seventh filler entry.
    events: List[Event] = []
    for index, text in enumerate(_FILLER):
        events.append(_add(0, "bf-%02d" % (index + 1), text, "ephemeral"))
        if index % 7 == 3 and index // 7 < len(_KEYS):
            key_id, content = _KEYS[index // 7]
            events.append(_add(0, key_id, content, "task"))
    for day_ in (1.0, 2.0):
        for key_id, _content in _KEYS:
            events.append(_access(day_, key_id))
    for noise_id in ("bf-01", "bf-02", "bf-14"):
        events.append(_access(1.0, noise_id))
    queries = [
        _q(1, "bud-01", "budget", "When is the security audit deliverable due?", "Friday 2026-04-17", ["bp-audit"]),
        _q(1, "bud-02", "budget", "When is the production database password rotation due?", "2026-05-01", ["bp-rotation"]),
        _q(1, "bud-03", "budget", "What compliance deliverable is due this week?", "the security audit", ["bp-audit"]),
        _q(2, "bud-04", "budget", "When does the vendor contract renew?", "2026-06-30", ["bp-vendor"]),
        _q(2, "bud-05", "budget", "Where is the SOC2 evidence pack?", "s3://compliance/soc2-2026", ["bp-soc2"]),
        _q(2, "bud-06", "budget", "When is the pen-test retest?", "2026-05-12", ["bp-pentest"]),
        _q(2, "bud-07", "budget", "Where should SOC2 evidence be stored?", "s3://compliance/soc2-2026", ["bp-soc2"]),
        _q(2, "bud-08", "budget", "What is the pen-test retest date?", "2026-05-12", ["bp-pentest"]),
        _q(3, "bud-09", "budget", "When are billing service deploys frozen until?", "2026-04-20", ["bp-freeze"]),
        _q(3, "bud-10", "budget", "When is the INC-4412 postmortem due?", "2026-04-15", ["bp-postmortem"]),
        _q(3, "bud-11", "budget", "Which postmortem is outstanding?", "INC-4412", ["bp-postmortem"]),
        _q(3, "bud-12", "budget", "Which release is the user managing?", "release/2026.4", ["bp-release"]),
    ]
    return Scenario("budget-pressure", "budget",
                    "Sixty-four memories into a 2,200-char budget; only usage-based "
                    "prioritisation can find the eight dated commitments.",
                    events, queries, source="curated")


# ----------------------------------------------------------- 6. coldstart
def _coldstart() -> Scenario:
    facts = [
        ("cs-mesh", "The service mesh is Linkerd.", "Linkerd", "Which service mesh do we use?"),
        ("cs-runner", "The primary CI runner label is ubuntu-latest.", "ubuntu-latest",
         "What CI runner label is used?"),
        ("cs-flags", "The feature flag provider is LaunchDarkly.", "LaunchDarkly",
         "Which feature flag provider do we use?"),
        ("cs-docs", "The docs site is built with MkDocs Material.", "MkDocs Material",
         "What builds the docs site?"),
        ("cs-cli", "The internal CLI is called hermes.", "hermes", "What is the internal CLI called?"),
        ("cs-ds", "The design system package is @acme/ui.", "@acme/ui",
         "What is the design system package called?"),
        ("cs-slo", "The error budget is 0.1% per month.", "0.1%", "What is the error budget?"),
        ("cs-inc", "The incident channel is #incidents in Slack.", "#incidents",
         "Which Slack channel is for incidents?"),
    ]
    events = [_add(0, mid, text, "general") for mid, text, _answer, _question in facts]
    queries = [
        _q(0, "cold-%02d" % n, "coldstart", question, answer, [mid])
        for n, (mid, _text, answer, question) in enumerate(facts, 1)
    ]
    return Scenario("coldstart-facts", "coldstart",
                    "Everything is brand new and nothing has been accessed yet.",
                    events, queries, source="curated")


# --------------------------------------------------------- 7. adversarial
# IMPORTANT: the four safety rules below are never accessed, while ~60 pieces of
# office trivia are accessed repeatedly. That inverts the Hebbian signal, and the
# bulk of the noise also overflows the 2,200-char budget, so a usage-weighted
# arm will bury the rules that matter. ``adv-i2`` is PINNED, which is the
# documented escape hatch ('--protected / pin for anything safety-critical or
# identity-fixed'): the test measures whether pinning actually rescues it.
def _adversarial() -> Scenario:
    events = [
        _add(0, "adv-i1", "Billing deploys are never allowed on Fridays.", "task"),
        _add(0, "adv-i2", "PII must never be written to application logs.", "identity", True),
        _add(0, "adv-i3", "The incident escalation contact is Dana, the platform lead.", "general"),
        _add(0, "adv-i4", "Customer data residency must stay in the EU.", "identity"),
        _add(0, "adv-n1", "There is a coffee machine on the third floor.", "ephemeral"),
        _add(0, "adv-n2", "The build takes about four minutes on CI.", "ephemeral"),
        _add(0, "adv-n3", "The team keeps notes in Notion.", "ephemeral"),
        _add(0, "adv-n4", "The office wifi password is on the whiteboard.", "ephemeral"),
        _add(0, "adv-n5", "Standup is at 09:15.", "ephemeral"),
    ]
    for index, text in enumerate(_FILLER):
        events.append(_add(0, "adv-f%02d" % (index + 1), text, "ephemeral"))
    for n in range(1, 17):
        for noise_id in ("adv-n1", "adv-n2", "adv-n3", "adv-n4", "adv-n5"):
            if (n + len(noise_id)) % 3 == 0:
                events.append(_access(float(n), noise_id))
        for index in range(len(_FILLER)):
            if (n + index) % 4 == 0:
                events.append(_access(float(n), "adv-f%02d" % (index + 1)))
    queries = [
        _q(10, "adv-01", "adversarial", "When are billing deploys allowed?", "never on Fridays", ["adv-i1"]),
        _q(10, "adv-02", "adversarial", "Which data must not appear in application logs?", "PII", ["adv-i2"]),
        _q(10, "adv-03", "adversarial", "Who should I escalate an incident to?", "Dana, the platform lead", ["adv-i3"]),
        _q(10, "adv-04", "adversarial", "Where must customer data reside?", "the EU", ["adv-i4"]),
        _q(12, "adv-05", "adversarial", "Can we deploy billing on a Friday?", "no", ["adv-i1"]),
        _q(12, "adv-06", "adversarial", "Is logging customer emails allowed?", "no, PII must not be logged", ["adv-i2"]),
        _q(12, "adv-07", "adversarial", "Who is the escalation contact?", "Dana", ["adv-i3"]),
        _q(14, "adv-08", "adversarial", "What is the data residency requirement?", "EU", ["adv-i4"]),
        _q(15, "adv-09", "adversarial", "When is billing frozen?", "Fridays", ["adv-i1"]),
        _q(15, "adv-10", "adversarial", "Which rule applies to customer data in logs?", "no PII in logs", ["adv-i2"]),
        _q(16, "adv-11", "adversarial", "Who owns incident escalation?", "Dana", ["adv-i3"]),
        _q(16, "adv-12", "adversarial", "Must customer data stay in the EU?", "yes", ["adv-i4"]),
    ]
    return Scenario("adversarial-noise", "adversarial",
                    "Sixty-five memories: heavily recalled trivia crowds the budget, and only "
                    "the pinned safety rule survives on usage alone.",
                    events, queries, source="curated")


# -------------------------------------------------------- 8. long dormancy
def _dormancy() -> Scenario:
    events = [
        _add(0, "ld-name", "The user's legal name is Alex Rivera and they go by Alex.", "identity", True),
        _add(0, "ld-git", "Never force-push to main or run destructive git commands without explicit approval.", "identity", True),
        _add(0, "ld-tz", "The user's timezone is Europe/London and they prefer ISO-8601 dates.", "preference"),
        _add(2, "ld-review", "The Q3 vendor review was due 2026-07-01.", "ephemeral"),
        _add(5, "ld-project", "The internal project codename is Helios.", "general"),
        _add(5, "ld-owner", "Priya Raman is the staff engineer who owns the payments service.", "general"),
        _add(5, "ld-standup", "Standup moved to 09:30 on Tuesdays.", "ephemeral"),
    ]
    queries = [
        _q(3, "dorm-01", "preference", "What is the user's legal name?", "Alex Rivera", ["ld-name"]),
        _q(3, "dorm-02", "preference", "What is the user's timezone?", "Europe/London", ["ld-tz"]),
        _q(30, "dorm-03", "preference", "What is the user's legal name?", "Alex Rivera", ["ld-name"]),
        _q(30, "dorm-04", "preference", "Which date format does the user prefer?", _ISO, ["ld-tz"]),
        _q(30, "dorm-05", "lookup", "What is the project codename?", "Helios", ["ld-project"]),
        _q(60, "dorm-06", "preference", "What is the user's name?", "Alex Rivera", ["ld-name"]),
        _q(60, "dorm-07", "lookup", "Who owns the payments service?", "Priya Raman", ["ld-owner"]),
        _q(60, "dorm-08", "lookup", "What is the project codename?", "Helios", ["ld-project"]),
        _q(95, "dorm-09", "preference", "Which git operations need explicit approval?", "force-push to main", ["ld-git"]),
        _q(95, "dorm-10", "preference", "What is the user's name?", "Alex Rivera", ["ld-name"]),
        _q(120, "dorm-11", "preference", "What is the user's legal name?", "Alex Rivera", ["ld-name"]),
        _q(120, "dorm-12", "preference", "Which git operations need explicit approval?", "force-push to main", ["ld-git"]),
        _q(120, "dorm-13", "preference", "What is the user's timezone?", "Europe/London", ["ld-tz"]),
        _q(120, "dorm-14", "lookup", "What is the project codename?", "Helios", ["ld-project"]),
    ]
    return Scenario("dormancy-120d", "lookup",
                    "120 virtual days: pinned identity/safety must survive, ephemeral trivia may decay.",
                    events, queries, source="curated")


# ------------------------------------------------------------------ public
def curated() -> List[Scenario]:
    """The full hand-authored suite, in a stable order."""
    return [
        _preference_tooling(),
        _infra_facts(),
        _flask_fastapi(),
        _postgres_upgrade(),
        _config_updates(),
        _secret_rotation(),
        _budget_pressure(),
        _coldstart(),
        _adversarial(),
        _dormancy(),
    ]


SCENARIOS: List[Scenario] = curated()


# -------------------------------------------------------------- validation
def validate(scenarios: Sequence[Scenario]) -> None:
    """Assert the ground-truth invariants documented at the top of this module."""
    from common import QUERY_KINDS

    total_queries = 0
    for scenario in scenarios:
        assert scenario.source == "curated", scenario.id
        add_days = {}
        for event in scenario.events:
            if event.op == "add":
                assert event.id, "%s: add event without an id" % scenario.id
                assert event.id not in add_days, "%s: duplicate id %s" % (scenario.id, event.id)
                assert event.content, "%s: %s has no content" % (scenario.id, event.id)
                add_days[event.id] = event.day
            else:
                assert event.op == "access", "%s: bad op %s" % (scenario.id, event.op)
                assert event.id in add_days, "%s: access to unknown %s" % (scenario.id, event.id)
        for query in scenario.queries:
            total_queries += 1
            assert query.kind in QUERY_KINDS, "%s/%s: bad kind %s" % (scenario.id, query.id, query.kind)
            assert query.evidence_ids, "%s/%s: no evidence" % (scenario.id, query.id)
            for memory_id in query.evidence_ids + query.stale_ids:
                assert memory_id in add_days, "%s/%s: unknown memory %s" % (
                    scenario.id, query.id, memory_id)
            for memory_id in query.evidence_ids:
                assert add_days[memory_id] <= query.session, "%s/%s: %s created after the query" % (
                    scenario.id, query.id, memory_id)
            if query.stale_ids:
                newest = max(add_days[m] for m in query.evidence_ids)
                for memory_id in query.stale_ids:
                    assert add_days[memory_id] < newest, "%s/%s: %s is not superseded" % (
                        scenario.id, query.id, memory_id)
    assert 10 <= len(scenarios) <= 12, "expected 10-12 scenarios, got %d" % len(scenarios)
    assert 100 <= total_queries <= 130, "expected 100-130 queries, got %d" % total_queries


if __name__ == "__main__":
    suite = curated()
    validate(suite)
    kinds = sorted({q.kind for s in suite for q in s.queries})
    print("curated ok: %d scenarios, %d queries, kinds=%s" % (
        len(suite), sum(len(s.queries) for s in suite), ",".join(kinds)))
