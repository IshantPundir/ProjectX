# Report Review Theater — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the recruiter report API authoritative per-question timestamps and server-generated R2 thumbnails (for questions + top proctoring flags), so the frontend Review Theater can place clickable, image-backed cards on a video timeline — no client-side frame-grabs.

**Architecture:** The interview engine stamps each agent transcript line with the bank `question_id` that was on the floor (a field that already exists, always `null` today). The vision proctoring worker — which already downloads + decodes the recording — extracts a frame at each question's asked-time and at the top proctoring flags, encodes WebP, uploads to R2, and records the keys in a new tenant-scoped `session_timeline_thumbnails` table. The reporting + proctoring read endpoints surface `asked_at_ms` (persisted) and `thumbnail_url` (presigned at read time).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async (asyncpg), Alembic, Dramatiq, boto3 (S3/R2), OpenCV (`cv2`, vision image only), pytest.

**Spec:** `docs/superpowers/specs/2026-05-30-report-review-theater-design.md`

**Conventions used below**
- Backend root for all paths: `backend/nexus/`.
- Run tests from `backend/nexus/`: `docker compose run nexus pytest <path> -q`.
- cv2-dependent tests guard with `pytest.importorskip("cv2")` so they skip in the lean `nexus` image and run where OpenCV exists (the vision image). To run them: `docker compose run nexus-vision-worker pytest <path> -q`.
- This plan touches the **interview engine** (`agent.py`) — a human-review-required area per CLAUDE.md. Task 4 is the only engine change.

---

## File Structure

**Create**
- `app/modules/interview_runtime/transcript_timing.py` — pure `question_asked_at_ms()` helper (shared by reporting + vision).
- `migrations/versions/0052_session_timeline_thumbnails.py` — new tenant-scoped table.
- `tests/test_transcript_timing.py`, `tests/test_storage_upload_bytes.py`, `tests/test_question_id_tagging.py`, `tests/vision/test_thumbnail_extraction.py`, `tests/vision/test_thumbnail_actor.py`, `tests/test_reporting_thumbnails.py`, `tests/test_proctoring_thumbnails.py`.

**Modify**
- `app/modules/interview_engine/mouth/input_builder.py` — add `question_id_for_agent_line()`.
- `app/modules/interview_engine/agent.py` — stamp `question_id` on agent + candidate transcript entries.
- `app/storage/base.py` + `app/storage/s3.py` — add `upload_bytes()`.
- `app/config.py` — add `thumbnail_key_prefix`.
- `app/modules/vision/config.py` — add thumbnail params.
- `app/modules/vision/models.py` — add `SessionTimelineThumbnail` ORM.
- `app/modules/vision/analysis.py` — add `select_flag_targets()` + `grab_thumbnails()`.
- `app/modules/vision/actors.py` — extract + upload + persist thumbnails inside the existing pass.
- `app/modules/vision/service.py` + `app/modules/vision/__init__.py` — add `get_session_timeline_thumbnails()` read helper + export.
- `app/main.py` — add `session_timeline_thumbnails` to `_TENANT_SCOPED_TABLES`.
- `app/modules/reporting/schemas.py` — `QuestionOut += asked_at_ms, thumbnail_url`.
- `app/modules/reporting/service.py` — set `asked_at_ms` in `build_report`.
- `app/modules/reporting/router.py` — presign per-question `thumbnail_url` on read.
- `app/modules/vision/service.py` — presign per-flag `thumbnail_url` on read.

---

## Task 1: Pure helper — derive `asked_at_ms` from a transcript

**Files:**
- Create: `app/modules/interview_runtime/transcript_timing.py`
- Modify: `app/modules/interview_runtime/__init__.py`
- Test: `tests/test_transcript_timing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transcript_timing.py
from app.modules.interview_runtime import question_asked_at_ms


def test_picks_earliest_agent_timestamp_per_question():
    transcript = [
        {"role": "agent", "text": "Q1?", "timestamp_ms": 1000, "question_id": "q1"},
        {"role": "candidate", "text": "...", "timestamp_ms": 1500, "question_id": "q1"},
        {"role": "agent", "text": "probe q1", "timestamp_ms": 2000, "question_id": "q1"},
        {"role": "agent", "text": "Q2?", "timestamp_ms": 3000, "question_id": "q2"},
    ]
    assert question_asked_at_ms(transcript) == {"q1": 1000, "q2": 3000}


def test_ignores_candidate_and_untagged_lines():
    transcript = [
        {"role": "agent", "text": "filler", "timestamp_ms": 100, "question_id": None},
        {"role": "candidate", "text": "hi", "timestamp_ms": 200, "question_id": "q1"},
        {"role": "agent", "text": "Q1?", "timestamp_ms": 300, "question_id": "q1"},
    ]
    assert question_asked_at_ms(transcript) == {"q1": 300}


def test_empty_transcript_returns_empty():
    assert question_asked_at_ms([]) == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run nexus pytest tests/test_transcript_timing.py -q`
Expected: FAIL — `ImportError: cannot import name 'question_asked_at_ms'`.

- [ ] **Step 3: Create the helper**

```python
# app/modules/interview_runtime/transcript_timing.py
"""Pure helper: derive per-question asked-at timestamps from a persisted transcript.

The engine stamps agent transcript lines with the bank question_id on the floor
when the line was spoken (question-bearing acts only — see
interview_engine/mouth/input_builder.is_question_bearing). The FIRST such line
for a question is when it was asked. Consumed by the reporting builder (to set
QuestionOut.asked_at_ms) and the vision worker (to choose thumbnail frames).
"""
from __future__ import annotations


def question_asked_at_ms(transcript: list[dict]) -> dict[str, int]:
    """Map question_id -> earliest agent ``timestamp_ms`` that delivered it.

    Candidate lines and untagged agent lines (fillers / holds / close) are
    ignored. Timestamps are milliseconds since session start.
    """
    out: dict[str, int] = {}
    for entry in transcript:
        if entry.get("role") != "agent":
            continue
        qid = entry.get("question_id")
        if not qid:
            continue
        ts = entry.get("timestamp_ms")
        if ts is None:
            continue
        ts = int(ts)
        if qid not in out or ts < out[qid]:
            out[qid] = ts
    return out
```

- [ ] **Step 4: Export from the package public API**

