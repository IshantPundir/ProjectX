# Report Sharing (PDF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a recruiter share a session report with an external client as a beautiful, client-curated PDF delivered by email.

**Architecture:** A recruiter clicks "Share" on the report page → dialog → recipient email → `POST /api/reports/session/{id}/share`. The endpoint writes a `report_shares` row and enqueues an async Dramatiq actor on a new `report_share` queue. The actor reloads the row (no recipient PII in broker args), renders a purpose-built Jinja2 print template to PDF with **headless Chromium (Playwright)**, uploads the PDF to R2, and emails it as an attachment. A dedicated lightweight `nexus-pdf-worker` (its own Chromium+fonts image) runs the queue — the lean API/engine images stay Chromium-free.

**Tech Stack:** FastAPI + SQLAlchemy async + asyncpg, Dramatiq (AsyncIO middleware), Jinja2, Playwright/Chromium, Cloudflare R2 (S3-compatible), Resend (email). Frontend: Next.js 16, TanStack Query, in-house `px/` primitives, sonner toasts.

**Design spec:** `docs/superpowers/specs/2026-06-16-report-sharing-pdf-design.md`

**Note on worker placement:** The spec named the vision worker; this plan uses a dedicated `nexus-pdf-worker` instead because the vision worker is GPU-coupled (nvidia device reservation) and not always running in dev. PDF rendering needs no GPU.

---

## File Structure

**Backend (create):**
- `app/modules/reporting/pdf/__init__.py` — public surface (`render_report_pdf`, `build_pdf_html`, `build_pdf_context`)
- `app/modules/reporting/pdf/context.py` — pure helpers: verdict→stamp, monogram, assessed-dimension filter, context builder
- `app/modules/reporting/pdf/render.py` — Jinja render to HTML + Playwright HTML→PDF (lazy playwright import)
- `app/modules/reporting/pdf/templates/report.html.j2` — the print template
- `app/modules/reporting/serialization.py` — `report_read_from_row(row) -> ReportRead` (shared by router + actor)
- `app/modules/notifications/templates/report_share.html` — email cover note
- `app/pdf_worker.py` — Dramatiq entrypoint registering only the share actor
- `Dockerfile.pdf` — Chromium + fonts image for `nexus-pdf-worker`
- `migrations/versions/0061_report_shares.py`

**Backend (modify):**
- `app/modules/reporting/models.py` — add `ReportShare` ORM
- `app/modules/reporting/schemas.py` — add `ShareReportIn`, `ShareReportOut`
- `app/modules/reporting/actors.py` — add `share_report_pdf` actor
- `app/modules/reporting/router.py` — add `POST /session/{id}/share`; use `report_read_from_row`
- `app/modules/reporting/__init__.py` — export `ReportShare`, `share_report_pdf`
- `app/modules/notifications/service.py` — `send_email(..., attachments=...)`
- `app/main.py` — add `report_shares` to `_TENANT_SCOPED_TABLES`
- `pyproject.toml` — `[project.optional-dependencies] pdf = ["playwright>=1.44"]`
- `docker-compose.yml` — add `nexus-pdf-worker` service

**Frontend (modify):**
- `lib/api/reports.ts` — `reportsApi.share(...)` + types
- `lib/hooks/use-share-report.ts` — mutation hook (create)
- `components/dashboard/reports/ShareReportDialog.tsx` — dialog (create)
- `components/dashboard/reports/ReportTopBar.tsx` — Share button + dialog
- `app/(dashboard)/reports/session/[sessionId]/page.tsx` — pass `sessionId` to `ReportTopBar`

---

## Task 1: `report_shares` table — ORM + migration

**Files:**
- Modify: `app/modules/reporting/models.py`
- Create: `migrations/versions/0061_report_shares.py`
- Modify: `app/main.py:36` (`_TENANT_SCOPED_TABLES`)
- Test: `tests/reporting/test_report_shares_table.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_report_shares_table.py
from app.main import _TENANT_SCOPED_TABLES
from app.modules.reporting.models import ReportShare


def test_report_shares_is_tenant_scoped():
    assert "report_shares" in _TENANT_SCOPED_TABLES


def test_report_share_model_columns():
    cols = ReportShare.__table__.columns.keys()
    for expected in (
        "id", "tenant_id", "session_id", "report_id", "recipient_email",
        "status", "pdf_r2_key", "requested_by", "requested_at", "sent_at", "error",
    ):
        assert expected in cols
    assert ReportShare.__table__.name == "report_shares"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_report_shares_table.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReportShare'`

- [ ] **Step 3: Add the ORM model**

Append to `app/modules/reporting/models.py`:

```python
class ReportShare(Base):
    __tablename__ = "report_shares"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_email: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'pending'")
    )
    pdf_r2_key: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
```

- [ ] **Step 4: Register the table in the RLS completeness list**

In `app/main.py`, inside `_TENANT_SCOPED_TABLES`, after the `"session_reels",` line (the last entry), add:

```python
    # Report sharing — emailed PDF shares (migration 0061).
    "report_shares",
```

- [ ] **Step 5: Create the migration**

```python
# migrations/versions/0061_report_shares.py
"""report_shares — emailed PDF shares of a session report.

One row per share request. Canonical tenant_isolation + service_bypass RLS pair
(NULLIF discipline). Rollback: downgrade drops the table.

Revision ID: 0061
Revises: 0060
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061"
down_revision = "0060"
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
        "report_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("session_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_email", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("pdf_r2_key", sa.Text()),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.execute(
        "ALTER TABLE report_shares ADD CONSTRAINT report_shares_status_check "
        "CHECK (status IN ('pending','rendering','sent','failed'))"
    )
    op.create_index("ix_report_shares_session", "report_shares", ["session_id"])
    _enable_rls("report_shares")
    op.execute("""
        CREATE TRIGGER report_shares_touch_updated_at
            BEFORE UPDATE ON report_shares
            FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS report_shares_touch_updated_at ON report_shares;")
    op.drop_index("ix_report_shares_session", table_name="report_shares")
    op.drop_table("report_shares")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/test_report_shares_table.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Apply the migration to the dev DB**

Run: `docker compose run --rm nexus alembic upgrade head`
Expected: `Running upgrade 0060 -> 0061, report_shares`

- [ ] **Step 8: Commit**

```bash
git add app/modules/reporting/models.py app/main.py migrations/versions/0061_report_shares.py tests/reporting/test_report_shares_table.py
git commit -m "feat(reporting): report_shares table + RLS (migration 0061)"
```

---

## Task 2: Share schemas

**Files:**
- Modify: `app/modules/reporting/schemas.py`
- Test: `tests/reporting/test_share_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_share_schemas.py
import pytest
from pydantic import ValidationError

from app.modules.reporting.schemas import ShareReportIn, ShareReportOut


def test_share_in_accepts_valid_email():
    assert ShareReportIn(recipient_email="client@acme.com").recipient_email == "client@acme.com"


def test_share_in_rejects_bad_email():
    with pytest.raises(ValidationError):
        ShareReportIn(recipient_email="not-an-email")


