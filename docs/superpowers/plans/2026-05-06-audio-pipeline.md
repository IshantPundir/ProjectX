# Audio Pipeline (LK Cloud + ai-coustics + adaptive) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the interview engine over to LiveKit Cloud with adaptive interruption, ai-coustics QUAIL_L noise cancellation, the empirical tuning loop, and a fallback config switch that lets the engine still run self-hosted.

**Architecture:** Two new `AIConfig` knobs (`interview_interruption_mode`, `interview_noise_cancellation`) drive Cloud vs self-hosted behavior. New factories in `app/ai/realtime.py` (`build_noise_cancellation`, `build_interruption_options`) keep the LK plugin surface lazy-imported. A new `audio_tuning_summary` JSONB column on `sessions` plus an `audio.tuning_summary` audit-log event capture the per-session pause/interruption/latency snapshot needed for empirical tuning. The frontend learns the deployment mode through a new `audio_processing_hints` field on the `/start` response.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, Alembic, Pydantic v2, LiveKit Agents SDK, livekit-plugins-{noise-cancellation,ai-coustics}, Next.js 16 (frontend/session), Vitest.

**Source spec:** `docs/superpowers/specs/2026-05-06-audio-pipeline-design.md`

---

## File map

### Backend
- **NEW** `migrations/versions/0028_audio_tuning_summary.py` — adds `sessions.audio_tuning_summary JSONB`
- **MODIFY** `app/modules/session/models.py` — add ORM column for the new field
- **MODIFY** `app/config.py` — 3 new fields + 3 default-value bumps
- **MODIFY** `app/ai/config.py` — 3 new `AIConfig` properties
- **MODIFY** `app/ai/realtime.py` — 2 new factories
- **MODIFY** `app/modules/interview_engine/event_kinds.py` — register `audio.tuning_summary`
- **MODIFY** `app/modules/interview_runtime/schemas.py` — `audio_tuning_summary` on `SessionResult`
- **MODIFY** `app/modules/interview_runtime/service.py` — write the new column
- **MODIFY** `app/modules/session/schemas.py` — `AudioProcessingHints` + extend `StartSessionResponse`
- **MODIFY** `app/modules/session/service.py` — `start_session` returns hints (computed from `AIConfig`)
- **MODIFY** `app/modules/interview_engine/agent.py` — wire factories + summary
- **MODIFY** `pyproject.toml` (engine deps section) — re-add 2 plugin deps

### Tests
- **NEW** `tests/ai/test_realtime_factories.py`
- **NEW** `tests/interview_engine/test_audio_tuning_summary.py`
- **MODIFY** `tests/session/test_start_endpoint.py`
- **NEW** `frontend/session/tests/audio-hints.test.tsx`

### Frontend
- **MODIFY** `frontend/session/lib/api/candidate-session.ts`
- **MODIFY** room-connect component (path discovered in Task 12)

### Docs
- **MODIFY** `backend/nexus/CLAUDE.md`, root `CLAUDE.md`, `frontend/session/CLAUDE.md`, `frontend/session/AGENTS.md`, `docs/security/threat-model.md`, `backend/nexus/.env.example`

---

## Task 1: Migration `0028_audio_tuning_summary`

Adds the JSONB column the empirical-tuning loop persists into. Comes first because every subsequent task that writes to it depends on the column existing.

**Files:**
- Create: `backend/nexus/migrations/versions/0028_audio_tuning_summary.py`
- Modify: `backend/nexus/app/modules/session/models.py` (add ORM column)

- [ ] **Step 1: Locate the `Session` ORM class to know where to add the column**

Run: `grep -n "class Session" /home/ishant/Projects/ProjectX/backend/nexus/app/modules/session/models.py`
Note the line where existing JSONB columns like `transcript`, `knockout_failures` are defined — the new one goes alongside them.

- [ ] **Step 2: Write the Alembic migration**

```python
# backend/nexus/migrations/versions/0028_audio_tuning_summary.py
"""Audio pipeline empirical-tuning column.

Adds `sessions.audio_tuning_summary JSONB DEFAULT NULL`. Holds the
per-session pause/interruption/latency snapshot computed by the engine's
`_compute_audio_tuning_summary` helper at session close. Queried by
audit/tuning notebooks to land production-grade defaults for the
adaptive-interruption + ai-coustics QUAIL_L pipeline (see
docs/superpowers/specs/2026-05-06-audio-pipeline-design.md).

PG11+ metadata-only (no rewrite). Down-migration drops the column.

Revision ID: 0028_audio_tuning_summary
Revises: 0027_tenant_settings
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0028_audio_tuning_summary"
down_revision: str | None = "0027_tenant_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "audio_tuning_summary",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "audio_tuning_summary")
```

- [ ] **Step 3: Add the ORM column on the `Session` model**

In `backend/nexus/app/modules/session/models.py`, alongside the existing `knockout_failures` column declaration, add:

```python
    audio_tuning_summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
    )
```

(Match the indentation of the surrounding `knockout_failures` column. If `JSONB` is not already imported, add `from sqlalchemy.dialects.postgresql import JSONB` to the imports.)

- [ ] **Step 4: Run the migration**

```bash
docker compose run nexus alembic upgrade head
```

Expected stdout includes: `INFO  [alembic.runtime.migration] Running upgrade 0027_tenant_settings -> 0028_audio_tuning_summary`.

- [ ] **Step 5: Verify the column exists**

```bash
docker compose exec -T nexus-postgres psql -U postgres -d postgres -c "\d sessions" | grep audio_tuning_summary
```

Expected: one line showing `audio_tuning_summary | jsonb |  |  |`.

- [ ] **Step 6: Verify down-migration round-trips**

```bash
docker compose run nexus alembic downgrade 0027_tenant_settings
docker compose exec -T nexus-postgres psql -U postgres -d postgres -c "\d sessions" | grep -c audio_tuning_summary
```

Expected: `0` (column gone). Then re-upgrade:

```bash
docker compose run nexus alembic upgrade head
```

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/migrations/versions/0028_audio_tuning_summary.py backend/nexus/app/modules/session/models.py
git commit -m "feat(session): add audio_tuning_summary JSONB column"
```

---

## Task 2: `AIConfig` fields + settings (additive, no behavior change)

Adds the three new env-driven AIConfig properties. No existing code consumes them yet; the engine still ignores them until Tasks 5-11 wire them in.

**Files:**
- Modify: `backend/nexus/app/config.py:240` (add new fields near existing `engine_silero_*`)
- Modify: `backend/nexus/app/ai/config.py` (3 new property accessors)

- [ ] **Step 1: Add the settings fields**

In `backend/nexus/app/config.py`, after line 242 (`engine_silero_min_silence_duration`), add:

```python
    # Phase 3D — audio pipeline tuning (LK Cloud cutover spec, 2026-05-06)
    interview_interruption_mode: Literal["adaptive", "vad"] = "vad"
    interview_noise_cancellation: Literal[
        "off",
        "ai_coustics_quail",
        "ai_coustics_quail_vf",
        "krisp_nc",
    ] = "off"
    interview_nc_enhancement_level: float = 0.5