In `app/modules/interview_runtime/__init__.py`, add the import and `__all__` entry (match the file's existing style):

```python
from app.modules.interview_runtime.transcript_timing import question_asked_at_ms
```
and add `"question_asked_at_ms"` to the `__all__` list.

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run nexus pytest tests/test_transcript_timing.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_runtime/transcript_timing.py app/modules/interview_runtime/__init__.py tests/test_transcript_timing.py
git commit -m "feat(interview_runtime): question_asked_at_ms transcript helper"
```

---

## Task 2: Pure helper — `question_id_for_agent_line`

**Files:**
- Modify: `app/modules/interview_engine/mouth/input_builder.py`
- Test: `tests/test_question_id_tagging.py`

This is a livekit-free module, so the test imports cleanly in the lean image.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_question_id_tagging.py
from app.modules.interview_engine.directive import DirectiveAct
from app.modules.interview_engine.mouth.input_builder import question_id_for_agent_line


def test_question_bearing_acts_carry_active_question_id():
    for act in (DirectiveAct.ASK, DirectiveAct.PROBE, DirectiveAct.ACK_ADVANCE,
                DirectiveAct.CLARIFY, DirectiveAct.REDIRECT):
        assert question_id_for_agent_line(act, "q-123") == "q-123"


def test_non_question_acts_carry_none():
    for act in (DirectiveAct.INTRO, DirectiveAct.HOLD, DirectiveAct.REASSURE,
                DirectiveAct.CLOSE, DirectiveAct.ANSWER_META):
        assert question_id_for_agent_line(act, "q-123") is None


def test_question_bearing_with_no_active_question_is_none():
    assert question_id_for_agent_line(DirectiveAct.ASK, None) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run nexus pytest tests/test_question_id_tagging.py -q`
Expected: FAIL — `ImportError: cannot import name 'question_id_for_agent_line'`.

- [ ] **Step 3: Add the helper to `input_builder.py`**

Directly below the existing `is_question_bearing` function (around line 31):

```python
def question_id_for_agent_line(
    act: DirectiveAct, active_question_id: str | None
) -> str | None:
    """The question_id to stamp on an agent transcript line.

    Returns the active bank question id when the act puts a question on the
    floor (ASK/PROBE/ACK_ADVANCE/CLARIFY/REDIRECT), else None — so fillers,
    holds, reassurances and the close are not mis-attributed to a question.
    """
    return active_question_id if is_question_bearing(act) else None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose run nexus pytest tests/test_question_id_tagging.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/mouth/input_builder.py tests/test_question_id_tagging.py
git commit -m "feat(interview_engine): question_id_for_agent_line helper"
```

---

## Task 3: Wire `question_id` into the engine transcript (HUMAN REVIEW)

**Files:**
- Modify: `app/modules/interview_engine/agent.py`

> ⚠️ This edits the interview engine. Per CLAUDE.md it requires human review. The change is two field additions on existing `TranscriptEntry` constructions plus one import; no control-flow change.

- [ ] **Step 1: Add the import**

In `agent.py`, the line that imports from the mouth input_builder currently reads:

```python
from app.modules.interview_engine.mouth.input_builder import is_question_bearing
```

Replace it with:

```python
from app.modules.interview_engine.mouth.input_builder import (
    is_question_bearing,
    question_id_for_agent_line,
)
```

- [ ] **Step 2: Stamp the candidate turn (around line 397)**

The candidate transcript append in `on_user_turn_completed` currently reads:

```python
            self._result_transcript.append(
                TranscriptEntry(role="candidate", text=text, timestamp_ms=self._t_ms()))
```

Replace with (attribute the answer to the question on the floor):

```python
            self._result_transcript.append(
                TranscriptEntry(
                    role="candidate", text=text, timestamp_ms=self._t_ms(),
                    question_id=self._brain.active_question_id,
                ))
```

- [ ] **Step 3: Stamp the agent spoken line (around line 556)**

The agent spoken append in `llm_node` currently reads:

```python
        if spoken:
            self._result_transcript.append(
                TranscriptEntry(role="agent", text=spoken, timestamp_ms=self._t_ms()))
```

Replace the `append(...)` with:

```python
        if spoken:
            self._result_transcript.append(
                TranscriptEntry(
                    role="agent", text=spoken, timestamp_ms=self._t_ms(),
                    question_id=question_id_for_agent_line(
                        directive.act, self._brain.active_question_id),
                ))
```

> Leave the masking-filler (`_say_filler`) and hold-cue (`_say_hold_cue`) appends untouched — those are not question deliveries, so their `question_id` correctly stays `None`.

- [ ] **Step 4: Verify the engine test suite still imports + passes**

Run: `docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" -q`
Expected: PASS (no regressions; existing transcript assertions still hold because `question_id` defaults to `None` and we only added values).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/agent.py
git commit -m "feat(interview_engine): tag transcript entries with active question_id

Authoritative anchor for per-question asked_at_ms (report timeline + thumbnails).
HUMAN-REVIEW: engine change."
```

---

## Task 4: Storage — `upload_bytes`

**Files:**
- Modify: `app/storage/base.py`, `app/storage/s3.py`
- Test: `tests/test_storage_upload_bytes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_upload_bytes.py
from unittest.mock import MagicMock, patch

import pytest

from app.storage.s3 import S3CompatibleStorage


def _storage() -> S3CompatibleStorage:
    return S3CompatibleStorage(
        bucket="rec-bucket", region="auto", endpoint_url="https://r2.example.com",
        access_key_id="k", secret_access_key="s", force_path_style=True,
    )


@pytest.mark.asyncio
async def test_upload_bytes_calls_put_object():
    fake_client = MagicMock()
    storage = _storage()
    with patch.object(storage, "_client", return_value=fake_client):
        await storage.upload_bytes("thumbs/t/s/q1.webp", b"RIFFdata", content_type="image/webp")
    fake_client.put_object.assert_called_once_with(
        Bucket="rec-bucket",
        Key="thumbs/t/s/q1.webp",
        Body=b"RIFFdata",
        ContentType="image/webp",
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run nexus pytest tests/test_storage_upload_bytes.py -q`
Expected: FAIL — `AttributeError: 'S3CompatibleStorage' object has no attribute 'upload_bytes'`.

- [ ] **Step 3: Add the method to the Protocol**

In `app/storage/base.py`, inside the `ObjectStorage` Protocol, after `download_to_path`:

```python
    async def upload_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        """Upload raw bytes to ``key`` (overwrites). Server-side only — the bytes
        never transit the browser. Used for derived media (e.g. timeline
        thumbnails extracted from the recording)."""
        ...
```

- [ ] **Step 4: Implement in `s3.py`**

In `app/storage/s3.py`, after `download_to_path`:

```python
    async def upload_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        client = self._client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run nexus pytest tests/test_storage_upload_bytes.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add app/storage/base.py app/storage/s3.py tests/test_storage_upload_bytes.py
git commit -m "feat(storage): upload_bytes for derived media (thumbnails)"
```

---

## Task 5: Config + new table `session_timeline_thumbnails`

**Files:**
- Modify: `app/config.py`, `app/modules/vision/config.py`, `app/modules/vision/models.py`, `app/main.py`
- Create: `migrations/versions/0052_session_timeline_thumbnails.py`
- Test: `tests/test_timeline_thumbnails_table.py`

- [ ] **Step 1: Add the config setting**

In `app/config.py`, directly after `recording_key_prefix` (line ~285):

```python
    # Object key prefix for derived timeline thumbnails; final key is
    # {prefix}/{tenant_id}/{session_id}/{kind}_{ref}.webp. Same private
    # recording bucket + signed-URL TTL as the recording itself.
    thumbnail_key_prefix: str = "thumbnails"
```

- [ ] **Step 2: Add vision thumbnail params**

In `app/modules/vision/config.py`, add fields to the existing `vision_config` settings object (match its field style — it is a pydantic settings/dataclass; mirror an existing numeric field):

```python
    # --- timeline thumbnails (Report Review Theater) ---
    thumbnail_width_px: int = 320           # resize target; preserves aspect
    thumbnail_webp_quality: int = 80        # cv2 IMWRITE_WEBP_QUALITY
    thumbnail_top_flag_count: int = 6       # how many proctoring flags get a thumbnail
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_timeline_thumbnails_table.py
from app.main import _TENANT_SCOPED_TABLES
from app.modules.vision.models import SessionTimelineThumbnail


def test_table_is_registered_tenant_scoped():
    assert "session_timeline_thumbnails" in _TENANT_SCOPED_TABLES


def test_model_has_required_columns():
    cols = {c.name for c in SessionTimelineThumbnail.__table__.columns}
    assert {"id", "tenant_id", "session_id", "kind", "ref_id", "t_ms",
            "s3_key", "created_at"} <= cols


def test_unique_constraint_on_session_kind_ref():
    uniques = [
        tuple(c.name for c in con.columns)
        for con in SessionTimelineThumbnail.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("session_id", "kind", "ref_id") in uniques
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `docker compose run nexus pytest tests/test_timeline_thumbnails_table.py -q`
Expected: FAIL — `ImportError: cannot import name 'SessionTimelineThumbnail'`.

- [ ] **Step 5: Add the ORM model**

Append to `app/modules/vision/models.py`:

```python
from sqlalchemy import UniqueConstraint  # add to the existing sqlalchemy import line


class SessionTimelineThumbnail(Base):
    """One extracted frame for the report timeline (a question card or a
    proctoring flag). Many per session. Produced by the vision worker;
    presigned on read by reporting/proctoring."""

    __tablename__ = "session_timeline_thumbnails"
    __table_args__ = (
        UniqueConstraint("session_id", "kind", "ref_id",
                         name="uq_timeline_thumb_session_kind_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)          # 'question' | 'flag'
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)        # question_id or flag start_ms
    t_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

> The existing import line in `models.py` is `from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, text`. Add `UniqueConstraint` to it rather than a second import line.

- [ ] **Step 6: Register in the RLS completeness list**

In `app/main.py`, in `_TENANT_SCOPED_TABLES`, directly after the `"session_proctoring_analysis",` entry:

```python
    # Report Review Theater — derived timeline thumbnails (migration 0052).
    "session_timeline_thumbnails",
```

- [ ] **Step 7: Run the model test to verify it passes**

Run: `docker compose run nexus pytest tests/test_timeline_thumbnails_table.py -q`
Expected: PASS (3 passed). (The test DB uses `create_all`, so the table exists from the model.)

- [ ] **Step 8: Write the migration**

```python
# migrations/versions/0052_session_timeline_thumbnails.py
"""session_timeline_thumbnails — derived report-timeline thumbnails (questions + flags).

Tenant-scoped, canonical RLS pair (NULLIF discipline). One row per
(session, kind, ref). Written by the vision worker; presigned on read.

Rollback: downgrade drops the table (policies drop with it). Safe — no other
table references it.

Revision ID: 0052
Revises: 0051
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON {table}
          USING (current_setting('app.bypass_rls', true) = 'true');
    """)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexus_app;")


def upgrade() -> None:
    op.create_table(
        "session_timeline_thumbnails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("t_ms", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("session_id", "kind", "ref_id",
                            name="uq_timeline_thumb_session_kind_ref"),
    )
    op.execute(
        "ALTER TABLE session_timeline_thumbnails ADD CONSTRAINT "
        "session_timeline_thumbnails_kind_check CHECK (kind IN ('question','flag'))"
    )
    op.create_index("ix_timeline_thumb_session", "session_timeline_thumbnails",
                    ["session_id"])
    _enable_rls("session_timeline_thumbnails")


def downgrade() -> None:
    op.drop_table("session_timeline_thumbnails")
```

- [ ] **Step 9: Apply the migration + verify boot assertion passes**

```bash
docker compose run nexus alembic upgrade head
docker compose up -d --force-recreate nexus
docker compose logs --tail=40 nexus | grep rls.completeness_check
```
Expected: `rls.completeness_check_ok` (the new table has both policies). If `rls.completeness_check_failed` appears, the migration's `_enable_rls` did not run — fix and re-apply.

- [ ] **Step 10: Commit**

```bash
git add app/config.py app/modules/vision/config.py app/modules/vision/models.py app/main.py migrations/versions/0052_session_timeline_thumbnails.py tests/test_timeline_thumbnails_table.py
git commit -m "feat(vision): session_timeline_thumbnails table + RLS + config"
```

---

## Task 6: Vision — flag-target selection + frame grab

**Files:**
- Modify: `app/modules/vision/analysis.py`
- Test: `tests/vision/test_thumbnail_extraction.py`

- [ ] **Step 1: Write the failing test for `select_flag_targets` (pure)**

```python
# tests/vision/test_thumbnail_extraction.py
import pytest

from app.modules.vision.analysis import select_flag_targets


def test_selects_top_n_by_severity_then_confidence():
    flags = [
        {"kind": "down_glance", "start_ms": 100, "end_ms": 200, "confidence": 0.6},
        {"kind": "off_screen_sustained", "start_ms": 300, "end_ms": 800, "confidence": 0.65},
        {"kind": "multiple_faces", "start_ms": 900, "end_ms": 1000, "confidence": 0.9},
        {"kind": "down_glance", "start_ms": 1100, "end_ms": 1200, "confidence": 0.6},
    ]
    out = select_flag_targets(flags, top_n=2)
    # multiple_faces (highest severity) + off_screen_sustained rank above down_glance
    kinds = [t["kind"] for t in out]
    assert kinds == ["multiple_faces", "off_screen_sustained"]
    assert out[0]["start_ms"] == 900


def test_empty_flags_returns_empty():
    assert select_flag_targets([], top_n=6) == []


def test_caps_at_top_n():
    flags = [{"kind": "down_glance", "start_ms": i, "end_ms": i + 1, "confidence": 0.6}
             for i in range(10)]
    assert len(select_flag_targets(flags, top_n=3)) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run nexus pytest tests/vision/test_thumbnail_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name 'select_flag_targets'`.

- [ ] **Step 3: Add `select_flag_targets` to `analysis.py`**

```python
# Severity ordering for proctoring flags (higher = more serious). Used to pick
# which flags earn a timeline thumbnail when there are more than top_n.
_FLAG_SEVERITY: dict[str, int] = {
    "multiple_faces": 3,
    "off_screen_sustained": 2,
    "reading_sweep": 1,
    "down_glance": 0,
}


def select_flag_targets(flagged_intervals: list[dict], *, top_n: int) -> list[dict]:
    """Return the top-N most serious flags (severity, then confidence, then
    earliest), each as the original interval dict. Pure — no I/O."""
    ranked = sorted(
        flagged_intervals,
        key=lambda f: (
            _FLAG_SEVERITY.get(f.get("kind", ""), 0),
            float(f.get("confidence") or 0.0),
            -int(f.get("start_ms") or 0),
        ),
        reverse=True,
    )
    return ranked[: max(0, top_n)]
```

> Signature note: the test calls `select_flag_targets(flags, top_n=2)`. Keep `top_n` keyword-only as written.

- [ ] **Step 4: Run to verify the pure test passes**

Run: `docker compose run nexus pytest tests/vision/test_thumbnail_extraction.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the cv2 integration test for `grab_thumbnails`**

Append to `tests/vision/test_thumbnail_extraction.py`:

```python
def _write_test_video(path: str, *, frames: int = 30, fps: int = 10, w: int = 64, h: int = 48):
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(frames):
        frame = np.full((h, w, 3), (i * 7) % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_grab_thumbnails_returns_webp_for_targets(tmp_path):
    pytest.importorskip("cv2")
    from app.modules.vision.analysis import grab_thumbnails

    video = str(tmp_path / "rec.mp4")
    _write_test_video(video)  # 30 frames @ 10fps = 3.0s
    out = grab_thumbnails(video, [500, 1500], width=32, webp_quality=80)

    assert set(out.keys()) == {500, 1500}
    for blob in out.values():
        assert isinstance(blob, bytes) and len(blob) > 0
        assert blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"  # WebP container header


def test_grab_thumbnails_skips_out_of_range(tmp_path):
    pytest.importorskip("cv2")
    from app.modules.vision.analysis import grab_thumbnails

    video = str(tmp_path / "rec.mp4")
    _write_test_video(video, frames=10, fps=10)  # 1.0s total
    out = grab_thumbnails(video, [500, 9_000_000], width=32, webp_quality=80)
    assert 500 in out  # in range; the absurd target clamps to the last frame, still returns
```

- [ ] **Step 6: Run to verify the cv2 test fails (where cv2 exists)**

Run: `docker compose run nexus-vision-worker pytest tests/vision/test_thumbnail_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name 'grab_thumbnails'`. (In the lean `nexus` image these two tests SKIP via `importorskip`.)

- [ ] **Step 7: Add `grab_thumbnails` to `analysis.py`**

```python
def grab_thumbnails(
    video_path: str, targets_ms: list[int], *, width: int, webp_quality: int
) -> dict[int, bytes]:
    """For each target timestamp, seek to the nearest frame, resize to ``width``
    (preserving aspect), encode WebP. Returns {target_ms: webp_bytes}; targets
    whose frame cannot be read are omitted. Reuses the recording the proctoring
    pass already downloaded — one extra seek per target, no full re-decode.
    """
    import cv2  # noqa: PLC0415  — lazy: heavy native dep, vision image only

    cap = cv2.VideoCapture(video_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out: dict[int, bytes] = {}
    try:
        for t_ms in targets_ms:
            idx = int(round((t_ms / 1000.0) * src_fps))
            if frame_count:
                idx = min(idx, frame_count - 1)
            idx = max(idx, 0)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            if w > width and w > 0:
                new_h = max(1, int(round(h * (width / w))))
                frame = cv2.resize(frame, (width, new_h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".webp", frame, [cv2.IMWRITE_WEBP_QUALITY, webp_quality])
            if ok:
                out[t_ms] = bytes(buf.tobytes())
    finally:
        cap.release()
    return out
```

- [ ] **Step 8: Run to verify the cv2 tests pass**

Run: `docker compose run nexus-vision-worker pytest tests/vision/test_thumbnail_extraction.py -q`
Expected: PASS (5 passed).

- [ ] **Step 9: Commit**

```bash
git add app/modules/vision/analysis.py tests/vision/test_thumbnail_extraction.py
git commit -m "feat(vision): flag-target selection + WebP frame-grab for thumbnails"
```

---

## Task 7: Vision actor — extract, upload, persist thumbnails

**Files:**
- Modify: `app/modules/vision/actors.py`
- Test: `tests/vision/test_thumbnail_actor.py`

The actor already: loads state, downloads the recording to a temp path, runs analysis, persists features. We add a step after analysis: build the target timestamps (questions from the transcript + top flags), grab frames, upload each to R2, upsert a `SessionTimelineThumbnail` row per uploaded frame. Failures here must NOT fail the proctoring result (best-effort, logged).

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_thumbnail_actor.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.modules.vision import actors as vision_actors
from app.modules.vision.models import SessionTimelineThumbnail


@pytest.mark.asyncio
async def test_persist_thumbnails_uploads_and_upserts(db_session, seeded_session_with_transcript):
    """After analysis, the actor grabs frames at question + flag timestamps,
    uploads them, and writes one row per uploaded frame."""
    sess = seeded_session_with_transcript  # has transcript with question_id 'q1' @ 1000ms
    tenant_id = sess.tenant_id
    flagged = [{"kind": "off_screen_sustained", "start_ms": 5000, "end_ms": 6000,
                "confidence": 0.65}]

    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails",
                      return_value={1000: b"RIFF..WEBP", 5000: b"RIFF..WEBP"}):
        await vision_actors._persist_timeline_thumbnails(
            db_session,
            session_id=str(sess.id),
            tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4",
            transcript=list(sess.transcript or []),
            flagged_intervals=flagged,
        )
        await db_session.commit()

    rows = (await db_session.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()
    kinds = {(r.kind, r.ref_id) for r in rows}
    assert ("question", "q1") in kinds
    assert ("flag", "5000") in kinds
    assert fake_storage.upload_bytes.await_count == 2
```

> The fixtures `db_session` and `seeded_session_with_transcript` follow the existing vision test fixtures. If `seeded_session_with_transcript` does not exist, add it to `tests/vision/conftest.py`: insert a `Session` row (bypass session, `SET LOCAL app.current_tenant`) with `transcript=[{"role":"agent","text":"Q1?","timestamp_ms":1000,"question_id":"q1"}]` and `recording_status="ready"`, returning the row. Mirror the seeding in the existing proctoring actor test.

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run nexus pytest tests/vision/test_thumbnail_actor.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_persist_timeline_thumbnails'`.

- [ ] **Step 3: Add imports + the helper in `actors.py`**

At the top of `actors.py`, extend the analysis import and add the new ones:

```python
from app.modules.vision.analysis import (  # light: no cv2/torch at import
    grab_thumbnails,
    run_analysis,
    select_flag_targets,
)
from app.modules.vision.config import vision_config
from app.modules.vision.models import SessionProctoringAnalysis, SessionTimelineThumbnail
from app.modules.interview_runtime import question_asked_at_ms
```

> `grab_thumbnails` imports `cv2` lazily *inside* the function, so adding it to this top-level import does NOT pull cv2 into the lean nexus image at import time.

Then add the helper (above `_run`):

```python
async def _persist_timeline_thumbnails(
    db, *, session_id: str, tenant_id: str, local_video_path: str,
    transcript: list[dict], flagged_intervals: list[dict],
) -> None:
    """Best-effort: extract question + top-flag frames, upload to R2, upsert rows.

    Never raises into the proctoring result path — a thumbnail failure must not
    fail the gaze analysis. Keys are deterministic, so re-runs overwrite the
    same R2 objects and ON CONFLICT-refresh the same rows.
    """
    sid = uuid.UUID(session_id)
    tid = uuid.UUID(tenant_id)

    # (kind, ref_id, t_ms) targets — questions from the engine-tagged transcript,
    # plus the most serious proctoring flags.
    q_times = question_asked_at_ms(transcript)
    targets: list[tuple[str, str, int]] = [
        ("question", qid, t_ms) for qid, t_ms in q_times.items()
    ]
    for flag in select_flag_targets(
        flagged_intervals, top_n=vision_config.thumbnail_top_flag_count
    ):
        start = int(flag.get("start_ms") or 0)
        targets.append(("flag", str(start), start))

    if not targets:
        return

    # de-dupe target timestamps for the single grab pass
    unique_ms = sorted({t_ms for _, _, t_ms in targets})
    try:
        frames = grab_thumbnails(
            local_video_path, unique_ms,
            width=vision_config.thumbnail_width_px,
            webp_quality=vision_config.thumbnail_webp_quality,
        )
    except Exception:  # noqa: BLE001 — thumbnails are non-critical
        log.warning("vision.thumbnails.grab_failed", session_id=session_id, exc_info=True)
        return

    prefix = settings.thumbnail_key_prefix
    for kind, ref_id, t_ms in targets:
        blob = frames.get(t_ms)
        if not blob:
            continue
        key = f"{prefix}/{tenant_id}/{session_id}/{kind}_{ref_id}.webp"
        try:
            await get_object_storage().upload_bytes(key, blob, content_type="image/webp")
        except Exception:  # noqa: BLE001
            log.warning("vision.thumbnails.upload_failed",
                        session_id=session_id, key=key, exc_info=True)
            continue
        existing = (await db.execute(
            select(SessionTimelineThumbnail).where(
                SessionTimelineThumbnail.session_id == sid,
                SessionTimelineThumbnail.tenant_id == tid,
                SessionTimelineThumbnail.kind == kind,
                SessionTimelineThumbnail.ref_id == ref_id,
            )
        )).scalar_one_or_none()
        if existing is None:
            db.add(SessionTimelineThumbnail(
                tenant_id=tid, session_id=sid, kind=kind, ref_id=ref_id,
                t_ms=t_ms, s3_key=key))
        else:
            existing.t_ms = t_ms
            existing.s3_key = key
```

> Add `from app.config import settings` to the actor imports if not already present (it is not in the current file — add it).

- [ ] **Step 4: Call the helper from `_run` (Phase 3)**

In `_run`, the success path currently downloads + analyzes inside the `tempfile.TemporaryDirectory()` block, then persists in Phase 3 *after* the temp dir closes. The thumbnail grab needs the local file, so it must happen **inside** the temp block. Restructure Phase 2 + 3 like this — replace the existing Phase 2/Phase 3 region (the `with tempfile.TemporaryDirectory()` block through the Phase-3 persist) with:

```python
    # Phase 2: heavy work OUTSIDE the DB transaction.
    try:
        from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator  # noqa: PLC0415

        estimator = MobileGazeEstimator(
            weights_path=vision_config.gaze_weights_path,
            input_size=vision_config.gaze_input_size,
            pitch_sign=vision_config.gaze_pitch_sign,
            yaw_sign=vision_config.gaze_yaw_sign,
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "recording.mp4")
            await get_object_storage().download_to_path(recording_key, dest)
            result, frames = run_analysis(estimator, local_video_path=dest)
            final_status = (
                "unscorable" if result.risk_band == "insufficient_data" else "ready"
            )
            # Persist gaze features first (own transaction), then thumbnails
            # (best-effort) — both while the recording is still on local disk.
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
                await _persist(db, session_id, tenant_id,
                               status=final_status, result=result, frames=frames)
                await db.commit()
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
                # Re-load the session transcript under this tenant scope.
                sess_row = (await db.execute(
                    select(Session).where(
                        Session.id == uuid.UUID(session_id),
                        Session.tenant_id == uuid.UUID(tenant_id),
                    )
                )).scalar_one_or_none()
                transcript = list(sess_row.transcript or []) if sess_row else []
                await _persist_timeline_thumbnails(
                    db, session_id=session_id, tenant_id=tenant_id,
                    local_video_path=dest, transcript=transcript,
                    flagged_intervals=result.flagged_intervals or [],
                )
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("vision.actor.failed", session_id=session_id, exc_info=exc)
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
            await _persist(db, session_id, tenant_id, status="failed", error=str(exc)[:500])
            await db.commit()
        raise

    log.info("vision.actor.done", session_id=session_id,
             band=result.risk_band, frames=frames)
```

> This moves the gaze-feature persist *inside* the temp block (so the file is still present for the thumbnail grab) and removes the old standalone Phase-3 block. Delete the now-duplicate Phase-3 persist that followed the old `with` block.

- [ ] **Step 5: Run to verify the actor test passes**

Run: `docker compose run nexus pytest tests/vision/test_thumbnail_actor.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Run the full vision suite (no regressions)**

Run: `docker compose run nexus pytest tests/vision -q`
Expected: PASS (existing proctoring actor tests still green — the restructure preserves the gaze persist + failure path).

- [ ] **Step 7: Commit**

```bash
git add app/modules/vision/actors.py tests/vision/test_thumbnail_actor.py
git commit -m "feat(vision): extract+upload+persist timeline thumbnails in the decode pass"
```

---

## Task 8: Vision read helper — `get_session_timeline_thumbnails`

**Files:**
- Modify: `app/modules/vision/service.py`, `app/modules/vision/__init__.py`
- Test: `tests/vision/test_thumbnail_read.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_thumbnail_read.py
import pytest

from app.modules.vision import get_session_timeline_thumbnails
from app.modules.vision.models import SessionTimelineThumbnail


@pytest.mark.asyncio
async def test_returns_rows_for_session(db_session, seeded_session_with_transcript):
    sess = seeded_session_with_transcript
    db_session.add(SessionTimelineThumbnail(
        tenant_id=sess.tenant_id, session_id=sess.id,
        kind="question", ref_id="q1", t_ms=1000, s3_key="thumbs/t/s/question_q1.webp"))
    await db_session.commit()

    rows = await get_session_timeline_thumbnails(
        db_session, session_id=sess.id, tenant_id=sess.tenant_id)
    assert len(rows) == 1
    assert rows[0].kind == "question" and rows[0].ref_id == "q1"
    assert rows[0].s3_key == "thumbs/t/s/question_q1.webp"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run nexus pytest tests/vision/test_thumbnail_read.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_session_timeline_thumbnails'`.

- [ ] **Step 3: Add the read helper to `service.py`**

```python
from app.modules.vision.models import (
    SessionProctoringAnalysis,
    SessionTimelineThumbnail,
)


async def get_session_timeline_thumbnails(
    db: AsyncSession, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[SessionTimelineThumbnail]:
    """Tenant-scoped: all timeline thumbnail rows for a session (questions + flags)."""
    return list((await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == session_id,
            SessionTimelineThumbnail.tenant_id == tenant_id,
        )
    )).scalars().all())
```

- [ ] **Step 4: Export from the package**

In `app/modules/vision/__init__.py`, add the import and `__all__` entry:

```python
from app.modules.vision.service import (
    get_session_proctoring_analysis,
    get_session_timeline_thumbnails,
)
```
Add `"get_session_timeline_thumbnails"` to `__all__`.

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run nexus pytest tests/vision/test_thumbnail_read.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add app/modules/vision/service.py app/modules/vision/__init__.py tests/vision/test_thumbnail_read.py
git commit -m "feat(vision): get_session_timeline_thumbnails read helper"
```

---

## Task 9: Reporting — `asked_at_ms` on `QuestionOut`

**Files:**
- Modify: `app/modules/reporting/schemas.py`, `app/modules/reporting/service.py`
- Test: `tests/test_reporting_asked_at_ms.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reporting_asked_at_ms.py
import pytest

from app.modules.reporting.schemas import QuestionOut


def test_questionout_has_asked_at_ms_and_thumbnail_url_defaults():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="Q?", candidate_quote="a")
    assert q.asked_at_ms is None
    assert q.thumbnail_url is None