def test_share_out_shape():
    out = ShareReportOut(share_id="abc", status="pending")
    assert out.model_dump() == {"share_id": "abc", "status": "pending"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'ShareReportIn'`

- [ ] **Step 3: Add the schemas**

In `app/modules/reporting/schemas.py`, add `EmailStr` to the pydantic import line (`from pydantic import BaseModel, EmailStr, Field`) and append:

```python
class ShareReportIn(BaseModel):
    recipient_email: EmailStr


class ShareReportOut(BaseModel):
    share_id: str
    status: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_schemas.py -v`
Expected: PASS (3 tests)

Note: `EmailStr` requires `email-validator`. If the import errors at collection with `email-validator is not installed`, add `"email-validator>=2.0"` to the `[project] dependencies` in `pyproject.toml`, then `docker compose build nexus`.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/schemas.py tests/reporting/test_share_schemas.py pyproject.toml
git commit -m "feat(reporting): ShareReportIn/Out schemas"
```

---

## Task 3: `send_email` attachment support

**Files:**
- Modify: `app/modules/notifications/service.py`
- Test: `tests/notifications/test_send_email_attachments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/notifications/test_send_email_attachments.py
import pytest

from app.modules.notifications import service as svc


class _CaptureProvider:
    def __init__(self):
        self.calls = []

    async def send(self, *, to, subject, html, attachments=None):
        self.calls.append({"to": to, "subject": subject, "attachments": attachments})


@pytest.mark.asyncio
async def test_send_email_forwards_attachments(monkeypatch):
    cap = _CaptureProvider()
    monkeypatch.setattr(svc, "_provider", cap)
    await svc.send_email(
        to="x@y.com", subject="s", html="<p>h</p>",
        attachments=[("report.pdf", b"%PDF-1.4 ...", "application/pdf")],
    )
    assert cap.calls[0]["attachments"] == [("report.pdf", b"%PDF-1.4 ...", "application/pdf")]


@pytest.mark.asyncio
async def test_send_email_attachments_default_none(monkeypatch):
    cap = _CaptureProvider()
    monkeypatch.setattr(svc, "_provider", cap)
    await svc.send_email(to="x@y.com", subject="s", html="<p>h</p>")
    assert cap.calls[0]["attachments"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/notifications/test_send_email_attachments.py -v`
Expected: FAIL — `send_email() got an unexpected keyword argument 'attachments'`

- [ ] **Step 3: Implement attachment support**

In `app/modules/notifications/service.py`:

Add a type alias under the imports (after line 21):

```python
# (filename, raw bytes, content_type) — e.g. ("report.pdf", b"...", "application/pdf")
Attachment = tuple[str, bytes, str]
```

Update the `EmailProvider` protocol:

```python
class EmailProvider(Protocol):
    """Provider-agnostic email interface."""
    async def send(
        self, *, to: str, subject: str, html: str,
        attachments: "list[Attachment] | None" = None,
    ) -> None: ...
```

Update `DryRunProvider.send`:

```python
    async def send(
        self, *, to: str, subject: str, html: str,
        attachments: "list[Attachment] | None" = None,
    ) -> None:
        invite_url = None
        if match := _INVITE_URL_RE.search(html):
            invite_url = match.group("url")
        otp_code = None
        if match := _OTP_CODE_RE.search(html):
            otp_code = match.group("code")

        logger.info(
            "email.dry_run",
            to=to,
            subject=subject,
            invite_url=invite_url,
            otp_code=otp_code,
            html_length=len(html),
            attachments=[(name, len(data)) for name, data, _ct in (attachments or [])],
        )
```

Update `ResendProvider.send`:

```python
    async def send(
        self, *, to: str, subject: str, html: str,
        attachments: "list[Attachment] | None" = None,
    ) -> None:
        import base64

        payload: dict = {"from": self._from, "to": to, "subject": subject, "html": html}
        if attachments:
            payload["attachments"] = [
                {
                    "filename": name,
                    "content": base64.b64encode(data).decode("ascii"),
                    "content_type": ct,
                }
                for name, data, ct in attachments
            ]
        await asyncio.to_thread(self._resend.Emails.send, payload)
```

Update `send_email`:

```python
async def send_email(
    *, to: str, subject: str, html: str,
    attachments: "list[Attachment] | None" = None,
) -> None:
    """Send an email. Business logic calls this — never import a provider directly."""
    try:
        await _provider.send(to=to, subject=subject, html=html, attachments=attachments)
        logger.info(
            "email.sent", to=to, subject=subject,
            attachment_count=len(attachments) if attachments else 0,
        )
    except Exception as exc:
        logger.error("email.failed", to=to, subject=subject, error=str(exc))
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/notifications/test_send_email_attachments.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the existing notifications suite to confirm no regressions**

Run: `docker compose run --rm nexus pytest tests/notifications -v`
Expected: PASS (all existing tests still green — callers passing no `attachments` are unaffected)

- [ ] **Step 6: Commit**

```bash
git add app/modules/notifications/service.py tests/notifications/test_send_email_attachments.py
git commit -m "feat(notifications): optional email attachments"
```

---

## Task 4: Share email template

**Files:**
- Create: `app/modules/notifications/templates/report_share.html`
- Test: `tests/notifications/test_report_share_template.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/notifications/test_report_share_template.py
from app.modules.notifications.service import render_template


def test_report_share_template_renders():
    html = render_template(
        "report_share.html",
        candidate_name="Ishant Pundir",
        job_title="Jr. Forward Deployed Engineer",
        shared_by="Acme Staffing",
    )
    assert "Ishant Pundir" in html
    assert "Jr. Forward Deployed Engineer" in html
    assert "attached" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/notifications/test_report_share_template.py -v`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: report_share.html`

- [ ] **Step 3: Create the template**

```html
<!-- app/modules/notifications/templates/report_share.html -->
<!DOCTYPE html>
<html>
  <body style="margin:0;background:#f6f6f9;font-family:Arial,Helvetica,sans-serif;color:#1c1c22">
    <div style="max-width:560px;margin:0 auto;padding:32px 24px">
      <div style="font-weight:800;font-size:18px;color:#4f46e5">BinQle<span style="color:#1c1c22">.ai</span></div>
      <div style="background:#fff;border-radius:12px;padding:28px 26px;margin-top:18px;box-shadow:0 2px 10px rgba(20,20,40,.06)">
        <h1 style="font-size:18px;margin:0 0 12px">Candidate evaluation enclosed</h1>
        <p style="font-size:14px;line-height:1.6;color:#3a3d45;margin:0 0 14px">
          {{ shared_by }} has shared an AI interview evaluation for
          <strong>{{ candidate_name }}</strong> ({{ job_title }}).
        </p>
        <p style="font-size:14px;line-height:1.6;color:#3a3d45;margin:0">
          The full report is <strong>attached</strong> as a PDF. It includes the verdict,
          score breakdown, a written rationale, strengths and concerns, and a
          question-by-question summary.
        </p>
      </div>
      <p style="font-size:11px;color:#9a9aa6;margin-top:18px">
        Confidential — intended only for the recipient. Powered by BinQle.ai.
      </p>
    </div>
  </body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/notifications/test_report_share_template.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add app/modules/notifications/templates/report_share.html tests/notifications/test_report_share_template.py
git commit -m "feat(notifications): report_share email template"
```

---

## Task 5: Shared report serializer

Extract the router's `_row_to_read` into a shared module so the actor can reuse it.

**Files:**
- Create: `app/modules/reporting/serialization.py`
- Modify: `app/modules/reporting/router.py`
- Test: `tests/reporting/test_serialization.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_serialization.py
from app.modules.reporting.models import SessionReport
from app.modules.reporting.serialization import report_read_from_row


def test_report_read_from_row_maps_columns():
    row = SessionReport(
        status="ready", engine_version="v3", version=1,
        verdict="advance", verdict_reason="Clears the bar",
        overall_score=90, overall_coverage=1.0, overall_confidence="high",
        dimension_scores={"overall": {"score": 90, "tier_label": "Strong",
                                      "tone": "ok", "confidence": "high", "coverage": 1.0}},
        summary={"quick_summary": "Solid screen.",
                 "decision": {"headline": "h",
                              "why_positive": {"title": "p", "body": "pb"},
                              "why_negative": {"title": "n", "body": "nb"}},
                 "strengths": [], "concerns": [],
                 "methodology": {"note": "", "charity_flags": []}},
        question_scorecards=[], signal_scorecards=[],
    )
    read = report_read_from_row(row)
    assert read.verdict == "advance"
    assert read.overall_score == 90
    assert read.quick_summary == "Solid screen."
    assert read.scores["overall"].score == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_serialization.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.reporting.serialization`

- [ ] **Step 3: Create the serializer**

Move the body of `_row_to_read` (router.py:72-119) into a new module verbatim:

```python
# app/modules/reporting/serialization.py
"""Shared SessionReport ORM row → ReportRead mapping.

Used by the reporting router (HTTP read) and the share actor (PDF render) so
both produce an identical recruiter-facing report shape.
"""
from __future__ import annotations

from app.modules.reporting.models import SessionReport
from app.modules.reporting.schemas import ReportRead


def report_read_from_row(row: SessionReport) -> ReportRead:
    """Assemble a ReportRead from a SessionReport ORM row.

    Column mapping (persist_report writes, this reads):
      dimension_scores    → scores
      summary             → decision, quick_summary, strengths, concerns, methodology
      question_scorecards → questions
      signal_scorecards   → signal_assessments
    """
    summary = row.summary or {}
    return ReportRead.model_validate(
        {
            "id": str(row.id) if row.id else None,
            "session_id": str(row.session_id) if row.session_id else None,
            "status": row.status,
            "engine_version": row.engine_version,
            "version": row.version,
            "verdict": row.verdict,
            "verdict_reason": row.verdict_reason,
            "overall_score": row.overall_score,
            "overall_coverage": (
                float(row.overall_coverage) if row.overall_coverage is not None else 0.0
            ),
            "overall_confidence": row.overall_confidence or "low",
            "decision": summary.get("decision") or {
                "headline": row.verdict_reason or "",
                "why_positive": {"title": "", "body": ""},
                "why_negative": {"title": "", "body": ""},
            },
            "scores": row.dimension_scores or {},
            "quick_summary": summary.get("quick_summary", ""),
            "strengths": summary.get("strengths", []),
            "concerns": summary.get("concerns", []),
            "questions": row.question_scorecards or [],
            "methodology": summary.get("methodology") or {"note": "", "charity_flags": []},
            "signal_assessments": row.signal_scorecards or [],
            "scoring_manifest": row.scoring_manifest,
            "human_decision": row.human_decision,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        }
    )
```

- [ ] **Step 4: Point the router at the shared serializer**

In `app/modules/reporting/router.py`, replace the entire `def _row_to_read(row)` function body (lines 72-119) with a thin delegate, and add the import near the other reporting imports:

```python
from app.modules.reporting.serialization import report_read_from_row
```

```python
def _row_to_read(row: SessionReport) -> ReportRead:
    return report_read_from_row(row)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/test_serialization.py tests/reporting -k "report" -v`
Expected: PASS (new test passes; existing reporting router tests unaffected)

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/serialization.py app/modules/reporting/router.py tests/reporting/test_serialization.py
git commit -m "refactor(reporting): extract report_read_from_row serializer"
```

---

## Task 6: PDF context helpers (pure)

**Files:**
- Create: `app/modules/reporting/pdf/__init__.py`
- Create: `app/modules/reporting/pdf/context.py`
- Test: `tests/reporting/pdf/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/pdf/test_context.py
from app.modules.reporting.pdf.context import (
    monogram_initials,
    verdict_stamp,
    assessed_dimensions,
)


def test_verdict_stamp_mapping():
    assert verdict_stamp("advance").text == "APPROVED"
    assert verdict_stamp("advance").color == "#138a47"
    assert verdict_stamp("borderline").text == "BORDERLINE"
    assert verdict_stamp("borderline").color == "#c98a16"
    assert verdict_stamp("reject").text == "REJECTED"
    assert verdict_stamp("reject").color == "#d23b34"


def test_monogram_initials():
    assert monogram_initials("Ishant Pundir") == "IP"
    assert monogram_initials("madonna") == "M"
    assert monogram_initials("  Aarav Kumar Mehta ") == "AM"
    assert monogram_initials("") == "?"
    assert monogram_initials(None) == "?"


def test_assessed_dimensions_drops_unassessed():
    scores = {
        "overall": {"score": 90},
        "technical": {"score": 89, "tier_label": "Strong"},
        "behavioral": {"score": None, "coverage": 0.0},
        "communication": {"score": 70, "tier_label": "Strong"},
    }
    dims = assessed_dimensions(scores)
    names = [d["name"] for d in dims]
    assert names == ["Technical", "Communication"]  # overall + behavioral excluded
    assert dims[0]["score"] == 89
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/pdf/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.reporting.pdf`

- [ ] **Step 3: Create the package + helpers**

```python
# app/modules/reporting/pdf/__init__.py
from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.pdf.render import build_pdf_html, render_report_pdf

__all__ = ["build_pdf_context", "build_pdf_html", "render_report_pdf"]
```

```python
# app/modules/reporting/pdf/context.py
"""Pure helpers that turn a ReportRead into the template context.

No I/O, no Playwright — unit-testable anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.schemas import ReportRead

# Verdict (enum) → stamp text + color. UI labels differ from enum values.
_STAMP = {
    "advance": ("APPROVED", "#138a47"),
    "borderline", : ("BORDERLINE", "#c98a16"),
    "reject": ("REJECTED", "#d23b34"),
}

# Dimension key → display name. "overall" is rendered as the gauge, not a meter.
_DIM_ORDER = [("technical", "Technical"), ("behavioral", "Behavioral"),
              ("communication", "Communication")]

# Bar color by score band.
def _bar_color(score: int) -> str:
    if score >= 80:
        return "#137a45"
    if score >= 60:
        return "#b4791a"
    return "#d23b34"


@dataclass(frozen=True)
class StampSpec:
    text: str
    color: str


def verdict_stamp(verdict: str) -> StampSpec:
    text, color = _STAMP.get(verdict, ("PENDING", "#6b6f7a"))
    return StampSpec(text=text, color=color)


def monogram_initials(name: str | None) -> str:
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def assessed_dimensions(scores: dict) -> list[dict]:
    """Return [{name, score, color}] for dimensions that were actually scored.

    A dimension with score=None (e.g. Behavioral on a technical-only screen) is
    omitted entirely — never shown as 0 or as a placeholder bar.
    """
    out: list[dict] = []
    for key, label in _DIM_ORDER:
        dim = scores.get(key) or {}
        score = dim.get("score")
        if score is None:
            continue
        out.append({"name": label, "score": int(score), "color": _bar_color(int(score))})
    return out
```

NOTE: there is a deliberate syntax error above (`"borderline",`) so Step 4 catches it — fix the stray comma to `"borderline":` before running. (If you copied cleanly, the dict line must read `"borderline": ("BORDERLINE", "#c98a16"),`.)

- [ ] **Step 4: Fix the stray comma, then run the test**

Ensure the `_STAMP` dict reads exactly:

```python
_STAMP = {
    "advance": ("APPROVED", "#138a47"),
    "borderline": ("BORDERLINE", "#c98a16"),
    "reject": ("REJECTED", "#d23b34"),
}
```

Run: `docker compose run --rm nexus pytest tests/reporting/pdf/test_context.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the full context builder + its test**

Append to `tests/reporting/pdf/test_context.py`:

```python
from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.schemas import ReportRead


def _min_report() -> ReportRead:
    return ReportRead.model_validate({
        "verdict": "advance", "verdict_reason": "ok",
        "overall_score": 90, "overall_coverage": 1.0, "overall_confidence": "high",
        "decision": {"headline": "h", "why_positive": {"title": "p", "body": "pb"},
                     "why_negative": {"title": "n", "body": "nb"}},
        "scores": {"overall": {"score": 90, "tier_label": "Strong", "tone": "ok",
                               "confidence": "high", "coverage": 1.0},
                   "technical": {"score": 89, "tier_label": "Strong", "tone": "ok",
                                 "confidence": "high", "coverage": 1.0},
                   "behavioral": {"score": None, "tier_label": "Not Assessed",
                                  "tone": "neutral", "confidence": "low", "coverage": 0.0},
                   "communication": {"score": 70, "tier_label": "Strong", "tone": "ok",
                                     "confidence": "medium", "coverage": 1.0}},
        "quick_summary": "Solid screen.",
        "strengths": [{"title": "s1", "detail": "d1"}],
        "concerns": [{"title": "c1", "detail": "d1", "severity": "moderate"}],
        "questions": [{"seq": 1, "question_id": "q1", "title": "Q one",
                       "status_badge": "passed", "status_tone": "ok",
                       "question_text": "text", "candidate_quote": "quote", "our_read": "",
                       "difficulty": "medium"}],
        "methodology": {"note": "", "charity_flags": []},
    })


def test_build_pdf_context_shape():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    assert ctx["candidate_name"] == "Ishant Pundir"
    assert ctx["monogram"] == "IP"
    assert ctx["stamp"].text == "APPROVED"
    assert ctx["overall_score"] == 90
    assert [d["name"] for d in ctx["dimensions"]] == ["Technical", "Communication"]
    assert ctx["reference_photo_url"] is None
    assert len(ctx["questions"]) == 1
```

Append to `app/modules/reporting/pdf/context.py`:

```python
def build_pdf_context(
    report: ReportRead,
    *,
    candidate_name: str,
    job_title: str,
    stage_label: str,
    generated_on: str,
    reference_photo_url: str | None,
    full_session_url: str,
) -> dict:
    """Flatten a ReportRead + session metadata into the print template context."""
    overall = report.scores.get("overall") or {}
    overall_tier = getattr(overall, "tier_label", None) if not isinstance(overall, dict) else overall.get("tier_label")
    # report.scores values are ScoreOut models; coerce to dict for uniform access.
    scores_as_dict = {
        k: (v.model_dump() if hasattr(v, "model_dump") else dict(v))
        for k, v in report.scores.items()
    }
    overall_d = scores_as_dict.get("overall", {})
    return {
        "candidate_name": candidate_name,
        "monogram": monogram_initials(candidate_name),
        "job_title": job_title,
        "stage_label": stage_label,
        "generated_on": generated_on,
        "reference_photo_url": reference_photo_url,
        "full_session_url": full_session_url,
        "stamp": verdict_stamp(report.verdict),
        "overall_score": report.overall_score,
        "overall_tier": overall_d.get("tier_label") or "",
        "overall_confidence": report.overall_confidence,
        "overall_coverage_pct": round((report.overall_coverage or 0.0) * 100),
        "dimensions": assessed_dimensions(scores_as_dict),
        "decision": report.decision.model_dump(),
        "quick_summary": report.quick_summary,
        "strengths": [s.model_dump() for s in report.strengths],
        "concerns": [c.model_dump() for c in report.concerns],
        "questions": [q.model_dump() for q in report.questions],
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/pdf/test_context.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting/pdf/__init__.py app/modules/reporting/pdf/context.py tests/reporting/pdf/test_context.py
git commit -m "feat(reporting): PDF template context helpers"
```

---

## Task 7: Print template + render functions

**Files:**
- Create: `app/modules/reporting/pdf/templates/report.html.j2`
- Create: `app/modules/reporting/pdf/render.py`
- Test: `tests/reporting/pdf/test_render_html.py`

- [ ] **Step 1: Write the failing test (HTML render only — no Chromium)**

```python
# tests/reporting/pdf/test_render_html.py
from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.pdf.render import build_pdf_html
from tests.reporting.pdf.test_context import _min_report


def test_build_pdf_html_contains_key_content():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    html = build_pdf_html(ctx)
    assert "Ishant Pundir" in html
    assert "APPROVED" in html          # stamp text
    assert "IP" in html                # monogram (no photo)
    assert "Technical" in html
    assert "Behavioral" not in html    # un-assessed dim omitted
    assert "Solid screen." in html     # summary
    assert "See full session" in html


def test_build_pdf_html_uses_photo_when_present():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url="https://r2/photo.jpg", full_session_url="https://x",
    )
    html = build_pdf_html(ctx)
    assert "https://r2/photo.jpg" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/pdf/test_render_html.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.reporting.pdf.render`

- [ ] **Step 3: Create the print template**

Create `app/modules/reporting/pdf/templates/report.html.j2`. This is the locked premium design (dark hero, SVG gauge, coded stamp with the dialed-down worn filter, score band, numbered sections). Fonts use families installed in `Dockerfile.pdf` (Liberation/DejaVu) so Chromium has real glyphs.

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  @page { size: A4; margin: 14mm 0 16mm; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Liberation Sans",Arial,sans-serif; color:#16181d; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  .serif { font-family:"Liberation Serif",Georgia,serif; }
  .hero { background:linear-gradient(120deg,#1a1b38,#33356b); color:#fff; padding:22px 30px 26px; }
  .hbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; }
  .logo { font-weight:800; font-size:15px; } .logo span { color:#a7a3f5; }
  .hmeta { font-size:9px; letter-spacing:2px; text-transform:uppercase; color:#9b9cc4; text-align:right; line-height:1.7; }
  .hgrid { display:flex; gap:18px; align-items:center; }
  .mono { height:74px; width:74px; border-radius:15px; background:linear-gradient(135deg,#6f68f0,#4f46e5);
          display:flex; align-items:center; justify-content:center; font-weight:800; font-size:28px; color:#fff;
          border:2px solid rgba(255,255,255,.14); flex:none; overflow:hidden; }
  .mono img { width:100%; height:100%; object-fit:cover; }
  .hname { font-size:24px; font-weight:600; margin:2px 0 3px; }
  .hrole { font-size:12px; color:#b9bad8; }
  .stub { display:inline-block; margin-top:12px; border:1px solid rgba(255,255,255,.28); color:#fff;
          font-size:11px; border-radius:8px; padding:6px 12px; text-decoration:none; }
  .stampwrap { margin-left:auto; }
  .band { display:flex; align-items:center; gap:24px; padding:16px 30px; border-bottom:1px solid #e9e9ee; }
  .ovl { text-align:center; padding-right:6px; border-right:1px solid #e9e9ee; }
  .ovl .t { font-size:9px; letter-spacing:1.2px; text-transform:uppercase; color:#6b6f7a; margin-top:3px; }
  .dim { flex:1; } .dim .top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px; }
  .dim .nm { font-size:10px; letter-spacing:.5px; text-transform:uppercase; color:#6b6f7a; font-weight:700; }
  .dim .vl { font-size:16px; font-weight:700; }
  .rail { height:6px; border-radius:6px; background:#eef0f4; overflow:hidden; }
  .rail > i { display:block; height:100%; border-radius:6px; }
  .sec { padding:16px 30px 0; }
  .secnum { display:flex; align-items:center; gap:12px; margin:0 0 12px; }
  .secnum .e { font-size:10px; letter-spacing:2.5px; text-transform:uppercase; font-weight:700; color:#6b6f7a; }
  .secnum .ln { flex:1; height:1px; background:#e9e9ee; }
  .body { font-size:12px; line-height:1.7; color:#3a3d45; }
  .why { display:flex; gap:14px; }
  .wcard { border:1px solid #e9e9ee; border-radius:12px; padding:14px 15px; position:relative; flex:1; }
  .wcard.pos { border-left:3px solid #0e7a45; } .wcard.neg { border-left:3px solid #b4791a; }
  .wtitle { font-weight:700; font-size:12px; margin-bottom:6px; }
  .wbody { font-size:11px; line-height:1.6; color:#54565f; }
  .srow { display:flex; gap:12px; padding:12px 0; border-top:1px solid #f0f0f4; break-inside:avoid; }
  .sico { width:22px; height:22px; border-radius:7px; display:flex; align-items:center; justify-content:center;
          font-size:12px; font-weight:800; flex:none; }
  .sico.s { background:#e7f7ee; color:#0e7a45; } .sico.c { background:#fbf1de; color:#b4791a; }
  .stitle { font-weight:700; font-size:12px; margin-bottom:3px; }
  .sdetail { font-size:11px; color:#54565f; line-height:1.6; }
  .sev { font-size:8.5px; font-weight:700; border-radius:5px; padding:2px 7px; background:#fbf1de;
         color:#b4791a; text-transform:uppercase; letter-spacing:.6px; margin-left:8px; }
  .qcard { border:1px solid #e9e9ee; border-radius:12px; padding:14px 16px; margin-bottom:11px; break-inside:avoid; }
  .qhead { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
  .qnum { font-family:"Liberation Serif",Georgia,serif; color:#c9cad4; font-size:20px; font-weight:600; margin-right:10px; }
  .qtitle { font-weight:700; font-size:12px; line-height:1.4; }
  .chip { font-size:8.5px; font-weight:700; border-radius:6px; padding:3px 8px; letter-spacing:.5px;
          text-transform:uppercase; white-space:nowrap; }
  .chip-pass { background:#e7f7ee; color:#0e7a45; } .chip-diff { background:#f1f1f5; color:#7c7c88; }
  .qquote { border-left:2px solid #e3e3ea; padding-left:13px; color:#52535d; font-size:11px;
            font-style:italic; line-height:1.6; margin-top:10px; }
  .qread { font-size:11px; color:#3a3d45; line-height:1.6; margin-top:8px; }
</style></head>
<body>
  <svg width="0" height="0" style="position:absolute"><defs>
    <filter id="worn" x="-12%" y="-25%" width="124%" height="150%">
      <feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="2" seed="7" result="warp"/>
      <feDisplacementMap in="SourceGraphic" in2="warp" scale="1.5" xChannelSelector="R" yChannelSelector="G" result="wob"/>
      <feTurbulence type="fractalNoise" baseFrequency="0.55" numOctaves="2" seed="4" result="speck"/>
      <feColorMatrix in="speck" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -0.45 1.06" result="mask"/>
      <feComposite in="wob" in2="mask" operator="in"/>
    </filter>
  </defs></svg>

  <div class="hero">
    <div class="hbar"><div class="logo">BinQle<span>.ai</span></div>
      <div class="hmeta">Candidate Evaluation<br>{{ stage_label }} · {{ generated_on }} · Confidential</div></div>
    <div class="hgrid">
      <div class="mono">{% if reference_photo_url %}<img src="{{ reference_photo_url }}" alt="">{% else %}{{ monogram }}{% endif %}</div>
      <div>
        <div class="hname serif">{{ candidate_name }}</div>
        <div class="hrole">{{ job_title }}</div>
        <a class="stub" href="{{ full_session_url }}">&#9654; See full session recording</a>
      </div>
      <div class="stampwrap">
        <svg width="178" height="90" viewBox="0 0 200 100">
          <g filter="url(#worn)" transform="rotate(-8 100 50)" fill="none" stroke="{{ stamp.color }}">
            <rect x="10" y="18" width="180" height="64" rx="9" stroke-width="5"/>
            <rect x="17" y="25" width="166" height="50" rx="6" stroke-width="2.2"/>
            <text x="100" y="59" text-anchor="middle" font-family="DejaVu Sans,Arial,sans-serif"
                  font-weight="bold" font-size="28" letter-spacing="1.5" fill="{{ stamp.color }}"
                  stroke="none" font-style="italic">{{ stamp.text }}</text>
          </g>
        </svg>
      </div>
    </div>
  </div>

  <div class="band">
    <div class="ovl">
      {% set off = 195 - (1.948 * (overall_score or 0)) %}
      <svg width="74" height="74" viewBox="0 0 76 76">
        <circle cx="38" cy="38" r="31" fill="none" stroke="#eef0f4" stroke-width="7"/>
        <circle cx="38" cy="38" r="31" fill="none" stroke="#0e7a45" stroke-width="7" stroke-linecap="round"
                stroke-dasharray="194.8" stroke-dashoffset="{{ off }}" transform="rotate(-90 38 38)"/>
        <text x="38" y="44" text-anchor="middle" font-size="22" font-weight="700" fill="#16181d"
              font-family="Liberation Serif,Georgia,serif">{{ overall_score if overall_score is not none else '—' }}</text>
      </svg>
      <div class="t">Overall{% if overall_tier %} · {{ overall_tier }}{% endif %}</div>
    </div>
    {% for d in dimensions %}
    <div class="dim"><div class="top"><span class="nm">{{ d.name }}</span><span class="vl" style="color:{{ d.color }}">{{ d.score }}</span></div>
      <div class="rail"><i style="width:{{ d.score }}%;background:{{ d.color }}"></i></div></div>
    {% endfor %}
  </div>

  <div class="sec">
    <div class="secnum"><span class="e">01 — Summary</span><span class="ln"></span></div>
    <p class="body">{{ quick_summary }}</p>
  </div>

  <div class="sec">
    <div class="secnum"><span class="e">02 — Why this verdict</span><span class="ln"></span></div>
    <div class="why">
      <div class="wcard pos"><div class="wtitle">{{ decision.why_positive.title }}</div>
        <div class="wbody">{{ decision.why_positive.body }}</div></div>
      <div class="wcard neg"><div class="wtitle">{{ decision.why_negative.title }}</div>
        <div class="wbody">{{ decision.why_negative.body }}</div></div>
    </div>
  </div>

  {% if strengths %}
  <div class="sec">
    <div class="secnum"><span class="e">03 — Strengths</span><span class="ln"></span></div>
    {% for s in strengths %}
    <div class="srow"><div class="sico s">&#10003;</div><div>
      <div class="stitle">{{ s.title }}</div><div class="sdetail">{{ s.detail }}</div></div></div>
    {% endfor %}
  </div>
  {% endif %}

  {% if concerns %}
  <div class="sec">
    <div class="secnum"><span class="e">04 — Concerns</span><span class="ln"></span></div>
    {% for c in concerns %}
    <div class="srow"><div class="sico c">!</div><div>
      <div class="stitle">{{ c.title }}<span class="sev">{{ c.severity|replace('_',' ') }}</span></div>
      <div class="sdetail">{{ c.detail }}</div></div></div>
    {% endfor %}
  </div>
  {% endif %}

  {% if questions %}
  <div class="sec">
    <div class="secnum"><span class="e">05 — Question by question</span><span class="ln"></span></div>
    {% for q in questions %}
    <div class="qcard"><div class="qhead">
      <div style="display:flex"><span class="qnum">{{ q.seq }}</span><span class="qtitle">{{ q.title }}</span></div>
      <div style="white-space:nowrap"><span class="chip chip-pass">{{ q.status_badge|replace('_',' ') }}</span>
        {% if q.difficulty %}<span class="chip chip-diff">{{ q.difficulty }}</span>{% endif %}</div></div>
      {% if q.candidate_quote %}<div class="qquote">&ldquo;{{ q.candidate_quote }}&rdquo;</div>{% endif %}
      {% if q.our_read %}<div class="qread">{{ q.our_read }}</div>{% endif %}
    </div>
    {% endfor %}
  </div>
  {% endif %}
</body></html>
```

- [ ] **Step 4: Create `render.py`**

```python
# app/modules/reporting/pdf/render.py
"""Render a report to HTML (Jinja) and to PDF (headless Chromium / Playwright).

The Playwright import is LAZY (inside render_report_pdf) so the helper/HTML
tests run in the lean nexus image, which has no Chromium. Only the nexus-pdf
worker image installs Playwright + Chromium + fonts.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

# Footer (page numbers) for Chromium's printToPDF.
_FOOTER = (
    '<div style="font-size:7px;color:#9a9aa6;width:100%;padding:0 14mm;'
    'display:flex;justify-content:space-between;font-family:Arial,sans-serif">'
    '<span>BINQLE.AI · AI VIDEO INTERVIEW PLATFORM</span>'
    '<span class="pageNumber"></span>/<span class="totalPages"></span></div>'
)
_HEADER = '<div></div>'  # empty header (hero handles page-1 branding)


def build_pdf_html(ctx: dict) -> str:
    """Render the print template to an HTML string (no browser)."""
    return _env.get_template("report.html.j2").render(**ctx)


async def render_report_pdf(ctx: dict) -> bytes:
    """Render the report HTML to PDF bytes via headless Chromium."""
    from playwright.async_api import async_playwright  # lazy: only in pdf worker

    html = build_pdf_html(ctx)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            pdf = await page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template=_HEADER,
                footer_template=_FOOTER,
                margin={"top": "14mm", "bottom": "16mm", "left": "0", "right": "0"},
            )
            return pdf
        finally:
            await browser.close()
