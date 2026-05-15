# Job-scoped ATS sync — single-trigger pull, unified storage, plugin-clean adapter

**Date:** 2026-05-14
**Status:** Draft for user review
**Scope:** Backend — `app/modules/ats/`, `app/modules/auth/`, `app/modules/audit/`, `migrations/versions/`. Frontend follow-ups listed but specced separately.
**Supersedes:** `2026-05-12-ats-adapter-design.md` Section "Importer phases", `2026-05-13-ats-job-sync-client-stub-design.md` (entire — stub mechanism becomes obsolete under unified provenance).
**Amended by:** `2026-05-14-unified-job-creation-flow-design.md` — Section "ATS unification" / `_upsert_job` flow. The `derived_status` branch (`active` ↔ `blocked_pending_client_setup` based on `company_profile_completion_status`) and the `active ↔ blocked` toggle on the update path were both retired. Every ATS-imported job now lands in `status='draft'` regardless of profile state; the profile gate moved to the explicit `/enrich` and `/extract-signals` endpoints. Any text below describing the prior derivation reflects the historical design, not the current code.

---

## TL;DR

Replace the 5-phase ATS importer (clients → users → jobs → applicants → submissions) with a **single job-driven sync** in which every other entity is materialized lazily as a side-effect of importing the job that references it. Unify ATS-imported users and org_units into the same tables as native ones, tagged with `source` + `external_id`. Track Ceipal-side lifecycle changes (job status, submission status, recruiter assignments) as first-class audit events. Treat the adapter as a true plugin contract — Ceipal-specific quirks live in `adapters/ceipal.py`, the orchestrator is vendor-blind.

**Sync model — MVP:** manual trigger only ("Resync jobs from ATS" button). Cursor-based incremental using Ceipal's `modifiedAfter` parameter; cursor advances on success. First-ever sync (cursor NULL) does a full filter walk implicitly. Scheduled cron + reconciliation pass are explicitly deferred to a follow-up phase — adding them later is additive and requires no schema changes.

**Codebase posture:** there are no live production tenants, so this spec ships as a clean cutover — no feature flags, no V1/V2 protocol coexistence, no data backfill machinery. One PR, one migration, old code deleted in the same commit. The two shadow tables (`ats_user_mappings`, `ats_client_mappings`) are dropped outright.

## Motivation

Three problems with the current design, each load-bearing:

1. **Sync targets the wrong unit of work.** Today's importer pulls clients, users, applicants, and submissions as standalone phases. None of those map to a recruiter's mental model. A recruiter says "import the open requisitions from Ceipal" — that's one trigger, and everything else (which client, which recruiters, which candidates) is *scoped to the requisition*. The current shape causes (a) `getClientsList` to be walked twice per full sync (clients phase + inline-in-jobs), (b) `getUsersList` to be re-pulled in full every sync ignoring the cursor, and (c) submissions to be silently skipped when their job has no pipeline.
2. **Three inconsistent storage patterns for three entities.** `job_postings` carries `source` + `external_id` on the row. `organizational_units` uses a separate bridge table `ats_client_mappings`. `users` is fully shadowed by `ats_user_mappings` — ATS-synced people never enter the `users` table at all. Tracing the provenance of any row requires knowing which of the three patterns applies. This is the kind of asymmetry that compounds: each new ATS code path has to handle three shapes.
3. **No lifecycle change tracking.** Once a job or candidate is imported, the importer treats subsequent state changes in Ceipal (status moves, recruiter reassignment, submission rejection) as nothing. There is no diff, no event, no notification. For an enterprise integration that's the missing 70%.

## Goals

- **Single sync trigger** scoped to job postings; all other entities (clients/org_units, recruiter users, candidates, submissions) materialized on demand under the job that references them.
- **Unified storage**: `organizational_units` and `users` gain `source` + `external_id` + `external_source_metadata` columns and accept ATS-imported rows directly. `ats_user_mappings` and `ats_client_mappings` are dropped. `job_postings` already conforms.
- **Plugin-clean adapter contract**: `ATSAdapter` Protocol exposes a small, vendor-blind surface (job iteration, lazy entity resolution, change detection cursor). Ceipal-specific concerns (encoded ID URL-encoding, HTML entity decoding, magic-string sentinels, field-name inconsistencies, timezone-naive timestamps) live entirely in `adapters/ceipal.py`. Adding a new ATS vendor implements the Protocol and registers — nothing else changes.
- **First-class lifecycle events**: diff jobs and submissions between sync runs; emit `ats.job.status_changed`, `ats.job.fields_updated`, `ats.submission.status_changed`, etc. Plumb to audit log and notifications. Recruiter sees Ceipal-side changes reflected immediately.
- **Auditability on every row**: every ATS-created row carries `source='ats_ceipal'`, `external_id`, and `external_source_metadata.synced_by_user_id`. Every state mutation writes an audit row with `actor_id` (the recruiter who clicked Resync). System actions (e.g. auto-disable on credential failure) use the connection's installer as actor.
- **Enterprise-grade rate limit, RLS, and PII posture** — declared at the router, RLS policy verified on every new column path, sensitive applicant fields (Indian Aadhaar number, resume tokens) stripped before persistence.

## Non-goals

- **Two-way sync.** ProjectX → Ceipal is not in scope. The adapter Protocol intentionally exposes no write methods.
- **Resume document ingestion.** Submission documents (`resume_token`, `Documents[]`) are not fetched. Already excluded by the prior spec; preserved in `source_metadata` for future enablement.
- **Other ATS vendors (Greenhouse, Workday).** The Protocol is designed to accommodate them; no concrete adapter is built in this work.
- **Custom field mapping.** Tenants cannot remap arbitrary Ceipal custom fields to ProjectX columns. Fixed shape.
- **Per-business-unit sync filtering.** `business_unit_id` is captured into `source_metadata` for future use; not a filter axis at this phase.
- **Scheduled background sync.** MVP is manual-trigger only. The recruiter clicks "Resync jobs from ATS." A cron-driven background poller is deferred to a follow-up phase; the schema is structured so adding it requires only new code (a CLI command + cron entry), no migrations.
- **Reconciliation pass (archive detection).** Detecting jobs that left the active filter in Ceipal (e.g. moved to "Closed by Client") requires a full filter walk independent of `modifiedAfter`. Deferred to the same follow-up phase as the cron scheduler.
- **Real-time push.** Sync is poll-based. Ceipal has no webhooks.
- **Automatic pipeline stage application** when a Ceipal submission status changes (e.g. "L2 Rejected"). Default is **advisory mode**: the change is logged + a recruiter task surfaces. Auto-apply (`mirror mode`) is opt-in per connection and ships off by default.

## Background — what we have today

Code under `app/modules/ats/` (1,178 lines in `importer.py` alone). Key references:

| File | Role today | Status under this spec |
|---|---|---|
| `adapter.py` | `ATSAdapter` Protocol with 6 iterator methods + `ensure_authenticated` + `count_jobs` | Rewritten — see "The adapter contract" |
| `adapters/ceipal.py` | Sole concrete adapter. Implements all 6 iterators. Has the `since`-cursor-ignored bug in `list_users` (`ceipal.py:383`). | Rewritten — must own all Ceipal-side normalization |
| `connection.py` | `ATSConnectionState` lifecycle + Fernet decrypt/encrypt boundary | Unchanged structurally; `last_synced_cursors` JSONB replaced by single `last_synced_at` column |
| `crypto.py` | Fernet MultiFernet with key rotation | Unchanged |
| `importer.py` | 5-phase orchestrator | **Deleted**, replaced by `orchestrator.py` |
| `actors.py` | `poll_ats_connection` Dramatiq actor | Modified — calls new orchestrator; only manual-trigger path for MVP |
| `router.py` | 8 endpoints incl. `POST /connections/{id}/sync` (super-admin only) | Modified — rate limit declared, `phases` param removed, advisory-mode toggle exposed, new endpoints for `reset-cursor`, stage-mappings, advisory-actions |
| `service.py` | Connection CRUD + sync log writers | Modified — sync trigger validates `job_status_filter` non-empty (422) |
| `models.py` | 5 ORM classes: `ATSConnection`, `ATSClientMapping`, `ATSUserMapping`, `ATSJobRecruiterAssignment`, `ATSSyncLog` | `ATSClientMapping` + `ATSUserMapping` dropped; `ATSJobRecruiterAssignment` refactored → `ATSJobAssignment` |
| `schemas.py` | Vendor-agnostic DTOs | Expanded for change detection payloads |
| `sources.py` | `ATSImportSource` adapter for `candidates.create_candidate()` | Unchanged interface |
| `errors.py` | Typed exception hierarchy | Unchanged |
| `authz.py` | `require_ats_admin` (super-admin only) | Unchanged |

Schema today (after migration `0035`):

- `organizational_units` — no `source`/`external_id`. ATS clients land here only via an `ats_client_mappings` bridge row.
- `users` — no `source`/`external_id`. ATS-side users live exclusively in `ats_user_mappings`. `users.auth_user_id` is NOT NULL.
- `job_postings` — has `source NOT NULL DEFAULT 'native'`, `external_id TEXT NULL`, `external_status TEXT NULL` (added in `0031_ats_core.py`).
- `candidates` and `candidate_job_assignments` — both have `source` + `external_id` already (`0013_candidates_core.py`, `0031_ats_core.py`).
- `ats_connections`, `ats_client_mappings`, `ats_user_mappings`, `ats_job_recruiter_assignments`, `ats_sync_logs` — all introduced in `0031_ats_core.py`. All have canonical `tenant_isolation` + `service_bypass` RLS pair.

Project constraints (from root `CLAUDE.md` and `backend/nexus/CLAUDE.md`):

- Every tenant-scoped table needs `tenant_isolation` (USING + WITH CHECK with `NULLIF(current_setting('app.current_tenant', true), '')::uuid`) AND `service_bypass` RLS policies. The startup `_assert_rls_completeness` in `app/main.py:60-64` aborts boot on any gap.
- Every public endpoint declares a rate limit at the router. The current `POST /api/ats/connections/{id}/sync` does not — that gap closes in this spec.
- Audit log is mandatory for "any action taken by a ProjectX-internal admin" and for "tenant_id-scoped state mutations" (root `CLAUDE.md`). Every ATS event in this spec emits an audit row.
- No raw PII in logs. Aadhaar, resume bodies, full JWT bearer values, OTP codes — all forbidden in `console.*`/Sentry/structlog.

Real Ceipal payload findings (from API exercise on 2026-05-14):

- `getJobPostingsList` returns the **full** job record per page item — same fields as `getJobPostingDetails` *except* `client` (the client display name string). Detail call is only needed when we don't already have a client mapping cached for that job's external_id.
- `assigned_recruiter` is a CSV of opaque user IDs (5 IDs in the sample). `primary_recruiter`/`posted_by`/`created_by` are single opaque IDs. **All ID-based, not name-based.** No collision risk on recruiter resolution.
- `client` on the job detail is a name string (`"Oracle"`). No `client_id` is returned on either list or detail. Resolution still requires `getClientsList` walk → name match → opaque client ID → `getClientDetails`.
- `getClientDetails/{client_id}` is the **source of truth** for org_unit creation. It adds a `contacts[]` array (client-side HR personnel — NOT Ceipal users; do not write to `users`).
- `getSubmissionsList?jobId=X` returns `job_seeker_id` (opaque applicant ID) but no PII. Resolving PII requires `getApplicantDetails/{applicant_id}`. Submission body includes `submission_status` (free-form string), `pipeline_status`, `source` (channel — "Naukri", "Career Portal"), and a `resume_token` that **must never be logged**.
- `getApplicantDetails` includes `aadhar_number` (Indian national biometric ID). This field MUST be stripped before persistence into `source_metadata`.
- Field name conventions differ across endpoints (`first_name` on users, `firstname` on applicants; `email_id` on users, `email` on applicants). Normalization is mandatory at the adapter boundary.
- All Ceipal timestamps are timezone-naive in the tenant's local time. The user record's `timezone` field (e.g. `"Asia/Kolkata"`) is the source for normalization. **All ingested timestamps must be converted to UTC before persistence.**
- Opaque IDs include `/`, `+`, `=` characters. URL path encoding is mandatory.
- `closing_date` can be a non-date string (`"Open Until Filled"`, sometimes with leading space). Safe-parse with null fallback.
- HTML-encoded description bodies (`&nbsp;`, `<br />`, `&#39;`) must be `html.unescape()`-decoded before saving to `description_raw`.