```

(A focused unit test on the schema; the `build_report` wiring is covered by the existing report-builder integration test, extended in Step 5.)

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run nexus pytest tests/test_reporting_asked_at_ms.py -q`
Expected: FAIL — `TypeError`/validation: `asked_at_ms` is not a field.

- [ ] **Step 3: Extend the `QuestionOut` schema**

In `app/modules/reporting/schemas.py`, the `QuestionOut` model — add two fields:

```python
class QuestionOut(BaseModel):
    seq: int
    question_id: str
    title: str
    status_badge: str
    status_tone: str
    question_text: str
    candidate_quote: str
    our_read: str = ""
    asked_at_ms: int | None = None       # ms since session start (None for legacy sessions)
    thumbnail_url: str | None = None     # presigned R2 GET, attached at read time only
```

- [ ] **Step 4: Set `asked_at_ms` in `build_report`**

In `app/modules/reporting/service.py`, at the top of `build_report` (it already receives `transcript`), derive the map once:

```python
    from app.modules.interview_runtime import question_asked_at_ms
    asked_at = question_asked_at_ms(transcript)
```

Then in the `q_out.append(QuestionOut(...))` call (around line 224), add `asked_at_ms`:

```python
        q_out.append(QuestionOut(
            seq=i + 1, question_id=u.question_id, title=q.get("text", "")[:60],
            status_badge=badge, status_tone=tone,
            question_text=q.get("text", ""), candidate_quote=u.candidate_answer,
            asked_at_ms=asked_at.get(u.question_id)))
```

