# Public Recordings Share — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an external recruiter who received the shared report PDF watch the candidate's full session recording (ReviewTheater: video + proctoring + scores + transcript + decision) and the highlight reel, via a secure, unguessable, revocable public link — without a BinQle account.

**Architecture:** A 256-bit opaque capability token is minted during the existing email-share flow, stored only as a keyed HMAC hash on the `report_shares` row, and embedded in the PDF button URL (`{frontend_base_url}/recordings/<token>`). One public, unauthenticated backend endpoint resolves the token (bypass-RLS lookup → validate → tenant-scoped reads → fresh-presigned R2 URLs) and returns a single envelope. A new public Next route renders the existing ReviewTheater + ReelTheater, fed from that envelope via a small React context so the existing components and recruiter-app behavior stay untouched.

**Tech Stack:** FastAPI + asyncpg/SQLAlchemy + Alembic + Dramatiq (backend); Next.js 16 App Router + TanStack Query + Vitest (frontend). Spec: `docs/superpowers/specs/2026-06-16-public-recordings-share-design.md`.

**Conventions for this plan:**
- Backend tests run in the container against `projectx_test` (created via `create_all`, no RLS, `DB_RUNTIME_ROLE=""`). Backend test command: `docker compose run --rm nexus pytest <path> -v`. If the stack isn't up, `docker compose up -d` first.
- Frontend test command (from `frontend/app/`): `npm run test -- <path>`.
- Commit after every task. Branch is already `feat/public-recordings-share`.

---

## File Structure

**Backend (`backend/nexus/`)**
- `app/config.py` — MODIFY: add `recording_share_hmac_secret` + `recording_share_ttl_days` + validator.
- `app/modules/reporting/share_tokens.py` — CREATE: `generate_share_token()`, `hash_share_token()`.
- `app/modules/reporting/models.py` — MODIFY: 5 new `ReportShare` columns.
- `migrations/versions/0062_report_share_tokens.py` — CREATE: columns + partial unique index.
- `app/modules/reporting/assets.py` — CREATE: extracted `attach_question_thumbnails` + `attach_reference_photo` (DRY: shared by both routers).
- `app/modules/reporting/labels.py` — CREATE: `load_session_labels()` (candidate/job/stage names).
- `app/modules/session/recording.py` — MODIFY: add `reconcile: bool = True` to `get_session_recording_playback`.
- `app/modules/reporting/schemas.py` — MODIFY: add `PublicRecordingsEnvelope`.
- `app/modules/reporting/public_share.py` — CREATE: `resolve_share_token()` + `build_public_envelope()`.
- `app/modules/reporting/public_router.py` — CREATE: `GET /api/public/recordings/{token}`.
- `app/modules/reporting/actors.py` — MODIFY: mint token, set real `full_session_url`.
- `app/modules/reporting/router.py` — MODIFY: use shared `assets.py` helpers; add revoke endpoint.
- `app/middleware/auth.py` — MODIFY: add `/api/public/recordings/` to `_PUBLIC_PREFIXES`.
- `app/main.py` — MODIFY: register `public_recordings_router`.
- `.env.example` — MODIFY: document new secrets.
- `tests/test_public_recordings.py`, `tests/test_share_tokens.py` — CREATE.

**Frontend (`frontend/app/`)**
- `lib/api/reports.ts` — MODIFY: `PublicRecordingsEnvelope` type + `getPublicRecordings()` fetcher.
- `lib/hooks/public-playback-context.tsx` — CREATE: context + provider supplying pre-fetched recording/proctoring.
- `lib/hooks/use-session-recording.ts`, `lib/hooks/use-session-proctoring.ts` — MODIFY: consume context.
- `proxy.ts` — MODIFY: allowlist `/recordings/`.
- `next.config.ts` — MODIFY: `X-Robots-Tag: noindex` for `/recordings/*`.
- `app/recordings/[token]/page.tsx`, `loading.tsx`, `error.tsx` — CREATE.
- `components/dashboard/reports/PublicRecordingsView.tsx` — CREATE: landing + theater/reel modals.
- `tests/api/public-recordings.test.ts`, `tests/components/PublicRecordingsView.test.tsx`, `tests/proxy.test.ts` (extend) — CREATE/MODIFY.
- root `CLAUDE.md` — MODIFY: rate-limit table row.

---

## Task 1: Backend config — share-token secrets

**Files:**
- Modify: `backend/nexus/app/config.py` (after the `candidate_jwt_ttl_hours` block, ~line 85-99)
- Modify: `backend/nexus/.env.example`
- Test: `backend/nexus/tests/test_config_share_secret.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_config_share_secret.py`:

```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_share_secret_required_outside_test_env():
    with pytest.raises(ValidationError):
        Settings(environment="production", candidate_jwt_secret="x",
                 recording_share_hmac_secret="")


def test_share_secret_optional_in_test_env():
    s = Settings(environment="test", recording_share_hmac_secret="")
    assert s.recording_share_hmac_secret == ""
    assert s.recording_share_ttl_days == 365
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_config_share_secret.py -v`
Expected: FAIL — `recording_share_hmac_secret` is not a field yet (or no validator).

- [ ] **Step 3: Implement**

In `app/config.py`, immediately after the `candidate_jwt_ttl_hours` field + its validator (around line 99), add:

```python
    # Public recordings share — keys the HMAC hash of the opaque share token
    # embedded in the report PDF link (/recordings/<token>). Treat as a DB
    # credential. REQUIRED in non-test environments. Rotating it invalidates
    # all outstanding share links (they can be re-shared).
    recording_share_hmac_secret: str = ""

    # Lifetime of a public recordings share link. The link lives in a PDF that
    # may sit in an inbox indefinitely, so this is long; revocation is the kill
    # switch, not expiry.
    recording_share_ttl_days: int = 365

    @field_validator("recording_share_hmac_secret")
    @classmethod
    def _share_secret_required(cls, v: str, info) -> str:
        env = info.data.get("environment", "development")
        if not v and env != "test":
            raise ValueError(
                "RECORDING_SHARE_HMAC_SECRET is required (generate with: "
                "`openssl rand -hex 32`). It keys the HMAC hash of public "
                "recordings share tokens. Set ENVIRONMENT=test to skip in tests."
            )
        return v
```

In `.env.example`, add near the `CANDIDATE_JWT_SECRET` entry:

```bash
# Keys the HMAC hash of public recordings share tokens (the /recordings/<token>
# link in the shared report PDF). Generate: openssl rand -hex 32
RECORDING_SHARE_HMAC_SECRET=
# Public recordings share link lifetime in days (default 365).
RECORDING_SHARE_TTL_DAYS=365
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_config_share_secret.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example backend/nexus/tests/test_config_share_secret.py
git commit -m "feat(reporting): config secrets for public recordings share tokens"
```

---

## Task 2: Share-token helpers (generate + HMAC hash)