```

- [ ] **Step 5: Run the HTML tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/pdf/test_render_html.py -v`
Expected: PASS (2 tests). These exercise `build_pdf_html` only — no Chromium needed.

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/pdf/templates/report.html.j2 app/modules/reporting/pdf/render.py tests/reporting/pdf/test_render_html.py
git commit -m "feat(reporting): PDF print template + render functions"
```

---

## Task 8: Share actor

**Files:**
- Modify: `app/modules/reporting/actors.py`
- Modify: `app/modules/reporting/__init__.py`
- Test: `tests/reporting/test_share_actor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_share_actor.py
import uuid
from datetime import UTC, datetime

import pytest

from app.modules.reporting import actors
from app.modules.reporting.models import ReportShare, SessionReport


@pytest.mark.asyncio
async def test_share_actor_renders_uploads_emails(db_session, monkeypatch, seeded_share):
    """Happy path: pending row → rendered → uploaded → emailed → sent."""
    sent = {}

    async def fake_render(ctx):
        sent["ctx"] = ctx
        return b"%PDF-1.4 fake"

    class FakeStorage:
        async def upload_bytes(self, key, data, *, content_type):
            sent["key"] = key
            sent["bytes"] = data

    async def fake_send_email(*, to, subject, html, attachments=None):
        sent["to"] = to
        sent["attachments"] = attachments

    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    monkeypatch.setattr(actors, "get_object_storage", lambda: FakeStorage())
    monkeypatch.setattr(actors, "send_email", fake_send_email)

    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="corr-1",
    )

    await db_session.refresh(seeded_share)
    assert seeded_share.status == "sent"
    assert seeded_share.pdf_r2_key == sent["key"]
    assert sent["to"] == "client@acme.com"
    assert sent["attachments"][0][2] == "application/pdf"
    assert sent["attachments"][0][1] == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_share_actor_idempotent_when_sent(db_session, monkeypatch, seeded_share):
    seeded_share.status = "sent"
    await db_session.commit()

    called = {"render": False}

    async def fake_render(ctx):
        called["render"] = True
        return b""

    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
    )
    assert called["render"] is False  # short-circuited