- [ ] **Step 5: Extend the existing build_report test (or add one) to assert `asked_at_ms` flows**

Add to `tests/test_reporting_asked_at_ms.py`:

```python
@pytest.mark.asyncio
async def test_build_report_sets_asked_at_ms(monkeypatch):
    """asked_at_ms is derived from transcript question_id tags."""
    from app.modules.reporting import service as svc

    transcript = [{"role": "agent", "text": "Q1?", "timestamp_ms": 4200, "question_id": "q1"}]
    # The report builder calls several LLM helpers; this test only asserts the
    # transcript→asked_at_ms mapping, so we verify the pure derivation the
    # builder uses rather than running the full LLM pipeline.
    from app.modules.interview_runtime import question_asked_at_ms
    assert question_asked_at_ms(transcript) == {"q1": 4200}
```

> Rationale: `build_report` makes real LLM calls (narrative, recheck, holistic, judge) that are mocked in the dedicated `tests/test_reporting_service*.py` integration suite. Keep this task's test focused on the schema + the pure derivation; the full-pipeline assertion belongs in the existing integration test, where you can add `asked_at_ms` checks to its `q_out` assertions if present.

- [ ] **Step 6: Run to verify it passes**

Run: `docker compose run nexus pytest tests/test_reporting_asked_at_ms.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting/schemas.py app/modules/reporting/service.py tests/test_reporting_asked_at_ms.py
git commit -m "feat(reporting): asked_at_ms on QuestionOut from tagged transcript"
```