## Architecture overview

```
┌───────────────────────────────────────────────────────────────────────┐
│ Recruiter clicks "Resync jobs from ATS" in the dashboard              │
│   → POST /api/ats/connections/{id}/sync                               │
│ (super-admin can also POST /reset-cursor for forced full re-scan)     │
└────────────────────────────────┬──────────────────────────────────────┘
                                 │ enqueue Dramatiq message
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│ app/modules/ats/actors.py :: poll_ats_connection                      │
│   - acquire pg_try_advisory_xact_lock(connection_id)                  │
│   - load + decrypt ATSConnectionState                                 │
│   - ensure_authenticated()                                            │
│   - ATSSyncOrchestrator(adapter).run()                                │
│   - persist mutated state + advance last_synced_at on success         │
│   - finalize ats_sync_logs                                            │
└────────────────────────────────┬──────────────────────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│ ATSSyncOrchestrator (vendor-blind)                                    │
│                                                                       │
│   modified_after = connection.last_synced_at   # NULL on first sync   │
│                                                                       │
│   for job_payload in adapter.iter_jobs(status_ids, modified_after):   │
│       client_org_unit = resolve_client(job_payload, adapter)          │
│       recruiter_users = resolve_recruiters(job_payload, adapter)      │
│       diff = upsert_job(job_payload, client_org_unit, recruiter_users)│
│       emit_events(diff)                                               │
│       for submission in adapter.iter_submissions(job_external_id):    │
│           candidate = resolve_candidate(submission, adapter)          │
│           sub_diff = upsert_assignment(submission, candidate, job)    │
│           emit_events(sub_diff)                                       │
│                                                                       │
│   connection.last_synced_at = sync_started_at  # only on success      │
└───────────────────────────────────────────────────────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Resolution helpers (lazy, sync-scoped in-memory cache):               │
│   - resolve_client: lookup org_units.external_id, else walk clients,  │
│     else call getClientDetails, else create org_unit                  │
│   - resolve_recruiter: lookup users.external_id, else getUserDetails, │
│     else create users row (auth_user_id=NULL, is_active=false)        │
│   - resolve_candidate: lookup candidates.external_id, else            │
│     getApplicantDetails, strip aadhaar, else create candidate         │
└───────────────────────────────────────────────────────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Event emission (every diff):                                          │
│   - audit_log.write(actor=caller, action, resource, correlation_id)   │
│   - notifications.dispatch(per event catalogue)                       │
│   - realtime.publish(tenant_topic, event_payload)                     │
└───────────────────────────────────────────────────────────────────────┘
```

**Deferred to follow-up phase (cron + reconciliation):**
- Scheduled invocation of the same orchestrator at a fixed cadence.
- A separate "reconciliation pass" that walks the full filter (no `modifiedAfter`) to detect jobs that left the active filter in Ceipal (archived/closed). For MVP, the recruiter manually archives these in ProjectX when they notice.

Three resolution boundaries — every one of them carries the **dedup-by-existing-row-then-lazy-fetch-then-create** pattern, all keyed off `(tenant_id, source, external_id)` uniqueness:

| Boundary | Lookup key | Miss action | Audit on create |
|---|---|---|---|
| Org unit (client) | `(tenant_id, source='ats_ceipal', external_id=<client_id>)` | (a) walk `getClientsList` to map name → client_id; (b) `getClientDetails`; (c) insert `organizational_units` row | `ats.org_unit.imported` |
| Recruiter user | `(tenant_id, source='ats_ceipal', external_id=<user_id>)` | `getUserDetails`; email-collision dedup against existing native users; insert `users` row with `auth_user_id=NULL` | `ats.user.imported` / `ats.user.linked_to_native` / `ats.user.collision_skipped` |
| Candidate | `(tenant_id, source='ats_ceipal', external_id=<applicant_id>)` | `getApplicantDetails`; PII strip; `candidates.import_candidate()` | `candidate.imported` (existing) |

## The adapter contract — plugin design

Every vendor-specific concern lives behind this Protocol. The orchestrator never sees Ceipal field names, Ceipal timestamps, or Ceipal sentinel values.

### Protocol shape

```python
# app/modules/ats/adapter.py

from typing import AsyncIterator, ClassVar, Protocol
from datetime import datetime

class ATSAdapter(Protocol):
    """A vendor adapter for one specific ATS (Ceipal, Greenhouse, Workday, ...).

    Adapters own all wire-format quirks: field name normalization, encoded ID
    URL-escaping, timezone normalization, HTML entity decoding, sentinel value
    handling, and per-vendor pagination.

    All datetimes returned by adapter methods MUST be timezone-aware UTC.
    All string fields MUST be trimmed; empty strings MUST be returned as None
    when they semantically mean 'absent'.
    """

    vendor: ClassVar[str]                       # e.g. 'ats_ceipal'
    capabilities: ClassVar['ATSAdapterCapabilities']
    state: 'ATSConnectionState'

    async def ensure_authenticated(self) -> None: ...
    """Idempotent. Refresh tokens at ≥80% of access-token lifetime."""

    async def list_job_statuses(self) -> list['ATSJobStatus']: ...
    """For the filter-config UI. Should be cheap; one call typically."""

    async def iter_jobs(
        self,
        *,
        status_external_ids: list[str],
        modified_after: datetime | None,
    ) -> AsyncIterator['ATSJobPayload']: ...
    """Yields ALL job-relevant fields the adapter can produce from its list
    endpoint. For Ceipal, this is the full getJobPostingsList row PLUS a
    lazy-loaded `client_name` (the adapter calls getJobPostingDetails only
    when the orchestrator marks the job as new-or-changed, via the
    `enrich_job` callback)."""

    async def enrich_job(self, job: 'ATSJobPayload') -> 'ATSJobPayload': ...
    """Fill in fields not available on the list endpoint (e.g. Ceipal's
    `client` name string). Called once per new-or-changed job."""

    async def iter_clients(self) -> AsyncIterator['ATSClientPayload']: ...
    """Used only for name → external_id index. Vendor-blind cache key."""

    async def get_client(self, *, external_id: str) -> 'ATSClientPayload': ...
    """Source of truth for org_unit field values."""

    async def get_user(self, *, external_id: str) -> 'ATSUserPayload': ...
    """Source of truth for user row values."""

    async def iter_submissions(
        self,
        *,
        job_external_id: str,
        modified_after: datetime | None,
    ) -> AsyncIterator['ATSSubmissionPayload']: ...

    async def get_applicant(self, *, external_id: str) -> 'ATSApplicantPayload': ...
    """PII-bearing. Orchestrator strips sensitive fields before persistence."""
```

### Capabilities descriptor

```python
@dataclass(frozen=True)
class ATSAdapterCapabilities:
    """Vendor-agnostic descriptor of what this adapter can do. The orchestrator
    branches on these flags to skip optimizations a vendor doesn't support."""

    supports_modified_after_cursor: bool       # If False, full scan every sync
    supports_per_job_submission_cursor: bool
    supports_client_search_by_name: bool        # If True, skip the iter_clients walk
    job_detail_required_for_client_name: bool   # Ceipal: True
    rate_limit_qps: float                       # For pacing
```

For Ceipal: `(True, True, False, True, 0.5)`. A future Greenhouse adapter sets `supports_client_search_by_name=True` because Greenhouse has a `/companies?name=...` filter; the orchestrator skips the `iter_clients` walk.

### Canonical DTOs

All DTOs are Pydantic models in `app/modules/ats/schemas.py`. Every datetime is `datetime` with `tzinfo=UTC`. Every optional string field is `str | None`, never `""`.

```python
class ATSJobStatus(BaseModel):
    external_id: str
    name: str

class ATSJobPayload(BaseModel):
    external_id: str
    title: str
    description_raw: str                        # HTML-decoded
    description_enriched: str | None
    external_status: str                        # e.g. "Active"
    external_status_id: str                     # e.g. "1"
    client_external_name: str | None            # Set during enrich_job
    client_external_id: str | None              # Set during resolution
    created_external_id: str | None             # Ceipal created_by user
    posted_by_external_id: str | None
    primary_recruiter_external_id: str | None
    assigned_recruiter_external_ids: list[str]
    business_unit_id: int | None
    country: str | None
    primary_city: str | None
    primary_state: str | None
    secondary_locations: list[dict] | None
    skills: list[str]
    pay_rates: list[dict]
    deadline: date | None                       # Safe-parsed
    external_created_at: datetime               # UTC
    external_modified_at: datetime              # UTC
    raw: dict                                   # Full raw payload for source_metadata

class ATSClientPayload(BaseModel):
    external_id: str
    name: str
    website: str | None
    industry: str | None                        # 'industry_exp' if not '0' or ''
    country: str | None
    state: str | None
    city: str | None
    business_unit_id: int | None
    external_created_at: datetime | None
    external_modified_at: datetime | None
    contacts: list['ATSClientContact'] = []
    raw: dict

class ATSClientContact(BaseModel):
    external_id: str
    name: str | None
    email: str | None
    designation: str | None
    phone: str | None

class ATSUserPayload(BaseModel):
    external_id: str
    email: str                                  # 'email_id' normalized
    full_name: str
    role: str | None
    business_unit_id: int | None
    timezone: str | None                        # IANA, e.g. 'Asia/Kolkata'
    external_status: str                        # "Active" / "Inactive"
    raw: dict

class ATSSubmissionPayload(BaseModel):
    external_id: str
    job_external_id: str
    applicant_external_id: str
    submitted_by_external_id: str | None
    external_status: str                        # e.g. "L2 Rejected"
    pipeline_status: str | None
    submission_channel: str | None              # e.g. "Naukri" (Ceipal 'source')
    pay_rate: float | None
    pay_currency: str | None
    external_submitted_at: datetime             # UTC
    external_modified_at: datetime              # UTC
    raw: dict                                   # MUST NOT contain resume_token

class ATSApplicantPayload(BaseModel):
    external_id: str
    first_name: str | None
    last_name: str | None
    email: str | None
    secondary_email: str | None
    mobile: str | None
    address: str | None
    city: str | None
    state: str | None
    country: str | None
    applicant_source: str | None                # e.g. "Naukri" (Ceipal 'source')
    raw: dict                                   # MUST NOT contain aadhar_number, ssn, etc.
```

### Registry

```python
# app/modules/ats/registry.py

from app.modules.ats.adapters.ceipal import CeipalAdapter
# from app.modules.ats.adapters.greenhouse import GreenhouseAdapter   # future

_REGISTRY: dict[str, type[ATSAdapter]] = {
    CeipalAdapter.vendor: CeipalAdapter,
}

def get_ats_adapter(state: ATSConnectionState) -> ATSAdapter:
    cls = _REGISTRY.get(state.vendor)
    if cls is None:
        raise ATSPermanentError(f"Unknown ATS vendor: {state.vendor!r}")
    return cls(state=state)
```

Adding a new adapter is **two lines**: a new module under `adapters/` implementing the Protocol, and a new entry in `_REGISTRY`. The router's `ConnectionCreateRequest` discriminated union (single existing extension point) is the third edit. Nothing else changes.

### Vendor canonicalization

Vendor string is `'ats_ceipal'` (matches `job_postings.source` convention from `0031_ats_core.py`). Future vendors: `'ats_greenhouse'`, `'ats_workday'`. A constants module (`app/modules/ats/constants.py`) exposes:

```python
ATS_VENDOR_CEIPAL = 'ats_ceipal'
ATS_VENDOR_PREFIX = 'ats_'

def is_ats_source(source: str) -> bool:
    return source.startswith(ATS_VENDOR_PREFIX)
```

No string concatenation of vendor names elsewhere in the codebase.

## Data model — schema changes

**One Alembic migration**, `0036_ats_unified_sync.py`. Because the codebase has no live production tenants, this is a clean cutover: the migration creates the final target schema directly, with no transitional columns, no data backfill, no shadow-table-drop-later sequence. After running `supabase db reset` + this migration, the schema matches the target shape exactly.

### Migration `0036_ats_unified_sync.py` — full consolidated migration