@pytest.mark.asyncio
async def test_share_actor_marks_failed_on_render_error(db_session, monkeypatch, seeded_share):
    async def boom(ctx):
        raise RuntimeError("chromium died")

    monkeypatch.setattr(actors, "render_report_pdf", boom)
    with pytest.raises(RuntimeError):
        await actors._share_report_pdf_async(
            share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
        )
    await db_session.refresh(seeded_share)
    assert seeded_share.status == "failed"
    assert "chromium died" in (seeded_share.error or "")
```

This test needs a `seeded_share` fixture that inserts a `clients`/`users`/`candidates`/`sessions`/`session_reports`/`report_shares` chain. Add it to `tests/reporting/conftest.py` (mirror the existing report-scoring fixtures in that directory — reuse whatever session/report seeding helper already exists there; the share row points at the seeded `session_reports.id`, a `ready` report, candidate name "Ishant Pundir", recipient "client@acme.com").

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_actor.py -v`
Expected: FAIL — `AttributeError: module 'app.modules.reporting.actors' has no attribute '_share_report_pdf_async'`

- [ ] **Step 3: Implement the actor**

Add to the top imports of `app/modules/reporting/actors.py`:

```python
from app.modules.notifications import send_email
from app.modules.reporting.models import ReportShare
from app.modules.reporting.pdf import build_pdf_context, render_report_pdf
from app.modules.reporting.serialization import report_read_from_row
from app.storage import get_object_storage
from app.config import settings
```

