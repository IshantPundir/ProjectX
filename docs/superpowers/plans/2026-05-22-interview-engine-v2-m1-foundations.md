# Interview Engine v2 — Milestone 1: Foundations & Cutover Scaffold

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking. This is **M1** of the master plan
> (`2026-05-22-interview-engine-v2-master-plan.md`) — read the master's §2 (build order), §3 (cutover),
> §4 (file structure) first. Guard each subagent's git scope (commit only the listed files).

**Goal:** Stand up the v2 engine module skeleton + the deterministic foundations (Directive contract,
pure controller, audit record, event-log envelope) + the AIConfig surface + the cutover feature flag,
so a job flagged `interview_engine_version='v2'` routes to a v2 proof-of-life path and writes a v2 audit
envelope — with v1 byte-for-byte unchanged and its test suite green.

**Architecture:** New parallel module `app/modules/interview_engine_v2/`. Everything in M1 except the
LiveKit entrypoint wiring is **pure Python** (no LLM, no livekit) and fully unit-tested. The cutover seam
lives entirely inside the engine container: `build_session_config` resolves the version onto
`SessionConfig`, and a single guarded branch in the existing `interview_engine/agent.py` dispatches to
v2. `INTERVIEW_ENGINE_DEFAULT_VERSION` defaults to `'v1'`.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy async, Alembic, pytest/pytest-asyncio, LiveKit
Agents (entrypoint only), `app/ai/realtime.build_tts_plugin` (proof-of-life line only).

**Conventions to honor (from CLAUDE.md + memory):**
- Module public-API discipline: cross-module imports go through `__init__.py` (`tests/test_module_boundaries.py`
  enforces it; add `interview_engine_v2` to `KNOWN_DOMAIN_MODULES`).
- Keep the **nexus/FastAPI process free of livekit** — `run` is exported via a lazy `__getattr__` so
  importing pure artifacts never pulls livekit.
- **No regex for intent/understanding.** The no-leak validator (Task 7) is a *structural safety
  backstop* (a tiny substring allowlist, explicitly sanctioned by DESIGN-SPEC §6/§12 + doc 11) — it is
  NOT intent classification. Use plain lowercased `in` checks, not `re`.
- `reasoning_effort` gating contract (`app/ai/config.py` header): callers gate on
  `if ai_config.<role>_effort:` before forwarding the param. M1 only defines the config; callers come in
  M4/M5.
- Run backend commands in Docker: `docker compose run --rm nexus <cmd>` (from `backend/nexus/`).

---

## Task 1: v2 module skeleton + public API

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/__init__.py`
- Create: `backend/nexus/app/modules/interview_engine_v2/brain/__init__.py` (empty)
- Create: `backend/nexus/app/modules/interview_engine_v2/mouth/__init__.py` (empty)
- Create: `backend/nexus/app/modules/interview_engine_v2/turn_taking/__init__.py` (empty)
- Create: `backend/nexus/app/modules/interview_engine_v2/event_log/__init__.py` (empty)
- Create: `backend/nexus/tests/interview_engine_v2/__init__.py` (empty)
- Create: `backend/nexus/tests/interview_engine_v2/test_package.py`
- Modify: `backend/nexus/tests/test_module_boundaries.py` (add module name)

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_package.py`:
```python
"""The v2 engine package imports cleanly and exposes its public API."""


def test_package_imports():
    import app.modules.interview_engine_v2 as m2

    assert hasattr(m2, "__all__")


def test_public_api_lists_pure_artifacts():
    from app.modules.interview_engine_v2 import __all__

    # Pure artifacts land in later tasks; `run` is exported lazily (Task 11).
    for name in ("Directive", "DirectiveAct", "DirectiveTone",
                 "DirectiveController", "TurnDecisionRecord", "run"):
        assert name in __all__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_package.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.interview_engine_v2`.

- [ ] **Step 3: Create the package `__init__.py`**

`backend/nexus/app/modules/interview_engine_v2/__init__.py`:
```python
"""Interview Engine v2 — two-plane hybrid (brain + mouth + Directive).

Ground-up rewrite of the decision/conversation core (see
docs/superpowers/plans/2026-05-22-interview-engine-v2-master-plan.md). Built as a
parallel module; the legacy `interview_engine` package is reference-only and
untouched. Selection is via SessionConfig.interview_engine_version + the guarded
branch in interview_engine/agent.py.

Pure artifacts (no livekit) are exported eagerly. `run` (the LiveKit entrypoint,
which pulls livekit) is exported lazily via __getattr__ so importing the pure
artifacts never loads livekit into the nexus/FastAPI process.
"""

from __future__ import annotations

from typing import Any

from app.modules.interview_engine_v2.audit import TurnDecisionRecord
from app.modules.interview_engine_v2.controller import DirectiveController
from app.modules.interview_engine_v2.directive import (
    Directive,
    DirectiveAct,
    DirectiveTone,
)

__all__ = [
    "Directive",
    "DirectiveAct",
    "DirectiveTone",
    "DirectiveController",
    "TurnDecisionRecord",
    "run",
]


def __getattr__(name: str) -> Any:  # PEP 562 — lazy livekit-bearing export
    if name == "run":
        from app.modules.interview_engine_v2.agent import run

        return run
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

Create the empty `__init__.py` files for `brain/`, `mouth/`, `turn_taking/`, `event_log/`, and
`tests/interview_engine_v2/`.

> Note: `__init__.py` references `audit`, `controller`, `directive` modules created in Tasks 6/8/9.
> Until those exist this import fails — that is expected; Step 4 verifies only after they land. To keep
> Task 1 self-contained and green NOW, temporarily stub the three imports:

For Task 1 only, create minimal stubs so the package imports (they are fully implemented in Tasks 6/8/9):

`backend/nexus/app/modules/interview_engine_v2/directive.py` (stub):
```python
"""Directive contract — implemented in Task 6/7."""
from enum import Enum


class DirectiveAct(str, Enum):  # filled in Task 6
    INTRO = "INTRO"


class DirectiveTone(str, Enum):  # filled in Task 6
    WARM = "WARM"


class Directive:  # replaced by a Pydantic model in Task 6
    pass
```

`backend/nexus/app/modules/interview_engine_v2/controller.py` (stub):
```python
"""Two-plane controller — implemented in Task 9."""


class DirectiveController:  # replaced in Task 9
    pass
```

`backend/nexus/app/modules/interview_engine_v2/audit.py` (stub):
```python
"""Per-turn audit decision record — implemented in Task 8."""


class TurnDecisionRecord:  # replaced by a Pydantic model in Task 8
    pass
```

- [ ] **Step 4: Add the module to the boundary lint**

In `backend/nexus/tests/test_module_boundaries.py`, find the `KNOWN_DOMAIN_MODULES` collection and add
`"interview_engine_v2"` to it (keep alphabetical/existing order). Run:
`docker compose run --rm nexus pytest tests/test_module_boundaries.py -v`
Expected: PASS.

- [ ] **Step 5: Run the package test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_package.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/ backend/nexus/tests/interview_engine_v2/ backend/nexus/tests/test_module_boundaries.py
git commit -m "feat(engine-v2): scaffold interview_engine_v2 module + public API"
```

---

## Task 2: AIConfig + Settings additions (version flag + brain/mouth model map)