**Files:**
- Create: `backend/nexus/app/modules/reporting/share_tokens.py`
- Test: `backend/nexus/tests/test_share_tokens.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_share_tokens.py`:

```python
from app.modules.reporting.share_tokens import generate_share_token, hash_share_token


def test_generate_share_token_is_high_entropy_and_unique():
    a = generate_share_token()
    b = generate_share_token()
    assert a != b
    assert len(a) >= 40  # token_urlsafe(32) → ~43 chars
    assert a.isascii()


def test_hash_is_deterministic_and_not_plaintext():
    token = "fixed-token-value"
    h1 = hash_share_token(token)
    h2 = hash_share_token(token)
    assert h1 == h2
    assert h1 != token
    assert len(h1) == 64  # sha256 hex


def test_hash_differs_per_token():
    assert hash_share_token("a") != hash_share_token("b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_share_tokens.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `backend/nexus/app/modules/reporting/share_tokens.py`:

```python
"""Opaque capability tokens for the public recordings share link.

The plaintext token lives ONLY in the rendered PDF link. We store its keyed
HMAC-SHA256 hash on the report_shares row, so a DB-only leak yields no working
links. The 256-bit token is unguessable and unenumerable.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.config import settings


def generate_share_token() -> str:
    """A 256-bit URL-safe random token (~43 chars)."""
    return secrets.token_urlsafe(32)


def hash_share_token(token: str) -> str:
    """Keyed HMAC-SHA256 hex digest of the token (constant-time-comparable via
    indexed equality lookup)."""
    return hmac.new(
        settings.recording_share_hmac_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_share_tokens.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/share_tokens.py backend/nexus/tests/test_share_tokens.py
git commit -m "feat(reporting): share-token generate + HMAC hash helpers"
```

---

## Task 3: DB columns + migration 0062

**Files:**
- Modify: `backend/nexus/app/modules/reporting/models.py` (`ReportShare`, after `updated_at` ~line 122)
- Create: `backend/nexus/migrations/versions/0062_report_share_tokens.py`
- Test: `backend/nexus/tests/test_report_share_columns.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_report_share_columns.py`:

```python
from app.modules.reporting.models import ReportShare


def test_report_share_has_token_columns():
    cols = set(ReportShare.__table__.columns.keys())
    for c in ("share_token_hash", "share_expires_at", "revoked_at",
              "last_viewed_at", "view_count"):
        assert c in cols, f"missing column {c}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_report_share_columns.py -v`
Expected: FAIL — columns missing.

- [ ] **Step 3: Implement model + migration**

In `app/modules/reporting/models.py`, inside `class ReportShare`, after the `updated_at` column add:

```python
    # --- Public recordings share link (capability URL) ---
    share_token_hash: Mapped[str | None] = mapped_column(Text)
    share_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
```

Ensure `Integer` is imported at the top of `models.py` (add to the existing `from sqlalchemy import ...` line if absent).

Create `backend/nexus/migrations/versions/0062_report_share_tokens.py`:

```python
"""report share tokens — public recordings link columns