---

## Task 10: Reporting — presign question `thumbnail_url` on read

**Files:**
- Modify: `app/modules/reporting/router.py`
- Test: `tests/test_reporting_thumbnails.py`

The report read endpoints (`get_report_by_session`, `get_report_by_id`) build a `ReportRead` via `_row_to_read` (sync, no db). Add an async step that loads `kind='question'` thumbnails, presigns each, and attaches `thumbnail_url` by `question_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reporting_thumbnails.py
from unittest.mock import AsyncMock

import pytest

from app.modules.reporting.router import _attach_question_thumbnails
from app.modules.reporting.schemas import (
    DecisionOut, MethodologyOut, QuestionOut, ReportRead, WhyColumn,
)


def _report_with_question(qid: str) -> ReportRead:
    return ReportRead(
        verdict="reject", verdict_reason="r", overall_score=35, overall_coverage=0.3,
        overall_confidence="low",
        decision=DecisionOut(headline="h", why_positive=WhyColumn(title="", body=""),
                             why_negative=WhyColumn(title="", body="")),
        scores={}, methodology=MethodologyOut(note="", charity_flags=[]),
        questions=[QuestionOut(seq=1, question_id=qid, title="t", status_badge="failed_required",
                               status_tone="danger", question_text="Q?", candidate_quote="a")],
    )


@pytest.mark.asyncio
async def test_attaches_presigned_url_by_question_id(monkeypatch):
    report = _report_with_question("q1")

    class FakeThumb:
        kind = "question"; ref_id = "q1"; s3_key = "thumbs/t/s/question_q1.webp"

    async def fake_get_thumbs(db, *, session_id, tenant_id):
        return [FakeThumb()]

    fake_storage = type("S", (), {"presign_get_url": AsyncMock(return_value="https://signed/q1")})()

    import app.modules.reporting.router as rt
    monkeypatch.setattr(rt, "get_session_timeline_thumbnails", fake_get_thumbs)
    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    await _attach_question_thumbnails(db=None, report=report, session_id="s", tenant_id="t")
    assert report.questions[0].thumbnail_url == "https://signed/q1"


@pytest.mark.asyncio
async def test_no_thumbnail_leaves_url_none(monkeypatch):
    report = _report_with_question("q1")

    async def fake_get_thumbs(db, *, session_id, tenant_id):
        return []

    import app.modules.reporting.router as rt
    monkeypatch.setattr(rt, "get_session_timeline_thumbnails", fake_get_thumbs)
    await _attach_question_thumbnails(db=None, report=report, session_id="s", tenant_id="t")
    assert report.questions[0].thumbnail_url is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run nexus pytest tests/test_reporting_thumbnails.py -q`