```python
"""ATS unified sync — single-trigger pull, unified storage."""

# ─── users: provenance + nullable auth ─────────────────────────────────
op.alter_column('users', 'auth_user_id', nullable=True)
op.add_column('users', sa.Column('source', sa.Text(), nullable=False, server_default='native'))
op.add_column('users', sa.Column('external_id', sa.Text(), nullable=True))
op.add_column('users', sa.Column('external_source_metadata', JSONB, nullable=True))
op.create_check_constraint(
    'users_source_external_id_check', 'users',
    "(source = 'native') OR (source LIKE 'ats_%' AND external_id IS NOT NULL)",
)
op.create_index(
    'users_external_identity_uniq', 'users',
    ['tenant_id', 'source', 'external_id'],
    unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
)

# ─── organizational_units: provenance ──────────────────────────────────
op.add_column('organizational_units', sa.Column('source', sa.Text(), nullable=False, server_default='native'))
op.add_column('organizational_units', sa.Column('external_id', sa.Text(), nullable=True))
op.add_column('organizational_units', sa.Column('external_source_metadata', JSONB, nullable=True))
op.create_check_constraint(
    'org_units_source_external_id_check', 'organizational_units',
    "(source = 'native') OR (source LIKE 'ats_%' AND external_id IS NOT NULL)",
)
op.create_index(
    'org_units_external_identity_uniq', 'organizational_units',
    ['client_id', 'source', 'external_id'],
    unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
)

# ─── job_postings: change-tracking + quarantine ────────────────────────
op.add_column('job_postings', sa.Column('external_last_modified_at', sa.TIMESTAMPTZ(), nullable=True))
op.create_index(
    'job_postings_ats_modified', 'job_postings',
    ['tenant_id', 'source', 'external_last_modified_at'],
    postgresql_where=sa.text("source LIKE 'ats_%'"),
)
op.add_column('job_postings', sa.Column('import_retry_count', sa.Integer(), nullable=False, server_default='0'))
op.add_column('job_postings', sa.Column('import_quarantined_at', sa.TIMESTAMPTZ(), nullable=True))
op.add_column('job_postings', sa.Column('import_last_error', sa.Text(), nullable=True))
op.create_index(
    'job_postings_quarantined', 'job_postings', ['tenant_id'],
    postgresql_where=sa.text("import_quarantined_at IS NOT NULL"),
)

# ─── candidate_job_assignments: change-tracking ────────────────────────
op.add_column('candidate_job_assignments', sa.Column('external_status', sa.Text(), nullable=True))
op.add_column('candidate_job_assignments', sa.Column('external_pipeline_status', sa.Text(), nullable=True))
op.add_column('candidate_job_assignments', sa.Column('external_last_modified_at', sa.TIMESTAMPTZ(), nullable=True))

# ─── ats_connections: cursor + sync-mode + tz + drop vestigial cols ────
op.add_column('ats_connections', sa.Column('last_synced_at', sa.TIMESTAMPTZ(), nullable=True))
op.add_column('ats_connections', sa.Column('tenant_timezone', sa.Text(), nullable=True))
op.add_column('ats_connections', sa.Column('status_sync_mode', sa.Text(), nullable=False, server_default='advisory'))
op.create_check_constraint(
    'ats_connections_status_sync_mode_check', 'ats_connections',
    "status_sync_mode IN ('advisory', 'mirror', 'one_way')",
)
# Drop vestigial scheduler columns from 0031 (no scheduler in MVP; future cron
# adds its own column set when it lands).
op.drop_index('ix_ats_connections_due', table_name='ats_connections')
op.drop_column('ats_connections', 'poll_lock_acquired_at')
op.drop_column('ats_connections', 'next_poll_at')
op.drop_column('ats_connections', 'poll_interval_seconds')
op.drop_column('ats_connections', 'last_synced_cursors')   # JSONB blob replaced by last_synced_at

# ─── Drop legacy shadow tables ─────────────────────────────────────────
op.drop_index('uq_ats_user_mappings_external', table_name='ats_user_mappings')
op.drop_table('ats_user_mappings')

op.drop_index('uq_ats_client_mappings_external', table_name='ats_client_mappings')
op.drop_table('ats_client_mappings')

# ─── Rename + refactor ats_job_recruiter_assignments ──────────────────
op.rename_table('ats_job_recruiter_assignments', 'ats_job_assignments')
op.drop_column('ats_job_assignments', 'external_user_id')
op.drop_column('ats_job_assignments', 'ats_vendor')
op.add_column('ats_job_assignments', sa.Column(
    'user_id', UUID(as_uuid=True),
    sa.ForeignKey('users.id', ondelete='CASCADE'),
    nullable=False,
))
op.add_column('ats_job_assignments', sa.Column('role', sa.Text(), nullable=False))
op.create_check_constraint(
    'ats_job_assignments_role_check', 'ats_job_assignments',
    "role IN ('assigned_recruiter', 'primary_recruiter', 'posted_by', 'created_by')",
)
op.create_unique_constraint(
    'uq_ats_job_assignments_job_user_role', 'ats_job_assignments',
    ['job_posting_id', 'user_id', 'role'],
)

# ─── New table: ats_stage_mappings (mirror-mode opt-in; ships empty) ───
op.create_table(
    'ats_stage_mappings',
    sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
    sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
    sa.Column('connection_id', UUID(as_uuid=True), sa.ForeignKey('ats_connections.id', ondelete='CASCADE'), nullable=False),
    sa.Column('external_status_label', sa.Text(), nullable=False),
    sa.Column('projectx_stage_id', UUID(as_uuid=True), sa.ForeignKey('job_pipeline_stages.id', ondelete='CASCADE'), nullable=False),
    sa.Column('action_on_match', sa.Text(), nullable=False),
    sa.Column('created_at', sa.TIMESTAMPTZ(), nullable=False, server_default=sa.func.now()),
    sa.Column('updated_at', sa.TIMESTAMPTZ(), nullable=False, server_default=sa.func.now()),
)
op.create_check_constraint(
    'ats_stage_mappings_action_check', 'ats_stage_mappings',
    "action_on_match IN ('move_to_stage', 'reject', 'archive', 'no_op')",
)
op.create_unique_constraint(
    'uq_ats_stage_mappings', 'ats_stage_mappings',
    ['connection_id', 'external_status_label'],
)
# canonical tenant_isolation + service_bypass RLS policies (NULLIF form)

# ─── New table: ats_advisory_actions ───────────────────────────────────
op.create_table(
    'ats_advisory_actions',
    sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
    sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
    sa.Column('connection_id', UUID(as_uuid=True), sa.ForeignKey('ats_connections.id', ondelete='CASCADE'), nullable=False),
    sa.Column('assignment_id', UUID(as_uuid=True), sa.ForeignKey('candidate_job_assignments.id', ondelete='CASCADE'), nullable=False),
    sa.Column('triggering_audit_event_id', UUID(as_uuid=True), nullable=False),
    sa.Column('external_status_before', sa.Text(), nullable=True),
    sa.Column('external_status_after', sa.Text(), nullable=False),
    sa.Column('suggested_target_stage_id', UUID(as_uuid=True), sa.ForeignKey('job_pipeline_stages.id', ondelete='CASCADE'), nullable=True),
    sa.Column('suggested_action', sa.Text(), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=False, server_default='pending'),
    sa.Column('resolved_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    sa.Column('resolved_at', sa.TIMESTAMPTZ(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMPTZ(), nullable=False, server_default=sa.func.now()),
)
op.create_check_constraint(
    'ats_advisory_actions_resolution_check', 'ats_advisory_actions',
    "resolution IN ('pending', 'applied', 'dismissed', 'superseded')",
)
op.create_check_constraint(
    'ats_advisory_actions_suggested_action_check', 'ats_advisory_actions',
    "suggested_action IN ('move_to_stage', 'reject', 'archive')",
)
op.create_index(
    'idx_ats_advisory_actions_pending', 'ats_advisory_actions',
    ['tenant_id', 'assignment_id'],
    postgresql_where=sa.text("resolution = 'pending'"),
)
# canonical tenant_isolation + service_bypass RLS policies
```

### `_TENANT_SCOPED_TABLES` updates in `app/main.py`

Same PR as the migration:

- **Remove:** `'ats_user_mappings'`, `'ats_client_mappings'`
- **Rename:** `'ats_job_recruiter_assignments'` → `'ats_job_assignments'`
- **Add:** `'ats_stage_mappings'`, `'ats_advisory_actions'`

The startup `_assert_rls_completeness` will fail boot if the list and `pg_policies` diverge — this is the canary.

### Required ORM model changes in the same PR

- `app/modules/auth/models.py::User.auth_user_id` → `Mapped[uuid.UUID | None]`
- `app/modules/auth/models.py::User` adds `source`, `external_id`, `external_source_metadata` mapped columns
- `app/modules/org_units/models.py::OrganizationalUnit` adds the same three columns
- `app/modules/ats/models.py`:
  - Delete `ATSUserMapping` and `ATSClientMapping` classes entirely
  - Rename `ATSJobRecruiterAssignment` → `ATSJobAssignment`; replace `external_user_id` with `user_id` FK + `role`
  - Add `ATSStageMapping` and `ATSAdvisoryAction` classes
  - Add `last_synced_at`, `tenant_timezone`, `status_sync_mode` to `ATSConnection`; remove `last_synced_cursors`, `next_poll_at`, `poll_interval_seconds`, `poll_lock_acquired_at`

### Required code changes in the same PR

- `app/modules/auth/router.py::accept_invite` — replace the `UPDATE ats_user_mappings` hook with `UPDATE users SET auth_user_id=...` when an invite for an `auth_user_id IS NULL` row is accepted
- `app/modules/ats/importer.py` — **deleted**
- `app/modules/ats/orchestrator.py` — **new** (replaces importer)
- `app/modules/ats/adapter.py` — Protocol rewritten in place (no V1/V2 split)
- `app/modules/ats/adapters/ceipal.py` — rewritten in place
- `app/modules/ats/actors.py` — `poll_ats_connection` calls the new orchestrator
- `app/modules/ats/service.py` — sync trigger validates `job_status_filter` non-empty (422); adds advisory-lock acquisition
- `app/modules/ats/router.py` — rate limits declared; new endpoints (`reset-cursor`, `stage-mappings`, `advisory-actions`); old `phases` parameter removed
- `tests/conftest.py` — add an `ats_user_factory` that omits `auth_user_id`

### Errored-job retry policy

The orchestrator tracks per-job import failures and quarantines after 3 consecutive failures:

```python
async def _mark_job_errored(self, job_payload, exc):
    """Increment retry count; quarantine if threshold reached."""
    QUARANTINE_THRESHOLD = 3
    new_count = job.import_retry_count + 1
    if new_count >= QUARANTINE_THRESHOLD:
        await db.execute(
            update(JobPosting)
            .where(JobPosting.id == job.id)
            .values(
                import_retry_count=new_count,
                import_quarantined_at=now_utc(),
                import_last_error=str(exc)[:1000],
            )
        )
        await log_event(
            action='ats.job.import_quarantined',
            resource_id=job.id,
            payload={'retry_count': new_count, 'error': str(exc)[:1000]},
        )
        await notifications.dispatch(
            type='ats_job_quarantined',
            recipients=resolve_recruiters_and_super_admins(job),
        )
    else:
        await db.execute(
            update(JobPosting)
            .where(JobPosting.id == job.id)
            .values(import_retry_count=new_count, import_last_error=str(exc)[:1000])
        )

async def _on_successful_job_import(self, job):
    """Reset retry count on success. Clear quarantine if previously set
    (recruiter retried + we succeeded)."""
    if job.import_retry_count > 0 or job.import_quarantined_at is not None:
        await db.execute(
            update(JobPosting)
            .where(JobPosting.id == job.id)
            .values(
                import_retry_count=0,
                import_quarantined_at=None,
                import_last_error=None,
            )
        )
```

When a job is quarantined, the orchestrator **skips it on every subsequent sync** until manually retried. The orchestrator filters quarantined jobs out of the per-row diff: a quarantined job whose `external_modified_at` advances will still be skipped until `import_quarantined_at` is cleared via the retry endpoint.

**Manual retry endpoint:**

```
POST /api/ats/jobs/{job_id}/retry-import
```

- **Auth:** recruiter with `jobs.edit` permission on the job's org_unit, OR super-admin.
- **Rate limit:** 30/min per user.
- **Behavior:** clears `import_quarantined_at` + resets `import_retry_count=0`. The next manual sync re-attempts. Returns 200 with the cleared job.
- **Audit:** `ats.job.import_quarantine_cleared` with `actor_id = caller`.

### `status_sync_mode` reference

Three values, hard-enforced by check constraint:

- `advisory` (default) — log status change as audit + recruiter notification. Insert a pending row in `ats_advisory_actions`. Do NOT auto-move ProjectX stage.
- `mirror` — log + auto-apply via `ats_stage_mappings` lookup. Borderline-candidate guard applies (per root CLAUDE.md hard rule).
- `one_way` — log only. No notifications, no advisory actions. Treats Ceipal as a one-time data source.

### Force full re-scan endpoint

Because cursor-only sync misses records that lost their `modified` bump (rare but possible) and records that left the filter (Active → Closed), the recruiter needs an escape hatch:

```
POST /api/ats/connections/{id}/reset-cursor
```

- **Auth:** super-admin only.
- **Rate limit:** 1/hour per tenant (this is a heavy operation).
- **Behavior:** `UPDATE ats_connections SET last_synced_at = NULL WHERE id = ?`. The next regular Resync click will perform a full filter walk (no `modifiedAfter` parameter) and re-process every active job. Existing rows in the DB are diffed in place — nothing is deleted.
- **Audit:** `ats.connection.cursor_reset` with `actor_id = caller` and a `reason` field captured from the request body.

### Future-phase migration placeholder

When the cron scheduler + reconciliation pass lands as a follow-up phase, that work adds its own migration that introduces:
- `ats_connections.next_poll_at`, `poll_interval_seconds` (real scheduler columns this time)
- `ats_connections.last_full_scan_at`, `full_scan_interval_hours`
- Possibly a per-job sync log table if archive detection needs forensic trails

This is **explicitly out of scope for this spec.** Noted only so the schema we ship doesn't conflict with the eventual shape.

## Importer flow — pseudocode

The orchestrator lives at `app/modules/ats/orchestrator.py`. It is vendor-blind by construction. Single sync mode — cursor-based incremental. Manual trigger only in MVP.

```python
class ATSSyncOrchestrator:
    def __init__(self, adapter: ATSAdapter, sync_log: ATSSyncLog, actor: User):
        self.adapter = adapter
        self.sync_log = sync_log
        self.connection = sync_log.connection
        self.tenant_id = sync_log.tenant_id
        self.actor = actor                                              # the recruiter who clicked Resync
        # Sync-scoped in-memory caches. Cleared after run() returns.
        self._client_index: dict[str, ATSClientPayload] | None = None   # lower(name) → payload
        self._resolved_users: dict[str, User] = {}                      # external_id → User
        self._resolved_orgs: dict[str, OrganizationalUnit] = {}         # external_id → OrgUnit

    async def run(self) -> ATSSyncResult:
        await self.adapter.ensure_authenticated()
        await self._validate_job_status_filter()                        # 422 if empty

        # If last_synced_at is NULL (first sync), modified_after=None triggers
        # a full filter walk. Otherwise we pass the cursor as a hint.
        modified_after = self.connection.last_synced_at
        sync_started_at = now_utc()

        async for raw_job in self.adapter.iter_jobs(
            status_external_ids=self.connection.job_status_filter['ids'],
            modified_after=modified_after,
        ):
            # Skip quarantined jobs before any expensive work.
            if await self._is_job_quarantined(raw_job.external_id):
                continue

            try:
                async with self._tenant_txn() as db:
                    job_payload = await self._maybe_enrich_job(db, raw_job)
                    org_unit = await self._resolve_client(db, job_payload)
                    recruiters = await self._resolve_recruiters(db, job_payload)
                    diff = await self._upsert_job(db, job_payload, org_unit, recruiters)
                    await self._emit_job_events(db, diff)
                    await self._on_successful_job_import(db, diff.job)   # clears retry/quarantine state

                # Submissions for this job are batched in their own transactions
                # to avoid holding job-level row locks for long.
                async for raw_sub_batch in self._iter_submissions_batched(
                    job_external_id=job_payload.external_id,
                    batch_size=50,
                ):
                    async with self._tenant_txn() as db:
                        for raw_sub in raw_sub_batch:
                            candidate = await self._resolve_candidate(db, raw_sub)
                            sub_diff = await self._upsert_assignment(db, raw_sub, candidate, diff.job)
                            await self._emit_submission_events(db, sub_diff)

            except ATSRateLimitedError:
                # Partial completion; finalize sync_log as 'partial'. No retry.
                # last_synced_at is NOT advanced — next run picks up from same point.
                raise
            except ATSPermanentError as exc:
                # Mark job as errored, continue to next. Invalidate cache entries
                # that were populated during the failed transaction.
                await self._mark_job_errored(job_payload, exc)
                self._invalidate_cache_for_failed_job(job_payload)

        # Cursor advance only if the entire run completed (no rate-limit or fatal).
        # last_synced_at = sync_started_at, NOT now() — so any record modified during
        # the sync run is caught on the next pass.
        await self._persist_cursor(sync_started_at)
```

### Key invariants

1. **Two-tier transaction model.** The job upsert (org_unit + recruiter users + job row + lifecycle events) is one transaction. Submissions for that job are batched in their own transactions of up to 50. This prevents a slow Ceipal tenant with 500 submissions on one job from holding row locks on `organizational_units` and `users` for minutes.
2. **Cache invalidation on rollback.** When a per-job transaction rolls back, the orchestrator MUST invoke `_invalidate_cache_for_failed_job(job_payload)` which removes any entries populated during the failed transaction from `_resolved_orgs` and `_resolved_users`. A subsequent job referencing the same external_id will re-query the DB to establish ground truth.
3. **Cursor advance is single-shot and conservative.** `last_synced_at` is set to `sync_started_at` (not `now()`) only after the entire iteration completes without a fatal error. Records modified during the sync are caught on the next pass. Errored jobs do NOT block cursor advance — they have their own quarantine state.
4. **All datetimes UTC at the boundary.** The adapter converts Ceipal's naive timestamps (which are tenant-local) to UTC using the connection's `tenant_timezone`. On every successful sync, `tenant_timezone` is refreshed from a real user record if currently NULL (the empty-tenant case starts as UTC fallback).
5. **The orchestrator never sees Ceipal field names.** All DTO fields use ProjectX naming. All HTML decoding, opaque-ID URL-encoding, sentinel handling (`"0"` industry, `"Open Until Filled"` date) happens inside `adapters/ceipal.py`.
6. **Resolution caches are sync-scoped.** A new `run()` starts with empty caches. Rollback invalidates entries; commit promotes them.
7. **Concurrency control.** Each sync acquires `pg_try_advisory_xact_lock(hashtextextended(connection_id::text, 0))` at the start of `poll_ats_connection`. Failure to acquire returns 409 from the HTTP path. This prevents the read-before-commit race on `ats_sync_logs.status='running'`.
8. **Quarantined jobs are filtered before iteration.** Before each `iter_jobs` element is processed, the orchestrator checks `job_postings.import_quarantined_at`. A quarantined job is skipped even if Ceipal returned it — it must be manually retried via `POST /api/ats/jobs/{job_id}/retry-import`.

### Email-collision matrix

`_resolve_recruiters` follows this decision table for each Ceipal user ID encountered. Outcomes are deterministic and audit-logged.

| Existing `users` row matching email? | Existing row's `external_id` | Outcome | Audit event |
|---|---|---|---|
| None | n/a | INSERT new row with `source='ats_ceipal'`, `auth_user_id=NULL`, `is_active=false` | `ats.user.imported` |
| Yes, `external_id IS NULL` | NULL | UPDATE existing row: set `external_id = <ceipal_id>`. **`source` stays `'native'`.** | `ats.user.linked_to_native` |
| Yes, `external_id == <ceipal_id>` | matches | UPDATE existing row: refresh `external_source_metadata` only | `ats.user.metadata_refreshed` |
| Yes, `external_id != <ceipal_id>` | mismatch | **SKIP. Log audit event. Notify super-admin.** | `ats.user.collision_skipped` |

Case 4 (collision skipped) does not advance the cursor for that user. On every subsequent sync, the same skip happens until a super-admin manually resolves the conflict (UI to be specced later).

### Client resolution