**Files:**
- Modify: `backend/nexus/app/config.py` (Settings — add fields near the other `engine_*` fields, ~line 407)
- Modify: `backend/nexus/app/ai/config.py` (AIConfig — add properties after `engine_speaker_prompt_version`)
- Test: `backend/nexus/tests/interview_engine_v2/test_config.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_config.py`:
```python
"""AIConfig surface for the v2 engine (default version + brain/mouth model map)."""

import pytest

from app.ai.config import AIConfig


def test_default_version_is_v1():
    assert AIConfig().interview_engine_default_version == "v1"


def test_default_version_env_override(monkeypatch):
    monkeypatch.setenv("INTERVIEW_ENGINE_DEFAULT_VERSION", "v2")
    assert AIConfig().interview_engine_default_version == "v2"


def test_brain_and_mouth_model_map():
    cfg = AIConfig()
    assert cfg.engine_brain_model.startswith("gpt-5.4")
    assert cfg.engine_brain_effort == "low"          # reasoning-first, low effort by design
    assert cfg.engine_mouth_model.startswith("gpt-5.4-mini")
    assert cfg.engine_mouth_effort == ""             # latency-first; no reasoning effort sent
    assert cfg.engine_brain_prompt_version == "v3"
    assert cfg.engine_mouth_prompt_version == "v3"
    assert cfg.engine_brain_prompt_cache_key == "brain:v1"
    assert cfg.engine_mouth_prompt_cache_key == "mouth:v1"


def test_model_overrides(monkeypatch):
    monkeypatch.setenv("ENGINE_BRAIN_MODEL", "gpt-5.5-2026-04-24")
    monkeypatch.setenv("ENGINE_BRAIN_EFFORT", "")
    cfg = AIConfig()
    assert cfg.engine_brain_model == "gpt-5.5-2026-04-24"
    assert cfg.engine_brain_effort == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'interview_engine_default_version'`.

- [ ] **Step 3: Add the Settings fields**

In `backend/nexus/app/config.py`, after the `engine_speaker_prompt_version` field (~line 408), add:
```python
    # --- Interview engine v2 (two-plane) — selection + model map ---
    # Selection: per-job override (job_postings.interview_engine_version) falls
    # back to this global default. 'v1' keeps every session on the legacy engine.
    interview_engine_default_version: Literal["v1", "v2"] = "v1"

    # Brain (Control Plane) — GPT-5.4, low reasoning_effort + a reasoning field
    # (design doc 10). Reasoning model => 'low' is the intended default; callers
    # still gate on `if ai_config.engine_brain_effort:` per the effort contract,
    # so overriding the model to a chat variant + clearing the effort is safe.
    engine_brain_model: str = "gpt-5.4-2026-03-05"
    engine_brain_effort: str = "low"

    # Mouth (Conversation Plane) — GPT-5.4 Mini, latency-first, no reasoning effort.
    engine_mouth_model: str = "gpt-5.4-mini-2026-03-17"
    engine_mouth_effort: str = ""

    # New v3 engine prompt family (rewritten from scratch — brain + per-act mouth).
    engine_brain_prompt_version: str = "v3"
    engine_mouth_prompt_version: str = "v3"

    # Explicit OpenAI prompt_cache_key per surface for stable cache routing
    # (design §11: stable-prefix -> dynamic-suffix). Bump the suffix on a
    # prompt change to avoid cross-version cache pollution.
    engine_brain_prompt_cache_key: str = "brain:v1"
    engine_mouth_prompt_cache_key: str = "mouth:v1"
```

- [ ] **Step 4: Add the AIConfig properties**

In `backend/nexus/app/ai/config.py`, after the `engine_speaker_prompt_version` property (~line 160), add:
```python
    # --- Interview engine v2 (two-plane) ---
    @property
    def interview_engine_default_version(self) -> str:
        return self._settings.interview_engine_default_version

    @property
    def engine_brain_model(self) -> str:
        return self._settings.engine_brain_model

    @property
    def engine_brain_effort(self) -> str:
        return self._settings.engine_brain_effort

    @property
    def engine_mouth_model(self) -> str:
        return self._settings.engine_mouth_model

    @property
    def engine_mouth_effort(self) -> str:
        return self._settings.engine_mouth_effort

    @property
    def engine_brain_prompt_version(self) -> str:
        return self._settings.engine_brain_prompt_version

    @property
    def engine_mouth_prompt_version(self) -> str:
        return self._settings.engine_mouth_prompt_version

    @property
    def engine_brain_prompt_cache_key(self) -> str:
        return self._settings.engine_brain_prompt_cache_key

    @property
    def engine_mouth_prompt_cache_key(self) -> str:
        return self._settings.engine_mouth_prompt_cache_key
```

- [ ] **Step 5: Document the env vars in `.env.example`**

In `backend/nexus/.env.example`, near the existing `ENGINE_*` vars, add (commented, with defaults):
```bash
# Interview engine v2 (two-plane). Default selection stays v1.
INTERVIEW_ENGINE_DEFAULT_VERSION=v1
ENGINE_BRAIN_MODEL=gpt-5.4-2026-03-05
ENGINE_BRAIN_EFFORT=low
ENGINE_MOUTH_MODEL=gpt-5.4-mini-2026-03-17
ENGINE_MOUTH_EFFORT=
ENGINE_BRAIN_PROMPT_VERSION=v3
ENGINE_MOUTH_PROMPT_VERSION=v3
ENGINE_BRAIN_PROMPT_CACHE_KEY=brain:v1
ENGINE_MOUTH_PROMPT_CACHE_KEY=mouth:v1
```

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_config.py -v`
Expected: PASS (all four tests).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/.env.example backend/nexus/tests/interview_engine_v2/test_config.py
git commit -m "feat(engine-v2): add v2 selection flag + brain/mouth model map to AIConfig"
```

---

## Task 3: `SessionConfig.interview_engine_version` field

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py` (add field to `SessionConfig`)
- Test: `backend/nexus/tests/interview_runtime/test_schemas.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/interview_runtime/test_schemas.py` (import `SessionConfig` if not already;
build a minimal valid config via the existing helper/fixture in that file, or construct directly):
```python
def test_session_config_engine_version_defaults_v1(minimal_session_config_kwargs):
    from app.modules.interview_runtime.schemas import SessionConfig

    cfg = SessionConfig(**minimal_session_config_kwargs)
    assert cfg.interview_engine_version == "v1"


def test_session_config_engine_version_accepts_v2(minimal_session_config_kwargs):
    from app.modules.interview_runtime.schemas import SessionConfig

    cfg = SessionConfig(**{**minimal_session_config_kwargs, "interview_engine_version": "v2"})
    assert cfg.interview_engine_version == "v2"
```

> If `minimal_session_config_kwargs` doesn't already exist in this test module, add a fixture at the top
> that returns the smallest valid kwargs dict for `SessionConfig` (reuse the construction in the existing
> `build_session_config` tests as the template — `session_id`, `job_id`, `candidate_id`, `job_title`,
> `role_summary`, `seniority_level`, `company=CompanyContext(...)`, `candidate=CandidateContext(name="X")`,
> `stage=StageConfig(...)` with an empty `questions=[]`).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_schemas.py -k engine_version -v`
Expected: FAIL — `interview_engine_version` is not a field of `SessionConfig`.

- [ ] **Step 3: Add the field**

