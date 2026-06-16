# Report Sharing (PDF) — Design

**Date:** 2026-06-16
**Status:** Approved (brainstorm) — pending implementation plan
**Surface:** `frontend/app` (recruiter dashboard) + `backend/nexus` (`reporting`, `notifications`)

---

## 1. Problem & Motivation

Hiring agencies running ProjectX/BinQle filter candidates and then sell their
best-fit shortlist to external clients (the actual hiring companies). Today the
session report only lives at an authenticated recruiter URL
(`/reports/session/[sessionId]`). There is no way to hand a client a clean,
self-contained artifact.

This feature lets a recruiter **share a session report as a PDF** with an
external recipient by email. The PDF must be **best-in-industry beautiful** and
**understandable in ~15 seconds** by a client who processes dozens-to-hundreds
of these a day — the verdict and the "why" must land instantly.

### In scope
- A "Share" affordance on the report page → dialog → recipient email → send.
- Server-side PDF generation of a **client-curated** view of an existing report.
- Email delivery of the PDF as an **attachment**.
- A coded (non-image) verdict **stamp** (APPROVED / BORDERLINE / REJECTED).
- A **stub** "See full session" button in the PDF (real shareable-video later).

### Out of scope (explicit)
- Shareable session **video** playback (the stub's eventual target).
- White-label / agency-own-branding of the PDF (BinQle branding only for now).
- A hosted/secure-link delivery mode (attachment only this round).
- Re-grading or any change to how reports are scored.

---

## 2. Audience & Content Curation

Recipient = the agency's **external client**, not the candidate. The PDF is a
**client-facing curated** subset of the report. It **omits** internal forensics:
signal-audit table, proctoring/integrity panel, methodology/scorer metadata
(model, reasoning effort, prompt version, correlation ID), and the human-decision
panel.

Included sections (in order):

1. **Hero** — BinQle logo, "Candidate Evaluation · {stage} · {date} · CONFIDENTIAL",
   candidate photo (or monogram fallback), name, role/subtitle, the **verdict
   stamp**, and a "See full session" stub button.
2. **Score band** — Overall score gauge (+ tier label, confidence, coverage) and
   horizontal meters for **assessed dimensions only**. A dimension that was *Not
   Assessed* (null score / 0 coverage, e.g. Behavioral on a technical screen) is
   **omitted entirely** — never shown as 0 or as a placeholder bar.
3. **01 — Summary** (`quick_summary`).
4. **02 — Why this verdict** — two cards: `decision.why_positive`
   (title + body) and `decision.why_negative` (title + body).
5. **03 — Strengths** (`strengths[]`, full-width icon rows — scales to any count).
6. **04 — Concerns** (`concerns[]`, with severity chip).
7. **05 — Question by question** (`questions[]`): seq, title, status badge,
   difficulty chip, candidate quote. `our_read` is rendered when present and
   omitted when empty (it is empty on older reports).

Every page carries the branded footer (`BINQLE.AI · AI VIDEO INTERVIEW PLATFORM`
left, `{CANDIDATE} · {n} / {total}` right). **No background watermark.**

### Verdict → stamp mapping

| `report.verdict` | Stamp text | Color |
|---|---|---|
| `advance` | APPROVED | green `#138a47` |
| `borderline` | BORDERLINE | amber `#c98a16` |
| `reject` | REJECTED | red `#d23b34` |

The stamp is a coded SVG (rotated ~ -8°, double rounded border, heavy italic
condensed text) with a **subtle** worn-edge filter — legibility first. No raster
image.

### Data source

The PDF consumes the **existing** `ReportRead` shape already produced by
`app/modules/reporting` (verdict, dimension scores, decision, quick_summary,
strengths, concerns, questions, reference_photo_url). No new scoring, no new LLM
calls. The candidate **name** (for the header + monogram initials) and **stage /
title / subtitle** come from the same places the report page uses; the
**reference photo** uses the existing presigned-URL attach path
(`sessions.reference_photo_key` → `presign_get_url`). When there is no photo, a
monogram is derived from the candidate's initials.

---

## 3. Rendering Engine

**Headless Chromium via Playwright**, rendering a purpose-built backend **Jinja2
print template** to PDF.

Rationale: the locked design uses CSS gradients, an SVG score gauge, and an SVG
filter for the stamp texture. **WeasyPrint cannot render SVG filters / the
distressed stamp** and degrades the gradients. Chromium renders the template
**pixel-identical to the approved browser mockups** ("what you saw is the PDF").
This is the enterprise-correct fidelity choice; the cost is a heavier dependency
(a Chromium binary), accepted deliberately.

Decisions:
- **Template, not a live route.** A self-contained server-side Jinja2 template
  (purpose-built for A4 print) is rendered to an HTML string, loaded into
  Chromium via `set_content`, and printed. We do **not** point Chromium at a
  `frontend/app` route — that would require cross-service auth and a running
  frontend, and is more fragile. Some markup is intentionally duplicated from the
  React report; the print template is the source of truth for the PDF.
- **Fonts embedded locally** via `@font-face` from files bundled in the repo (a
  serif display face, a clean sans for body, and a black/heavy face for the
  stamp). No network fetch at render time → deterministic, offline-safe.
- **Page format:** A4, `printBackground: true`.
- **Repeating footer + page numbers** via Chromium's `footerTemplate`
  (`pageNumber` / `totalPages`); the hero renders only on page 1. Cards use
  `break-inside: avoid` so question/strength cards don't split across pages.
- **Where it runs:** the **vision worker** image (`Dockerfile.vision`), already
  our heavy media-rendering worker (ffmpeg, reel/vision). Chromium + Playwright
  are installed there. A new Dramatiq queue **`report_share`** is added to that
  worker's command (`-Q vision reel report_share`). The lean `nexus` API and
  `nexus-worker` images stay free of Chromium.

A thin module `app/modules/reporting/pdf/` owns: the Jinja2 environment + print
template, a `render_report_pdf(report: ReportRead, ctx) -> bytes` function
(launches/reuses a Chromium instance, returns PDF bytes), and the verdict→stamp
+ monogram helpers. Chromium/Playwright is imported **only** here.

---

## 4. Share Flow

### 4.1 Frontend (`frontend/app`)

- Add a **Share** icon button to `components/dashboard/reports/ReportTopBar.tsx`
  (beside the existing Regenerate action).
- Clicking opens a **dialog** (existing `px-dialog` / Base UI) containing:
  - a single **recipient email** input (client-side validated),
  - a **Send** button with loading state,
  - inline error + success ("Report sent to {email}") states, and a toast.
- On submit → `POST /api/reports/session/{sessionId}/share` with
  `{ recipient_email }`. 202 → success toast + close. 4xx → inline error.
- New hook `useShareReport(sessionId)` (TanStack Query mutation) +
  `shareReport()` in `lib/api/reports.ts`.

### 4.2 Endpoint (`app/modules/reporting/router.py`)

`POST /api/reports/session/{session_id}/share`

- **Auth:** `reports.view` or `is_super_admin` (same guard as report view).
- **Body:** `ShareReportIn { recipient_email: EmailStr }`.
- **Preconditions:** the report exists and `status == "ready"` (else **409**;
  a pending/failed report cannot be shared).
- **Action:** insert a `report_shares` row (`status="pending"`), enqueue
  `share_report_pdf.send(share_id, tenant_id, correlation_id)`, return **202**
  `{ share_id, status: "pending" }`.
- **Rate limit (declared in the router):** sharing sends external email, so it
  gets its own tighter class than the generic authenticated bucket:
  **10/min per-IP** and a **per-tenant daily cap** (e.g. 200/day). Added to the
  root CLAUDE.md rate-limit table as the "report share" class.
- **No recipient PII in logs:** log `share_id` + a masked recipient
  (`a***@domain`), never the full address; never the candidate email.

### 4.3 Actor (`app/modules/reporting/actors.py`)

`share_report_pdf(share_id: str, tenant_id: str, correlation_id: str)`
— async, queue **`report_share`**.

1. Bypass-RLS session + `SET LOCAL app.current_tenant = tenant_id`.
2. Load the `report_shares` row; **idempotency gate** — if `status == "sent"`,
   return. Reload the recipient email from the row (so **no recipient PII rides
   in broker args**, satisfying the "IDs not bodies" actor rule).
3. Load the `ReportRead` for the session (+ presigned reference photo, candidate
   name/role/stage) exactly as the report view assembles it.
4. `status="rendering"` → `render_report_pdf(...)` → PDF bytes.
5. Upload PDF to **R2** under a private, tenant-prefixed key
   `report-shares/{tenant_id}/{share_id}.pdf`; store the key on the row.
6. `send_email(..., attachments=[(filename, pdf_bytes, "application/pdf")])`
   using the new `report_share.html` template (short cover note from the
   sharing recruiter's org; mentions the attached PDF).
7. `status="sent"`, `sent_at=now()`. Write an **audit-log** event
   `report.shared` (`actor_id`=requester, `resource_type="session_report"`,
   `resource_id`=report id, masked recipient in metadata, `correlation_id`).
8. **Retry policy:** transient failures (render flake, R2 5xx, email 5xx) retry
   with backoff; permanent failures (missing report, validation) mark the row
   `status="failed"` with a truncated error and do not retry.

Storing the PDF on R2 + the `report_shares` row gives: a durable record of
exactly what a client received (important for an agency reselling — dispute
trail), share history per report, and re-send without re-render.

---

## 5. Supporting Changes

### 5.1 Notifications — attachment support

Extend the provider-agnostic interface in
`app/modules/notifications/service.py`:

```python
Attachment = tuple[str, bytes, str]  # (filename, content_bytes, content_type)

async def send_email(
    *, to: str, subject: str, html: str,
    attachments: list[Attachment] | None = None,
) -> None: ...
```

- `EmailProvider.send` protocol + both providers updated.
- **ResendProvider:** map to Resend's `attachments` param
  (`{filename, content: base64}`).
- **DryRunProvider:** log each attachment's `filename` + byte length (never the
  bytes). Existing callers pass `attachments=None` (default) — no behavior change.

### 5.2 New table `report_shares` (migration `0061`)

Tenant-scoped, canonical RLS pair (`tenant_isolation` USING+WITH CHECK with
`NULLIF(...,'')::uuid`, `service_bypass`); `tenant_id` `ON DELETE CASCADE`;
registered in `_TENANT_SCOPED_TABLES` (`app/main.py:36`) and in the
`_assert_rls_completeness` enumerated list.

Columns: `id` (uuid pk), `tenant_id`, `session_id`, `report_id`,
`recipient_email` (text), `status` (`pending|rendering|sent|failed`),
`pdf_r2_key` (text null), `requested_by` (user id), `requested_at`, `sent_at`
(null), `error` (text null). Down-migration drops the table.

### 5.3 New email template

`app/modules/notifications/templates/report_share.html` — brief cover note
(candidate name, role, who shared it) + a line that the full evaluation is
attached as a PDF. Built via the existing `render_template(...)` path. Uses
`settings.frontend_base_url` for any BinQle link (the recipient is a client, not
a candidate — **not** `candidate_session_base_url`).

### 5.4 "See full session" stub

Rendered as a styled button in the PDF hero pointing at a placeholder
"coming soon" URL (a constant). This is the explicit seam where the future
**shareable session video** link will plug in. No video sharing is built now.
The spec for that feature will replace the placeholder with a tokenized public
video URL.

---

## 6. Edge Cases

- **Report not ready** → 409 at the endpoint; actor never enqueued.
- **No reference photo** (older sessions) → monogram from candidate initials.
- **Un-assessed dimension** → omitted from the score band entirely.
- **Empty `our_read`** → that line omitted; the candidate quote carries the card.
- **Redacted candidate** (PII gate) → name/initials fall back to a neutral label.
- **Many questions / long content** → multi-page flow; `break-inside: avoid` on
  cards keeps them whole.
- **Re-share to the same/another recipient** → a new `report_shares` row each
  time (full history); idempotency is per-row, not per-report.

---

## 7. Testing

Backend (test deltas required for `reporting` + `notifications`):
- Endpoint: auth guard, `EmailStr` validation, `status != ready` → 409,
  happy-path → 202 + row inserted + actor enqueued (enqueue mocked, not real
  Redis), rate-limit declared.
- Actor: idempotency (`status=="sent"` short-circuits), state transitions
  (`pending→rendering→sent` / `→failed`), reloads recipient from row (no PII in
  args), audit event written, masked-recipient logging.
- `send_email` attachment plumbing: ResendProvider maps attachments; DryRun logs
  filename+size; default `None` unchanged.
- RLS: cross-tenant read of `report_shares` returns 0 rows; table present in the
  startup completeness check.
- PDF render **smoke** only: `render_report_pdf` returns non-empty bytes starting
  with `%PDF`. No pixel/snapshot testing.

Frontend (`frontend/app`):
- Dialog: opens from the Share button, validates email, Send shows loading,
  success closes + toasts, server error shows inline.
- `shareReport()` posts the right payload to the right URL.

---

## 8. Module Touch List

- `frontend/app/components/dashboard/reports/ReportTopBar.tsx` (+ a new
  `ShareReportDialog` component), `lib/api/reports.ts`, a `useShareReport` hook.
- `app/modules/reporting/router.py` (endpoint + rate limit + `ShareReportIn`).
- `app/modules/reporting/actors.py` (`share_report_pdf` on `report_share`).
- `app/modules/reporting/pdf/` (new: Jinja2 print template, fonts,
  `render_report_pdf`, stamp/monogram helpers — only Playwright import site).
- `app/modules/reporting/models.py` / `schemas.py` (`report_shares` ORM +
  `ShareReportIn`/`ShareReportOut`).
- `app/modules/notifications/service.py` (+ templates/`report_share.html`).
- `migrations/versions/0061_report_shares.py`.
- `app/main.py` (`_TENANT_SCOPED_TABLES` + completeness list).
- `Dockerfile.vision` (Chromium + Playwright), `docker-compose.yml`
  (`-Q vision reel report_share`), `pyproject.toml` (playwright dep).
- Root + backend CLAUDE.md (rate-limit "report share" class; vision worker now
  also runs `report_share` + carries Chromium).

---

## 9. Open Questions / Future

- **White-label** (agency branding instead of BinQle) — future, likely a
  tenant-settings toggle + template variant.
- **Secure-link delivery** (vs attachment) — future; the `report_shares` row +
  R2 key already make a presigned-link mode cheap to add.
- **Shareable session video** — separate feature; replaces the stub button.