Revision ID: 0062_report_share_tokens
Revises: 0061_report_shares
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_report_share_tokens"
down_revision = "0061_report_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_shares", sa.Column("share_token_hash", sa.Text(), nullable=True))
    op.add_column("report_shares", sa.Column("share_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("report_shares", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("report_shares", sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "report_shares",
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # Partial unique index for O(1) token lookup; only hashed rows participate.
    op.create_index(
        "ix_report_shares_token_hash",
        "report_shares",
        ["share_token_hash"],
        unique=True,
        postgresql_where=sa.text("share_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_report_shares_token_hash", table_name="report_shares")
    op.drop_column("report_shares", "view_count")
    op.drop_column("report_shares", "last_viewed_at")
    op.drop_column("report_shares", "revoked_at")
    op.drop_column("report_shares", "share_expires_at")
    op.drop_column("report_shares", "share_token_hash")
```

- [ ] **Step 4: Run model test + apply migration**

Run: `docker compose run --rm nexus pytest tests/test_report_share_columns.py -v`
Expected: PASS.

Apply to the dev DB and verify up/down:
Run: `docker compose run --rm nexus alembic upgrade head`
Expected: applies `0062_report_share_tokens` with no error.
Run: `docker compose run --rm nexus alembic downgrade -1 && docker compose run --rm nexus alembic upgrade head`
Expected: clean down + re-up (rollback script verified).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/models.py backend/nexus/migrations/versions/0062_report_share_tokens.py backend/nexus/tests/test_report_share_columns.py
git commit -m "feat(reporting): migration 0062 — report_shares token columns"
```

---

## Task 4: Extract presign-asset helpers into `assets.py` (DRY)

**Files:**
- Create: `backend/nexus/app/modules/reporting/assets.py`
- Modify: `backend/nexus/app/modules/reporting/router.py` (replace `_attach_question_thumbnails`/`_attach_reference_photo` bodies with imports)
- Test: `backend/nexus/tests/test_reporting_assets_import.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_reporting_assets_import.py`:

```python
def test_assets_helpers_importable():
    from app.modules.reporting.assets import (
        attach_question_thumbnails,
        attach_reference_photo,
    )
    assert callable(attach_question_thumbnails)
    assert callable(attach_reference_photo)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_reporting_assets_import.py -v`
Expected: FAIL — `assets` module does not exist.

- [ ] **Step 3: Implement**

Create `backend/nexus/app/modules/reporting/assets.py` by moving the two helpers verbatim out of `router.py:79-128` (rename to public names, drop leading underscore):

```python
"""Best-effort presigning of report assets (timeline thumbnails + reference
photo). Shared by the authenticated reports router and the public share path."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.reporting.schemas import ReportRead
from app.modules.session.models import Session  # models cross-module carve-out
from app.modules.vision import get_session_timeline_thumbnails
from app.storage import get_object_storage


async def attach_question_thumbnails(
    *, db: AsyncSession, report: ReportRead, session_id: Any, tenant_id: Any
) -> None:
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


async def attach_reference_photo(
    *, db: AsyncSession, report: ReportRead, session_id: Any, tenant_id: Any
) -> None:
    try:
        sess = (await db.execute(
            select(Session).where(
                Session.id == session_id, Session.tenant_id == tenant_id)
        )).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return
    if not sess or not sess.reference_photo_key:
        return
    try:
        report.reference_photo_url = await get_object_storage().presign_get_url(
            sess.reference_photo_key,
            ttl_seconds=settings.recording_signed_url_ttl_seconds,
        )
    except Exception:  # noqa: BLE001
        return
```

In `router.py`: delete the two `_attach_*` function definitions (lines 79-128) and replace the call sites in `get_report_by_session` with the imported names:

```python
from app.modules.reporting.assets import (
    attach_question_thumbnails,
    attach_reference_photo,
)
```
and in `get_report_by_session`:
```python
    read = _row_to_read(row)
    await attach_question_thumbnails(
        db=db, report=read, session_id=session_id, tenant_id=tenant_id)
    await attach_reference_photo(
        db=db, report=read, session_id=session_id, tenant_id=tenant_id)
    return read.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify**

Run: `docker compose run --rm nexus pytest tests/test_reporting_assets_import.py tests/ -k "report" -v`
Expected: PASS — new import test passes and existing report endpoint tests still pass (no behavior change).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/assets.py backend/nexus/app/modules/reporting/router.py backend/nexus/tests/test_reporting_assets_import.py
git commit -m "refactor(reporting): extract presign-asset helpers into assets.py"
```

---

## Task 5: `load_session_labels` helper + recording read-only flag

**Files:**
- Create: `backend/nexus/app/modules/reporting/labels.py`
- Modify: `backend/nexus/app/modules/session/recording.py` (`get_session_recording_playback` signature)
- Test: `backend/nexus/tests/test_recording_reconcile_flag.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_recording_reconcile_flag.py`:

```python
import inspect

from app.modules.session.recording import get_session_recording_playback


def test_recording_playback_has_reconcile_flag():
    sig = inspect.signature(get_session_recording_playback)
    assert "reconcile" in sig.parameters
    assert sig.parameters["reconcile"].default is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_recording_reconcile_flag.py -v`
Expected: FAIL — no `reconcile` parameter.

- [ ] **Step 3: Implement**

In `app/modules/session/recording.py`, change the signature and guard the side effects:

```python
async def get_session_recording_playback(
    db: AsyncSession, *, session_id: UUID, tenant_id: UUID, reconcile: bool = True
) -> RecordingPlayback:
```
and replace the two side-effecting calls:
```python
    if reconcile:
        await _reconcile(db, sess)
        await _maybe_enqueue_vision(db, sess)
```

Create `backend/nexus/app/modules/reporting/labels.py`:

```python
"""Resolve human-facing labels (candidate / job / stage) for a session.

Shared by the public share envelope and the PDF share actor. Best-effort: any
missing link in the chain falls back to a generic label, never raises.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.jd.models import JobPosting
from app.modules.pipelines.models import JobPipelineStage
from app.modules.session.models import Session as SessionRow


async def load_session_labels(
    db: AsyncSession, *, session_id: UUID, tenant_id: UUID
) -> tuple[str, str, str]:
    """Return (candidate_name, job_title, stage_label)."""
    sess = (await db.execute(
        select(SessionRow).where(
            SessionRow.id == session_id, SessionRow.tenant_id == tenant_id)
    )).scalar_one_or_none()
    assignment = (await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.id == sess.assignment_id,
            CandidateJobAssignment.tenant_id == tenant_id)
    )).scalar_one_or_none() if sess else None
    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id,
                                Candidate.tenant_id == tenant_id)
    )).scalar_one_or_none() if assignment else None
    job = (await db.execute(
        select(JobPosting).where(JobPosting.id == assignment.job_posting_id,
                                 JobPosting.tenant_id == tenant_id)
    )).scalar_one_or_none() if assignment else None
    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == sess.stage_id,
                                       JobPipelineStage.tenant_id == tenant_id)
    )).scalar_one_or_none() if sess else None

    candidate_name = (candidate.name if candidate else None) or "Candidate"
    job_title = (job.title if job else None) or "Role"
    stage_label = (stage.name if stage else None) or "Interview"
    return candidate_name, job_title, stage_label
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_recording_reconcile_flag.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/session/recording.py backend/nexus/app/modules/reporting/labels.py backend/nexus/tests/test_recording_reconcile_flag.py
git commit -m "feat(reporting): session-label helper + read-only recording-playback flag"
```

---

## Task 6: Public envelope schema + `public_share.py` resolver/builder

**Files:**
- Modify: `backend/nexus/app/modules/reporting/schemas.py` (add `PublicRecordingsEnvelope`)
- Create: `backend/nexus/app/modules/reporting/public_share.py`
- Test: covered end-to-end in Task 8 (endpoint). Add a focused resolver unit test here.
- Test: `backend/nexus/tests/test_public_share_resolve.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_public_share_resolve.py`:

```python
import inspect

def test_public_share_exports():
    from app.modules.reporting.public_share import (
        resolve_share_token,
        build_public_envelope,
    )
    assert inspect.iscoroutinefunction(resolve_share_token)
    assert inspect.iscoroutinefunction(build_public_envelope)


def test_envelope_schema_fields():
    from app.modules.reporting.schemas import PublicRecordingsEnvelope
    fields = set(PublicRecordingsEnvelope.model_fields.keys())
    for f in ("candidate_name", "job_title", "stage_label",
              "report", "recording", "proctoring", "reel"):
        assert f in fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_public_share_resolve.py -v`
Expected: FAIL — module/schema missing.

- [ ] **Step 3: Implement**

In `app/modules/reporting/schemas.py`, add (import the referenced types at top of file):

```python
from app.modules.session.schemas import RecordingPlayback
from app.modules.vision.schemas import ProctoringAnalysisRead
from app.modules.reel.schemas import ReelPlayback


class PublicRecordingsEnvelope(BaseModel):
    """Everything the public /recordings/<token> page needs, in one payload."""
    candidate_name: str
    job_title: str
    stage_label: str
    report: ReportRead
    recording: RecordingPlayback
    proctoring: ProctoringAnalysisRead
    reel: ReelPlayback
```

> Note: confirm the exact class name for the recording schema. Run
> `grep -rn "class .*Playback\|class Recording" backend/nexus/app/modules/session/schemas.py`
> and use the actual exported name (the recording hook types it as `RecordingPlayback`).

Create `backend/nexus/app/modules/reporting/public_share.py`:

```python
"""Public recordings share — token resolution + envelope assembly.

Security: tokens are resolved on a bypass-RLS session (we don't know the tenant
until the row is read — mirrors candidate-session token resolution). Once the
row is found, the tenant_id from the row scopes every downstream read (explicit
WHERE tenant_id filters in the reused service functions). Any invalid / revoked
/ expired / unknown token resolves to None → the endpoint returns a uniform 404
(no enumeration oracle).
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reporting.assets import (
    attach_question_thumbnails,
    attach_reference_photo,
)
from app.modules.reporting.labels import load_session_labels
from app.modules.reporting.models import ReportShare, SessionReport
from app.modules.reporting.schemas import PublicRecordingsEnvelope
from app.modules.reporting.serialization import report_read_from_row
from app.modules.reporting.share_tokens import hash_share_token
from app.modules.reel.service import build_playback, check_eligibility, get_reel
from app.modules.session import get_session_recording_playback
from app.modules.vision import get_session_proctoring_analysis

_log = structlog.get_logger("reporting.public_share")


async def resolve_share_token(db: AsyncSession, token: str) -> ReportShare | None:
    """Look up a share by token hash and validate it. None on any failure."""
    if not token or len(token) > 256:
        return None
    token_hash = hash_share_token(token)
    share = (await db.execute(
        select(ReportShare).where(ReportShare.share_token_hash == token_hash)
    )).scalar_one_or_none()
    if share is None:
        return None
    if share.revoked_at is not None:
        return None
    if share.share_expires_at is not None and share.share_expires_at <= datetime.now(UTC):
        return None
    return share


async def build_public_envelope(
    db: AsyncSession, share: ReportShare
) -> PublicRecordingsEnvelope | None:
    """Assemble the full envelope for a validated share. None if the report is
    not ready (treated as 404 by the endpoint)."""
    tenant_id = share.tenant_id
    session_id = share.session_id

    report_row = (await db.execute(
        select(SessionReport).where(
            SessionReport.id == share.report_id,
            SessionReport.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if report_row is None or report_row.status != "ready":
        return None

    report = report_read_from_row(report_row)
    await attach_question_thumbnails(
        db=db, report=report, session_id=session_id, tenant_id=tenant_id)
    await attach_reference_photo(
        db=db, report=report, session_id=session_id, tenant_id=tenant_id)

    recording = await get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id, reconcile=False)
    proctoring = await get_session_proctoring_analysis(
        db, session_id=session_id, tenant_id=tenant_id)

    reel_row = await get_reel(db, session_id=session_id, tenant_id=tenant_id)
    eligible, reason = await check_eligibility(
        db, session_id=session_id, tenant_id=tenant_id)
    reel = await build_playback(reel_row, eligible=eligible, reason=reason)

    candidate_name, job_title, stage_label = await load_session_labels(
        db, session_id=session_id, tenant_id=tenant_id)

    return PublicRecordingsEnvelope(
        candidate_name=candidate_name,
        job_title=job_title,
        stage_label=stage_label,
        report=report,
        recording=recording,
        proctoring=proctoring,
        reel=reel,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_public_share_resolve.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/schemas.py backend/nexus/app/modules/reporting/public_share.py backend/nexus/tests/test_public_share_resolve.py
git commit -m "feat(reporting): public share envelope schema + token resolver/builder"
```

---

## Task 7: Public router + middleware allowlist + registration

**Files:**
- Create: `backend/nexus/app/modules/reporting/public_router.py`
- Modify: `backend/nexus/app/middleware/auth.py` (`_PUBLIC_PREFIXES`)
- Modify: `backend/nexus/app/main.py` (register router)
- Test: `backend/nexus/tests/test_public_recordings.py` (the end-to-end happy + 404 paths)

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_public_recordings.py`. Use the existing seeding fixtures/conventions from `tests/` (look at `tests/conftest.py` + an existing reporting test such as `tests/test_reporting_*` to seed a tenant + session + ready report + a `report_shares` row). The test must cover: valid token → 200 envelope; unknown/garbage/revoked/expired → uniform 404.

```python
import pytest
from datetime import UTC, datetime, timedelta

from app.modules.reporting.share_tokens import generate_share_token, hash_share_token

# Assumes a helper that seeds a ready report + session and returns its ids.
# Mirror the seeding already used in tests/test_reporting_*.py.


@pytest.mark.asyncio
async def test_valid_token_returns_envelope(client, seed_ready_report):
    ctx = seed_ready_report  # {tenant_id, session_id, report_id, share_id_factory}
    token = generate_share_token()
    await ctx["attach_token"](token=token,
                              expires_at=datetime.now(UTC) + timedelta(days=365))
    resp = await client.get(f"/api/public/recordings/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_name"]
    assert "report" in body and "recording" in body
    assert "proctoring" in body and "reel" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["does-not-exist", "x" * 300, "////"])
async def test_unknown_or_garbage_token_404(client, bad):
    resp = await client.get(f"/api/public/recordings/{bad}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoked_token_404(client, seed_ready_report):
    ctx = seed_ready_report
    token = generate_share_token()
    await ctx["attach_token"](token=token,
                              expires_at=datetime.now(UTC) + timedelta(days=365),
                              revoked=True)
    resp = await client.get(f"/api/public/recordings/{token}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_expired_token_404(client, seed_ready_report):
    ctx = seed_ready_report
    token = generate_share_token()
    await ctx["attach_token"](token=token,
                              expires_at=datetime.now(UTC) - timedelta(seconds=1))
    resp = await client.get(f"/api/public/recordings/{token}")
    assert resp.status_code == 404
```

> If no reusable report-seeding fixture exists, add one to this test file that
> inserts: a client (tenant), session, a `session_reports` row with
> `status="ready"`, and a `report_shares` row; `attach_token` sets
> `share_token_hash=hash_share_token(token)`, `share_expires_at`, and optionally
> `revoked_at`. Use a `get_bypass_session()` with `SET LOCAL app.bypass_rls='true'`.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_public_recordings.py -v`
Expected: FAIL — route 404s for everything (router not registered) / fixture errors.

- [ ] **Step 3: Implement**

Create `backend/nexus/app/modules/reporting/public_router.py`:

```python
"""Public, unauthenticated, token-gated recordings endpoint.

GET /api/public/recordings/{token} — resolve an opaque capability token to a
session's full playback envelope (report + recording + proctoring + reel).

This router's prefix is allowlisted in app/middleware/auth.py::_PUBLIC_PREFIXES,
so no Supabase JWT is required. Tenant isolation is enforced by resolving the
tenant from the token's row and scoping every downstream read by that tenant_id
on a bypass-RLS session (the interview_runtime defense pattern).

Rate limiting: this codebase enforces limits via global middleware only; the
intended 'public share' class (30/min per-IP + 60/hour per-token) is documented
in the design spec + root CLAUDE.md.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.database import get_bypass_session
from app.modules.reporting.models import ReportShare
from app.modules.reporting.public_share import (
    build_public_envelope,
    resolve_share_token,
)

router = APIRouter(prefix="/api/public/recordings", tags=["public-recordings"])
_log = structlog.get_logger("reporting.public_router")


@router.get("/{token}", summary="Public recordings playback by share token")
async def get_public_recordings(token: str) -> Any:
    async with get_bypass_session() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        share = await resolve_share_token(db, token)
        if share is None:
            raise HTTPException(status_code=404, detail="Not found")

        envelope = await build_public_envelope(db, share)
        if envelope is None:
            raise HTTPException(status_code=404, detail="Not found")

        # Best-effort view tracking — must never block or fail the response.
        try:
            share.view_count = (share.view_count or 0) + 1
            share.last_viewed_at = datetime.now(UTC)
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()

        _log.info("reporting.public_recordings.served",
                  share_id=str(share.id), session_id=str(share.session_id))
        return envelope.model_dump(mode="json")
```

In `app/middleware/auth.py`, add the prefix to `_PUBLIC_PREFIXES`:

```python
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/verify-invite",
    "/api/auth/accept-invite",
    "/api/auth/login",
    "/api/public/recordings/",  # Public — opaque share token verified in-handler
)
```

In `app/main.py`, register the router near the other reporting routers (after line ~374):

```python
    from app.modules.reporting.public_router import router as public_recordings_router
    ...
    application.include_router(public_recordings_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_public_recordings.py -v`
Expected: PASS (all cases — 200 + uniform 404s).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/public_router.py backend/nexus/app/middleware/auth.py backend/nexus/app/main.py backend/nexus/tests/test_public_recordings.py
git commit -m "feat(reporting): public token-gated recordings endpoint"
```

---

## Task 8: Mint token in the share actor + real PDF URL

**Files:**
- Modify: `backend/nexus/app/modules/reporting/actors.py` (`_share_report_pdf_async`, line ~450)
- Test: `backend/nexus/tests/test_share_actor_token.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_share_actor_token.py`. Seed a ready report + a pending `report_shares` row, run `_share_report_pdf_async`, assert the row now has a non-null `share_token_hash` and `share_expires_at`, and that the PDF context received a `/recordings/<token>` URL (assert via monkeypatching `build_pdf_context` to capture `full_session_url`).

```python
import pytest
from app.modules.reporting import actors as share_actors


@pytest.mark.asyncio
async def test_share_actor_mints_token_and_sets_url(seed_pending_share, monkeypatch):
    ctx = seed_pending_share  # {tenant_id, share_id}
    captured = {}

    real_build = share_actors.build_pdf_context
    def _capture(*args, **kwargs):
        captured["full_session_url"] = kwargs.get("full_session_url")
        return real_build(*args, **kwargs)
    monkeypatch.setattr(share_actors, "build_pdf_context", _capture)
    # Stub the heavy render + email + R2 so the test stays unit-level.
    monkeypatch.setattr(share_actors, "render_report_pdf", _async_return(b"%PDF-1.4"))
    # ... stub get_object_storage().upload_bytes and send_email per existing
    #     share-actor test patterns in tests/test_reporting_share*.py

    await share_actors._share_report_pdf_async(
        ctx["share_id"], ctx["tenant_id"], "corr-1")

    assert "/recordings/" in captured["full_session_url"]
    # row got a hash + expiry
    row = await ctx["reload_share"]()
    assert row.share_token_hash is not None
    assert row.share_expires_at is not None
```

> Reuse the stubbing approach already present in the existing share-actor tests
> (`grep -rln "_share_report_pdf_async\|render_report_pdf" backend/nexus/tests`).
> `_async_return` is a tiny helper returning an async function that yields a value.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_share_actor_token.py -v`
Expected: FAIL — URL is still the `/shared-session/coming-soon` stub; hash is None.

- [ ] **Step 3: Implement**

In `app/modules/reporting/actors.py`, add imports near the top:

```python
from datetime import timedelta  # if not already imported
from app.modules.reporting.share_tokens import generate_share_token, hash_share_token
```

Replace line 450 (`full_session_url = f"{settings.frontend_base_url}/shared-session/coming-soon"`) with:

```python
            share_token = generate_share_token()
            share.share_token_hash = hash_share_token(share_token)
            share.share_expires_at = datetime.now(UTC) + timedelta(
                days=settings.recording_share_ttl_days)
            full_session_url = f"{settings.frontend_base_url}/recordings/{share_token}"
```

(The `share.status = "rendering"` + `await db.flush()` that follows persists these
column writes before render; the existing `await db.commit()` at the end durably
saves them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_share_actor_token.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/actors.py backend/nexus/tests/test_share_actor_token.py
git commit -m "feat(reporting): mint share token in PDF actor, set real recordings URL"
```

---

## Task 9: Authenticated revoke endpoint

**Files:**
- Modify: `backend/nexus/app/modules/reporting/router.py` (add revoke route + audit action)
- Test: `backend/nexus/tests/test_share_revoke.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_share_revoke.py`: seed a tenant user with `reports.view`, a ready report, and a share row with a token. Assert: (a) revoke as authorized user → 200 and `revoked_at` set; (b) a subsequent public GET with that token → 404; (c) revoke without `reports.view` → 403.

```python
import pytest
from datetime import UTC, datetime, timedelta
from app.modules.reporting.share_tokens import generate_share_token


@pytest.mark.asyncio
async def test_revoke_then_public_404(client, auth_headers, seed_ready_report):
    ctx = seed_ready_report
    token = generate_share_token()
    share_id = await ctx["attach_token"](
        token=token, expires_at=datetime.now(UTC) + timedelta(days=365))

    r = await client.post(
        f"/api/reports/session/{ctx['session_id']}/shares/{share_id}/revoke",
        headers=auth_headers)
    assert r.status_code == 200

    pub = await client.get(f"/api/public/recordings/{token}")
    assert pub.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_share_revoke.py -v`
Expected: FAIL — revoke route does not exist (404/405).

- [ ] **Step 3: Implement**

In `app/modules/reporting/router.py`, add (near the share endpoint):

```python
@router.post(
    "/session/{session_id}/shares/{share_id}/revoke",
    summary="Revoke a public recordings share link",
)
async def revoke_share(
    session_id: uuid_mod.UUID,
    share_id: uuid_mod.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Set revoked_at on a share row → its public link stops working immediately.
    RBAC: reports.view or super-admin (same set that can create the share)."""
    _require_reports_view(user)
    tenant_id = user.user.tenant_id

    share = (await db.execute(
        select(ReportShare).where(
            ReportShare.id == share_id,
            ReportShare.session_id == session_id,
            ReportShare.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
        await db.flush()
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=user.user.id,
            actor_email=user.user.email,
            action="session_report.share_revoked",
            resource="session_report",
            resource_id=share.report_id,
            payload={"share_id": str(share.id),
                     "correlation_id": _get_correlation_id(request)},
        )
    return {"share_id": str(share.id), "revoked": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_share_revoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/router.py backend/nexus/tests/test_share_revoke.py
git commit -m "feat(reporting): authenticated revoke endpoint for share links"
```

---

## Task 10: Frontend — public envelope type + fetcher

**Files:**
- Modify: `frontend/app/lib/api/reports.ts` (add type + `getPublicRecordings`)
- Test: `frontend/app/tests/api/public-recordings.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/api/public-recordings.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'
import { reportsApi } from '@/lib/api/reports'

afterEach(() => vi.restoreAllMocks())

describe('reportsApi.getPublicRecordings', () => {
  it('GETs the public endpoint with NO Authorization header', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        candidate_name: 'A', job_title: 'R', stage_label: 'S',
        report: {}, recording: { status: 'ready' }, proctoring: { status: 'absent' },
        reel: { status: 'absent' },
      }), { status: 200, headers: { 'content-type': 'application/json' } }),
    )
    const env = await reportsApi.getPublicRecordings('tok123')
    expect(env.candidate_name).toBe('A')
    const init = fetchSpy.mock.calls[0][1] as RequestInit
    const headers = new Headers(init?.headers)
    expect(headers.has('authorization')).toBe(false)
    expect(String(fetchSpy.mock.calls[0][0])).toContain('/api/public/recordings/tok123')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/app/`): `npm run test -- tests/api/public-recordings.test.ts`
Expected: FAIL — `getPublicRecordings` is not defined.

- [ ] **Step 3: Implement**

In `frontend/app/lib/api/reports.ts`, add the type (reuse existing `ReportRead`, `RecordingPlayback`, `ProctoringAnalysis` already in this file; import `ReelPlayback` from `./reels`) and fetcher. `apiFetch` only adds `Authorization` when a `token` is passed, so omitting `token` yields an unauthenticated request:

```typescript
import type { ReelPlayback } from './reels'

export interface PublicRecordingsEnvelope {
  candidate_name: string
  job_title: string
  stage_label: string
  report: ReportRead
  recording: RecordingPlayback
  proctoring: ProctoringAnalysis
  reel: ReelPlayback
}
```
and inside the `reportsApi` object:
```typescript
  /** GET /api/public/recordings/{token} — public, no auth. 404 → throws ApiError. */
  getPublicRecordings: (
    token: string,
    opts?: { signal?: AbortSignal },
  ): Promise<PublicRecordingsEnvelope> =>
    apiFetch<PublicRecordingsEnvelope>(
      `/api/public/recordings/${token}`,
      { signal: opts?.signal },
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/api/public-recordings.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/api/reports.ts frontend/app/tests/api/public-recordings.test.ts
git commit -m "feat(reports): public recordings envelope type + unauthenticated fetcher"
```

---

## Task 11: Frontend — public playback context + context-aware hooks

**Files:**
- Create: `frontend/app/lib/hooks/public-playback-context.tsx`
- Modify: `frontend/app/lib/hooks/use-session-recording.ts`
- Modify: `frontend/app/lib/hooks/use-session-proctoring.ts`
- Test: `frontend/app/tests/hooks/public-playback.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/hooks/public-playback.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PublicPlaybackProvider } from '@/lib/hooks/public-playback-context'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(() => {
    throw new Error('must NOT fetch a Supabase token on the public page')
  }),
}))

it('returns context-provided recording without hitting the authed path', async () => {
  const recording = { status: 'ready', signed_url: 'https://r2/x', offset_ms: 0,
                       duration_seconds: 120, transcript: [] }
  const qc = new QueryClient()
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>
      <PublicPlaybackProvider value={{ recording: recording as never, proctoring: { status: 'absent' } as never }}>
        {children}
      </PublicPlaybackProvider>
    </QueryClientProvider>
  )
  const { result } = renderHook(() => useSessionRecording('sess-1'), { wrapper })
  await waitFor(() => expect(result.current.data?.status).toBe('ready'))
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/hooks/public-playback.test.tsx`
Expected: FAIL — context module missing.

- [ ] **Step 3: Implement**

Create `frontend/app/lib/hooks/public-playback-context.tsx`:

```tsx
'use client'

import { createContext, useContext } from 'react'

import type { ProctoringAnalysis, RecordingPlayback } from '@/lib/api/reports'

/**
 * Pre-fetched playback data for the PUBLIC /recordings/<token> page. When this
 * context is present, the recording/proctoring hooks return it directly instead
 * of calling the authenticated /api/reports endpoints (which require a Supabase
 * token the external recruiter does not have). Absent in the recruiter app →
 * hooks behave normally.
 */
export interface PublicPlaybackData {
  recording: RecordingPlayback
  proctoring: ProctoringAnalysis
}

const PublicPlaybackContext = createContext<PublicPlaybackData | null>(null)

export function PublicPlaybackProvider({
  value,
  children,
}: {
  value: PublicPlaybackData
  children: React.ReactNode
}) {
  return (
    <PublicPlaybackContext.Provider value={value}>
      {children}
    </PublicPlaybackContext.Provider>
  )
}

export function usePublicPlayback(): PublicPlaybackData | null {
  return useContext(PublicPlaybackContext)
}
```

Modify `frontend/app/lib/hooks/use-session-recording.ts` to short-circuit on context:

```typescript
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type RecordingPlayback } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { usePublicPlayback } from '@/lib/hooks/public-playback-context'

export function useSessionRecording(sessionId: string): UseQueryResult<RecordingPlayback> {
  const pub = usePublicPlayback()
  return useQuery<RecordingPlayback>({
    queryKey: pub ? ['public-recording', sessionId] : ['session-recording', sessionId],
    queryFn: async ({ signal }) => {
      if (pub) return pub.recording
      const token = await getFreshSupabaseToken()
      return reportsApi.getRecording(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    // No polling on the public page — the recording is always final post-share.
    refetchInterval: (q) =>
      !pub && q.state.data?.status === 'recording' ? 5000 : false,
    refetchOnWindowFocus: !pub,
  })
}
```

Modify `frontend/app/lib/hooks/use-session-proctoring.ts` the same way:

```typescript
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type ProctoringAnalysis } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { usePublicPlayback } from '@/lib/hooks/public-playback-context'

export function useSessionProctoring(sessionId: string): UseQueryResult<ProctoringAnalysis> {
  const pub = usePublicPlayback()
  return useQuery<ProctoringAnalysis>({
    queryKey: pub ? ['public-proctoring', sessionId] : ['session-proctoring', sessionId],
    queryFn: async ({ signal }) => {
      if (pub) return pub.proctoring
      const token = await getFreshSupabaseToken()
      return reportsApi.getProctoring(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    refetchInterval: (q) =>
      !pub && (q.state.data?.status === 'pending' || q.state.data?.status === 'running')
        ? 5000
        : false,
    refetchOnWindowFocus: !pub,
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/hooks/public-playback.test.tsx`
Expected: PASS.

Also run the existing recording/proctoring hook tests (if any) to confirm no regression:
Run: `npm run test -- tests/hooks`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/hooks/public-playback-context.tsx frontend/app/lib/hooks/use-session-recording.ts frontend/app/lib/hooks/use-session-proctoring.ts frontend/app/tests/hooks/public-playback.test.tsx
git commit -m "feat(reports): context-aware playback hooks for the public recordings page"
```

---

## Task 12: Frontend — proxy allowlist + noindex header

**Files:**
- Modify: `frontend/app/proxy.ts` (allow `/recordings/` before the auth gate)
- Modify: `frontend/app/next.config.ts` (`X-Robots-Tag: noindex` for `/recordings/*`)
- Test: `frontend/app/tests/proxy.test.ts` (extend; this file is on the 100%-branch gate)

- [ ] **Step 1: Write the failing test**

In `frontend/app/tests/proxy.test.ts` (create if absent; otherwise extend), assert that a request to `/recordings/<token>` is allowed through without a Supabase user and without redirecting to `/login`. Follow the existing test harness pattern used for the `/invite` and `/login` cases (mock `createServerClient` so `getUser()` returns no user).

```typescript
import { describe, expect, it, vi } from 'vitest'

// Mock @supabase/ssr so proxy() runs with NO authenticated user.
vi.mock('@supabase/ssr', () => ({
  createServerClient: () => ({
    auth: {
      getUser: async () => ({ data: { user: null }, error: null }),
      getSession: async () => ({ data: { session: null } }),
    },
  }),
}))

import { proxy } from '@/proxy'
import { NextRequest } from 'next/server'

it('lets /recordings/<token> through unauthenticated (no redirect to /login)', async () => {
  const req = new NextRequest(new URL('http://localhost:3000/recordings/abc123'))
  const res = await proxy(req)
  // Pass-through response, NOT a 307/308 redirect to /login.
  expect(res.headers.get('location')).toBeNull()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/proxy.test.ts`
Expected: FAIL — current proxy redirects the unauthenticated request to `/login`.

- [ ] **Step 3: Implement**

In `frontend/app/proxy.ts`, add a public allowance right after the `/invite` block (around line 69, BEFORE the `getUser`-based gate):

```typescript
  // Public recordings share page — token-gated by the backend, no Supabase
  // session. Must bypass the auth gate (the page calls the public API directly).
  if (path.startsWith("/recordings/")) {
    return supabaseResponse;
  }
```

In `frontend/app/next.config.ts`, add a `headers()` entry (merge into the existing returned array) so the public page is never indexed:

```typescript
      {
        source: "/recordings/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }],
      },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/proxy.test.ts`
Expected: PASS. Then confirm the file's branch coverage gate still holds:
Run: `npm run test -- tests/proxy.test.ts --coverage` (or the project's coverage command) and confirm 100% branch on `proxy.ts`.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/proxy.ts frontend/app/next.config.ts frontend/app/tests/proxy.test.ts
git commit -m "feat(app): allow public /recordings/* through proxy + noindex header"
```

---

## Task 13: Frontend — public page + view component

**Files:**
- Create: `frontend/app/app/recordings/[token]/page.tsx`
- Create: `frontend/app/app/recordings/[token]/loading.tsx`
- Create: `frontend/app/app/recordings/[token]/error.tsx`
- Create: `frontend/app/components/dashboard/reports/PublicRecordingsView.tsx`
- Test: `frontend/app/tests/components/PublicRecordingsView.test.tsx`

> Before writing the route files, consult `node_modules/next/dist/docs/` for the
> Next.js 16 App Router page/params conventions — this version differs from
> training-data Next. In particular, confirm whether `params` is a Promise that
> must be awaited in the page server component.

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/PublicRecordingsView.test.tsx`. Mock `reportsApi.getPublicRecordings` and assert: (a) renders the candidate name + role; (b) a "Watch full session" trigger exists; (c) when `reel.status==='ready'` a reel trigger exists, and when `'absent'` it does not.

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api/reports', async (orig) => {
  const mod = await orig<typeof import('@/lib/api/reports')>()
  return {
    ...mod,
    reportsApi: {
      ...mod.reportsApi,
      getPublicRecordings: vi.fn(async () => ({
        candidate_name: 'Jane Doe', job_title: 'FDE', stage_label: 'New Stage',
        report: { session_id: 's1', questions: [], reference_photo_url: null,
                  verdict: 'advance' },
        recording: { status: 'ready', signed_url: 'https://r2/v', offset_ms: 0,
                     duration_seconds: 60, transcript: [] },
        proctoring: { status: 'absent' },
        reel: { status: 'absent', signed_url: null, chapters: [],
                duration_seconds: null, eligible: false, ineligible_reason: null,
                version: 0, generation_error: null, expires_at: null },
      })),
    },
  }
})

import { PublicRecordingsView } from '@/components/dashboard/reports/PublicRecordingsView'

function renderView() {
  const qc = new QueryClient()
  return render(
    <QueryClientProvider client={qc}>
      <PublicRecordingsView token="tok" />
    </QueryClientProvider>,
  )
}

it('renders candidate identity and a watch-session trigger', async () => {
  renderView()
  await waitFor(() => expect(screen.getByText('Jane Doe')).toBeInTheDocument())
  expect(screen.getByText(/FDE/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /full session/i })).toBeInTheDocument()
})

it('hides the reel trigger when the reel is absent', async () => {
  renderView()
  await waitFor(() => expect(screen.getByText('Jane Doe')).toBeInTheDocument())
  expect(screen.queryByRole('button', { name: /highlight reel/i })).toBeNull()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/PublicRecordingsView.test.tsx`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement**

Create `frontend/app/components/dashboard/reports/PublicRecordingsView.tsx`:

```tsx
'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { BrandLogo, Button, Skeleton } from '@/components/px'
import { reportsApi } from '@/lib/api/reports'
import { PublicPlaybackProvider } from '@/lib/hooks/public-playback-context'
import { ReviewTheater } from './theater/ReviewTheater'
import { ReelTheater } from './theater/ReelTheater'

/**
 * Public, token-gated playback surface. Fetches the full envelope from the
 * public API (no auth), then lets an external recruiter watch the full session
 * (ReviewTheater: video + proctoring + scores + transcript + decision) and the
 * highlight reel. The recruiter REPORT page stays private — this only plays back
 * the videos a shared PDF points at.
 */
export function PublicRecordingsView({ token }: { token: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public-recordings', token],
    queryFn: ({ signal }) => reportsApi.getPublicRecordings(token, { signal }),
    retry: false,
  })
  const [theaterOpen, setTheaterOpen] = useState(false)
  const [reelOpen, setReelOpen] = useState(false)

  if (isLoading) {
    return <div className="mx-auto max-w-2xl p-10"><Skeleton className="h-64 w-full" /></div>
  }
  if (isError || !data) {
    return (
      <div className="mx-auto max-w-md p-10 text-center">
        <BrandLogo className="mx-auto mb-6 h-8" />
        <h1 className="text-lg font-semibold">This link is no longer available</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The shared recording link may have expired or been revoked. Ask your
          contact to share it again.
        </p>
      </div>
    )
  }

  const reelReady = data.reel.status === 'ready'
  const subtitle = `${data.job_title} · ${data.stage_label}`

  return (
    <PublicPlaybackProvider
      value={{ recording: data.recording, proctoring: data.proctoring }}
    >
      <div className="mx-auto max-w-2xl p-8">
        <BrandLogo className="mb-8 h-8" />
        <div className="rounded-2xl border bg-white p-8 shadow-sm">
          {data.report.reference_photo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={data.report.reference_photo_url}
              alt={data.candidate_name}
              className="mb-4 h-20 w-20 rounded-full object-cover"
            />
          ) : null}
          <h1 className="text-xl font-semibold">{data.candidate_name}</h1>
          <p className="text-sm text-muted-foreground">{subtitle}</p>

          <div className="mt-6 flex flex-col gap-3">
            <Button onClick={() => setTheaterOpen(true)}>
              ▶ Watch full session
            </Button>
            {reelReady ? (
              <Button variant="secondary" onClick={() => setReelOpen(true)}>
                ✨ Watch highlight reel
              </Button>
            ) : null}
          </div>
        </div>
      </div>

      <ReviewTheater
        open={theaterOpen}
        report={data.report}
        candidateName={data.candidate_name}
        subtitle={subtitle}
        onClose={() => setTheaterOpen(false)}
      />
      {reelReady ? (
        <ReelTheater
          open={reelOpen}
          signedUrl={data.reel.signed_url}
          chapters={data.reel.chapters}
          durationSeconds={data.reel.duration_seconds}
          candidateName={data.candidate_name}
          onClose={() => setReelOpen(false)}
        />
      ) : null}
    </PublicPlaybackProvider>
  )
}
```

> Confirm `Button`'s `variant="secondary"` is a valid `ButtonVariant` in
> `components/px/Button.tsx`; if not, use an existing variant. Confirm `BrandLogo`
> accepts `className`. Adjust the `report` field references (`reference_photo_url`,
> `verdict`) to the actual `ReportRead` shape in `lib/api/reports.ts`.

Create `frontend/app/app/recordings/[token]/page.tsx` (verify the Next 16 params
convention first — in current Next, `params` is async):

```tsx
import { PublicRecordingsView } from '@/components/dashboard/reports/PublicRecordingsView'

export const metadata = {
  robots: { index: false, follow: false },
}

export default async function PublicRecordingsPage({
  params,
}: {
  params: Promise<{ token: string }>
}) {
  const { token } = await params
  return <PublicRecordingsView token={token} />
}
```

Create `frontend/app/app/recordings/[token]/loading.tsx`:

```tsx
import { Skeleton } from '@/components/px'

export default function Loading() {
  return <div className="mx-auto max-w-2xl p-10"><Skeleton className="h-64 w-full" /></div>
}
```

Create `frontend/app/app/recordings/[token]/error.tsx`:

```tsx
'use client'

export default function Error() {
  return (
    <div className="mx-auto max-w-md p-10 text-center">
      <h1 className="text-lg font-semibold">This link is no longer available</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The shared recording link may have expired or been revoked.
      </p>
    </div>
  )
}
```

- [ ] **Step 4: Run tests + build to verify**

Run: `npm run test -- tests/components/PublicRecordingsView.test.tsx`
Expected: PASS (both cases).
Run: `npm run type-check && npm run lint`
Expected: zero errors.
Run: `npm run build`
Expected: build succeeds, `/recordings/[token]` route compiled.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/app/recordings frontend/app/components/dashboard/reports/PublicRecordingsView.tsx frontend/app/tests/components/PublicRecordingsView.test.tsx
git commit -m "feat(app): public /recordings/[token] page — ReviewTheater + reel"
```

---

## Task 14: Docs — rate-limit table + CLAUDE.md state

**Files:**
- Modify: root `CLAUDE.md` (Rate Limiting table — add public share row)
- Modify: `backend/nexus/CLAUDE.md` (migrations list → add `0062`; reporting state → mention public share)

- [ ] **Step 1: Update root CLAUDE.md rate-limit table**

In `CLAUDE.md` → "Rate Limiting & Abuse Posture", add a row:

```
| `/api/public/recordings/*` | 30/min | 60/hour per token |
```

Add a one-line note under the table: the public recordings endpoint is the only
unauthenticated read of evaluation data; it is gated by a 256-bit revocable token
and returns a uniform 404 on any invalid/expired/revoked token.

- [ ] **Step 2: Update backend CLAUDE.md**

In `backend/nexus/CLAUDE.md`:
- Migrations list: append `- 0062_report_share_tokens — adds report_shares token columns (share_token_hash + partial unique index, share_expires_at, revoked_at, last_viewed_at, view_count) for the public recordings share link.`
- Update the migration-head line from `0061_report_shares` to `0062_report_share_tokens`.
- In the "Report sharing (PDF)" bullet, append: "The PDF's 'See full session recording' button now points at the public `/recordings/<token>` page (opaque HMAC-hashed capability token minted in the share actor; resolved by the unauthenticated `GET /api/public/recordings/{token}` endpoint). Revoke via `POST /api/reports/session/{id}/shares/{share_id}/revoke`."

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md backend/nexus/CLAUDE.md
git commit -m "docs: rate-limit + state notes for public recordings share"
```

---

## Task 15: End-to-end manual smoke (no automated step)

**Goal:** confirm the full path works against the running stack.

- [ ] **Step 1: Ensure migration applied + worker restarted**

```bash
cd backend/nexus && docker compose run --rm nexus alembic upgrade head
docker compose restart nexus-pdf-worker   # PDF actor has NO hot-reload
docker compose up -d --force-recreate nexus
```
Set `RECORDING_SHARE_HMAC_SECRET` in the backend env (`openssl rand -hex 32`).

- [ ] **Step 2: Drive the flow**

1. From the recruiter app, open a completed session report and use "Share" → enter your email.
2. Open the received PDF; click "▶ See full session recording".
3. Confirm it lands on `http://localhost:3000/recordings/<token>` and shows the candidate card.
4. Click "Watch full session" → ReviewTheater plays the recording with scores/transcript/proctoring.
5. If the session has a ready reel, click "Watch highlight reel" → ReelTheater plays.

- [ ] **Step 3: Verify security properties**

1. Tamper one character of the token in the URL → expect the "no longer available" page (404 from API).
2. Revoke: `POST /api/reports/session/<sid>/shares/<share_id>/revoke` (authenticated) → reload the public link → "no longer available".
3. `curl -I http://localhost:3000/recordings/<token>` → confirm `X-Robots-Tag: noindex, nofollow`.

- [ ] **Step 4: Note results**

Record outcomes (pass/fail per check) in the PR/commit message. If anything fails,
debug before declaring complete (see superpowers:systematic-debugging).

---

## Self-Review Notes (author)

- **Spec coverage:** token model (T2,T8) ✓; DB columns + migration (T3) ✓; mint in share flow + real URL (T8) ✓; public endpoint with bypass→validate→tenant-scope→presign + uniform 404 (T6,T7) ✓; read-only recording (T5) ✓; full ReviewTheater + reel render (T13) ✓; revoke backend-only (T9) ✓; proxy allow + noindex (T12) ✓; rate-limit doc (T14) ✓; config secret (T1) ✓; tests across backend+frontend ✓; DRY asset/label extraction (T4,T5) ✓.
- **No oracle:** all invalid-token paths in `resolve_share_token`/endpoint return identical 404 (T6,T7).
- **Type consistency:** `PublicRecordingsEnvelope` fields identical across backend schema (T6) and frontend type (T10); `getPublicRecordings`, `usePublicPlayback`, `PublicPlaybackProvider` names consistent (T10,T11,T13).
- **Verify-before-fabricate flags** intentionally left as inline "confirm the actual name/shape" notes where a symbol couldn't be 100% pinned from reading (recording schema class name; `ButtonVariant`; `ReportRead` photo/verdict fields; Next 16 `params` await). Each is a quick grep, not a design gap.