In `backend/nexus/app/modules/interview_runtime/schemas.py`, inside `class SessionConfig`, after the
`keyterms` field (~line 265), add:
```python
    interview_engine_version: Literal["v1", "v2"] = Field(
        default="v1",
        description=(
            "Which engine core runs this session. Resolved in build_session_config "
            "as `job.interview_engine_version or ai_config.interview_engine_default_version`. "
            "The engine entrypoint (interview_engine/agent.py) branches on this: "
            "'v1' = legacy orchestrator, 'v2' = interview_engine_v2. Default 'v1' so "
            "any caller that omits it (or a pre-migration job row) stays on the legacy path."
        ),
    )
```
(`Literal` and `Field` are already imported in this module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_schemas.py -k engine_version -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py backend/nexus/tests/interview_runtime/test_schemas.py
git commit -m "feat(engine-v2): add interview_engine_version to SessionConfig wire contract"
```

---

## Task 4: Migration `0044` + JobPosting ORM column

**Files:**
- Create: `backend/nexus/migrations/versions/0044_interview_engine_version.py`
- Modify: `backend/nexus/app/modules/jd/models.py` (add the mapped column to `JobPosting`)
- Test: `backend/nexus/tests/interview_runtime/test_engine_version_column.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_runtime/test_engine_version_column.py`:
```python
"""JobPosting carries the v2 selection column."""

from app.modules.jd.models import JobPosting


def test_jobposting_has_interview_engine_version_column():
    assert "interview_engine_version" in JobPosting.__table__.columns
    col = JobPosting.__table__.columns["interview_engine_version"]
    assert col.nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_engine_version_column.py -v`
Expected: FAIL — column not present.

- [ ] **Step 3: Add the ORM column**

In `backend/nexus/app/modules/jd/models.py`, inside `class JobPosting`, add a mapped column alongside the
other optional columns (match the file's existing `Mapped[...] = mapped_column(...)` style):
```python
    interview_engine_version: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        doc="v2 engine opt-in: NULL inherits the global default; 'v1'/'v2' overrides it.",
    )
```
(Ensure `Text` is imported in that module; it almost certainly already is — if not, add it to the
`from sqlalchemy import ...` line.)

- [ ] **Step 4: Write the migration**

`backend/nexus/migrations/versions/0044_interview_engine_version.py`:
```python
"""interview engine v2 — per-job selection column

Revision ID: 0044_interview_engine_version
Revises: 0043_session_proctoring
Create Date: 2026-05-22

Adds job_postings.interview_engine_version (nullable). NULL inherits the global
INTERVIEW_ENGINE_DEFAULT_VERSION; 'v1'/'v2' overrides it per job so a single test
job can be flipped to v2 without affecting any live flow. No RLS change (column
on an already-policied table).

Rollback: downgrade() drops the CHECK + column.
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_interview_engine_version"
down_revision = "0043_session_proctoring"
branch_labels = None
depends_on = None

_CK = "ck_job_postings_interview_engine_version"


def upgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("interview_engine_version", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        _CK,
        "job_postings",
        "interview_engine_version IS NULL OR interview_engine_version IN ('v1', 'v2')",
    )


def downgrade() -> None:
    op.drop_constraint(_CK, "job_postings", type_="check")
    op.drop_column("job_postings", "interview_engine_version")
```

- [ ] **Step 5: Apply the migration + verify up/down/up is clean**

Run:
```bash
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: all three succeed with no error; `alembic current` shows `0044_interview_engine_version`.

- [ ] **Step 6: Run the ORM test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_engine_version_column.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/migrations/versions/0044_interview_engine_version.py backend/nexus/app/modules/jd/models.py backend/nexus/tests/interview_runtime/test_engine_version_column.py
git commit -m "feat(engine-v2): migration 0044 + JobPosting.interview_engine_version column"
```

---

## Task 5: `build_session_config` resolves the engine version

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py`
- Test: `backend/nexus/tests/interview_runtime/test_build_session_config_engine_version.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_runtime/test_build_session_config_engine_version.py`:
```python
"""build_session_config resolves the engine version: job override else global default."""

import pytest

from app.modules.interview_runtime.service import build_session_config


@pytest.mark.asyncio
async def test_defaults_to_v1(bypass_db, seeded_ai_session):
    # seeded_ai_session: existing fixture that creates a confirmed-bank AI-screening
    # session and returns (session_id, tenant_id). job.interview_engine_version is NULL.
    session_id, tenant_id = seeded_ai_session
    cfg = await build_session_config(bypass_db, session_id=session_id, tenant_id=tenant_id)
    assert cfg.interview_engine_version == "v1"


@pytest.mark.asyncio
async def test_job_override_v2(bypass_db, seeded_ai_session, set_job_engine_version):
    session_id, tenant_id = seeded_ai_session
    await set_job_engine_version(session_id, tenant_id, "v2")
    cfg = await build_session_config(bypass_db, session_id=session_id, tenant_id=tenant_id)
    assert cfg.interview_engine_version == "v2"


@pytest.mark.asyncio
async def test_global_default_v2_when_job_null(bypass_db, seeded_ai_session, monkeypatch):
    session_id, tenant_id = seeded_ai_session
    monkeypatch.setenv("INTERVIEW_ENGINE_DEFAULT_VERSION", "v2")
    cfg = await build_session_config(bypass_db, session_id=session_id, tenant_id=tenant_id)
    assert cfg.interview_engine_version == "v2"
```

> Reuse the existing build_session_config fixtures. The current suite already has a fixture that seeds a
> full session graph with a confirmed bank (it's what `tests/interview_runtime/test_build_session_config_*.py`
> uses — locate it in `tests/interview_runtime/conftest.py` or the test module and reference it as
> `seeded_ai_session`, renaming the test args to match the real fixture name). Add a tiny
> `set_job_engine_version` helper fixture that issues
> `UPDATE job_postings SET interview_engine_version=:v WHERE ...` under the bypass session, OR set the
> attribute on the job ORM object and flush. If the global-default test can't read the monkeypatched env
> (AIConfig caches `settings` at import), have it construct via `AIConfig()` — which it does — so the
> monkeypatch is picked up; if `build_session_config` imports the module-level `ai_config` singleton,
> see Step 3's note.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_build_session_config_engine_version.py -v`
Expected: FAIL — `cfg.interview_engine_version` is `"v1"` for the override case (resolution not wired) /
AttributeError if the env path isn't read.

- [ ] **Step 3: Wire the resolution**

In `backend/nexus/app/modules/interview_runtime/service.py`:

(a) Add the import near the other `app.*` imports (top of file, after line 26):
```python
from app.ai.config import AIConfig
```

(b) In `build_session_config`, just before the `config = SessionConfig(` construction (~line 190), add:
```python
    # v2 cutover selector: per-job override falls back to the global default.
    # Construct a fresh AIConfig() so a monkeypatched env is honored in tests
    # (the module-level singleton caches the boot-time Settings).
    resolved_engine_version = (
        getattr(job, "interview_engine_version", None)
        or AIConfig().interview_engine_default_version
    )
```

(c) Add the field to the `SessionConfig(...)` call (e.g. right after `keyterms=...`):
```python
        interview_engine_version=resolved_engine_version,
```

(d) Add `interview_engine_version=resolved_engine_version` to the existing
`logger.info("interview_runtime.session_config.built", ...)` kwargs so the selection is observable.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_build_session_config_engine_version.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Run the full interview_runtime suite (regression)**

Run: `docker compose run --rm nexus pytest tests/interview_runtime -v`
Expected: PASS — no existing test broke.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/service.py backend/nexus/tests/interview_runtime/test_build_session_config_engine_version.py
git commit -m "feat(engine-v2): resolve interview_engine_version in build_session_config"
```

---

## Task 6: Directive schema + closed `act`/`tone` enums

**Files:**
- Modify (replace stub): `backend/nexus/app/modules/interview_engine_v2/directive.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_directive.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_directive.py`:
```python
"""Directive contract — closed enums, field defaults, ASK/PROBE require verbatim text."""

import pytest
from pydantic import ValidationError

from app.modules.interview_engine_v2.directive import Directive, DirectiveAct, DirectiveTone


def test_act_enum_is_closed_and_complete():
    acts = {a.value for a in DirectiveAct}
    assert acts == {
        "INTRO", "ASK", "PROBE", "CLARIFY", "ACK_ADVANCE", "REPEAT", "REDIRECT",
        "HOLD", "REASSURE", "HINT", "ANSWER_META", "CONFIRM", "CLOSE",
    }


def test_tone_enum_is_closed():
    assert {t.value for t in DirectiveTone} == {"WARM", "NEUTRAL", "ENCOURAGING", "CALM"}


def test_minimal_ask_directive():
    d = Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK,
                  say="Tell me about a tricky incident you owned.")
    assert d.tone == DirectiveTone.NEUTRAL          # default
    assert d.is_terminal is False
    assert d.speculative is False
    assert d.supersedes is None
    assert d.compose_hint is None


def test_ask_requires_say():
    with pytest.raises(ValidationError):
        Directive(id="d-2", turn_ref="t-1", act=DirectiveAct.ASK, say=None)


def test_probe_requires_say():
    with pytest.raises(ValidationError):
        Directive(id="d-3", turn_ref="t-1", act=DirectiveAct.PROBE, say=None)


def test_close_must_be_terminal():
    with pytest.raises(ValidationError):
        Directive(id="d-4", turn_ref="t-1", act=DirectiveAct.CLOSE,
                  compose_hint="thank warmly", is_terminal=False)


def test_terminal_only_on_close():
    with pytest.raises(ValidationError):
        Directive(id="d-5", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="Next question.", is_terminal=True)


def test_composed_act_allows_null_say():
    d = Directive(id="d-6", turn_ref="t-1", act=DirectiveAct.HOLD,
                  say=None, compose_hint="warm 'take your time', short")
    assert d.say is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_directive.py -v`
Expected: FAIL — stub `Directive` is not a Pydantic model / enum incomplete.

- [ ] **Step 3: Implement the Directive model**

Replace `backend/nexus/app/modules/interview_engine_v2/directive.py` with:
```python
"""The Directive — the ONLY object that crosses Control Plane (brain) -> Conversation
Plane (mouth). Carries only speakable text + delivery metadata; never rubric/evidence
(no-leak by construction; see directive no-leak validator in this module). Supports
Option C staging/supersession. Design: DESIGN-SPEC §7 + doc 11.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class DirectiveAct(str, Enum):
    """Closed interviewer move-set. The mouth has no behavior outside this enum."""

    INTRO = "INTRO"            # greeting + AI disclosure + format + hand-off
    ASK = "ASK"               # deliver the next main question (verbatim bank text)
    PROBE = "PROBE"           # deliver a follow-up (verbatim bank follow-up)
    CLARIFY = "CLARIFY"       # rephrase/explain the current question
    ACK_ADVANCE = "ACK_ADVANCE"  # brief neutral ack, then ask next
    REPEAT = "REPEAT"         # replay last question (mouth uses cached last question)
    REDIRECT = "REDIRECT"     # gently bring back on-topic
    HOLD = "HOLD"             # patience cue ("take your time")
    REASSURE = "REASSURE"     # calm a nervous candidate
    HINT = "HINT"             # technical nudge (brain-composed safe hint)
    ANSWER_META = "ANSWER_META"  # answer a logistics/role question (grounded)
    CONFIRM = "CONFIRM"       # reflect-to-confirm a garbled/ambiguous answer
    CLOSE = "CLOSE"           # warm close + next steps (terminal)