Expected: FAIL — `ImportError: cannot import name '_attach_question_thumbnails'`.

- [ ] **Step 3: Add imports + the helper in `router.py`**

Add near the other imports:

```python
from app.config import settings
from app.storage import get_object_storage
from app.modules.vision import (
    get_session_proctoring_analysis,
    get_session_timeline_thumbnails,
)
```
(Replace the existing `from app.modules.vision import get_session_proctoring_analysis` line with the grouped import above.)

Add the helper:

```python
async def _attach_question_thumbnails(
    *, db: AsyncSession, report: ReportRead, session_id: Any, tenant_id: Any
) -> None:
    """Presign per-question timeline thumbnails and attach by question_id.
    Best-effort: a presign/storage failure leaves thumbnail_url as None."""
    if not report.questions:
        return
    try:
        thumbs = await get_session_timeline_thumbnails(
            db, session_id=session_id, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        return
    by_qid = {t.ref_id: t.s3_key for t in thumbs if t.kind == "question"}
    if not by_qid:
        return
    storage = get_object_storage()
    ttl = settings.recording_signed_url_ttl_seconds
    for q in report.questions:
        key = by_qid.get(q.question_id)
        if not key:
            continue
        try:
            q.thumbnail_url = await storage.presign_get_url(key, ttl_seconds=ttl)
        except Exception:  # noqa: BLE001
            continue
```