Append the actor + inner function to `app/modules/reporting/actors.py`:

```python
def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else "" for c in name).strip()
    return (cleaned or "candidate").replace(" ", "-")


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    head = (local[:1] + "***") if local else "***"
    return f"{head}@{domain}" if domain else "***"


async def _share_report_pdf_async(
    share_id: UUID, tenant_id: UUID, correlation_id: str,
) -> None:
    log = logger.bind(share_id=str(share_id), tenant_id=str(tenant_id),
                      correlation_id=correlation_id)
    safe_tenant_id = str(tenant_id)

    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'"))

        share = (await db.execute(
            select(ReportShare).where(
                ReportShare.id == share_id, ReportShare.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if share is None:
            log.warning("reporting.share.row_not_found")
            return
        if share.status == "sent":
            log.info("reporting.share.skip_already_sent")
            return

        try:
            report_row = (await db.execute(
                select(SessionReport).where(
                    SessionReport.id == share.report_id,
                    SessionReport.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if report_row is None or report_row.status != "ready":
                raise ValueError("report not ready")

            report = report_read_from_row(report_row)

            # Candidate name / job title / stage label + reference photo presign.
            from app.modules.candidates.models import Candidate, CandidateJobAssignment
            from app.modules.jd.models import JobPosting
            from app.modules.pipelines.models import JobPipelineStage
            from app.modules.session.models import Session as SessionRow

            sess = (await db.execute(
                select(SessionRow).where(SessionRow.id == share.session_id,
                                         SessionRow.tenant_id == tenant_id)
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

            reference_photo_url = None
            if sess and getattr(sess, "reference_photo_key", None):
                with contextlib.suppress(Exception):
                    reference_photo_url = await get_object_storage().presign_get_url(
                        sess.reference_photo_key,
                        ttl_seconds=settings.recording_signed_url_ttl_seconds,
                    )

            generated_on = (
                report_row.generated_at.strftime("%b %d, %Y")
                if report_row.generated_at else datetime.now(UTC).strftime("%b %d, %Y")
            )
            full_session_url = f"{settings.frontend_base_url}/shared-session/coming-soon"

            share.status = "rendering"
            await db.flush()

            ctx = build_pdf_context(
                report,
                candidate_name=candidate_name,
                job_title=job_title,
                stage_label=stage_label,
                generated_on=generated_on,
                reference_photo_url=reference_photo_url,
                full_session_url=full_session_url,
            )
            pdf_bytes = await render_report_pdf(ctx)

            key = f"report-shares/{tenant_id}/{share.id}.pdf"
            await get_object_storage().upload_bytes(
                key, pdf_bytes, content_type="application/pdf")
            share.pdf_r2_key = key

            from app.modules.notifications import render_template
            html = render_template(
                "report_share.html",
                candidate_name=candidate_name,
                job_title=job_title,
                shared_by="Your recruiting team",
            )
            filename = f"BinQle-Evaluation-{_safe_filename(candidate_name)}.pdf"
            await send_email(
                to=share.recipient_email,
                subject=f"Candidate evaluation — {candidate_name} ({job_title})",
                html=html,
                attachments=[(filename, pdf_bytes, "application/pdf")],
            )

            share.status = "sent"
            share.sent_at = datetime.now(UTC)
            await db.commit()
            log.info("reporting.share.sent", recipient=_mask_email(share.recipient_email))

        except Exception as exc:
            with contextlib.suppress(Exception):
                share.status = "failed"
                share.error = str(exc)[:500]
                await db.commit()
            log.error("reporting.share.failed", error_type=type(exc).__name__,
                      error_message=str(exc)[:500])
            raise


@dramatiq.actor(
    max_retries=2,
    min_backoff=5_000,
    max_backoff=120_000,
    queue_name="report_share",
)
async def share_report_pdf(
    share_id: str, tenant_id: str, correlation_id: str,
) -> None:
    """Render a report PDF and email it as an attachment. Runs on nexus-pdf-worker."""
    await _share_report_pdf_async(UUID(share_id), UUID(tenant_id), correlation_id)
```