class DirectiveTone(str, Enum):
    WARM = "WARM"
    NEUTRAL = "NEUTRAL"
    ENCOURAGING = "ENCOURAGING"
    CALM = "CALM"


# Acts whose `say` is the VERBATIM bank text — the brain selects, never rewrites.
_SAY_REQUIRED: frozenset[DirectiveAct] = frozenset({DirectiveAct.ASK, DirectiveAct.PROBE})


class Directive(BaseModel):
    """A single brain->mouth instruction. See DESIGN-SPEC §7 / doc 11."""

    model_config = {"frozen": False}

    id: str = Field(min_length=1, description="Unique id; used for supersession + audit.")
    turn_ref: str = Field(
        min_length=1,
        description="The candidate turn this was computed for (staleness guard).",
    )
    act: DirectiveAct
    say: str | None = Field(
        default=None,
        description=(
            "Verbatim speakable text. For ASK/PROBE this is the verbatim bank "
            "question/follow-up (brain selects, never rewrites). For other acts it "
            "is brain-composed safe text, or null when the mouth composes from "
            "`compose_hint`."
        ),
    )
    compose_hint: str | None = Field(
        default=None,
        description="SHORT, leak-safe styling for composed parts, or null.",
    )
    tone: DirectiveTone = DirectiveTone.NEUTRAL
    is_terminal: bool = Field(
        default=False, description="True only on CLOSE; session ends after delivery."
    )
    speculative: bool = Field(
        default=False, description="True = Option C pre-staged guess; discard if not confirmed."
    )
    supersedes: str | None = Field(
        default=None, description="id of a staged Directive this replaces, or null."
    )

    @model_validator(mode="after")
    def _validate_act_invariants(self) -> "Directive":
        if self.act in _SAY_REQUIRED and not (self.say and self.say.strip()):
            raise ValueError(f"act {self.act.value} requires non-empty `say` (verbatim bank text)")
        if self.act is DirectiveAct.CLOSE and not self.is_terminal:
            raise ValueError("CLOSE directive must have is_terminal=True")
        if self.is_terminal and self.act is not DirectiveAct.CLOSE:
            raise ValueError("is_terminal=True is only valid on a CLOSE directive")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_directive.py -v`
Expected: PASS (all eight).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/directive.py backend/nexus/tests/interview_engine_v2/test_directive.py
git commit -m "feat(engine-v2): Directive schema with closed act/tone enums + invariants"
```

---

## Task 7: No-rubric-leak validator (structural safety backstop)

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine_v2/directive.py` (add validator + constant + error)
- Test: `backend/nexus/tests/interview_engine_v2/test_directive_no_leak.py`

> This is a **structural safety backstop**, explicitly sanctioned by DESIGN-SPEC §6/§12 + doc 11 ("a
> cheap validator can flag if a Directive's text smells like rubric"). It is NOT intent classification,
> so the `no-regex` rule does not apply — it's a tiny lowercased substring allowlist. The real guarantee
> is structural: the mouth process holds no rubric at all. This validator catches a brain authoring bug.

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_directive_no_leak.py`:
```python
"""No-rubric-leak: a Directive may not carry evaluation/rubric text in say/compose_hint."""

import pytest
from pydantic import ValidationError

from app.modules.interview_engine_v2.directive import (
    Directive,
    DirectiveAct,
    FORBIDDEN_RUBRIC_TOKENS,
)


def test_clean_directive_passes():
    Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK,
              say="Walk me through a deploy that went wrong.")


@pytest.mark.parametrize("leak", [
    "We're looking for evidence of idempotency.",
    "Listen for the red flags here.",
    "Score against the rubric: excellent / meets_bar / below_bar.",
    "positive_evidence: names a real tool.",
])
def test_leak_in_say_rejected(leak):
    with pytest.raises(ValidationError):
        Directive(id="d-2", turn_ref="t-1", act=DirectiveAct.ANSWER_META, say=leak)


def test_leak_in_compose_hint_rejected():
    with pytest.raises(ValidationError):
        Directive(id="d-3", turn_ref="t-1", act=DirectiveAct.HOLD, say=None,
                  compose_hint="hint at the red_flags we track")


def test_forbidden_tokens_are_lowercased_substrings():
    # negative control: every token must trip the check regardless of case.
    for tok in FORBIDDEN_RUBRIC_TOKENS:
        with pytest.raises(ValidationError):
            Directive(id="d-x", turn_ref="t-1", act=DirectiveAct.ANSWER_META,
                      say=f"... {tok.upper()} ...")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_directive_no_leak.py -v`