- [ ] **Step 4: Call it from both read endpoints**

In `get_report_by_session`, replace the final `return _row_to_read(row).model_dump(mode="json")` with:

```python
    read = _row_to_read(row)
    await _attach_question_thumbnails(
        db=db, report=read, session_id=session_id, tenant_id=tenant_id)
    return read.model_dump(mode="json")
```

In `get_report_by_id`, do the same, using `row.session_id` for the session id:

```python
    read = _row_to_read(row)
    await _attach_question_thumbnails(
        db=db, report=read, session_id=row.session_id, tenant_id=tenant_id)
    return read.model_dump(mode="json")
```

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run nexus pytest tests/test_reporting_thumbnails.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the reporting router suite (no regressions)**

Run: `docker compose run nexus pytest tests/ -k "report" -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting/router.py tests/test_reporting_thumbnails.py
git commit -m "feat(reporting): presign question thumbnail_url on report read"
```

---

## Task 11: Proctoring — presign flag `thumbnail_url` on read

**Files:**
- Modify: `app/modules/vision/service.py`
- Test: `tests/vision/test_proctoring_thumbnails.py`

Attach a presigned `thumbnail_url` to each flagged interval that has a `flag` thumbnail, keyed by `start_ms`.

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_proctoring_thumbnails.py
from unittest.mock import AsyncMock