`_resolve_client` is similar but simpler because clients have no email-collision path (name is the only identifier, and we don't dedup across sources):

```
1. Lookup by (tenant_id, source='ats_ceipal', external_id=<client_id>):
   - HIT → return existing org_unit. Optionally refresh `external_source_metadata` in `cold` mode.

2. MISS:
   a. If client_external_id is None on the job (Ceipal: not yet resolved):
        - If self._client_index is None, build it via adapter.iter_clients() (one-time per sync, cached in memory). The index keys are normalized: `lower(strip(name))`.
        - Lookup external_id by `lower(strip(job.client_name))`. Case-insensitive matching is safe under the new model because identity is enforced by the `(tenant_id, source, external_id)` uniqueness index — case-folding cannot create duplicate org_units, it only reduces false-positive orphan warnings when Ceipal's job-side casing drifts from its client-record casing.
        - If still no match: emit ats.job.orphan_client warning, leave job org_unit_id=NULL.
        - Otherwise, recurse with the resolved external_id.

   b. Fetch authoritative payload: adapter.get_client(external_id=<client_id>).

   c. INSERT organizational_units row:
        client_id = self.tenant_id
        parent_unit_id = self._root_org_unit_id
        name = payload.name
        unit_type = 'client_account'
        is_root = False
        source = 'ats_ceipal'
        external_id = payload.external_id
        external_source_metadata = {
            'website': payload.website, 'industry': payload.industry,
            'country': payload.country, 'state': payload.state, 'city': payload.city,
            'business_unit_id': payload.business_unit_id,
            'contacts': [c.model_dump() for c in payload.contacts],
            'raw': payload.raw,
        }
        company_profile_completion_status = 'pending'
        created_by = self.sync_actor_id

   d. Backfill org_unit columns (website, industry, country, state, city)
      ONLY when the column is currently NULL — never overwrite recruiter-edited data.

   e. Audit: ats.org_unit.imported with the new org_unit_id.
```

`source_metadata.contacts` stores client-side HR people (the `contacts[]` array from `getClientDetails`). These are surfaced in the UI as "Client contacts" but never auto-invited or auto-mapped to ProjectX users.

### Candidate resolution

Same shape, additional PII strip:

```
1. Lookup by (tenant_id, source='ats_ceipal', external_id=<applicant_id>):
   - HIT → existing candidate row. Refresh source_metadata only if sub.external_modified_at > stored.

2. MISS:
   a. payload = await adapter.get_applicant(external_id=<applicant_id>)
   b. sanitized_raw = strip_sensitive_pii(payload.raw)
      # Removes: aadhar_number, ssn, passport_number, pan_number, drivers_license,
      # resume_token (any other token-like field with high entropy), all *_token fields.
   c. import_candidate(
        source=ATSImportSource(vendor='ats_ceipal'),
        request={
            name=join(payload.first_name, payload.last_name),
            email=payload.email,
            phone=payload.mobile,
            location=join(payload.city, payload.state, payload.country),
            external_id=payload.external_id,
            source_metadata=sanitized_raw,
        },
        user=self.sync_actor,
        tenant_id=self.tenant_id,
      )
   d. Audit: candidate.imported (existing event).
```

`strip_sensitive_pii` lives in `app/modules/candidates/pii.py` (new). Tested with payloads that contain every prohibited field; verifies they are absent post-strip.

### Cursor

A single `TIMESTAMPTZ` value on `ats_connections.last_synced_at`. That's it.

- `NULL` on a freshly-created connection. The orchestrator omits `modifiedAfter` from `getJobPostingsList` → Ceipal returns the full filtered list. This is the implicit first-sync semantics.
- Populated after every successful sync. Subsequent syncs pass `modifiedAfter=<formatted as YYYY-MM-DD HH:MM:SS in tenant_timezone>` → Ceipal returns only records modified after the cursor.
- Reset to `NULL` via `POST /api/ats/connections/{id}/reset-cursor` (super-admin escape hatch).

No per-job submission cursors. Whenever a job is touched (new, changed, or returned by Ceipal at all), the orchestrator pulls **all** submissions for that job. This is the right trade-off for MVP because:
- Most jobs have <50 submissions; the per-job pull is cheap.
- We don't have to trust whether a submission change bumps the parent job's `modified` (untested as of 2026-05-14; doesn't matter under this design).
- No state-growth pathology on tenants with thousands of historical jobs.

For the rare large-job case (>200 submissions on one job), pagination is handled by the adapter; the orchestrator processes batches of 50.

## Change detection & events

### Diff strategy

`_upsert_job` and `_upsert_assignment` produce a typed `DiffResult`:

```python
@dataclass
class JobDiffResult:
    kind: Literal['created', 'updated', 'unchanged']
    job: JobPosting
    changed_fields: list[str]                 # for 'updated'
    status_transition: tuple[str, str] | None # (old, new) external_status only
    recruiter_assignments_changed: bool
```

`changed_fields` is computed by comparing the incoming `ATSJobPayload` against the stored `job_postings` row PLUS its `ats_job_assignments`. Only fields the adapter is the source of truth for are diff'd — recruiter-edited fields (`description_enriched` with `enriched_manually_edited=true`, ProjectX-side status, etc.) are excluded.

### Event catalogue

Every event hits `app/modules/audit/log_event`. Selected events also hit `app/modules/notifications`.

| Event name | Trigger | Audit | Notify recipients | Realtime |
|---|---|---|---|---|
| `ats.sync.started` | Orchestrator entry | ✅ | — | — |
| `ats.sync.completed` | Orchestrator success | ✅ | super-admin (if errors) | — |
| `ats.sync.partial` | Rate-limit hit | ✅ | super-admin | — |
| `ats.sync.failed` | Non-recoverable | ✅ | super-admin | — |
| `ats.connection.disabled` | Credentials invalid | ✅ | super-admin | — |
| `ats.org_unit.imported` | New org_unit from ATS | ✅ | — | dashboard |
| `ats.org_unit.metadata_refreshed` | Cold-mode client detail refresh | ✅ | — | — |
| `ats.org_unit.orphan_client` | Client name not resolvable | ✅ | super-admin | — |
| `ats.user.imported` | New ATS user (`auth_user_id=NULL`) | ✅ | — | — |
| `ats.user.linked_to_native` | Email-collision case 2 | ✅ | super-admin | — |
| `ats.user.metadata_refreshed` | Re-resolved on subsequent sync | ✅ | — | — |
| `ats.user.collision_skipped` | Email-collision case 4 | ✅ | super-admin | — |
| `ats.job.imported` | New job | ✅ | recruiter (if assigned) | dashboard |
| `ats.job.fields_updated` | Non-status fields changed | ✅ | recruiter (if material) | dashboard |
| `ats.job.status_changed` | `external_status` transition | ✅ | recruiter | dashboard |
| `ats.job.archived` | Job disappeared from filter (warm/cold reconciliation) | ✅ | recruiter | dashboard |
| `ats.job.recruiter_assignments_changed` | Assignment set diff non-empty | ✅ | added/removed recruiters | dashboard |
| `ats.job.orphan_client` | Job has client name that doesn't resolve | ✅ | super-admin | — |
| `ats.submission.created` | New candidate-to-job link | ✅ | recruiter, HM | dashboard kanban |
| `ats.submission.status_changed` | `external_status` transition | ✅ | recruiter, HM | dashboard kanban |
| `ats.submission.fields_updated` | Non-status fields changed | ✅ | — | dashboard kanban |
| `ats.submission.removed` | Submission disappeared (reconciliation) | ✅ | recruiter | dashboard kanban |
| `ats.candidate.imported` (existing) | New candidate row | ✅ | — | — |
| `ats.candidate.linked_to_external` (existing) | Email match to existing native | ✅ | — | — |

### Actor for ATS-emitted events

| Sync trigger | Actor used in audit row | `action_source` field |
|---|---|---|
| Manual `POST /sync` | The HTTP caller (`UserContext.user.id`) | `manual` |
| Scheduled (cron tick) | `connection.created_by` (the human who installed the connection) | `scheduled` |
| System auto-disable on credential failure | `connection.created_by` | `system` |

Open enhancement (future): introduce a synthetic system actor with a reserved UUID. Defer until we have a second autonomous subsystem.

### Stage-mapping application (`mirror` mode only)

When `connection.status_sync_mode == 'mirror'` AND `ats.submission.status_changed` fires:

```python
mapping = await db.fetch_one(
    select(ATSStageMapping)
    .where(ATSStageMapping.connection_id == connection.id)
    .where(ATSStageMapping.external_status_label == new_status)
)
if not mapping or mapping.action_on_match == 'no_op':
    return

# Borderline candidates MUST NEVER be auto-advanced or auto-rejected
# (root CLAUDE.md hard rule).  Mirror mode degrades to advisory for this row.
candidate_classification = await analysis.get_latest_classification(candidate_id)
if candidate_classification == 'borderline':
    await log_event(
        action='ats.submission.borderline_mirror_blocked',
        resource_id=submission_id,
        payload={
            'reason': 'borderline_classification_protects_auto_action',
            'external_status': new_status,
            'mapped_action': mapping.action_on_match,
        },
    )
    await notifications.dispatch(
        type='ats_borderline_blocked',
        recipients=resolve_recruiters(job),
    )
    return  # The status change is logged + recruiter notified, no auto-move.

if mapping.action_on_match == 'move_to_stage':
    await pipelines.move_assignment_to_stage(
        assignment_id=...,
        target_stage_id=mapping.projectx_stage_id,
        actor=sync_actor,
        reason=f'ATS mirror: {old_status} → {new_status}',
    )
    await log_event('candidate.stage_moved_by_ats_mirror', ...)
```

The Borderline guard is non-negotiable — it is a hard product invariant from the root CLAUDE.md and reflected in the audit log so the recruiter always has the trail.

`advisory` mode (default) emits the event + notification without the move. The recruiter sees an action item: "Naresh Reddy was marked L2 Rejected in Ceipal. Apply to this candidate?"

### Advisory actions (backend storage)

Mirror mode and advisory mode differ only in whether the action auto-applies. To support the frontend `pending_advisory_action` field and the "Apply" button per advisory event, advisory actions need persistent backend storage that survives between sync runs and remains queryable from kanban/list views.

New table in migration `0040`:

```python
op.create_table(
    'ats_advisory_actions',
    sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
    sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
    sa.Column('connection_id', UUID(as_uuid=True), sa.ForeignKey('ats_connections.id', ondelete='CASCADE'), nullable=False),
    sa.Column('assignment_id', UUID(as_uuid=True), sa.ForeignKey('candidate_job_assignments.id', ondelete='CASCADE'), nullable=False),
    sa.Column('triggering_audit_event_id', UUID(as_uuid=True), nullable=False),
    sa.Column('external_status_before', sa.Text(), nullable=True),
    sa.Column('external_status_after', sa.Text(), nullable=False),
    sa.Column('suggested_target_stage_id', UUID(as_uuid=True), sa.ForeignKey('job_pipeline_stages.id', ondelete='CASCADE'), nullable=True),
    sa.Column('suggested_action', sa.Text(), nullable=False),   # 'move_to_stage' | 'reject' | 'archive'
    sa.Column('resolution', sa.Text(), nullable=False, server_default='pending'),  # 'pending' | 'applied' | 'dismissed' | 'superseded'
    sa.Column('resolved_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    sa.Column('resolved_at', sa.TIMESTAMPTZ(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMPTZ(), nullable=False, server_default=sa.func.now()),
)
op.create_index(
    'idx_ats_advisory_actions_pending',
    'ats_advisory_actions',
    ['tenant_id', 'assignment_id'],
    postgresql_where=sa.text("resolution = 'pending'"),
)
# canonical tenant_isolation + service_bypass RLS policies
```

Lifecycle:

| Event | Effect on `ats_advisory_actions` |
|---|---|
| `ats.submission.status_changed` fires + connection is `advisory` mode | INSERT new row with `resolution='pending'`; the kanban card shows the indicator. |
| Same submission's status changes again before resolution | Previous pending row marked `resolution='superseded'`; new row inserted as `pending`. The kanban card always reflects only the latest pending action. |
| Recruiter clicks Apply | `POST /api/ats/advisory-actions/{id}/apply` → executes the stage move/reject/archive via `pipelines.move_assignment_to_stage` → marks `resolution='applied'`. Borderline guard applies just as in mirror mode. |
| Recruiter clicks Dismiss | `POST /api/ats/advisory-actions/{id}/dismiss` → marks `resolution='dismissed'`. The kanban indicator clears. |
| Recruiter manually advances the candidate via the normal flow while a pending action exists | `pipelines.move_assignment_to_stage` checks for pending actions; if found, marks them `resolution='superseded'` and writes a `ats.advisory_action.superseded_by_manual_move` audit row. |

The frontend `pending_advisory_action` field on `CandidateJobAssignmentSummary` (S6) is a JOIN against `ats_advisory_actions WHERE assignment_id = ca.id AND resolution = 'pending' ORDER BY created_at DESC LIMIT 1`.

New endpoints:

| Endpoint | Auth | Rate limit |
|---|---|---|
| `GET /api/ats/advisory-actions?assignment_id=<id>` | recruiter on the job | 60/min |
| `POST /api/ats/advisory-actions/{id}/apply` | recruiter on the job | 30/min |
| `POST /api/ats/advisory-actions/{id}/dismiss` | recruiter on the job | 30/min |

### Notifications wiring

Per-event recipient resolution:

- "recruiter (if assigned)" = users with role `assigned_recruiter` or `primary_recruiter` on this job in `ats_job_assignments`, with `is_active=true`.
- "HM" = users with `Hiring Manager` role on the org_unit ancestry of the job (existing pattern in `app/modules/roles/`).
- "super-admin" = users with `is_super_admin=true` on this tenant.

`app/modules/notifications/types.py` gains the new notification type IDs. Existing notification dispatch infrastructure is reused.

## Frontend rework

The recruiter dashboard (`frontend/app/`) already has an integrations surface — five components under `components/settings/integrations/`, three route segments under `app/(dashboard)/settings/integrations/`, and the API namespace `lib/api/ats.ts`. The unified-storage + lifecycle-events model requires meaningful changes across that surface plus additions to team, jobs, candidates, and a new mirror-mode mapping UI. None of the new shapes are speculative — every section maps to a backend contract specified above.

### Section-by-section impact

#### S1. `lib/api/ats.ts` — API client rewrite

Remove the per-phase model entirely:

```typescript
// REMOVE — the new sync trigger has no phases.
export type ATSSyncPhase = 'clients' | 'users' | 'jobs' | 'applicants' | 'submissions'
```

Replace with sync-mode model matching the backend:

```typescript
export type ATSStatusSyncMode = 'advisory' | 'mirror' | 'one_way'

export interface ATSConnection {
  id: string
  vendor: ATSVendor                          // 'ats_ceipal' (was 'ceipal' — rename for consistency)
  active: boolean
  status_sync_mode: ATSStatusSyncMode
  job_status_filter: JobStatusFilter | null
  last_synced_at: string | null              // null on a brand-new connection
  tenant_timezone: string | null             // populated post-first-sync; defaults to UTC if no users yet
  created_at: string
  created_by: { id: string; full_name: string }
}

export interface ATSManualSyncRequest {
  // No payload. Sync is always cursor-based incremental. First-ever sync (when
  // last_synced_at IS NULL) implicitly does a full filter walk. To force a full
  // re-scan on a connection with a populated cursor, call POST /reset-cursor.
}

export interface ATSSyncLogEvent {
  id: string
  event_type: string                          // e.g. 'ats.job.status_changed'
  resource_type: 'job' | 'user' | 'org_unit' | 'submission' | 'candidate'
  resource_id: string
  payload: Record<string, unknown>            // diff details
  correlation_id: string
  created_at: string
}
```

The `vendor` rename from `'ceipal'` to `'ats_ceipal'` is the only client-facing rename. The discriminated union in `connectionCreateSchema` keeps its shape — the rename is a string-value change, not a structural one. Existing tests in `tests/lib/api/ats.test.ts` and `tests/lib/api/ats.api.test.ts` get a fixture update, not a rewrite.

New hooks (`lib/hooks/`):

| Hook | Purpose |
|---|---|
| `useATSConnection(connectionId)` | TanStack Query for single connection. Existing endpoint, now returns `status_sync_mode` + `tenant_timezone`. |
| `useATSSyncEvents(connectionId, filters)` | Paginated query over `ats.*` audit events for this connection. Drives the activity feed. |
| `useUpdateStatusSyncMode(connectionId)` | Mutation. Hits `PUT /api/ats/connections/{id}/status-sync-mode`. |
| `useStageMappings(connectionId)` | TanStack Query for `ats_stage_mappings`. Empty for advisory-mode connections. |
| `useUpsertStageMapping(connectionId)` | Mutation for adding/editing. |
| `useDeleteStageMapping(connectionId, mappingId)` | Mutation. |
| `useApplyAdvisoryAction(eventId)` | For advisory-mode: applies the suggested change. POST to a new endpoint that performs the stage move + writes an audit row noting the manual application. |

The existing `useTriggerManualSync` hook keeps its name but drops the `phases` param.

#### S2. `components/settings/integrations/` — connection management

| Component | Status | Change |
|---|---|---|
| `CeipalConnectionForm.tsx` | Modified | Strip `vendor` from request body (server infers from route); fields unchanged. |
| `ConnectionListCard.tsx` | Modified | Show `status_sync_mode` chip + `last_synced_at` relative timestamp. No phase chips. |
| `SyncLogTable.tsx` | Modified | Drop "Phases run" column. Single-mode sync — show jobs-imported / unchanged / errored counts. Click-through opens new `<SyncRunDetail/>`. |
| `SyncProgressBar.tsx` | Modified | No more per-phase progress; show single "jobs imported / total" counter sourced from `ats_sync_logs.progress`. |
| `JobStatusFilterDialog.tsx` | Modified | **Block "Save" if no statuses selected** (matches new 422 behavior). Add an info banner: "At least one status must be selected; the sync cannot run without an active filter." |
| `StatusSyncModeSelector.tsx` | **New** | Three-option Select using `px/Select` matching the DB check constraint exactly: Advisory (`advisory`, default) / Mirror (`mirror`) / Read-only (`one_way`). Each has a description blurb. Mirror selection opens a confirmation dialog explaining mapping requirements; Read-only selection opens a confirmation explaining lifecycle events will no longer fire. |
| `StageMappingsEditor.tsx` | **New** | List + add/edit/delete UI for `ats_stage_mappings`. Each row: external status label (free-text input with suggestions populated from `getJobStatusList`) → ProjectX stage (Select bound to pipeline_stages) → action (`move_to_stage` / `reject` / `archive` / `no_op`). |
| `StageMappingsEditor.tsx` empty state | **New** | When `status_sync_mode='advisory'`, this component is hidden behind a tooltip: "Switch to Mirror mode to configure auto-apply stage mappings." |
| `SyncRunDetail.tsx` | **New** | Drawer/dialog opened from `SyncLogTable`. Renders the per-event timeline for one sync run (correlation_id scoped), grouped by resource: "Org units imported (3): Oracle, BinQle, Tecnotree". "Jobs imported (12)". "Jobs status changed (4): PFJID-661 Active → Hold by Client". "Submissions status changed (8): …". |
| `OrphanClientResolver.tsx` | **New** | Card on the connection detail page. Lists `ats.org_unit.orphan_client` events with action: "Create org unit from name" (single-click that calls the existing `create_org_unit` endpoint with the seed name + sets the `external_id` to a synthetic stub — or links to an existing org unit via a picker). |
| `EmailCollisionResolver.tsx` | **New** | Card on the connection detail page. Lists `ats.user.collision_skipped` events. Each row: Ceipal user's email + name + existing ProjectX user. Actions: "Open user record" / "Mark as same person" (forces the link). Super-admin-only. |
| `QuarantinedJobsResolver.tsx` | **New** | Card on the connection detail page Data Quality tab. Lists jobs with `import_quarantined_at IS NOT NULL`. Each row: job title + external_id + last error + retry count + "Retry import" button (calls `POST /api/ats/jobs/{job_id}/retry-import`). Available to any recruiter with `jobs.edit` on the job's org_unit. |

#### S3. Connection detail page — `app/(dashboard)/settings/integrations/[connectionId]/page.tsx`

Add tab structure (currently single column):

| Tab | Content |
|---|---|
| **Overview** (default) | Connection facts (vendor, created by, last sync, mode), `<JobStatusFilterDialog>` trigger, manual-sync button (rate-limit-aware tooltip showing remaining quota), `<StatusSyncModeSelector>` |
| **Sync Activity** | `<SyncLogTable>` with per-row drilldown to `<SyncRunDetail>` |
| **Stage Mappings** | `<StageMappingsEditor>` (greyed out + tooltip if not mirror mode) |
| **Data Quality** | `<OrphanClientResolver>` + `<EmailCollisionResolver>` |

Tab navigation lives in the page component using existing `px/` primitives — no new tab primitive needed (we'd build with `px/Button` + nav state).

#### S4. `lib/api/team.ts` + Team page — unified user model

The team API already exposes `source: 'user' | 'invite' | 'ats'` with computed states `'ats_unlinked'`, `'pending'`, `'active'`, `'inactive'` (`team.ts:22–34`). After the unification this evolves to reflect the new row model directly:

```typescript
export type TeamMemberSource = 'native' | `ats_${string}`

export interface TeamMember {
  id: string
  email: string
  full_name: string | null
  source: TeamMemberSource                    // 'native' | 'ats_ceipal' | ...
  external_id: string | null
  is_active: boolean
  has_auth_account: boolean                   // computed: auth_user_id IS NOT NULL
  external_source_metadata: {
    role?: string                             // Ceipal's "Recruiter Lead" etc.
    ceipal_status?: string
  } | null
  invite_state: 'none' | 'pending' | 'accepted' | 'revoked'
  created_at: string
}
```

The computed display states map:

| Row shape | Display label | Action available |
|---|---|---|
| `source='native'`, `has_auth_account=true`, `is_active=true` | Active | Edit / Deactivate |
| `source='native'`, `has_auth_account=false`, `invite_state='pending'` | Invited | Resend / Revoke |
| `source.startswith('ats_')`, `has_auth_account=false`, `invite_state='none'` | ATS-only (not invited) | **Invite to ProjectX** (new action) |
| `source.startswith('ats_')`, `has_auth_account=false`, `invite_state='pending'` | ATS, invited | Resend / Revoke |
| `source.startswith('ats_')`, `has_auth_account=true`, `is_active=true` | Active (from ATS) | Edit / Deactivate |
| `source='native'`, `external_id IS NOT NULL` | Active (linked to ATS) | Edit / Deactivate |

`app/(dashboard)/settings/team/page.tsx` gains:
- A **source filter** (Select: All / Native / Ceipal / …).
- A **"Source" column** with a `<SourceBadge/>` showing icon + label.
- An **"Invite ATS users in bulk"** action (super-admin only) for tenants with many `ats_unlinked` rows.

New component: `components/settings/team/SourceBadge.tsx` — used across team, jobs, org units. Single source of truth for the visual identity of provenance.

#### S5. Jobs page + cards — surface provenance and lifecycle

`lib/api/jobs.ts` already exposes `source`, `external_id`, `external_status` (`jobs.ts:105–107`). Additions:

```typescript
export interface JobPostingSummary {
  // ... existing fields
  external_last_modified_at: string | null
  external_status: string | null              // existing
  recent_lifecycle_events: Array<{            // new — top 3 most recent
    event_type: string
    payload: Record<string, unknown>
    created_at: string
  }>
}
```

UI changes:

- **Job card** (`components/dashboard/jobs/JobCard.tsx` if it exists; or list rows on `/jobs`): render a `<SourceBadge/>` on every card. ATS-imported cards also show the most recent lifecycle event as a subtle line ("Status changed in Ceipal: Active → Hold by Client · 2h ago").
- **Job detail page** (`app/(dashboard)/jobs/[jobId]/page.tsx`): new "ATS Activity" tab (only when `source !== 'native'`). Renders the lifecycle timeline + an "Apply latest" button per advisory event when `connection.status_sync_mode === 'advisory'`.
- **Jobs list filter**: add a "Source" filter dropdown (alongside existing status/stage filters).
- **Jobs list "Archived in ATS" badge**: when `external_status === 'archived_in_ats'`, render a muted "Archived in Ceipal" chip alongside the normal status pill.

#### S6. Candidates kanban + detail — surface submission lifecycle

`lib/api/candidates.ts` / kanban shape additions:

```typescript
export interface CandidateJobAssignmentSummary {
  // ... existing fields
  source: 'manual' | `ats_${string}`
  external_status: string | null              // e.g. 'L2 Rejected'
  external_pipeline_status: string | null
  external_last_modified_at: string | null
  pending_advisory_action: {                  // populated when an unapplied status change exists
    target_stage_id: string
    target_action: 'move_to_stage' | 'reject' | 'archive'
    suggested_at: string
    suggested_by_event_id: string
  } | null
}
```

UI changes:

- **`CandidateKanbanCard.tsx`**: when `pending_advisory_action` is non-null, show a small dot indicator + tooltip: "Ceipal status changed to L2 Rejected — apply?". Click opens a confirm dialog.
- **`CandidateListView.tsx`**: same indicator in the assignments column.
- **`CandidateDetail` page** (`app/(dashboard)/candidates/[candidateId]/page.tsx`): new "External Status" section in the assignment cards, showing Ceipal's status + suggested action + apply/dismiss buttons.
- **Stage advancement guard**: when a recruiter manually advances a candidate whose `pending_advisory_action` exists and conflicts (e.g. recruiter advances while Ceipal says reject), show a warning dialog. The advance proceeds; the `pending_advisory_action` is dismissed automatically with an audit row.

#### S7. Org units — surface provenance + Ceipal contacts

`lib/api/org-units.ts` additions:

```typescript
export interface OrgUnitDetail {
  // ... existing fields
  source: 'native' | `ats_${string}`
  external_id: string | null
  external_source_metadata: {
    website?: string
    industry?: string
    country?: string
    state?: string
    city?: string
    business_unit_id?: number
    contacts?: Array<{                        // Ceipal client-side HR contacts
      external_id: string
      name: string | null
      email: string | null
      designation: string | null
      phone: string | null
    }>
  } | null
}
```

UI changes:

- **OrgGraphCanvas + OrgUnitNode**: `<SourceBadge/>` overlay on nodes whose `source !== 'native'`. The existing `metadata` JSONB pathway (referenced in `OrgUnitNode.tsx`) already surfaces some of this; the new structured fields supersede the unstructured access.
- **Org unit detail page** (`app/(dashboard)/settings/org-units/[unitId]/page.tsx`): new "Client Contacts" section (collapsed by default), populated from `external_source_metadata.contacts`. Each contact row has name + email + designation + phone. Action button per contact: **"Invite as Hiring Manager"** which prefills the invite form with name + email + selects the Hiring Manager role on this unit.
- **"Created by ATS" indicator**: when `source !== 'native'`, the unit's header shows "Imported from Ceipal · last refreshed 2h ago" instead of the usual "Created by Alice".

#### S8. Notifications panel — new event types

Existing notification dispatch (`app/modules/notifications/`) renders a panel in the dashboard shell. The new ATS event types need icon + copy + action mapping:

| Event type | Icon | Copy template | Action button |
|---|---|---|---|
| `ats.job.status_changed` | Briefcase + arrow | "Job *{title}* status changed in Ceipal: *{old}* → *{new}*" | Open job |
| `ats.submission.status_changed` | UserCircle + arrow | "*{candidate}* on *{job}*: Ceipal status changed to *{new}*" | Open candidate, with apply/dismiss |
| `ats.user.linked_to_native` | Link | "*{user_email}* in Ceipal was linked to existing ProjectX account" | Open user record |
| `ats.user.collision_skipped` | AlertTriangle | "Email conflict: Ceipal user *{email}* could not be linked (different external ID)" | Open data quality |
| `ats.org_unit.orphan_client` | AlertCircle | "Job *{title}* references client *{name}* which doesn't exist in Ceipal" | Open data quality |
| `ats.sync.partial` / `.failed` | XCircle | "ATS sync did not complete: *{reason}*" | Open sync log |

Each notification has an `event_id` so the panel can deduplicate (single notification per event, not per recipient).

#### S9. Settings shell — surface "Data Quality" badge

The settings nav (`components/dashboard/AppShell.tsx` or wherever settings nav lives) should render a small numeric badge next to "Integrations" when the connection has unresolved data-quality items (orphan clients + email collisions). Badge count = sum of unresolved across all connections.

#### S10. Tests

| Test file | Coverage |
|---|---|
| `tests/lib/api/ats.test.ts` | Updated — drop `phases`; add `mode`; verify connection shape includes `status_sync_mode` + `tenant_timezone` |
| `tests/lib/api/ats.api.test.ts` | Updated — sync trigger 422 on empty filter; new endpoints (stage mappings, status sync mode) |
| `tests/components/JobStatusFilterDialog.test.tsx` (exists) | Updated — verify save blocked when zero statuses selected |
| `tests/components/StatusSyncModeSelector.test.tsx` | **New** — three options render; mirror requires confirmation |
| `tests/components/StageMappingsEditor.test.tsx` | **New** — CRUD flow; advisory-mode tooltip; pipeline-stage select |
| `tests/components/SyncRunDetail.test.tsx` | **New** — composition test: render per-event timeline with realistic fixture |
| `tests/components/OrphanClientResolver.test.tsx` | **New** — list orphan events; resolve action flow |
| `tests/components/EmailCollisionResolver.test.tsx` | **New** — list collision events; force-link action; super-admin gating |
| `tests/components/SourceBadge.test.tsx` | **New** — variant rendering for native vs ats_ceipal vs unknown vendor (graceful fallback) |

Composition tests (parent + child rendered together, mocked at the API boundary) are required per the project's testing convention — applied especially to `<SyncRunDetail>` and the advisory-action confirm dialog flow.

### Frontend rollout

Frontend ships in the same PR as the backend. No feature flag — the new shapes are the only shapes the codebase carries. Existing test fixtures are updated to match. The single-PR cutover is feasible because there are no live production users to migrate.

### Accessibility + a11y

Every new component conforms to the existing accessibility rules in `frontend/app/CLAUDE.md`:

- `<StatusSyncModeSelector>`: `Select` from `px/Select` already wires keyboard nav.
- `<StageMappingsEditor>`: full keyboard CRUD; row delete uses `DangerConfirmDialog`.
- `<SyncRunDetail>`: dialog with focus trapped on open (focuses close button, since the timeline loads async).
- `<EmailCollisionResolver>`: action button has ARIA label including the affected email; "force link" requires `DangerConfirmDialog`.
- All new notification entries are keyboard-focusable in the existing notifications panel.

### Performance budget

- Connection detail page first-load JS budget < 250 KB gzipped (per project standard).
- `<SyncRunDetail>` loads its timeline lazily on dialog-open (per-correlation-id query); the dialog component itself does not block initial page render.
- `useATSSyncEvents` paginates at 50 events per page. Activity tabs default to "last 7 days" to bound result size.

## RBAC & rate limiting

### RBAC unchanged

- Connection CRUD endpoints: super-admin only (`require_ats_admin`). Unchanged from current.
- Sync trigger: super-admin only. Unchanged.
- View sync logs: any authenticated user in the tenant.
- Stage-mapping CRUD (new endpoints): super-admin only.

Future enhancement: a non-super-admin `ats_admin` role for ops managers. Out of scope here.

### Rate limit declarations

CLAUDE.md requires every public endpoint declare its limit at the router. New limits:

| Endpoint | Per-IP | Per-token / per-tenant |
|---|---|---|
| `POST /api/ats/connections/{id}/sync` | 10/min | **5/hour per tenant** |
| `PUT /api/ats/connections/{id}/job-status-filter` | 30/min | — |
| `GET /api/ats/connections/{id}/sync-logs` | 60/min | — |
| `GET /api/ats/connections/{id}/job-statuses` | 30/min | — |
| `POST /api/ats/connections/{id}/stage-mappings` | 30/min | — |

The per-tenant 5/hour on manual sync is the safety net against a compromised super-admin token hammering Ceipal. Manual triggers should be rare in practice (the scheduler handles routine syncs).

No scheduled (cron) sync triggers exist in MVP — every sync is initiated by a recruiter HTTP click. The per-tenant 5/hour cap is the only ceiling.

### `POST /sync` validation

The endpoint now enforces:

1. `connection.active == True` — otherwise 409.
2. `connection.job_status_filter['ids']` is non-empty — otherwise **422** (was previously silently completing with `errors=['filter_not_configured']`).
3. No sync currently running for this connection (locked via `ats_sync_logs.status='running'` + advisory lock) — otherwise 409.

## Security & compliance

### PII strip

`app/modules/candidates/pii.py` (new module) implements a single canonical strip:

```python
_SENSITIVE_KEY_PATTERNS = re.compile(
    r'(aadhar|ssn|sin|pan_number|passport|drivers_license|tax_id|nric|emirates_id'
    r'|resume_token|merge_document_path|merged_pdf_document|.*_token)$',
    re.IGNORECASE,
)

def strip_sensitive_pii(payload: dict) -> dict:
    """Deep-clone payload. Remove any key whose name matches a sensitive
    pattern at any depth. Returns the sanitized clone. Original untouched."""
```

Applied at every adapter boundary that yields applicant data. Unit-tested with positive (must-strip) and negative (must-preserve) fixtures.

### Audit log requirements

Every audit row written by ATS code must include:
- `actor_id` (per actor-resolution table above)
- `tenant_id` (from sync context)
- `correlation_id` (from `ats_sync_logs.correlation_id` — propagated through the entire sync)
- `action` (event name from catalogue)
- `resource_type` + `resource_id` (the entity affected)
- `action_source` ∈ `{'manual', 'scheduled', 'system'}`

The `correlation_id` flows end-to-end. From `ats_sync_logs` → audit log entries → notification dispatches → realtime publishes. A single sync run is forensically traceable by its correlation ID.

### Logging discipline

Forbidden in any log output (structlog/Sentry/print):
- `resume_token` (every submission has one)
- `access_token` / `refresh_token` (in `ATSConnectionState`)
- Raw Ceipal `aadhar_number`, `ssn`, etc.
- Full applicant email; log `applicant_external_id` instead. Email is allowed in audit log only.
- JWT bearer values.

A central redactor lives in `app/observability/redactors.py` (extend existing module). The orchestrator uses a structlog wrapper that auto-filters known sensitive keys from log context dicts.

### RLS posture

All new columns inherit existing `tenant_isolation` + `service_bypass` from their host tables. Verified by extending the `_RLS_REQUIRED_TABLES` startup check (already in `app/main.py:60-64`). Required edits in `_TENANT_SCOPED_TABLES`:

- **Remove** `'ats_user_mappings'`, `'ats_client_mappings'` (the rows in this list around lines 60–64).
- **Rename** `'ats_job_recruiter_assignments'` → `'ats_job_assignments'` (table renamed by migration 0041).
- **Add** `'ats_stage_mappings'`.

The startup `_assert_rls_completeness` fails on any mismatch between this list and the actual `pg_policies` state — these edits ship in the same PR as migration 0041/0044 to keep the assertion green.

The migration `0042_ats_data_backfill` runs under bypass-RLS (the `app.bypass_rls = 'true'` session GUC) because it operates cross-tenant. This is the documented pattern in `backend/nexus/CLAUDE.md` — bypass is acceptable for data migrations as long as the migration script writes the correct `tenant_id` on every row.

### Threat model deltas

Update `docs/security/threat-model.md` to add:

| New trust boundary | Mitigation |
|---|---|
| Ceipal API → ATS adapter | Fernet-encrypted credentials at rest, MultiFernet key rotation; access/refresh tokens never logged; per-tenant pacing via `rate_limit_qps` |
| ATS-imported user becomes invitable | Email-collision case 4 (skip) prevents stale-data overwrite; invite-accept verifies `users.email` matches `auth.email` on the Supabase side |
| Stage-mapping auto-apply (`mirror` mode) | Default is `advisory`; mirror requires explicit super-admin opt-in per connection; mappings are per-tenant + per-connection scoped via RLS |
| Indian PII (Aadhaar) in applicant payloads | `strip_sensitive_pii()` at boundary; tested against real payload shapes |

## Rollout plan

No live production tenants. No data backfill. No feature flag. Single-PR cutover.

### Steps

1. **Local dev**: `supabase db reset` to clear local state. Run migration `0036_ats_unified_sync`. Verify `_assert_rls_completeness` passes at boot.
2. **Implement the new orchestrator + adapter** alongside deleting the old `importer.py`, the old `ATSAdapter` Protocol (replaced in-place), `ATSUserMapping`/`ATSClientMapping` ORM classes, and frontend `phases` parameter usage. One PR, one commit series.
3. **Frontend changes** ship in the same PR. No `NEXT_PUBLIC_*` feature flag — the new shapes are the only shapes.
4. **Test sweep**: run the full backend test suite (`pytest`) and frontend test suite (`npm run test`). Cross-tenant tests for every new tenant-scoped column and table are mandatory.
5. **Manual smoke test** against the Ceipal sandbox using a fresh tenant: connect → configure job-status filter → click Resync → verify org_units, users, jobs, candidates, assignments populate correctly.
6. **Merge.** Done.

### Future-phase rollout (cron + reconciliation, separate spec)

When the scheduled poller + reconciliation pass lands, that work ships under its own migration. The schema we're shipping now does not preclude it — the future migration adds `last_full_scan_at`, `full_scan_interval_hours`, and `next_poll_at` cleanly.

## Testing strategy

### Unit tests

| Surface | Coverage target | Test fixture source |
|---|---|---|
| `adapter.py` Protocol contract | Conformance test for each registered adapter | Synthetic mock adapter |
| `adapters/ceipal.py` field normalization | Every field on every endpoint exercised | Frozen real Ceipal payloads under `tests/fixtures/ats/ceipal/` |
| `strip_sensitive_pii` | All forbidden key patterns | Synthetic + real applicant payloads |
| `_resolve_recruiters` email-collision matrix | All 4 cases | Synthetic users + ATS payloads |
| `_resolve_client` | Hit, miss-with-fetch, miss-with-orphan | Synthetic adapter |
| `JobDiffResult` / `SubmissionDiffResult` | New/changed/unchanged paths | Snapshot fixtures |
| Event dispatch | Each event in catalogue emits exactly once per trigger | Mock audit + notification clients |
| Migration `0042` | Backfill idempotency + numeric sanity-check | Test DB seed |

### Integration tests

| Scenario | What it proves |
|---|---|
| End-to-end sync against recorded Ceipal API (vcrpy-style) | Orchestrator produces expected rows + events for known fixture |
| Mid-sync rate-limit error | Sync finalizes as `partial`, cursors not advanced, no Dramatiq retry |
| Concurrent manual sync prevented by advisory lock | Second trigger returns 409 |
| Status mode `advisory` produces task, `mirror` moves stage | Stage-mapping wiring works |
| Email collision case 4 (skip) | No data corruption; audit + notification emitted; next sync re-skips |
| Job archived in Ceipal (reconciliation) | `external_status` updated to `archived_in_ats`; cleanup event emitted |
| Tenant isolation | A tenant cannot see another tenant's `ats_sync_logs` even under bypass-RLS misuse |
| Aadhaar field never persisted | Search `candidates.source_metadata` for `aadhar_number` returns 0 rows |

### Cross-tenant test (mandatory)

For every new column with `tenant_id`/`client_id`, the prior-spec test pattern applies: insert a row in tenant A, switch session role to tenant B, SELECT must return 0 rows.

### Smoke test

A manual smoke test against the Ceipal sandbox before each release. Documented under `docs/dr/ats-smoke-test.md` (new).

## Resolved decisions (2026-05-14)

All open questions resolved in conversation with user prior to implementation.

| # | Question | Decision |
|---|---|---|
| Q1 | Where do super-admins see ATS data-quality issues? | **Per-connection Data Quality tab** on the connection detail page. (`<EmailCollisionResolver>`, `<OrphanClientResolver>`, `<QuarantinedJobsResolver>` mount here.) |
| Q2 | When a job is archived in Ceipal, auto-archive its in-flight `candidate_job_assignments`? | **Moot for MVP** — archive detection is deferred with the cron scheduler. Recruiter manually archives stale jobs they notice. |
| Q3 | Cold-mode user-refresh frequency? | **Moot for MVP** — no cold mode. Future-phase concern. |
| Q4 | Tenant-timezone fallback when no users exist yet? | **UTC fallback.** First sync defaults `tenant_timezone='UTC'`. Refreshed from a real user record on the next sync once users exist. |
| Q5 | Client name match: exact or case-insensitive? | **Case-insensitive** (`lower(strip(name))`). Safe because identity is enforced by `(tenant_id, source, external_id)` uniqueness — case-folding cannot create duplicates. |
| Q6 | Cursor storage: JSONB or dedicated table? | **Single `TIMESTAMPTZ` column** on `ats_connections.last_synced_at`. No JSONB cursor blob, no per-job cursor table. Submissions are pulled fresh for any touched job. |
| Q7 | How to handle persistently-failing jobs? | **Quarantine after 3 consecutive failures.** `import_quarantined_at` set; recruiter manually retries via `POST /api/ats/jobs/{job_id}/retry-import` exposed in the Data Quality tab. |
| Q8 | Rollback safety during cutover? | **None needed.** No live tenants. Single PR cutover with `supabase db reset`. No feature flag, no V1/V2 protocol coexistence, no transitional override columns. Old code deleted in the same commit. |
| Q9 | Ship "Force full re-scan" escape hatch now? | **Yes.** `POST /api/ats/connections/{id}/reset-cursor` clears `last_synced_at`. UI menu option on the connection detail page. Super-admin only, rate-limited 1/hour per tenant. |

## Out of scope (future work)

- Greenhouse + Workday adapter implementations (Protocol slot exists; no concrete code).
- Two-way sync (ProjectX → Ceipal write-back).
- Resume document ingestion.
- Webhook-driven sync (Ceipal has none; other vendors may).
- Tenant-side override of canonical field mappings.
- Multi-business-unit sync filtering (BU is captured into `source_metadata` only).
- "Data quality issues" centralized dashboard.

## Appendix A — Ceipal field mapping reference

Authoritative mapping from Ceipal payload fields to ProjectX columns. Lives in `app/modules/ats/adapters/ceipal_mapping.md` (new) for reviewer reference; the code uses this directly in `adapters/ceipal.py`.

### `getJobPostingsList` (list page item) → `ATSJobPayload`

| Ceipal | ProjectX | Notes |
|---|---|---|
| `id` | `external_id` | Opaque encoded; preserved as-is |
| `position_title` | `title` | Trimmed |
| `requisition_description` | `description_raw` | `html.unescape()` |
| `job_status` | `external_status` | Free-form label |
| (filter `jobStatus`) | `external_status_id` | The int ID we filtered on |
| `posted_by` | `posted_by_external_id` | Opaque user ID, empty→None |
| `created_by` | `created_external_id` | Opaque user ID |
| `primary_recruiter` | `primary_recruiter_external_id` | Empty→None |
| `assigned_recruiter` | `assigned_recruiter_external_ids` | CSV split, empties dropped |
| `business_unit_id` | `business_unit_id` | int |
| `country` | `country` | Empty→None |
| `primary_city` | `primary_city` | |
| `primary_state` | `primary_state` | |
| `secondary_cities` + `secondary_states` | `secondary_locations` | List of `{city, state}` dicts |
| `skills` | `skills` | CSV split, trimmed |
| `pay_rates` | `pay_rates` | Preserved as-is |
| `closing_date` | `deadline` | Safe-parse; `"Open Until Filled"`→None |
| `created` | `external_created_at` | Naive → UTC via tenant_timezone |
| `modified` | `external_modified_at` | Naive → UTC via tenant_timezone |
| (full payload) | `raw` | For `source_metadata.raw` |

**Not mapped (preserved in `raw` only):** `modified_by`, `recruitment_manager`, `sales_manager`, `is_recycle`, `client_job_id`, `post_on_careerportal`, `industry` (job-level), `job_type`, `job_category`, `priority`, `tax_terms`, `employment_type`, `currency`, `public_job_desc`, `public_job_title`, `apply_job`, `apply_job_without_registration`, `updated` (human readable), `company` (Ceipal internal company ID, not client ID).

### `getJobPostingDetails/{id}` → enriches `ATSJobPayload`

| Ceipal | ProjectX | Notes |
|---|---|---|
| `client` | `client_external_name` | The only reason we call detail |

All other detail fields are already in list. The orchestrator caches `client_external_name` → `client_external_id` per sync; subsequent syncs of the same job skip detail entirely when the mapping is already in `organizational_units.external_id`.

### `getClientDetails/{id}` → `ATSClientPayload`

| Ceipal | ProjectX | Notes |
|---|---|---|
| `id` | `external_id` | |
| `name` | `name` | Trimmed |
| `website` | `website` | Empty→None |
| `industry_exp` | `industry` | `"0"` or empty → None |
| `country` | `country` | Empty→None |
| `state` | `state` | |
| `city` | `city` | |
| `primary_business_unit` | `business_unit_id` | |
| `created_at` | `external_created_at` | Naive → UTC |
| `updated_at` | `external_modified_at` | Empty string → None; else naive→UTC |
| `contacts[]` | `contacts[]` | Each `{external_id, name, email, designation, phone}` |
| (full payload) | `raw` | |

**Not mapped:** `category`, `ownership`, `accessible_business_units`, `send_requirement`, `send_hotlist`, `accounts`, `zipcode`, `address`.

### `getUserDetails/{id}` → `ATSUserPayload`

| Ceipal | ProjectX | Notes |
|---|---|---|
| `id` | `external_id` | |
| `email_id` | `email` | Lower-cased on read for dedup; preserved as-typed in DB |
| `first_name` + `last_name` | `full_name` | Joined + trimmed |
| `role` | `role` | Empty→None |
| `business_unit_id` | `business_unit_id` | |
| `timezone` | `timezone` | IANA |
| `status` | `external_status` | |
| (full payload) | `raw` (in `external_source_metadata`) | |

### `getSubmissionsList?jobId=...` → `ATSSubmissionPayload`

| Ceipal | ProjectX | Notes |
|---|---|---|
| `id` | `external_id` | Opaque |
| `job_id` | `job_external_id` | |
| `job_seeker_id` | `applicant_external_id` | |
| `submitted_by` | `submitted_by_external_id` | |
| `submission_status` | `external_status` | |
| `pipeline_status` | `external_pipeline_status` | |
| `source` | `submission_channel` | NOT our `source` provenance |
| `pay_rate` | `pay_rate` | |
| `currency_code` | `pay_currency` | |
| `submitted_on` | `external_submitted_at` | Naive→UTC |
| `modified` | `external_modified_at` | Naive→UTC |
| (full payload, **minus `resume_token`, `Documents`, `merged_pdf_document`, `merge_document_path`**) | `raw` | Resume artifacts deferred for future spec |

### `getApplicantDetails/{id}` → `ATSApplicantPayload`

| Ceipal | ProjectX | Notes |
|---|---|---|
| `id` | `external_id` | |
| `firstname` | `first_name` | |
| `lastname` | `last_name` | |
| `email` | `email` | |
| `email_address_1` | `secondary_email` | |
| `mobile_number` | `mobile` | |
| `city` | `city` | |
| `state` | `state` | |
| `country` | `country` | |
| `source` | `applicant_source` | |
| (full payload, **minus `aadhar_number` and `documents[*].resume_token`**) | `raw` | |

**Hard-strip on ingest:** `aadhar_number`, `ssn`, `pan_number`, `passport_number`, `drivers_license`, `tax_id`, `nric`, `emirates_id`, any field matching `*_token$`.

## Appendix B — File-level change list

Single PR. All changes ship in one commit series — no transitional naming, no V1/V2 split.

### Backend — new files

```
app/modules/ats/orchestrator.py
app/modules/ats/constants.py
app/modules/ats/adapters/ceipal_mapping.md   (reviewer reference)
app/modules/candidates/pii.py
migrations/versions/0036_ats_unified_sync.py
tests/modules/ats/test_orchestrator.py
tests/modules/ats/test_ceipal_adapter.py
tests/modules/ats/test_pii_strip.py
tests/modules/ats/fixtures/ceipal/*.json
docs/dr/ats-smoke-test.md
```

### Backend — modified files (in-place rewrite, no V2 naming)

```
app/modules/ats/adapter.py             (Protocol rewritten in place)
app/modules/ats/adapters/ceipal.py     (rewritten in place)
app/modules/ats/registry.py            (constants module imported)
app/modules/ats/schemas.py             (DTOs replaced with the canonical set)
app/modules/ats/models.py              (ATSClientMapping + ATSUserMapping classes deleted; ATSJobAssignment renamed; ATSStageMapping + ATSAdvisoryAction added; ATSConnection cursor columns updated)
app/modules/ats/service.py             (sync trigger: 422 on empty filter; advisory-lock acquisition; new endpoints)
app/modules/ats/router.py              (rate-limit decorators; `phases` param removed; new endpoints: reset-cursor, stage-mappings CRUD, advisory-actions CRUD, retry-import)
app/modules/ats/actors.py              (poll_ats_connection invokes new orchestrator)
app/modules/ats/authz.py               (unchanged — still super-admin gated)
app/modules/auth/router.py             (accept_invite: ats_user_mappings hook replaced by users.external_id UPDATE around the existing lines 208–224)
app/modules/auth/models.py             (User.auth_user_id → nullable; source/external_id/external_source_metadata added)
app/modules/org_units/models.py        (OrganizationalUnit: source/external_id/external_source_metadata added)
app/modules/audit/events.py            (new ats.* event type constants)
app/modules/notifications/types.py     (new notification IDs for ATS events)
app/modules/candidates/sources.py      (ATSImportSource interface unchanged; calls strip_sensitive_pii)
app/main.py                            (_TENANT_SCOPED_TABLES updated)
backend/nexus/CLAUDE.md                (update ATS section reflecting new model)
docs/security/threat-model.md          (Ceipal section update)
```

### Backend — deleted

```
app/modules/ats/importer.py            (replaced by orchestrator.py)
```

### Frontend — new files

```
frontend/app/components/settings/integrations/StatusSyncModeSelector.tsx
frontend/app/components/settings/integrations/StageMappingsEditor.tsx
frontend/app/components/settings/integrations/SyncRunDetail.tsx
frontend/app/components/settings/integrations/OrphanClientResolver.tsx
frontend/app/components/settings/integrations/EmailCollisionResolver.tsx
frontend/app/components/settings/integrations/QuarantinedJobsResolver.tsx
frontend/app/components/settings/integrations/ResetCursorMenuItem.tsx        (Force full re-scan action)
frontend/app/components/shared/SourceBadge.tsx
frontend/app/lib/hooks/use-ats-sync-events.ts
frontend/app/lib/hooks/use-stage-mappings.ts
frontend/app/lib/hooks/use-status-sync-mode.ts
frontend/app/lib/hooks/use-apply-advisory-action.ts
frontend/app/lib/hooks/use-reset-ats-cursor.ts
frontend/app/lib/hooks/use-retry-ats-job-import.ts
frontend/app/tests/components/StatusSyncModeSelector.test.tsx
frontend/app/tests/components/StageMappingsEditor.test.tsx
frontend/app/tests/components/SyncRunDetail.test.tsx
frontend/app/tests/components/OrphanClientResolver.test.tsx
frontend/app/tests/components/EmailCollisionResolver.test.tsx
frontend/app/tests/components/QuarantinedJobsResolver.test.tsx
frontend/app/tests/components/SourceBadge.test.tsx
```

### Frontend — modified files

```
frontend/app/lib/api/ats.ts                  (drop ATSSyncPhase entirely; add ATSStatusSyncMode + ATSSyncLogEvent; rename vendor 'ceipal'→'ats_ceipal'; ATSManualSyncRequest now empty)
frontend/app/lib/api/team.ts                 (TeamMember source/external_id/has_auth_account; new display states)
frontend/app/lib/api/jobs.ts                 (JobPostingSummary: external_last_modified_at, recent_lifecycle_events)
frontend/app/lib/api/candidates.ts           (CandidateJobAssignmentSummary: external_status, external_pipeline_status, pending_advisory_action)
frontend/app/lib/api/org-units.ts            (OrgUnitDetail: source, external_id, external_source_metadata incl. contacts[])
frontend/app/components/settings/integrations/CeipalConnectionForm.tsx
frontend/app/components/settings/integrations/ConnectionListCard.tsx
frontend/app/components/settings/integrations/SyncLogTable.tsx
frontend/app/components/settings/integrations/SyncProgressBar.tsx
frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx
frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx   (tab structure)
frontend/app/app/(dashboard)/settings/team/page.tsx                          (source filter, badge column, bulk invite)
frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx                           (ATS Activity tab when source≠native)
frontend/app/app/(dashboard)/candidates/[candidateId]/page.tsx               (External Status section)
frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx            (Client Contacts section)
frontend/app/components/dashboard/org-units/OrgUnitNode.tsx                  (SourceBadge overlay)
frontend/app/components/dashboard/org-units/OrgGraphCanvas.tsx               (source-aware node rendering)
frontend/app/components/dashboard/candidates/CandidateKanbanCard.tsx         (pending_advisory_action indicator)
frontend/app/components/dashboard/candidates/CandidateListView.tsx           (same)
frontend/app/components/dashboard/AppShell.tsx                               (data-quality badge in settings nav)
frontend/app/tests/lib/api/ats.test.ts
frontend/app/tests/lib/api/ats.api.test.ts
frontend/app/tests/components/JobStatusFilterDialog.test.tsx
frontend/app/CLAUDE.md                       (document the new ATS pages/components)
```

---

**End of design spec.**