Expected: FAIL — `FORBIDDEN_RUBRIC_TOKENS` import error / leaks not rejected.

- [ ] **Step 3: Implement the validator**

In `backend/nexus/app/modules/interview_engine_v2/directive.py`, add near the top (after the enums):
```python
# Structural no-leak backstop (DESIGN-SPEC §6/§12, doc 11). Plain lowercased
# substring matching — NOT intent classification, so the no-regex rule does not
# apply. The real guarantee is that the mouth holds no rubric; this catches a
# brain-authoring bug where evaluation text leaks into speakable fields. Keep the
# list tight to avoid false positives on natural speech.
FORBIDDEN_RUBRIC_TOKENS: tuple[str, ...] = (
    "positive_evidence",
    "red_flag",
    "red flag",
    "red flags",
    "rubric",
    "meets_bar",
    "meets bar",
    "below_bar",
    "below bar",
    "evaluation_hint",
    "signal_value",
    "we're looking for",
    "we are looking for",
    "what i'm listening for",
    "what we're scoring",
)


class RubricLeakError(ValueError):
    """A Directive's speakable text smelled like rubric/evaluation criteria."""
```

Then extend the `_validate_act_invariants` model validator (or add a second `model_validator(mode="after")`)
with the leak scan. Add this block at the end of the existing validator, before `return self`:
```python
        haystack = " ".join(p for p in (self.say, self.compose_hint) if p).lower()
        for token in FORBIDDEN_RUBRIC_TOKENS:
            if token in haystack:
                raise RubricLeakError(
                    f"Directive {self.id} carries rubric-smelling text "
                    f"(token={token!r}); only speakable text may cross to the mouth"
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_directive_no_leak.py -v`
Expected: PASS.

- [ ] **Step 5: Re-run the Task 6 suite (no regression)**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_directive.py -v`
Expected: PASS (clean directives still construct).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/directive.py backend/nexus/tests/interview_engine_v2/test_directive_no_leak.py
git commit -m "feat(engine-v2): structural no-rubric-leak validator on Directive"
```

---

## Task 8: `TurnDecisionRecord` audit schema

**Files:**
- Modify (replace stub): `backend/nexus/app/modules/interview_engine_v2/audit.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_audit.py`

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_audit.py`:
```python
"""TurnDecisionRecord — the brain-side audit pairing for every Directive (doc 11/13)."""

import pytest
from pydantic import ValidationError

from app.modules.interview_engine_v2.audit import TurnDecisionRecord


def test_minimal_record():
    r = TurnDecisionRecord(
        turn_ref="t-42",
        candidate_quote="I built the billing sync end to end.",
        move="probe",
        reasoning="Concrete on ownership; one probe to test depth.",
        directive_id="d-7f3a",
    )
    assert r.attributed_signals == []
    assert r.coverage_delta == {}
    assert r.policy_checks == []
    assert r.grade is None


def test_full_record():
    r = TurnDecisionRecord(
        turn_ref="t-42",
        candidate_quote="...",
        attributed_signals=["workato_recipes", "api_integration"],
        grade="concrete",
        coverage_delta={"workato_recipes": "sufficient"},
        move="advance",
        reasoning="...",
        policy_checks=["no_leak_ok", "knockout_not_triggered"],
        directive_id="d-9",
    )
    assert r.grade == "concrete"
    assert r.coverage_delta["workato_recipes"] == "sufficient"


def test_grade_is_constrained():
    with pytest.raises(ValidationError):
        TurnDecisionRecord(turn_ref="t-1", candidate_quote="x", move="probe",
                           reasoning="y", directive_id="d", grade="amazing")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_audit.py -v`
Expected: FAIL — stub `TurnDecisionRecord` is not a Pydantic model.

- [ ] **Step 3: Implement the record**

Replace `backend/nexus/app/modules/interview_engine_v2/audit.py` with:
```python
"""Per-turn audit decision record — the brain's full reasoning trail.

This is the auditable artifact (EEOC/bias defensibility, forensic debugging). It is
the SUPERSET of what the Directive carries: the Directive is just the speakable
projection of this record (DESIGN-SPEC §13, doc 11). The brain's rubric/evidence
reasoning lives HERE and never travels to the mouth.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TurnDecisionRecord(BaseModel):
    """One brain decision, logged alongside (not inside) the Directive it produced."""

    turn_ref: str = Field(min_length=1)
    candidate_quote: str = Field(description="The candidate utterance graded this turn (quoted DATA).")
    attributed_signals: list[str] = Field(
        default_factory=list, description="Signal value(s) the answer was credited to."
    )
    grade: Literal["thin", "concrete", "strong"] | None = Field(
        default=None, description="Evidence grade vs rubric; None when not a gradeable turn."
    )
    coverage_delta: dict[str, str] = Field(
        default_factory=dict,
        description="signal_value -> new coverage state (none/partial/sufficient/failed).",
    )
    move: str = Field(min_length=1, description="The chosen move (probe/advance/clarify/knockout/...).")
    reasoning: str = Field(description="The brain's autoregressive reasoning for this decision.")
    policy_checks: list[str] = Field(
        default_factory=list, description="Deterministic gates that passed/fired this turn."
    )
    directive_id: str = Field(min_length=1, description="The Directive this record produced.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_audit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/audit.py backend/nexus/tests/interview_engine_v2/test_audit.py
git commit -m "feat(engine-v2): TurnDecisionRecord audit schema"
```

---

## Task 9: `DirectiveController` — queue, supersession, staleness, speculative discard

**Files:**
- Modify (replace stub): `backend/nexus/app/modules/interview_engine_v2/controller.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_controller.py`
- Create: `backend/nexus/tests/interview_engine_v2/conftest.py` (directive factory fixture)

- [ ] **Step 1: Add the directive factory fixture**

`backend/nexus/tests/interview_engine_v2/conftest.py`:
```python
import pytest

from app.modules.interview_engine_v2.directive import Directive, DirectiveAct


@pytest.fixture
def make_directive():
    """Factory: make_directive(id, turn_ref, act=ASK, say='...', **kw)."""
    def _make(id, turn_ref, *, act=DirectiveAct.ASK, say="Tell me about your last project.", **kw):
        return Directive(id=id, turn_ref=turn_ref, act=act, say=say, **kw)
    return _make
```

- [ ] **Step 2: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_controller.py`:
```python
"""DirectiveController — the pure two-plane concurrency core (Option C).