Note: `render_template` is part of the notifications public API — confirm it is exported from `app/modules/notifications/__init__.py`; if not, add `render_template` to that module's `__all__` (it lives in `notifications/service.py`).

- [ ] **Step 4: Export the new symbols**

Replace `app/modules/reporting/__init__.py` with:

```python
"""reporting module — post-session report compilation and score aggregation."""

from app.modules.reporting.actors import score_session_report, share_report_pdf
from app.modules.reporting.models import ReportShare, SessionReport

__all__ = ["SessionReport", "ReportShare", "score_session_report", "share_report_pdf"]
```

- [ ] **Step 5: Run the actor tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_actor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/actors.py app/modules/reporting/__init__.py tests/reporting/test_share_actor.py tests/reporting/conftest.py
git commit -m "feat(reporting): share_report_pdf actor (render + upload + email)"
```

---

## Task 9: Share endpoint

**Files:**
- Modify: `app/modules/reporting/router.py`
- Test: `tests/reporting/test_share_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_share_endpoint.py
import pytest


@pytest.mark.asyncio
async def test_share_returns_202_and_enqueues(client, monkeypatch, ready_report_ctx):
    enq = {}

    def fake_send(*args):
        enq["args"] = args

    from app.modules.reporting import router as rep_router
    monkeypatch.setattr(rep_router.share_report_pdf, "send", fake_send)

    resp = await client.post(
        f"/api/reports/session/{ready_report_ctx.session_id}/share",
        json={"recipient_email": "client@acme.com"},
        headers=ready_report_ctx.auth_headers,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert "share_id" in body
    assert enq["args"][0] == body["share_id"]  # share_id passed to actor


@pytest.mark.asyncio
async def test_share_409_when_report_not_ready(client, monkeypatch, pending_report_ctx):
    resp = await client.post(
        f"/api/reports/session/{pending_report_ctx.session_id}/share",
        json={"recipient_email": "client@acme.com"},
        headers=pending_report_ctx.auth_headers,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_share_422_on_bad_email(client, ready_report_ctx):
    resp = await client.post(
        f"/api/reports/session/{ready_report_ctx.session_id}/share",
        json={"recipient_email": "nope"},
        headers=ready_report_ctx.auth_headers,
    )
    assert resp.status_code == 422
```

Add `ready_report_ctx` / `pending_report_ctx` fixtures to `tests/reporting/conftest.py` mirroring the existing authed-client + seeded-report fixtures used by the other reporting router tests (`tests/reporting/test_*endpoint*` or `test_router*`). Each yields an object with `.session_id` and `.auth_headers` (a recruiter token with `reports.view`). `ready_report_ctx` seeds a `ready` report; `pending_report_ctx` seeds a `pending` one.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_endpoint.py -v`
Expected: FAIL — 404 (route not defined yet)

- [ ] **Step 3: Add the endpoint**

In `app/modules/reporting/router.py`:

Extend the schema import:

```python
from app.modules.reporting.schemas import (
    HumanDecisionIn,
    ReportIndexItem,
    ReportIndexPage,
    ReportRead,
    ShareReportIn,
    ShareReportOut,
)
from app.modules.reporting.actors import score_session_report, share_report_pdf
from app.modules.reporting.models import ReportShare, SessionReport
```

Add the route (place it after `regenerate_report`, before `GET /{report_id}` is fine — it is under `/session/...` so no UUID-path ambiguity):

```python
@router.post(
    "/session/{session_id}/share",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email this report as a PDF to an external recipient",
)
async def share_report(
    session_id: uuid_mod.UUID,
    body: ShareReportIn,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Create a report_shares row, enqueue the PDF render+email actor, return 202.

    RBAC: reports.view or super-admin (same set that can view the report).
    Preconditions: the report exists and is `ready` (else 409). Note: this
    codebase enforces rate limits via global middleware only — no per-route
    decorator exists; the intended 'report share' class (10/min per-IP +
    per-tenant daily cap) is documented in the design spec for when a per-route
    limiter lands.
    """
    _require_reports_view(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id
    correlation_id = _get_correlation_id(request)

    report_row = (await db.execute(
        select(SessionReport).where(
            SessionReport.session_id == session_id,
            SessionReport.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if report_row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report_row.status != "ready":
        raise HTTPException(status_code=409, detail="Report is not ready to share")

    share = ReportShare(
        tenant_id=tenant_id,
        session_id=session_id,
        report_id=report_row.id,
        recipient_email=str(body.recipient_email),
        status="pending",
        requested_by=user.user.id,
    )
    db.add(share)
    await db.flush()  # populate share.id

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="session_report.shared",
        resource="session_report",
        resource_id=report_row.id,
        payload={"share_id": str(share.id), "correlation_id": correlation_id},
    )

    share_report_pdf.send(str(share.id), str(tenant_id), correlation_id)

    _log.info(
        "reporting.share.enqueued",
        session_id=str(session_id),
        share_id=str(share.id),
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    return ShareReportOut(share_id=str(share.id), status="pending").model_dump()
```

- [ ] **Step 4: Run the endpoint tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_endpoint.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full reporting suite**

Run: `docker compose run --rm nexus pytest tests/reporting -v`
Expected: PASS (all reporting tests green)

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/router.py tests/reporting/test_share_endpoint.py tests/reporting/conftest.py
git commit -m "feat(reporting): POST /session/{id}/share endpoint"
```

---

## Task 10: PDF worker image + service

**Files:**
- Create: `app/pdf_worker.py`
- Create: `Dockerfile.pdf`
- Modify: `pyproject.toml`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the `pdf` dependency group**

In `pyproject.toml`, under `[project.optional-dependencies]` (next to `vision = [...]`), add:

```toml
pdf = [
    "playwright>=1.44",
]
```

- [ ] **Step 2: Create the worker entrypoint**

```python
# app/pdf_worker.py
"""Dramatiq worker entry point for the report_share queue only.

Run by the dedicated nexus-pdf-worker service (Dockerfile.pdf — Chromium +
fonts). Renders report PDFs with Playwright and emails them.

Why a separate entrypoint (not app.worker)? The lean nexus/nexus-worker image
has no Chromium; if app.worker registered share_report_pdf it would consume
report_share messages and crash on the lazy Playwright import. Registering the
actor ONLY here means only this process declares + consumes report_share.

Import-ordering: app.brokers must initialize the broker before any actor module.
"""
import structlog

from app import brokers  # noqa: F401  (side effect: init broker before actor import)
from app.config import settings
from app.model_registry import configure_all_models
from app.modules.reporting import actors  # noqa: F401  (register share_report_pdf)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.debug
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
)

# Full ORM mapper registry — ReportShare/SessionReport FK to clients/sessions.
configure_all_models()
```

- [ ] **Step 3: Create `Dockerfile.pdf`**

```dockerfile
# Dockerfile.pdf — image for nexus-pdf-worker only.
# Renders report PDFs with headless Chromium (Playwright). No GPU.

FROM python:3.13-slim AS deps
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[pdf]"

FROM python:3.13-slim AS runner
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 fonts-liberation fonts-dejavu fonts-dejavu-extra \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Install Chromium + its OS deps into a path OUTSIDE /app (dev bind-mounts .:/app
# and would otherwise shadow it). Done as root before dropping to the nexus user.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN mkdir -p /opt/pw-browsers \
    && playwright install --with-deps chromium \
    && chmod -R a+rx /opt/pw-browsers

RUN groupadd --system --gid 1001 nexus && \
    useradd --system --uid 1001 --gid nexus nexus

COPY . .
USER nexus

CMD dramatiq app.pdf_worker --processes 1 --threads 2 -Q report_share
```

- [ ] **Step 4: Add the compose service**

In `docker-compose.yml`, add after the `nexus-vision-worker` service block (before `redis:`):

```yaml
  nexus-pdf-worker:
    # Report sharing — renders report PDFs with headless Chromium (Playwright)
    # and emails them. Runs the report_share queue ONLY. No GPU.
    build:
      context: .
      dockerfile: Dockerfile.pdf
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@host.docker.internal:54322/postgres
      - REDIS_URL=redis://redis:6379/0
      - PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - .:/app
    command: sh -c 'dramatiq app.pdf_worker --processes 1 --threads 2 -Q report_share'
```

- [ ] **Step 5: Build the image and verify the worker boots**

Run: `docker compose build nexus-pdf-worker`
Expected: build succeeds (Chromium download + font install complete).

Run: `docker compose up -d nexus-pdf-worker && docker compose logs --tail=20 nexus-pdf-worker`
Expected: dramatiq boot log lines, no import error, listening on `report_share`.

- [ ] **Step 6: Render smoke test (Chromium present)**

```python
# tests/reporting/pdf/test_render_pdf_smoke.py
import pytest

from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.pdf.render import render_report_pdf
from tests.reporting.pdf.test_context import _min_report

pytest.importorskip("playwright")


@pytest.mark.asyncio
async def test_render_report_pdf_returns_pdf_bytes():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    pdf = await render_report_pdf(ctx)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
```

Run (inside the pdf worker image, which has Chromium):
`docker compose run --rm nexus-pdf-worker python -m pytest tests/reporting/pdf/test_render_pdf_smoke.py -v`
Expected: PASS. (In the lean `nexus` image this test is skipped via `importorskip`.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml app/pdf_worker.py Dockerfile.pdf docker-compose.yml tests/reporting/pdf/test_render_pdf_smoke.py
git commit -m "feat(reporting): nexus-pdf-worker (Chromium) + report_share queue"
```

---

## Task 11: Frontend — share API + hook

**Files:**
- Modify: `frontend/app/lib/api/reports.ts`
- Create: `frontend/app/lib/hooks/use-share-report.ts`
- Test: `frontend/app/tests/api/reports-share.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/app/tests/api/reports-share.test.ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { reportsApi } from '@/lib/api/reports'
import * as client from '@/lib/api/client'

describe('reportsApi.share', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('POSTs the recipient email to the share endpoint', async () => {
    const spy = vi.spyOn(client, 'apiFetch').mockResolvedValue({ share_id: 's1', status: 'pending' })
    const res = await reportsApi.share('tok', 'sess-1', 'client@acme.com')
    expect(res).toEqual({ share_id: 's1', status: 'pending' })
    expect(spy).toHaveBeenCalledWith('/api/reports/session/sess-1/share', {
      method: 'POST',
      token: 'tok',
      body: { recipient_email: 'client@acme.com' },
    })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- reports-share`
Expected: FAIL — `reportsApi.share is not a function`

- [ ] **Step 3: Add the type + API method**

In `frontend/app/lib/api/reports.ts`, add near the other interfaces:

```typescript
export interface ShareReportResponse {
  share_id: string
  status: string
}
```

Inside the `reportsApi` object, add:

```typescript
  /** POST /api/reports/session/{sessionId}/share — email the report PDF. */
  share: (
    token: string,
    sessionId: string,
    recipientEmail: string,
  ): Promise<ShareReportResponse> =>
    apiFetch<ShareReportResponse>(`/api/reports/session/${sessionId}/share`, {
      method: 'POST',
      token,
      body: { recipient_email: recipientEmail },
    }),
```

- [ ] **Step 4: Create the mutation hook**

```typescript
// frontend/app/lib/hooks/use-share-report.ts
'use client'

import { useMutation } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { reportsApi } from '@/lib/api/reports'

export function useShareReport(sessionId: string) {
  return useMutation({
    mutationFn: async (recipientEmail: string) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.share(token, sessionId, recipientEmail)
    },
  })
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend/app && npm run test -- reports-share`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/api/reports.ts frontend/app/lib/hooks/use-share-report.ts frontend/app/tests/api/reports-share.test.ts
git commit -m "feat(app): reportsApi.share + useShareReport hook"
```

---

## Task 12: Frontend — Share dialog + button

**Files:**
- Create: `frontend/app/components/dashboard/reports/ShareReportDialog.tsx`
- Modify: `frontend/app/components/dashboard/reports/ReportTopBar.tsx`
- Modify: `frontend/app/app/(dashboard)/reports/session/[sessionId]/page.tsx`
- Test: `frontend/app/tests/components/ShareReportDialog.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/ShareReportDialog.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const shareMock = vi.fn()
vi.mock('@/lib/hooks/use-share-report', () => ({
  useShareReport: () => ({ mutateAsync: shareMock, isPending: false }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { ShareReportDialog } from '@/components/dashboard/reports/ShareReportDialog'

describe('ShareReportDialog', () => {
  it('rejects an invalid email and does not call share', async () => {
    render(<ShareReportDialog sessionId="s1" open onOpenChange={() => {}} />)
    fireEvent.change(screen.getByLabelText(/recipient email/i), { target: { value: 'bad' } })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    await waitFor(() => expect(screen.getByText(/valid email/i)).toBeInTheDocument())
    expect(shareMock).not.toHaveBeenCalled()
  })

  it('calls share with a valid email', async () => {
    shareMock.mockResolvedValue({ share_id: 'x', status: 'pending' })
    render(<ShareReportDialog sessionId="s1" open onOpenChange={() => {}} />)
    fireEvent.change(screen.getByLabelText(/recipient email/i), { target: { value: 'client@acme.com' } })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    await waitFor(() => expect(shareMock).toHaveBeenCalledWith('client@acme.com'))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- ShareReportDialog`
Expected: FAIL — cannot resolve `@/components/dashboard/reports/ShareReportDialog`

- [ ] **Step 3: Create the dialog**

```tsx
// frontend/app/components/dashboard/reports/ShareReportDialog.tsx
'use client'

import * as React from 'react'
import { toast } from 'sonner'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
} from '@/components/px'
import { useShareReport } from '@/lib/hooks/use-share-report'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface Props {
  sessionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ShareReportDialog({ sessionId, open, onOpenChange }: Props) {
  const [email, setEmail] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const share = useShareReport(sessionId)

  async function handleSend() {
    if (!EMAIL_RE.test(email.trim())) {
      setError('Enter a valid email address')
      return
    }
    setError(null)
    try {
      await share.mutateAsync(email.trim())
      toast.success(`Report is being sent to ${email.trim()}`)
      setEmail('')
      onOpenChange(false)
    } catch {
      setError('Could not send the report. Please try again.')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Share report</DialogTitle>
          <DialogDescription>
            Email a PDF of this evaluation to a client. They receive the verdict,
            scores, rationale, and question-by-question summary.
          </DialogDescription>
        </DialogHeader>
        <div className="mt-2">
          <Label htmlFor="share-email">Recipient email</Label>
          <Input
            id="share-email"
            type="email"
            placeholder="client@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void handleSend() }}
          />
          {error && (
            <p className="mt-1.5 text-[12px]" style={{ color: 'var(--px-danger)' }}>{error}</p>
          )}
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" onClick={() => void handleSend()} disabled={share.isPending}>
            {share.isPending ? 'Sending…' : 'Send'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

Confirm the imported primitives (`Dialog`, `DialogContent`, …, `Input`, `Label`) are all exported from `@/components/px` (the barrel `components/px/index.ts`). If `Label` is not re-exported, add it to the barrel.

- [ ] **Step 4: Wire the Share button into `ReportTopBar`**

Replace `frontend/app/components/dashboard/reports/ReportTopBar.tsx` with:

```tsx
'use client'

import * as React from 'react'
import Link from 'next/link'
import { Share2 } from 'lucide-react'

import { Button } from '@/components/px'
import type { Verdict } from '@/lib/api/reports'
import { VerdictChip } from './VerdictBand'
import { ShareReportDialog } from './ShareReportDialog'

interface Props {
  sessionId: string
  candidateName: string
  candidateId: string
  title: string
  subtitle: string
  verdict: Verdict
  canRegenerate: boolean
  onRegenerate: () => void
}

export function ReportTopBar({
  sessionId, candidateName, candidateId, title, subtitle, verdict, canRegenerate, onRegenerate,
}: Props) {
  const [shareOpen, setShareOpen] = React.useState(false)
  return (
    <div className="mb-4 flex items-center gap-3">
      <Link href={`/candidates/${candidateId}?tab=sessions`} className="text-[12px] hover:underline" style={{ color: 'var(--px-fg-3)' }}>
        ← {candidateName}
      </Link>
      <div className="min-w-0 flex-1">
        <h1 className="px-serif m-0 truncate text-[24px] font-normal" style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}>
          Evaluation — {title}
        </h1>
        <p className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>{subtitle}</p>
      </div>
      <VerdictChip verdict={verdict} />
      <Button type="button" variant="outline" size="sm" onClick={() => setShareOpen(true)}>
        <Share2 size={14} className="mr-1.5" /> Share
      </Button>
      {canRegenerate && (
        <Button type="button" variant="outline" size="sm" onClick={onRegenerate}>Regenerate</Button>
      )}
      <ShareReportDialog sessionId={sessionId} open={shareOpen} onOpenChange={setShareOpen} />
    </div>
  )
}
```

- [ ] **Step 5: Pass `sessionId` from the page**

In `frontend/app/app/(dashboard)/reports/session/[sessionId]/page.tsx`, find the `<ReportTopBar ... />` usage and add the `sessionId` prop (the page already has `sessionId` in scope from the route params / `useReport(sessionId)` call):

```tsx
<ReportTopBar
  sessionId={sessionId}
  candidateName={...}
  candidateId={...}
  title={...}
  subtitle={...}
  verdict={...}
  canRegenerate={...}
  onRegenerate={...}
/>
```

(Keep all existing props; only add `sessionId={sessionId}`.)

- [ ] **Step 6: Run the component test + type-check + lint**

Run: `cd frontend/app && npm run test -- ShareReportDialog`
Expected: PASS (2 tests)

Run: `cd frontend/app && npm run type-check && npm run lint`
Expected: zero errors

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/reports/ShareReportDialog.tsx frontend/app/components/dashboard/reports/ReportTopBar.tsx "frontend/app/app/(dashboard)/reports/session/[sessionId]/page.tsx" frontend/app/tests/components/ShareReportDialog.test.tsx
git commit -m "feat(app): Share button + ShareReportDialog on the report page"
```

---

## Task 13: End-to-end manual verification

**Files:** none (manual)

- [ ] **Step 1: Start the stack incl. the pdf worker**

Run: `docker compose up -d nexus nexus-pdf-worker redis`
Confirm `nexus-pdf-worker` logs show it listening on `report_share` with no errors.

- [ ] **Step 2: Confirm dry-run email mode**

Confirm `.env` has `NOTIFICATIONS_DRY_RUN=true` (dev). The DryRunProvider will log the email + attachment filename/size instead of sending.

- [ ] **Step 3: Share a real report from the UI**

Open `http://localhost:3000/reports/session/af2d3a81-a384-42d5-ab48-f6f37ba86c43?...` (the known-good ready report). Click **Share**, enter an email, click **Send**. Expect the success toast.

- [ ] **Step 4: Confirm the pipeline ran**

Run: `docker compose logs --tail=40 nexus-pdf-worker`
Expected: `reporting.share.sent` with a masked recipient; a `report-shares/<tenant>/<id>.pdf` upload; and (from the nexus/worker log) an `email.dry_run` line listing `attachments=[("BinQle-Evaluation-Ishant-Pundir.pdf", <bytes>)]`.

Run: `docker exec supabase_db_backend psql -U postgres -d postgres -c "select status, pdf_r2_key, recipient_email from report_shares order by created_at desc limit 1;"`
Expected: one row, `status=sent`, a non-null `pdf_r2_key`.

- [ ] **Step 5: Eyeball the PDF**

Download the object from R2 (or temporarily switch the actor to also write the bytes to `/tmp` during dev) and open it. Verify: dark hero + logo, monogram "IP" (no photo on this session), green **APPROVED** stamp, Overall 90 gauge, only Technical + Communication meters (no Behavioral), Summary → Why → Strengths(5) → Concerns(1) → 6 question cards, branded footer + page numbers, no background watermark.

- [ ] **Step 6: Commit any dev-only tweaks reverted**

Ensure no `/tmp` debug writes or `NOTIFICATIONS_DRY_RUN` changes are committed. Nothing to commit if clean.

---

## Self-Review

**Spec coverage:**
- §2 curated content + verdict→stamp + assessed-only dimensions + photo fallback → Tasks 6, 7.
- §3 Chromium/Playwright render of a backend Jinja template, fonts, A4, footer/page-numbers, dedicated worker → Tasks 7, 10 (worker is `nexus-pdf-worker`, a documented refinement of the spec's "vision worker").
- §4 Share button/dialog (Task 12), endpoint + 409 + audit + enqueue (Task 9), actor reload-from-row + render + R2 upload + email attachment + idempotency + failure marking (Task 8).
- §5 notifications attachments (Task 3), `report_shares` table + RLS + registry (Task 1), email template (Task 4), "See full session" stub URL (Task 7 template + Task 8 `full_session_url`).
- §6 edge cases: report-not-ready 409 (Task 9), no-photo monogram (Tasks 6/7), un-assessed dim omitted (Tasks 6/7), empty `our_read` omitted (Task 7 template).
- §7 testing across Tasks 1–12; render smoke Task 10; manual e2e Task 13.

**Placeholder scan:** none — the one intentional syntax error in Task 6 Step 3 is called out and fixed in Step 4. Frontend page edit (Task 12 Step 5) shows the exact prop to add against the existing call site.

**Type consistency:** `report_read_from_row` (Task 5) used by router + actor; `build_pdf_context`/`render_report_pdf`/`build_pdf_html` signatures consistent across Tasks 6, 7, 8, 10; `share_report_pdf(share_id, tenant_id, correlation_id)` enqueue args match the actor signature (Tasks 8, 9); `ShareReportOut{share_id,status}` matches the frontend `ShareReportResponse` (Tasks 2, 9, 11); queue name `report_share` consistent across actor, worker entrypoint, Dockerfile, compose (Tasks 8, 10).