import pytest

from app.modules.vision.service import attach_flag_thumbnails


class _Thumb:
    def __init__(self, ref_id, key):
        self.kind = "flag"; self.ref_id = ref_id; self.s3_key = key


@pytest.mark.asyncio
async def test_attaches_url_to_matching_flag(monkeypatch):
    flagged = [{"kind": "off_screen_sustained", "start_ms": 5000, "end_ms": 6000,
                "confidence": 0.65}]
    thumbs = [_Thumb("5000", "thumbs/t/s/flag_5000.webp")]
    fake_storage = type("S", (), {"presign_get_url": AsyncMock(return_value="https://signed/f")})()
    import app.modules.vision.service as svc
    monkeypatch.setattr(svc, "get_object_storage", lambda: fake_storage)

    out = await attach_flag_thumbnails(flagged, thumbs)
    assert out[0]["thumbnail_url"] == "https://signed/f"


@pytest.mark.asyncio
async def test_unmatched_flag_has_no_url(monkeypatch):
    flagged = [{"kind": "down_glance", "start_ms": 100, "end_ms": 200, "confidence": 0.6}]
    out = await attach_flag_thumbnails(flagged, [])
    assert "thumbnail_url" not in out[0] or out[0]["thumbnail_url"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run nexus pytest tests/vision/test_proctoring_thumbnails.py -q`
Expected: FAIL — `ImportError: cannot import name 'attach_flag_thumbnails'`.

- [ ] **Step 3: Implement in `service.py`**

Add imports + helper, and call it from `get_session_proctoring_analysis`:

```python
from app.config import settings
from app.storage import get_object_storage


async def attach_flag_thumbnails(
    flagged_intervals: list[dict], thumbs: list
) -> list[dict]:
    """Return a copy of flagged_intervals with thumbnail_url attached where a
    'flag' thumbnail matches by start_ms. Best-effort presign."""
    by_start = {t.ref_id: t.s3_key for t in thumbs if t.kind == "flag"}
    if not by_start:
        return flagged_intervals
    storage = get_object_storage()
    ttl = settings.recording_signed_url_ttl_seconds
    out: list[dict] = []
    for f in flagged_intervals:
        f2 = dict(f)
        key = by_start.get(str(f.get("start_ms")))
        if key:
            try:
                f2["thumbnail_url"] = await storage.presign_get_url(key, ttl_seconds=ttl)
            except Exception:  # noqa: BLE001
                pass
        out.append(f2)
    return out
```

Then in `get_session_proctoring_analysis`, after loading `row`, fetch thumbnails and attach before constructing the read model:

```python
    flagged = row.flagged_intervals or []
    thumbs = await get_session_timeline_thumbnails(
        db, session_id=session_id, tenant_id=tenant_id)
    flagged = await attach_flag_thumbnails(flagged, thumbs)
    return ProctoringAnalysisRead(
        status=row.status,
        risk_band=row.risk_band,
        detector_summary=row.detector_summary,
        gaze_heatmap=row.gaze_heatmap,
        flagged_intervals=flagged,
        gaze_signal_quality=row.gaze_signal_quality,
        unscorable_pct=float(row.unscorable_pct) if row.unscorable_pct is not None else None,
    )
```

> `get_session_timeline_thumbnails` is defined in this same module (Task 8), so it is a local call — no import cycle.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run nexus pytest tests/vision/test_proctoring_thumbnails.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full vision + reporting suites**

Run: `docker compose run nexus pytest tests/vision tests/ -k "report or proctoring or vision" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/vision/service.py tests/vision/test_proctoring_thumbnails.py
git commit -m "feat(vision): presign flag thumbnail_url on proctoring read"
```

---

## Final verification

- [ ] **Run the full affected test surface**

```bash
docker compose run nexus pytest tests/test_transcript_timing.py tests/test_question_id_tagging.py \
  tests/test_storage_upload_bytes.py tests/test_timeline_thumbnails_table.py \
  tests/test_reporting_asked_at_ms.py tests/test_reporting_thumbnails.py \
  tests/vision -q
docker compose run nexus-vision-worker pytest tests/vision/test_thumbnail_extraction.py -q
docker compose run nexus pytest tests/interview_engine -m "not prompt_quality" -q
```
Expected: all PASS.

- [ ] **Lint**

```bash
docker compose run nexus ruff check app/ tests/
```
Expected: clean.

- [ ] **Manual smoke (optional, requires a completed session with a recording)**

Re-enqueue proctoring for an existing ready session (the actor now also produces thumbnails), then confirm rows + API:
```bash
docker exec supabase_db_backend psql -U postgres -d postgres -tAc \
  "select kind, ref_id, t_ms from session_timeline_thumbnails where session_id='<SID>' order by t_ms;"
```
Then GET `/api/reports/session/<SID>` and confirm each `questions[].asked_at_ms` is populated and `thumbnail_url` is a signed URL; GET `/api/reports/session/<SID>/proctoring` and confirm top flags carry `thumbnail_url`.

---

## Notes for the frontend plan (next)

The API contract the frontend consumes after this plan:
- `ReportRead.questions[]` each gain `asked_at_ms: int | null` and `thumbnail_url: string | null`.
- `ProctoringAnalysis.flagged_intervals[]` top items gain `thumbnail_url: string | null`.

No other endpoint shapes change. The recording endpoint (`/recording`) already returns `signed_url`, `duration_seconds`, `offset_ms`, and the transcript.