Invariants under test (DESIGN-SPEC §2/§7, doc 08 system-concurrency):
  - never deliver a directive computed for a different turn (staleness)
  - never deliver a superseded directive
  - never deliver two directives for the same boundary
  - a wrong speculative pre-stage is cleanly discarded
"""

import pytest

from app.modules.interview_engine_v2.controller import DirectiveController


def test_stage_then_current_for_matching_turn(make_directive):
    c = DirectiveController()
    d = make_directive("d-1", "t-1")
    c.stage(d)
    assert c.current_for_turn("t-1") is d


def test_stale_directive_not_delivered(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-1", "t-1"))
    assert c.current_for_turn("t-2") is None  # computed for t-1, now at t-2


def test_supersession_replaces_and_discards_old(make_directive):
    c = DirectiveController()
    old = make_directive("d-old", "t-1", speculative=True)
    c.stage(old)
    new = make_directive("d-new", "t-1", supersedes="d-old")
    c.stage(new)
    assert c.current_for_turn("t-1") is new
    assert c.is_discarded("d-old")
    # the superseded directive is never returned, even by id-targeted lookups
    assert c.current_for_turn("t-1").id == "d-new"


def test_discard_speculative(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-spec", "t-1", speculative=True))
    c.discard_speculative()
    assert c.current_for_turn("t-1") is None
    assert c.is_discarded("d-spec")


def test_discard_speculative_noop_on_confirmed(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-real", "t-1", speculative=False))
    c.discard_speculative()  # must NOT drop a confirmed directive
    assert c.current_for_turn("t-1").id == "d-real"


def test_mark_delivered_clears_current(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-1", "t-1"))
    c.mark_delivered("d-1")
    assert c.current_for_turn("t-1") is None
    assert c.was_delivered("d-1")


def test_never_returns_discarded(make_directive):
    c = DirectiveController()
    d = make_directive("d-1", "t-1", speculative=True)
    c.stage(d)
    c.discard_speculative()
    # re-staging is allowed; a discarded id stays discarded if re-presented
    assert c.current_for_turn("t-1") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_controller.py -v`
Expected: FAIL — stub `DirectiveController` has no methods.

- [ ] **Step 4: Implement the controller**

Replace `backend/nexus/app/modules/interview_engine_v2/controller.py` with:
```python
"""The two-plane controller core (pure, no LLM, no livekit).

Holds the single current Directive and enforces the Option C delivery invariants:
staging, supersession, staleness, and speculative discard. The mouth asks
`current_for_turn(turn_ref)` at a turn boundary and delivers whatever it returns
(or nothing). Because the AI only ever delivers at a turn boundary (one-directional
interruption invariant, doc 08), supersession is always clean — there is never a
half-spoken Directive to abort.
"""

from __future__ import annotations

from app.modules.interview_engine_v2.directive import Directive


class DirectiveController:
    def __init__(self) -> None:
        self._staged: Directive | None = None
        self._delivered_ids: set[str] = set()
        self._discarded_ids: set[str] = set()

    def stage(self, directive: Directive) -> None:
        """Stage a directive. If it supersedes the staged one, discard the old."""
        if (
            directive.supersedes is not None
            and self._staged is not None
            and self._staged.id == directive.supersedes
        ):
            self._discarded_ids.add(self._staged.id)
        self._staged = directive

    def discard_speculative(self) -> None:
        """Drop the staged directive iff it is a speculative pre-stage."""
        if self._staged is not None and self._staged.speculative:
            self._discarded_ids.add(self._staged.id)
            self._staged = None

    def current_for_turn(self, turn_ref: str) -> Directive | None:
        """The directive to deliver at this boundary, or None.

        Returns None when: nothing staged, the staged directive was discarded /
        superseded, or it was computed for a different turn (stale).
        """
        d = self._staged
        if d is None:
            return None
        if d.id in self._discarded_ids:
            return None
        if d.turn_ref != turn_ref:
            return None
        return d

    def mark_delivered(self, directive_id: str) -> None:
        """Record delivery; clears the staged slot if it matches."""
        self._delivered_ids.add(directive_id)
        if self._staged is not None and self._staged.id == directive_id:
            self._staged = None

    def was_delivered(self, directive_id: str) -> bool:
        return directive_id in self._delivered_ids

    def is_discarded(self, directive_id: str) -> bool:
        return directive_id in self._discarded_ids
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_controller.py -v`
Expected: PASS (all seven).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/controller.py backend/nexus/tests/interview_engine_v2/conftest.py backend/nexus/tests/interview_engine_v2/test_controller.py
git commit -m "feat(engine-v2): DirectiveController (queue/supersession/staleness/discard)"
```

---

## Task 10: v2 event-log envelope + collector

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/event_log/envelope.py`
- Create: `backend/nexus/app/modules/interview_engine_v2/event_log/collector.py`
- Test: `backend/nexus/tests/interview_engine_v2/test_event_log.py`

> Self-contained copy of the v1 envelope *shape* (it is tiny and permissive) so v2 carries no dependency
> on the to-be-deleted `interview_engine` package, and so it can embed the new `TurnDecisionRecord`
> event. Reuse the same `correlation_id`-anchored concept (DESIGN-SPEC §13).

- [ ] **Step 1: Write the failing test**

`backend/nexus/tests/interview_engine_v2/test_event_log.py`:
```python
"""v2 event-log collector: records events + decision records into a valid envelope."""

from app.modules.interview_engine_v2.audit import TurnDecisionRecord
from app.modules.interview_engine_v2.event_log.collector import EventCollector
from app.modules.interview_engine_v2.event_log.envelope import EventLogEnvelope


def test_collector_records_event():
    c = EventCollector(session_id="s-1", tenant_id="ten-1", correlation_id="corr-1")
    c.record("engine.v2.dispatched", {"job_title": "FDE"}, t_ms=0, wall_ms=1)
    env = c.envelope(closed_at="2026-05-22T00:00:01Z")
    assert isinstance(env, EventLogEnvelope)
    assert env.session_id == "s-1"
    assert env.correlation_id == "corr-1"
    assert len(env.events) == 1
    assert env.events[0].kind == "engine.v2.dispatched"


def test_collector_records_decision():
    c = EventCollector(session_id="s-1", tenant_id="ten-1", correlation_id="corr-1")
    rec = TurnDecisionRecord(turn_ref="t-1", candidate_quote="x", move="advance",
                             reasoning="y", directive_id="d-1")
    c.record_decision(rec, t_ms=10, wall_ms=2)
    env = c.envelope()
    assert env.events[0].kind == "turn.decision"
    assert env.events[0].payload["directive_id"] == "d-1"
    assert env.closed_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_event_log.py -v`
Expected: FAIL — modules don't exist.

- [ ] **Step 3: Implement the envelope**

`backend/nexus/app/modules/interview_engine_v2/event_log/envelope.py`:
```python
"""v2 per-session audit envelope (shape mirrors interview_engine/event_log/envelope.py)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EventLogEvent(BaseModel):
    t_ms: int = Field(ge=0, description="Monotonic ms since session start (relative).")
    wall_ms: int = Field(ge=0, description="Unix-epoch ms (absolute).")
    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction: Literal["metadata", "full"] = "metadata"


class EventLogEnvelope(BaseModel):
    session_id: str
    tenant_id: str
    correlation_id: str
    started_at: str
    closed_at: str | None = None
    engine_version: str = "v2"
    model_versions: dict[str, str] = Field(default_factory=dict)
    redaction_mode: Literal["metadata", "full"] = "metadata"
    events: list[EventLogEvent] = Field(default_factory=list)