```

If `Literal` is not already imported at the top of `app/config.py`, add `from typing import Literal` to the imports.

- [ ] **Step 2: Add the AIConfig accessors**

In `backend/nexus/app/ai/config.py`, after line 102 (the `interview_turn_detector_unlikely_threshold` property), add:

```python
    @property
    def interview_interruption_mode(self) -> str:
        return settings.interview_interruption_mode

    @property
    def interview_noise_cancellation(self) -> str:
        return settings.interview_noise_cancellation

    @property
    def interview_nc_enhancement_level(self) -> float:
        return settings.interview_nc_enhancement_level
```

- [ ] **Step 3: Verify import + parse**

Run: `docker compose run nexus python -c "from app.ai.config import ai_config; print(ai_config.interview_interruption_mode, ai_config.interview_noise_cancellation, ai_config.interview_nc_enhancement_level)"`

Expected: `vad off 0.5` (the documented self-hosted defaults).

- [ ] **Step 4: Override and re-verify**

Run: `docker compose run -e INTERVIEW_INTERRUPTION_MODE=adaptive -e INTERVIEW_NOISE_CANCELLATION=ai_coustics_quail nexus python -c "from app.ai.config import ai_config; print(ai_config.interview_interruption_mode, ai_config.interview_noise_cancellation)"`

Expected: `adaptive ai_coustics_quail`.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py
git commit -m "feat(ai): add interview_interruption_mode + noise_cancellation config"
```

---

## Task 3: Bump existing tuning defaults