```

- [ ] **Step 4: Implement the collector**

`backend/nexus/app/modules/interview_engine_v2/event_log/collector.py`:
```python
"""Accumulates v2 events + decision records and builds the per-session envelope."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.modules.interview_engine_v2.audit import TurnDecisionRecord
from app.modules.interview_engine_v2.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EventCollector:
    def __init__(
        self,
        *,
        session_id: str,
        tenant_id: str,
        correlation_id: str,
        redaction_mode: str = "metadata",
    ) -> None:
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._redaction_mode = redaction_mode
        self._started_at = _now_iso()
        self._events: list[EventLogEvent] = []

    def record(self, kind: str, payload: dict[str, Any], *, t_ms: int, wall_ms: int) -> None:
        self._events.append(
            EventLogEvent(
                t_ms=t_ms, wall_ms=wall_ms, kind=kind, payload=payload,
                redaction=self._redaction_mode,  # type: ignore[arg-type]
            )
        )

    def record_decision(self, record: TurnDecisionRecord, *, t_ms: int, wall_ms: int) -> None:
        self.record("turn.decision", record.model_dump(mode="json"), t_ms=t_ms, wall_ms=wall_ms)

    def envelope(self, *, closed_at: str | None = None) -> EventLogEnvelope:
        return EventLogEnvelope(
            session_id=self._session_id,
            tenant_id=self._tenant_id,
            correlation_id=self._correlation_id,
            started_at=self._started_at,
            closed_at=closed_at,
            redaction_mode=self._redaction_mode,  # type: ignore[arg-type]
            events=list(self._events),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_event_log.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/event_log/ backend/nexus/tests/interview_engine_v2/test_event_log.py
git commit -m "feat(engine-v2): v2 event-log envelope + collector"
```

---

## Task 11: v2 entrypoint stub + the guarded branch in `agent.py`

**Files:**
- Create: `backend/nexus/app/modules/interview_engine_v2/selection.py` (pure, livekit-free selector)
- Create: `backend/nexus/app/modules/interview_engine_v2/agent.py` (livekit entrypoint)
- Modify: `backend/nexus/app/modules/interview_engine_v2/__init__.py` (export `should_run_v2`)
- Modify: `backend/nexus/app/modules/interview_engine/agent.py` (single guarded branch in `_run_entrypoint`)
- Test: `backend/nexus/tests/interview_engine_v2/test_entrypoint_branch.py`

> This is the one task with a livekit-bearing path. The dispatch *predicate* (`should_run_v2`) lives in a
> livekit-free `selection.py` so it's unit-testable and importable by the legacy entrypoint **through the
> public API** (no cross-module deep import — the boundary lint requires this; `run` itself is the only
> livekit-bearing export and stays lazy). The actual spoken proof-of-life line is verified by **talking
> to it** (the user's preferred method) — see Step 6.

- [ ] **Step 1: Write the failing test (branch dispatch logic)**

`backend/nexus/tests/interview_engine_v2/test_entrypoint_branch.py`:
```python
"""The engine entrypoint routes to v2 iff SessionConfig.interview_engine_version == 'v2'.

Imports the predicate through the package PUBLIC API (not the livekit-bearing agent
module) — the same surface the legacy entrypoint uses.
"""

from app.modules.interview_engine_v2 import should_run_v2


def test_should_run_v2_true_for_v2():
    class _Cfg:
        interview_engine_version = "v2"
    assert should_run_v2(_Cfg()) is True


def test_should_run_v2_false_for_v1():
    class _Cfg:
        interview_engine_version = "v1"
    assert should_run_v2(_Cfg()) is False


def test_should_run_v2_false_when_missing():
    class _Cfg:
        pass  # defensive: a config without the field stays on v1
    assert should_run_v2(_Cfg()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_entrypoint_branch.py -v`
Expected: FAIL — `should_run_v2` is not exported from `interview_engine_v2` (selection.py doesn't exist).

- [ ] **Step 3a: Implement the pure selector**

`backend/nexus/app/modules/interview_engine_v2/selection.py`:
```python
"""Pure engine-version selector (no livekit). Lives apart from agent.py so the
legacy entrypoint can import it via the public API without pulling livekit, and so
the dispatch rule is unit-testable in isolation.
"""

from __future__ import annotations


def should_run_v2(config: object) -> bool:
    """True iff this session should run on the v2 engine. Defensive default: a config
    missing the field stays on v1."""
    return getattr(config, "interview_engine_version", "v1") == "v2"
```

- [ ] **Step 3b: Export it from the package public API**

In `backend/nexus/app/modules/interview_engine_v2/__init__.py`, add the import (with the other eager
pure imports) and the name to `__all__`:
```python
from app.modules.interview_engine_v2.selection import should_run_v2
```
and add `"should_run_v2"` to the `__all__` list (it is a pure export; `run` remains the only lazy one).

- [ ] **Step 3c: Implement the v2 entrypoint**

`backend/nexus/app/modules/interview_engine_v2/agent.py`:
```python
"""Interview Engine v2 — LiveKit entrypoint (M1 proof-of-life).