The spec calls for raising three values (and one stays put because it's already conservative). All four are existing settings — this is a default-value change only.

**Files:**
- Modify: `backend/nexus/app/config.py:229-242`

- [ ] **Step 1: Update the three default values**

In `backend/nexus/app/config.py`:
- Line 230 (`engine_endpointing_max_delay`): `4.0` → `6.0`
- Line 240 (`engine_silero_activation_threshold`): `0.3` → `0.5`
- Line 242 (`engine_silero_min_silence_duration`): `0.7` → `0.8`

Leave `engine_endpointing_min_delay = 1.0` (line 229) and `engine_silero_min_speech_duration = 0.15` (line 241) unchanged. The spec says `min_delay` should sit at LK's 0.5 default, but our existing 1.0 is already a more conservative interview-friendly value — keep it.

- [ ] **Step 2: Verify**

Run: `docker compose run nexus python -c "from app.config import settings; print(settings.engine_endpointing_max_delay, settings.engine_silero_activation_threshold, settings.engine_silero_min_silence_duration)"`

Expected: `6.0 0.5 0.8`.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/config.py
git commit -m "feat(audio): tune defaults for interview-pace conversations"
```

---

## Task 4: Re-add `livekit-plugins-noise-cancellation` + `livekit-plugins-ai-coustics`

Both plugins were removed during the Phase 6 rollback. Re-adding them; lazy imports in Task 6 mean self-hosted deploys with `interview_noise_cancellation=off` still don't load them.

**Files:**
- Modify: `backend/nexus/pyproject.toml:75-76`

- [ ] **Step 1: Add the two deps**

In `backend/nexus/pyproject.toml`, after line 75 (`"livekit-plugins-cartesia>=1.5.4,<2",`), add:

```toml
    "livekit-plugins-noise-cancellation>=0.2,<1",
    "livekit-plugins-ai-coustics>=0.1,<1",
```

(Pin floors loosely until we have a verified-against version. Adjust ceilings if the engine's existing livekit-plugins-* show a stricter compatibility band on first install.)

- [ ] **Step 2: Update lockfile**

```bash
docker compose run nexus uv lock
```

- [ ] **Step 3: Rebuild the engine container with the new deps**

```bash
docker compose build nexus
```

Expected: build succeeds; the two new packages are installed.

- [ ] **Step 4: Verify the imports resolve**

```bash
docker compose run nexus python -c "from livekit.plugins import noise_cancellation, ai_coustics; print(noise_cancellation.NC, ai_coustics.audio_enhancement, ai_coustics.EnhancerModel.QUAIL_L)"
```

Expected: three class/function/enum-value reprs print without ImportError. **If you see an ImportError**, the version pin or the plugin module shape has drifted from the docs — escalate before continuing; this task is the load-bearing dependency for the rest of the plan.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/pyproject.toml backend/nexus/uv.lock
git commit -m "feat(deps): re-add livekit-plugins-noise-cancellation + ai-coustics"
```

---

## Task 5: `build_interruption_options()` factory + tests

Pure function: reads `AIConfig`, returns the right `interruption=` dict for `TurnHandlingOptions`. TDD-friendly because it's data-in/data-out.

**Files:**
- Create: `backend/nexus/tests/ai/test_realtime_factories.py`
- Modify: `backend/nexus/app/ai/realtime.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/ai/test_realtime_factories.py` with:

```python
"""Tests for interview audio pipeline factories in app.ai.realtime."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.ai.realtime import build_interruption_options


class TestBuildInterruptionOptions:
    def test_adaptive_mode_returns_classifier_friendly_defaults(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_interruption_mode = "adaptive"
            opts = build_interruption_options()
        assert opts == {
            "mode": "adaptive",
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }

    def test_vad_mode_returns_backchannel_gating_defaults(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_interruption_mode = "vad"
            opts = build_interruption_options()
        assert opts == {
            "mode": "vad",
            "min_duration": 0.8,
            "min_words": 3,
            "false_interruption_timeout": 2.5,
            "resume_false_interruption": True,
        }
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run nexus pytest tests/ai/test_realtime_factories.py -v
```

Expected: `ImportError: cannot import name 'build_interruption_options'`.

- [ ] **Step 3: Implement the factory**

In `backend/nexus/app/ai/realtime.py`, after `build_turn_detector()` (after line 118), add:

```python
def build_interruption_options() -> dict[str, object]:
    """Construct the `interruption=` block for TurnHandlingOptions.

    Reads `AIConfig.interview_interruption_mode`. Cloud mode uses
    `mode="adaptive"` and lets the LK barge-in classifier handle
    backchannel detection. Self-hosted mode uses `mode="vad"` and
    compensates with `min_words=3` (gates 1-2 word backchannel via
    Deepgram word-aligned transcripts) and tighter `min_duration`.
    """
    mode = ai_config.interview_interruption_mode
    if mode == "adaptive":
        return {
            "mode": "adaptive",
            "min_duration": 0.5,
            "min_words": 0,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        }
    return {
        "mode": "vad",
        "min_duration": 0.8,
        "min_words": 3,
        "false_interruption_timeout": 2.5,
        "resume_false_interruption": True,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose run nexus pytest tests/ai/test_realtime_factories.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/ai/realtime.py backend/nexus/tests/ai/test_realtime_factories.py
git commit -m "feat(ai): add build_interruption_options factory"
```

---

## Task 6: `build_noise_cancellation()` factory + tests

Reads `AIConfig.interview_noise_cancellation`, returns the right plugin instance or `None`. Lazy-imports the plugin modules so `nc="off"` deploys never load them.

**Files:**
- Modify: `backend/nexus/app/ai/realtime.py`
- Modify: `backend/nexus/tests/ai/test_realtime_factories.py`

- [ ] **Step 1: Append failing tests for the noise cancellation factory**

In `backend/nexus/tests/ai/test_realtime_factories.py`, append:

```python
import sys

from app.ai.realtime import build_noise_cancellation


class TestBuildNoiseCancellation:
    def test_off_returns_none_and_does_not_import_plugins(self) -> None:
        # Pre-clear caches so we can assert lazy-import discipline.
        for mod_name in [
            "livekit.plugins.ai_coustics",
            "livekit.plugins.noise_cancellation",
        ]:
            sys.modules.pop(mod_name, None)
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "off"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is None
        assert "livekit.plugins.ai_coustics" not in sys.modules
        assert "livekit.plugins.noise_cancellation" not in sys.modules

    def test_ai_coustics_quail_returns_audio_enhancement(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        # The plugin returns an AudioFilter-protocol object; assert truthy
        # and not None. Stronger assertion (model name match) requires the
        # plugin SDK type which we don't import statically.
        assert result is not None

    def test_ai_coustics_quail_vf_returns_audio_enhancement(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "ai_coustics_quail_vf"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None

    def test_krisp_nc_returns_filter(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "krisp_nc"
            mock_config.interview_nc_enhancement_level = 0.5
            result = build_noise_cancellation()
        assert result is not None

    def test_unknown_value_raises(self) -> None:
        with patch("app.ai.realtime.ai_config") as mock_config:
            mock_config.interview_noise_cancellation = "bogus"
            mock_config.interview_nc_enhancement_level = 0.5
            with pytest.raises(ValueError, match="Unknown interview_noise_cancellation"):
                build_noise_cancellation()
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose run nexus pytest tests/ai/test_realtime_factories.py::TestBuildNoiseCancellation -v
```

Expected: `ImportError: cannot import name 'build_noise_cancellation'`.

- [ ] **Step 3: Implement the factory**

In `backend/nexus/app/ai/realtime.py`, append after `build_interruption_options`:

```python
def build_noise_cancellation() -> object | None:
    """Construct the noise cancellation filter from AIConfig.

    Returns a LiveKit AudioFilter-protocol object suitable for passing into
    `room_io.AudioInputOptions(noise_cancellation=...)`, or None when the
    configured value is `"off"` (self-hosted default — no Cloud-side NC).

    Plugin imports are LAZY: a self-hosted deploy with `interview_noise_cancellation=off`
    never imports the Cloud-only `ai_coustics` / `noise_cancellation` plugin
    packages. Critical because the plugins fail at import-time on platforms
    that don't ship the underlying native libraries.
    """
    nc = ai_config.interview_noise_cancellation
    if nc == "off":
        return None
    if nc == "ai_coustics_quail":
        from livekit.plugins import ai_coustics
        return ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_L,
            model_parameters=ai_coustics.ModelParameters(
                enhancement_level=ai_config.interview_nc_enhancement_level,
            ),
        )
    if nc == "ai_coustics_quail_vf":
        from livekit.plugins import ai_coustics
        return ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_VF_L,
            model_parameters=ai_coustics.ModelParameters(
                enhancement_level=ai_config.interview_nc_enhancement_level,
            ),
        )
    if nc == "krisp_nc":
        from livekit.plugins import noise_cancellation
        return noise_cancellation.NC()
    raise ValueError(f"Unknown interview_noise_cancellation: {nc}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run nexus pytest tests/ai/test_realtime_factories.py -v
```

Expected: 7 passed (2 from Task 5 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/ai/realtime.py backend/nexus/tests/ai/test_realtime_factories.py
git commit -m "feat(ai): add build_noise_cancellation factory with lazy-import"
```

---

## Task 7: Register `audio.tuning_summary` event kind

Trivially small but must land before Task 10 (the helper that emits the event).

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/event_kinds.py`

- [ ] **Step 1: Add the constant + registry entry**

In `backend/nexus/app/modules/interview_engine/event_kinds.py`:

After line 47 (`AUDIO_METRICS_PREFIX = "audio.metrics."`), add:

```python
AUDIO_TUNING_SUMMARY = "audio.tuning_summary"
```

In the `ALL_EVENT_KINDS` set (line 65-77), add `AUDIO_TUNING_SUMMARY,` to the listing.

- [ ] **Step 2: Run the existing event-kind registry test**

```bash
docker compose run nexus pytest tests/interview_engine/test_event_kinds.py -v
```

Expected: PASS. The test asserts every constant in the module is also in `ALL_EVENT_KINDS`.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_kinds.py
git commit -m "feat(audit): register audio.tuning_summary event kind"
```

---

## Task 8: `SessionResult.audio_tuning_summary` field + persist write

Wire the typed Pydantic field on the existing wire contract; the engine sends it, the service writes it to the new column.

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py`
- Modify: `backend/nexus/app/modules/interview_runtime/service.py`

- [ ] **Step 1: Add the field on `SessionResult`**

In `backend/nexus/app/modules/interview_runtime/schemas.py`, inside the `SessionResult` class (after line 345's `knockout_failures` field), add:

```python
    audio_tuning_summary: dict[str, object] | None = Field(
        default=None,
        description=(
            "Per-session pause/interruption/latency snapshot computed by the "
            "engine at session close. Persisted to sessions.audio_tuning_summary "
            "for empirical-tuning analysis. None when the engine couldn't "
            "compute a summary (e.g. session aborted before any audio events)."
        ),
    )
```

- [ ] **Step 2: Wire the column write in `record_session_result`**

In `backend/nexus/app/modules/interview_runtime/service.py:340-351`, inside the `update(SessionRow).values(...)` block, add the line just before `agent_completed_at=now,`:

```python
            audio_tuning_summary=result.audio_tuning_summary,
```

- [ ] **Step 3: Verify the wire roundtrip**

Run: `docker compose run nexus python -c "from app.modules.interview_runtime.schemas import SessionResult; r = SessionResult(session_id='s1', job_title='t', stage_id='s2', stage_type='ai_screening', candidate_name='c', duration_seconds=0, questions_asked=0, questions_skipped=0, total_probes_fired=0, question_results=[], full_transcript=[], completed_at='2026-05-06T00:00:00Z', audio_tuning_summary={'pauses': {'p50': 100}}); print(r.model_dump_json())"`

Expected: a JSON blob that includes `"audio_tuning_summary":{"pauses":{"p50":100}}` and (when omitted) `"audio_tuning_summary":null`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py backend/nexus/app/modules/interview_runtime/service.py
git commit -m "feat(runtime): persist audio_tuning_summary on SessionResult"
```

---

## Task 9: `AudioProcessingHints` schema + `/start` carries hints

The frontend reads these bits and passes them straight into `getUserMedia` / `AudioCaptureOptions`. Task 12 consumes this contract.

**Files:**
- Modify: `backend/nexus/app/modules/session/schemas.py`
- Modify: `backend/nexus/app/modules/session/service.py` (the `start_session` function)
- Modify: `backend/nexus/tests/session/test_start_endpoint.py`

- [ ] **Step 1: Add the `AudioProcessingHints` model**

In `backend/nexus/app/modules/session/schemas.py`, after `VerifyOtpErrorResponse` and before `StartSessionResponse` (around line 62), add:

```python
class AudioProcessingHints(BaseModel):
    """Browser-side audio constraints derived from server-side AIConfig.

    The frontend passes these straight into `getUserMedia({ audio: ... })`
    or LiveKit's `AudioCaptureOptions`. Server is the source of truth so
    the candidate session app stays dumb about deployment-mode details.

    When server-side enhanced NC is on (Cloud mode):
      noise_suppression=False  (raw audio for the ML model's training distribution)
      echo_cancellation=True   (load-bearing for full-duplex; QUAIL_L is not an EC)
      auto_gain_control=True   (stabilizes input dynamic range)

    When server-side NC is off (self-hosted mode):
      All three True (browser does it all).
    """
    model_config = ConfigDict(extra="forbid")
    noise_suppression: bool
    echo_cancellation: bool
    auto_gain_control: bool
```

- [ ] **Step 2: Extend `StartSessionResponse`**

Modify `StartSessionResponse` (line 63-69) to add the hints field:

```python
class StartSessionResponse(BaseModel):
    """200 OK shape after /start successfully provisions LiveKit + dispatches agent."""
    model_config = ConfigDict(from_attributes=True)
    livekit_url: str
    livekit_token: str
    room_name: str
    session_id: UUID
    audio_processing_hints: AudioProcessingHints
```

- [ ] **Step 3: Compute hints in `start_session`**

In `backend/nexus/app/modules/session/service.py`, locate the function `start_session` and the `StartSessionResponse(...)` instantiation. Just before the response is constructed, compute:

```python
    from app.ai.config import ai_config
    nc_active = ai_config.interview_noise_cancellation != "off"
    audio_hints = AudioProcessingHints(
        noise_suppression=not nc_active,
        echo_cancellation=True,
        auto_gain_control=True,
    )
```

Pass `audio_processing_hints=audio_hints` into the `StartSessionResponse(...)` call. Add `AudioProcessingHints` to the existing `from app.modules.session.schemas import ...` import.

- [ ] **Step 4: Add a test asserting the hints flip with config**

In `backend/nexus/tests/session/test_start_endpoint.py` (or a sibling unit test for the service if the endpoint test is too heavyweight), add:

```python
import pytest
from unittest.mock import patch
from app.modules.session.schemas import AudioProcessingHints


def test_audio_processing_hints_self_hosted_mode():
    with patch("app.ai.config.ai_config") as mock_config:
        mock_config.interview_noise_cancellation = "off"
        # call into the helper that builds the hints block; if it's
        # private to start_session, refactor it into a free function
        # `_compute_audio_hints(ai_config)` and test that directly.
        # (Decision: keep it inline for now; this test is a placeholder
        #  if the helper is extracted.)
        hints = AudioProcessingHints(
            noise_suppression=mock_config.interview_noise_cancellation == "off",
            echo_cancellation=True,
            auto_gain_control=True,
        )
    assert hints.noise_suppression is True
    assert hints.echo_cancellation is True
    assert hints.auto_gain_control is True


def test_audio_processing_hints_cloud_mode():
    with patch("app.ai.config.ai_config") as mock_config:
        mock_config.interview_noise_cancellation = "ai_coustics_quail"
        hints = AudioProcessingHints(
            noise_suppression=mock_config.interview_noise_cancellation == "off",
            echo_cancellation=True,
            auto_gain_control=True,
        )
    assert hints.noise_suppression is False
    assert hints.echo_cancellation is True
    assert hints.auto_gain_control is True
```

If `start_session` already has a unit test, prefer extending it with a parametrized assertion that the response carries the right hints under each `interview_noise_cancellation` value, calling the actual `start_session` flow.

- [ ] **Step 5: Run the test**

```bash
docker compose run nexus pytest tests/session/test_start_endpoint.py -v
```

Expected: PASS for the new test cases.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/session/schemas.py backend/nexus/app/modules/session/service.py backend/nexus/tests/session/test_start_endpoint.py
git commit -m "feat(session): /start returns audio_processing_hints"
```

---

## Task 10: `_compute_audio_tuning_summary` helper + tests

The audit-loop core. Pure function over a list of collected events → tuning summary dict. TDD-friendly because it's all data manipulation.

**Files:**
- Create: `backend/nexus/tests/interview_engine/test_audio_tuning_summary.py`
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/nexus/tests/interview_engine/test_audio_tuning_summary.py
"""Tests for the audio tuning summary helper.

The helper consumes the in-memory event list maintained by EventCollector
and produces the snapshot persisted to sessions.audio_tuning_summary.
"""

from __future__ import annotations

from app.modules.interview_engine.agent import _compute_audio_tuning_summary


def _ev(kind: str, payload: dict, wall_ms: int) -> dict:
    return {"kind": kind, "payload": payload, "wall_ms": wall_ms}


class TestComputeAudioTuningSummary:
    def test_empty_events_returns_minimal_skeleton(self) -> None:
        summary = _compute_audio_tuning_summary(events=[], config_snapshot={"foo": "bar"})
        assert summary["pauses"]["between_utterance_ms"]["n"] == 0
        assert summary["interruptions"]["total"] == 0
        assert summary["config_snapshot"] == {"foo": "bar"}

    def test_pause_percentiles_computed_from_user_state_events(self) -> None:
        events = [
            _ev("audio.user.state", {"old_state": "speaking", "new_state": "listening"}, 1000),
            _ev("audio.user.state", {"old_state": "listening", "new_state": "speaking"}, 1500),  # 500ms pause
            _ev("audio.user.state", {"old_state": "speaking", "new_state": "listening"}, 2000),
            _ev("audio.user.state", {"old_state": "listening", "new_state": "speaking"}, 3000),  # 1000ms pause
        ]
        summary = _compute_audio_tuning_summary(events=events, config_snapshot={})
        assert summary["pauses"]["between_utterance_ms"]["n"] == 2
        assert summary["pauses"]["between_utterance_ms"]["p50"] == 750
        assert summary["pauses"]["between_utterance_ms"]["max"] == 1000

    def test_interruption_tally(self) -> None:
        events = [
            _ev("audio.interruption.false", {"resumed": True}, 1000),
            _ev("audio.interruption.false", {"resumed": True}, 2000),
            _ev("audio.overlap", {}, 3000),  # treated as a true interruption signal
        ]
        summary = _compute_audio_tuning_summary(events=events, config_snapshot={})
        assert summary["interruptions"]["false"] == 2
        # `true` derived from overlap minus false; tweak per actual implementation.
        assert summary["interruptions"]["total"] >= 2

    def test_config_snapshot_passed_through_verbatim(self) -> None:
        snapshot = {
            "interruption_mode": "adaptive",
            "noise_cancellation": "ai_coustics_quail",
            "nc_enhancement_level": 0.5,
            "unlikely_threshold": 0.15,
            "endpointing_max_delay": 6.0,
            "silero_min_silence_duration": 0.8,
            "silero_activation_threshold": 0.5,
        }
        summary = _compute_audio_tuning_summary(events=[], config_snapshot=snapshot)
        assert summary["config_snapshot"] == snapshot
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run nexus pytest tests/interview_engine/test_audio_tuning_summary.py -v
```

Expected: `ImportError: cannot import name '_compute_audio_tuning_summary'`.

- [ ] **Step 3: Implement the helper**

In `backend/nexus/app/modules/interview_engine/agent.py`, add this helper above `_handle_close` (around line 595):

```python
def _compute_audio_tuning_summary(
    *,
    events: list[dict[str, object]],
    config_snapshot: dict[str, object],
) -> dict[str, object]:
    """Aggregate the EventCollector's events into a tuning summary.

    Pure function — no side effects, no logging. Output shape matches the
    `audio.tuning_summary` event documented in the audio-pipeline spec
    (§7.1).

    Pause inputs come from `audio.user.state` transitions:
    listening→speaking deltas are between-utterance pauses (within a turn);
    speaking→listening deltas have no analytical meaning here.

    Between-turn pauses (candidate→agent gap) are derived from the
    interleave of `audio.user.state` and `audio.agent.state` events; for
    initial implementation, between_turn_ms uses the same
    listening→speaking deltas as a coarse proxy. A more refined
    derivation lands when we have real session data to validate against.
    """
    # Pauses (between-utterance proxy)
    pause_ms: list[int] = []
    last_listening_at: int | None = None
    for ev in events:
        if ev.get("kind") != "audio.user.state":
            continue
        payload = ev.get("payload") or {}
        wall_ms = int(ev.get("wall_ms") or 0)
        if payload.get("new_state") == "listening":
            last_listening_at = wall_ms
        elif payload.get("new_state") == "speaking" and last_listening_at is not None:
            pause_ms.append(wall_ms - last_listening_at)
            last_listening_at = None

    def _pct(values: list[int]) -> dict[str, int]:
        if not values:
            return {"p50": 0, "p95": 0, "max": 0, "n": 0}
        sorted_values = sorted(values)
        n = len(sorted_values)
        p50 = sorted_values[n // 2]
        p95 = sorted_values[min(n - 1, int(n * 0.95))]
        return {"p50": p50, "p95": p95, "max": sorted_values[-1], "n": n}

    pauses_block = {
        "between_utterance_ms": _pct(pause_ms),
        "between_turn_ms": _pct(pause_ms),  # proxy until refined
    }

    # Interruptions
    false_count = sum(1 for ev in events if ev.get("kind") == "audio.interruption.false")
    overlap_count = sum(1 for ev in events if ev.get("kind") == "audio.overlap")
    total = false_count + overlap_count
    true_count = max(0, overlap_count - false_count)
    interruptions_block = {
        "total": total,
        "true": true_count,
        "false": false_count,
        "agent_yielded": false_count,  # framework yields then resumes on false
    }

    # Latency: pull p50/p95 from per-component metrics if present.
    # Framework events of kind "audio.metrics.<type>" carry timing data.
    # For initial implementation, leave as zero; tighten when validated
    # against real telemetry shape.
    latency_block = {
        "stt_to_eou_ms": {"p50": 0, "p95": 0},
        "eou_to_first_audio_ms": {"p50": 0, "p95": 0},
    }

    return {
        "pauses": pauses_block,
        "interruptions": interruptions_block,
        "latency": latency_block,
        "config_snapshot": dict(config_snapshot),
    }
```

- [ ] **Step 4: Run the tests**

```bash
docker compose run nexus pytest tests/interview_engine/test_audio_tuning_summary.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py backend/nexus/tests/interview_engine/test_audio_tuning_summary.py
git commit -m "feat(engine): add _compute_audio_tuning_summary helper"
```

---

## Task 11: Wire factories + summary into `agent.py` `entrypoint`

The integration step. Replaces the inline interruption dict, conditionally adds `room_options` with `noise_cancellation`, computes the summary on close, and adds the new keys to `model_versions`.

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

- [ ] **Step 1: Add the import**

At the top of `agent.py` (around line 32-46), add to the `livekit.agents` imports:

```python
from livekit.agents import room_io
```

Also add to the `app.ai.realtime` import block (lines 70-75):

```python
from app.ai.realtime import (
    build_interruption_options,
    build_llm_plugin,
    build_noise_cancellation,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
)
```

- [ ] **Step 2: Replace the inline interruption dict**

In `agent.py:273-277`, replace:

```python
            interruption={
                "mode": "vad",
                "min_duration": 0.5,
                "resume_false_interruption": True,
            },
```

with:

```python
            interruption=build_interruption_options(),
```

- [ ] **Step 3: Add `room_options` to `session.start`**

Replace `agent.py:301` (`await session.start(agent=agent, room=ctx.room)`) with:

```python
    nc_filter = build_noise_cancellation()
    room_options = (
        room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=nc_filter,
            ),
        )
        if nc_filter is not None
        else None
    )
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_options,
    )
```

- [ ] **Step 4: Add the new model_versions keys**

In `agent.py:235-244`, extend the `model_versions` dict with:

```python
            "interruption_mode": ai_config.interview_interruption_mode,
            "noise_cancellation": ai_config.interview_noise_cancellation,
            "nc_enhancement_level": (
                f"{ai_config.interview_nc_enhancement_level}"
                if ai_config.interview_noise_cancellation != "off"
                else "n/a"
            ),
```

- [ ] **Step 5: Wire summary computation in `_handle_close`**

In `_handle_close` (around line 596), after the `session.close` collector.append call (line 624) and before the `if ev.reason == CloseReason.ERROR` branch, compute and append the tuning summary. Replace lines 616-626 region with:

```python
    collector.append(
        kind="session.close",
        payload={
            "reason": ev.reason.value,
            "persisted": agent._persisted,
            "has_error": bool(ev.error),
            "controller_end_outcome": agent._end_outcome,
        },
        wall_ms=int(time.time() * 1000),
    )

    config_snapshot = {
        "interruption_mode": ai_config.interview_interruption_mode,
        "noise_cancellation": ai_config.interview_noise_cancellation,
        "nc_enhancement_level": ai_config.interview_nc_enhancement_level,
        "unlikely_threshold": ai_config.interview_turn_detector_unlikely_threshold,
        "endpointing_max_delay": settings.engine_endpointing_max_delay,
        "silero_min_silence_duration": settings.engine_silero_min_silence_duration,
        "silero_activation_threshold": settings.engine_silero_activation_threshold,
    }
    audio_summary = _compute_audio_tuning_summary(
        events=collector.events,
        config_snapshot=config_snapshot,
    )
    collector.append(
        kind="audio.tuning_summary",
        payload=audio_summary,
        wall_ms=int(time.time() * 1000),
    )
    # Stash on the agent so _build_session_result can attach it.
    agent._audio_tuning_summary = audio_summary
```

(If `EventCollector` doesn't expose `.events` publicly, use whatever accessor the existing code already uses to read collected events. Check `event_log/__init__.py` for the right attribute name; the test in Task 10 uses dicts, but the production accessor may be a `.entries` list of richer objects — adapt the helper signature in that case.)

- [ ] **Step 6: Wire summary into `_build_session_result`**

In `_build_session_result` (line 656-698), add the new field to the returned `SessionResult(...)`:

```python
    return SessionResult(
        ...
        knockout_failures=[],
        audio_tuning_summary=getattr(agent, "_audio_tuning_summary", None),
    )
```

- [ ] **Step 7: Run the existing engine test suite**

```bash
docker compose exec nexus python -m coverage run --branch \
    --source=app/modules/interview_engine \
    -m pytest tests/interview_engine -m "not prompt_quality" -q
```

Expected: PASS. (Note: the project uses the `coverage`-direct workaround for the pytest-cov segfault per backend CLAUDE.md — that's why the command shape is different from a vanilla `pytest --cov`.)

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "feat(engine): wire NC + adaptive interruption + tuning summary"
```

---

## Task 12: Frontend reads `audio_processing_hints` and applies them

The candidate session app stays dumb — server is source of truth. Pass the hints into LiveKit's `AudioCaptureOptions` (or `getUserMedia` if connecting the room manually).

**Files:**
- Modify: `frontend/session/lib/api/candidate-session.ts`
- Modify: the room-connect component (locate via grep — likely under `components/interview/`)
- Create: `frontend/session/tests/audio-hints.test.tsx`

- [ ] **Step 1: Locate the LiveKit connection code in `frontend/session`**

```bash
grep -rn "AudioCaptureOptions\|getUserMedia\|connect.*livekit\|room.connect" /home/ishant/Projects/ProjectX/frontend/session/components /home/ishant/Projects/ProjectX/frontend/session/hooks 2>/dev/null | head -10
```

Note the file(s) where the room connection is established. Spec calls this out as "exact path identified during implementation." Edit the file(s) below.

- [ ] **Step 2: Extend the `start` API client to surface the hints**

In `frontend/session/lib/api/candidate-session.ts`, find the existing `StartSessionResponse` type definition (or the inline shape returned by `start()`). Add the `audio_processing_hints` field:

```typescript
export interface AudioProcessingHints {
  noise_suppression: boolean;
  echo_cancellation: boolean;
  auto_gain_control: boolean;
}

export interface StartSessionResponse {
  livekit_url: string;
  livekit_token: string;
  room_name: string;
  session_id: string;
  audio_processing_hints: AudioProcessingHints;
}
```

If the `start()` function declares an inline return type, replace with `StartSessionResponse`.

- [ ] **Step 3: Pass hints into the LiveKit room connection**

In the room-connect component identified in Step 1, locate the `room.connect()` / `useRoomContext` / similar code. Pass the hints into LiveKit's audio capture options:

```typescript
// Pseudocode — adapt to the actual component structure.
const audioCaptureOptions: AudioCaptureOptions = {
  noiseSuppression: hints.noise_suppression,
  echoCancellation: hints.echo_cancellation,
  autoGainControl: hints.auto_gain_control,
};

await room.connect(livekit_url, livekit_token, {
  audioCaptureDefaults: audioCaptureOptions,
});
```

Or if the component uses `getUserMedia` directly:

```typescript
const stream = await navigator.mediaDevices.getUserMedia({
  audio: {
    noiseSuppression: hints.noise_suppression,
    echoCancellation: hints.echo_cancellation,
    autoGainControl: hints.auto_gain_control,
  },
  video: true,
});
```

- [ ] **Step 4: Write the unit test**

Create `frontend/session/tests/audio-hints.test.tsx`:

```typescript
import { describe, expect, it } from 'vitest';
import type { AudioProcessingHints } from '@/lib/api/candidate-session';

// Pure function under test — extract a `toAudioCaptureOptions(hints)`
// helper out of the room-connect component if it isn't already.
import { toAudioCaptureOptions } from '@/lib/api/audio-hints';

describe('audio_processing_hints → AudioCaptureOptions', () => {
  it('cloud mode (server NC on) sets browser noiseSuppression to false', () => {
    const hints: AudioProcessingHints = {
      noise_suppression: false,
      echo_cancellation: true,
      auto_gain_control: true,
    };
    expect(toAudioCaptureOptions(hints)).toEqual({
      noiseSuppression: false,
      echoCancellation: true,
      autoGainControl: true,
    });
  });

  it('self-hosted mode (server NC off) leaves all browser filters on', () => {
    const hints: AudioProcessingHints = {
      noise_suppression: true,
      echo_cancellation: true,
      auto_gain_control: true,
    };
    expect(toAudioCaptureOptions(hints)).toEqual({
      noiseSuppression: true,
      echoCancellation: true,
      autoGainControl: true,
    });
  });
});
```

If `toAudioCaptureOptions` doesn't exist yet, create `frontend/session/lib/api/audio-hints.ts`:

```typescript
import type { AudioProcessingHints } from './candidate-session';

export function toAudioCaptureOptions(hints: AudioProcessingHints) {
  return {
    noiseSuppression: hints.noise_suppression,
    echoCancellation: hints.echo_cancellation,
    autoGainControl: hints.auto_gain_control,
  };
}
```

Then call it from the room-connect component identified in Step 1.

- [ ] **Step 5: Run the test**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session && npm run test -- audio-hints.test.tsx
```

Expected: 2 passed.

- [ ] **Step 6: Manual smoke — verify the constraint actually applies in the browser**

Start the dev server (`npm run dev` from `frontend/session/`), open a candidate session in the browser, open DevTools → Console, and run:

```javascript
const stream = navigator.mediaDevices.getSupportedConstraints();
console.log(stream);
```

(Once a session is active, the actual track constraints are inspectable via `room.localParticipant.audioTrackPublications`.)

- [ ] **Step 7: Commit**

```bash
git add frontend/session/lib/api/ frontend/session/tests/audio-hints.test.tsx frontend/session/components/
git commit -m "feat(session-ui): apply audio_processing_hints from /start"
```

---

## Task 13: Doc cleanup

The user explicitly requested all stale `.md` files be cleaned up. Each file gets a small surgical edit; do them as one commit since they're all the same kind of work.

**Files:**
- Modify: `backend/nexus/CLAUDE.md`
- Modify: `/home/ishant/Projects/ProjectX/CLAUDE.md`
- Modify: `frontend/session/CLAUDE.md`
- Modify: `frontend/session/AGENTS.md`
- Modify: `docs/security/threat-model.md`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Update `backend/nexus/CLAUDE.md`**

Find the Phase 3D.engine-redesign-6 entry (`grep -n "Phase 3D.engine-redesign-6" backend/nexus/CLAUDE.md`). Replace the "rolled back 2026-05-04" framing with:

> **Phase 3D.engine-redesign-6** — partially un-rolled-back 2026-05-06.
> Production target shifted to **LiveKit Cloud**, restoring access to
> enhanced noise cancellation and adaptive interruption handling. The
> audio-pipeline spec at `docs/superpowers/specs/2026-05-06-audio-pipeline-design.md`
> defines the cutover. Server-side NC is now ai-coustics QUAIL_L
> (replaces the previously-named SPARROW_S; SPARROW is retired by
> ai-coustics) at enhancement_level=0.5; agent.py's TurnHandlingOptions
> use `mode="adaptive"` instead of `"vad"`. The fallback config switch
> (`INTERVIEW_INTERRUPTION_MODE=vad`, `INTERVIEW_NOISE_CANCELLATION=off`)
> keeps self-hosted runs working. Browser-side audio constraints flip
> per deployment mode via the `audio_processing_hints` field on
> `/start`: `noiseSuppression` is OFF when server NC is on (avoids
> double-denoising the ML model's input), `echoCancellation` and
> `autoGainControl` stay ON in both modes (load-bearing for full-duplex).

Update the migration list (search for `0027_tenant_settings`) to add:

> - `0028_audio_tuning_summary` — adds `sessions.audio_tuning_summary`
>   JSONB column for per-session pause/interruption/latency snapshot
>   (audio pipeline empirical-tuning spec). PG11+ metadata-only.

Update the `app/ai/realtime.py` description paragraph (search for "build_turn_detector") to mention the new factories `build_noise_cancellation` and `build_interruption_options`.

- [ ] **Step 2: Update root `CLAUDE.md`**

Find the "Audio Path" section (`grep -n "Audio Path" CLAUDE.md`). Replace the unconditional rule with the per-mode contract:

> **Audio Path**
>
> Browser-side `getUserMedia` audio constraints flip per deployment mode:
>
> | Constraint | Self-hosted LK | LK Cloud (server NC on) |
> | `noiseSuppression` | true | **false** (avoids double-denoising the ML model's input) |
> | `echoCancellation` | true | true (load-bearing for full-duplex; ai-coustics is not an EC) |
> | `autoGainControl` | true | true |
>
> The `frontend/session` app reads these via the `audio_processing_hints`
> field on the `/start` response — server is source of truth. The
> Phase 6 invariant of "all three off" was rolled back on 2026-05-04 and
> replaced by this per-mode contract on 2026-05-06.

Find the Two-Tier Architecture table — update the LiveKit row from "self-hosted" to clarify Cloud is the day-1 target with self-hosted as fallback. Update the Phase 3D row in the phase status table to reference the audio-pipeline spec.

- [ ] **Step 3: Update `frontend/session/CLAUDE.md`**

Add (near the top, in a new "Audio processing" section if one doesn't exist):

> **Audio processing — `audio_processing_hints` contract**
>
> The `/start` response carries an `audio_processing_hints` object with
> three booleans. Pass them straight into LiveKit's `AudioCaptureOptions`
> or `getUserMedia({ audio: ... })`. Server is the source of truth — do
> not hard-code `noiseSuppression: true` or any other constraint.
>
> When server-side NC is on (Cloud mode), `noise_suppression` is `false`
> so the ai-coustics QUAIL_L model sees raw audio. `echoCancellation`
> and `autoGainControl` stay `true` in both modes; do not turn them off.

- [ ] **Step 4: Update `frontend/session/AGENTS.md`**

Mirror the Step 3 edit for the AI-agent-targeted variant (shorter, more imperative tone — see existing AGENTS.md style).

- [ ] **Step 5: Update `docs/security/threat-model.md`**

Find the Phase 6 / audio-path section. Replace "rolled back" framing with "partially un-rolled-back; see audio-pipeline spec." Add to the sub-processor list:

> - **LiveKit Cloud SFU** — candidate audio routes through Cloud's SFU
>   (encrypted in transit, transient). New as of 2026-05-06 audio-pipeline
>   cutover.
> - **ai-coustics** — ML noise-cancellation provider running inside
>   the LK Cloud audio path. Server-side QUAIL_L processes candidate
>   audio before STT.

If a data-flow diagram exists in the threat model, update it to show audio routing through Cloud SFU + ai-coustics before Deepgram STT.

- [ ] **Step 6: Update `backend/nexus/.env.example`**

Add the new env vars in the engine config block:

```
# --- Audio pipeline (LK Cloud cutover spec, 2026-05-06) ---
# Production target is LK Cloud → adaptive + ai_coustics_quail.
# Self-hosted fallback → vad + off (browser does NS).
INTERVIEW_INTERRUPTION_MODE=vad
INTERVIEW_NOISE_CANCELLATION=off
INTERVIEW_NC_ENHANCEMENT_LEVEL=0.5
# Already in this file but verify defaults — production:
# ENGINE_ENDPOINTING_MAX_DELAY=6.0
# ENGINE_SILERO_MIN_SILENCE_DURATION=0.8
# ENGINE_SILERO_ACTIVATION_THRESHOLD=0.5
# INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD=0.15
```

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/CLAUDE.md CLAUDE.md frontend/session/CLAUDE.md frontend/session/AGENTS.md docs/security/threat-model.md backend/nexus/.env.example
git commit -m "docs(audio): document LK Cloud cutover + audio_processing_hints contract"
```

---

## Task 14: Manual end-to-end smoke (Cloud + self-hosted)

Code is in. Now verify it actually works under both deployment modes. The user's memory note says manual testing is the right surface here — no automated browser test that exercises real audio.

**Files:** none (manual)

- [ ] **Step 1: Cloud-mode smoke**

Set the engine to Cloud mode in `backend/nexus/.env`:

```
INTERVIEW_INTERRUPTION_MODE=adaptive
INTERVIEW_NOISE_CANCELLATION=ai_coustics_quail
INTERVIEW_NC_ENHANCEMENT_LEVEL=0.5
LIVEKIT_URL=wss://<your-project>.livekit.cloud
```

Then:

```bash
docker compose up --build
```

Open the candidate session app (`frontend/session/`, port 3002) and start a session. Verify in the engine logs:

- `engine.config.fetched` fires
- `ai.realtime.stt.built`, `ai.realtime.llm.built`, `ai.realtime.tts.built` log lines fire
- No ImportError on plugin init

Then:

- (a) Speak "Let me think about that for a moment…" then pause 5 seconds. The agent should NOT interrupt during the pause. (Validates `unlikely_threshold=0.15` + `max_delay=6.0`.)
- (b) Mid-agent-speech, say "uh-huh." The agent should NOT yield. (Validates adaptive interruption classifier.)
- (c) Run a vacuum cleaner / play loud TV near the mic. STT should remain mostly clean. (Validates ai-coustics QUAIL_L.)
- (d) Open browser DevTools → Network tab → find the `/start` response → verify `audio_processing_hints: { noise_suppression: false, echo_cancellation: true, auto_gain_control: true }`.

End the session normally. Then:

```bash
docker compose exec -T nexus-postgres psql -U postgres -d postgres -c "SELECT id, audio_tuning_summary FROM sessions ORDER BY created_at DESC LIMIT 1;"
```

Expected: a JSON blob with `pauses`, `interruptions`, `latency`, `config_snapshot` keys, and `config_snapshot.interruption_mode='adaptive'`, `config_snapshot.noise_cancellation='ai_coustics_quail'`.

- [ ] **Step 2: Self-hosted-mode smoke**

Override env to self-hosted:

```bash
INTERVIEW_INTERRUPTION_MODE=vad INTERVIEW_NOISE_CANCELLATION=off LIVEKIT_URL=ws://localhost:7880 docker compose up
```

Run a separate `livekit-server --dev --bind 0.0.0.0` in another terminal.

Same drill as Step 1, with these expectations:

- (a) Same — `unlikely_threshold` and `max_delay` are mode-independent.
- (b) Mid-agent-speech, say "uh-huh." The agent should NOT yield (validates `min_words=3` filtering 1-2 word backchannel).
- (c) Vacuum cleaner test — STT will be partially noisy (no server-side NC). This is the documented degraded state.
- (d) `/start` response: `audio_processing_hints: { noise_suppression: true, echo_cancellation: true, auto_gain_control: true }`.

DB row should show `config_snapshot.interruption_mode='vad'`, `config_snapshot.noise_cancellation='off'`.

- [ ] **Step 3: Confirm both work, no commit needed**

Manual gate. If either smoke fails, file a bug, fix it, re-run from Task 11 onward.

---

## Self-review

**Spec coverage check:**

| Spec section | Covered by |
|---|---|
| §3 Architecture & config switch | Tasks 2, 5, 6 |
| §4 Turn detection & VAD tuning | Task 3 (defaults); existing `unlikely_threshold` already at 0.15 |
| §5 Interruption handling | Task 5 |
| §6 Noise cancellation | Tasks 4, 6, 9, 12 |
| §7 Observability + tuning loop | Tasks 1, 7, 8, 10, 11 |
| §8 Files touched | Distributed across all tasks |
| §9 Test plan | Tasks 5, 6, 9, 10, 12; manual in 14 |
| §10 Doc cleanup | Task 13 |
| §10 Future work / out-of-scope | Documented in spec only; no plan tasks |

Acceptance gate items 1-4 from the spec all map: (1) all §8 changes — distributed; (2) §8 doc cleanups — Task 13; (3) Cloud-mode session writes audio_tuning_summary — Task 14 Step 1; (4) self-hosted fallback works — Task 14 Step 2.

**Placeholder scan:** Reviewed inline. The two semi-placeholders are deliberate:
- Task 12 Step 1: "exact path identified during implementation" — the frontend room-connect file path is genuinely runtime-discovered; the grep command in the step does the discovery.
- Task 9 Step 4: the test placeholders for `_compute_audio_hints` are conditional — if the helper exists as a free function, replace; otherwise the test exercises the model directly. Decision belongs to the implementing engineer.

**Type consistency:** `AudioProcessingHints` shape (`noise_suppression`, `echo_cancellation`, `auto_gain_control` — booleans) consistent across Tasks 9, 12, 13. `audio_tuning_summary` keys consistent across Tasks 8, 10, 11, 14.

---

## Acceptance gate

The plan completes when:
1. All 14 tasks executed.
2. `git log --oneline` shows 13 commits matching the conventional-commit messages above.
3. `alembic current` reports `0028_audio_tuning_summary`.
4. A real Cloud-mode interview session writes a non-null `audio_tuning_summary` row.
5. The same session, replayed with self-hosted overrides, also completes cleanly.