M1 scope: confirm the v2 route is taken, connect, write the v2 audit envelope, and
speak ONE canned line (proof-of-life via the existing TTS plugin). Turn-taking, the
mouth, and the brain land in M3-M5. This module imports livekit; it is only ever
imported lazily (via interview_engine_v2.__getattr__('run')) inside the engine
container, so the FastAPI/nexus process never loads livekit.
"""

from __future__ import annotations

import time
import uuid

import structlog
from livekit.agents import Agent, AgentSession, JobContext

from app.ai.realtime import build_tts_plugin
from app.modules.interview_engine_v2.event_log.collector import EventCollector
from app.modules.interview_runtime.schemas import SessionConfig

log = structlog.get_logger("interview_engine_v2")


def _now_ms() -> int:
    return int(time.time() * 1000)


async def run(
    ctx: JobContext,
    config: SessionConfig,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """v2 per-session run. M1: proof-of-life only."""
    collector = EventCollector(
        session_id=config.session_id,
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    collector.record(
        "engine.v2.dispatched",
        {"job_title": config.job_title, "question_count": len(config.stage.questions)},
        t_ms=0,
        wall_ms=_now_ms(),
    )
    log.info("engine.v2.boot", job_title=config.job_title,
             question_count=len(config.stage.questions))

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    log.info("engine.v2.participant.joined",
             participant_identity=getattr(participant, "identity", None))

    session = AgentSession(tts=build_tts_plugin())
    await session.start(room=ctx.room, agent=Agent(instructions=""))
    name = config.candidate.name or "there"
    await session.say(
        f"Hi {name}. This is the version two interview engine, just confirming the "
        f"connection works. We'll continue shortly."
    )
    collector.record("engine.v2.proof_of_life_spoken", {}, t_ms=_now_ms(), wall_ms=_now_ms())

    # Dev-only (point 5): publish session_outcome so the candidate screen shows the
    # ended state instead of hanging. Set the attribute directly (no dependency on
    # v1's frontend_attributes.AttributePublisher — keeps v2 self-contained for the
    # M6 v1 deletion). frontend/session reads this attr to render the ended screen.
    try:
        await ctx.room.local_participant.set_attributes({"session_outcome": "completed"})
    except Exception:  # never let a cosmetic attr failure crash the proof-of-life
        log.warning("engine.v2.session_outcome.publish_failed", exc_info=True)

    # NOTE: M1 does NOT call record_session_result (the full SessionResult contract is
    # produced by the brain/coverage path in M5 — see master plan CMI-1). The session
    # row therefore stays 'active'; run against throwaway test jobs only. The envelope
    # is held in memory (sink wiring lands in M6).
    log.info("engine.v2.proof_of_life.complete",
             events=len(collector.envelope().events))
    await session.aclose()
```

> The `session_outcome` value `"completed"` matches the contract `frontend/session` already reads (the
> same attribute the v1 engine publishes via `_publish_session_outcome`). If a quick check of
> `frontend/session` shows it expects a different terminal string (e.g. `"ended"`), use that value — but
> `"completed"` is the v1 convention and the safe default.

- [ ] **Step 4: Add the guarded branch in the legacy entrypoint**

In `backend/nexus/app/modules/interview_engine/agent.py`, in `_run_entrypoint`, immediately AFTER the
`log.info("engine.config.fetched", ...)` block (ends ~line 308) and BEFORE the `await ctx.connect()`
call (~line 321), insert:
```python
    # --- Interview Engine v2 cutover branch (additive; v1 path below unchanged) ---
    # The new engine owns its own connect/turn-loop, so we branch before the legacy
    # ctx.connect(). Selection resolved in build_session_config
    # (job.interview_engine_version or the global default). Default 'v1' => fall through.
    # Imported through the v2 public API: should_run_v2 is pure; `run` resolves lazily
    # (PEP-562 __getattr__) so livekit loads only when v2 actually runs.
    from app.modules.interview_engine_v2 import run as run_v2, should_run_v2

    if should_run_v2(session_config):
        log.info("engine.v2.selected")
        await run_v2(
            ctx,
            session_config,
            tenant_id=tenant_uuid,
            correlation_id=correlation_id,
        )
        return
    # --- end v2 branch ---
```

- [ ] **Step 5: Run the branch-logic test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2/test_entrypoint_branch.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Manual proof-of-life (talk-test) — verification, not a unit test**

The actual LiveKit speak path can't be unit-tested without a live room. Verify by talking to it:
```bash
# 1. flip a throwaway test job (with a confirmed AI-screening bank) to v2:
docker compose run --rm nexus python -c "import asyncio, uuid; \
from app.database import get_bypass_session; from sqlalchemy import text; \
async def go(): \
  async with get_bypass_session() as db: \
    await db.execute(text(\"UPDATE job_postings SET interview_engine_version='v2' WHERE id=:j\"), {'j': '<TEST_JOB_UUID>'}); \
    await db.commit(); \
asyncio.run(go())"
# 2. start the stack + engine, dial the candidate session for that job, and listen.
docker compose up --build
```
Expected: on joining, you hear "Hi <name>. This is the version two interview engine, just confirming the
connection works." and the engine logs `engine.v2.selected` / `engine.v2.boot` /
`engine.v2.proof_of_life.complete`. A v1 job (column NULL) still runs the legacy orchestrator unchanged.
> If `session.say` proves awkward against the installed livekit version, consult the LiveKit docs MCP
> (`docs_search "AgentSession say"`); the acceptance can degrade to "routes to v2 + connects + writes
> envelope" (drop the spoken line) without blocking M1.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_engine_v2/agent.py backend/nexus/app/modules/interview_engine/agent.py backend/nexus/tests/interview_engine_v2/test_entrypoint_branch.py
git commit -m "feat(engine-v2): v2 entrypoint proof-of-life + guarded cutover branch"
```

---

## Task 12: Full-package public API + regression gate

**Files:**
- (no new files) — verify the package public API resolves and v1 stays green.
- Test: `backend/nexus/tests/interview_engine_v2/test_package.py` (already written in Task 1)

- [ ] **Step 1: Verify the public API resolves end-to-end**

Run: `docker compose run --rm nexus python -c "from app.modules.interview_engine_v2 import Directive, DirectiveAct, DirectiveTone, DirectiveController, TurnDecisionRecord; print('pure ok'); from app.modules.interview_engine_v2 import run; print('lazy run ok')"`
Expected: prints `pure ok` then `lazy run ok` (the second line confirms the lazy `__getattr__` export
imports the livekit-bearing `agent.run` only on demand).

- [ ] **Step 2: Run the entire v2 suite**

Run: `docker compose run --rm nexus pytest tests/interview_engine_v2 -v`
Expected: PASS (package, config, directive, no-leak, audit, controller, event_log, entrypoint-branch).

- [ ] **Step 3: Run the v1 engine suite (cutover regression backstop)**

Run (uses the documented coverage-segfault-safe invocation only if measuring coverage; plain pytest is
fine here):
`docker compose run --rm nexus pytest tests/interview_engine tests/interview_runtime -m "not prompt_quality" -q`
Expected: PASS — the legacy engine + runtime are byte-for-byte behaviorally unchanged.

- [ ] **Step 4: Run the module-boundary lint**

Run: `docker compose run --rm nexus pytest tests/test_module_boundaries.py -v`
Expected: PASS — `interview_engine_v2` is a known module; no illegal cross-module deep imports
(the legacy `agent.py` imports v2 via the public API + lazy `__getattr__`).

- [ ] **Step 5: Commit (if any fixups were needed; otherwise skip)**

```bash
git add -A
git commit -m "test(engine-v2): M1 foundations green; v1 regression backstop verified"
```

---

## M1 acceptance checklist (run before declaring M1 done)

- [ ] `pytest tests/interview_engine_v2 -v` — all green.
- [ ] `pytest tests/interview_runtime -v` — all green (version field + resolution).
- [ ] `pytest tests/interview_engine -m "not prompt_quality" -q` — v1 unchanged, green.
- [ ] `pytest tests/test_module_boundaries.py` — green.
- [ ] `alembic upgrade head` / `downgrade -1` / `upgrade head` — clean (migration `0044`).
- [ ] Manual talk-test: a v2-flagged throwaway job routes to v2, connects, speaks the proof-of-life line,
      then publishes `session_outcome=completed` so the candidate screen shows the ended state (not a hang).
- [ ] Manual: a v1 job (column NULL, default version) runs the legacy orchestrator unchanged.
- [ ] No livekit import is pulled into the FastAPI process (Task 12 Step 1 confirms lazy `run`).
- [ ] `git log --oneline` shows one focused commit per task; no unrelated file churn (guard subagent scope).

## Self-review notes
- **Cross-milestone issues (master plan §3a):** CMI-1 (result-contract decoupling) forces **no** M1 code
  change — the M1 stub deliberately skips `record_session_result`; it is M5 Task 1. CMI-2/3/4 are M2/M3/
  M4/M5 concerns. Point 5 (`session_outcome`) is folded into Task 11 as a dev-only courtesy.
- **Spec coverage:** M1 implements DESIGN-SPEC §7 (Directive schema + closed enum + no-leak), §2/§8
  (controller staging/supersession/staleness — the Option C plumbing), §13 (audit record + envelope),
  §10/§11 (model map + cache keys in config), and the §15 cutover seam. Brain/mouth/turn-taking/bank are
  out of M1 scope by design (M2–M5).
- **Type consistency:** `Directive`/`DirectiveAct`/`DirectiveTone` field+method names match across Tasks
  6/7/9/11; `TurnDecisionRecord` fields match Tasks 8/10; `interview_engine_version` is spelled
  identically in Settings, AIConfig, SessionConfig, the ORM column, the migration, and the branch.
- **No placeholders:** every code step contains the actual code; commands have expected output.
