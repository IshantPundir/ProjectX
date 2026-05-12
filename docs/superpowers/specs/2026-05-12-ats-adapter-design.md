# ATS adapter system — Ceipal first, modular for future vendors

**Status:** Draft for user review · **Date:** 2026-05-12

## Summary

ProjectX needs to ingest jobs and candidates from clients' existing ATS systems (Ceipal first; Greenhouse and Workday on the roadmap). The MVP is **read-only inbound sync** — Ceipal → ProjectX, no outbound push, no webhooks (Ceipal has none).

The integration is architected as a **plug-and-play adapter system**: a vendor-agnostic `ATSAdapter` Protocol with a canonical DTO surface, per-tenant adapter instances holding their own auth state, and an importer orchestrator that translates DTOs into ProjectX rows via existing module services. Adding a new ATS becomes "implement one Protocol and add it to the registry" — every other layer (scheduler, importer, persistence, UI) is unchanged.

ATS integration is **optional per tenant** — clients without an ATS continue using the manual candidate-creation flow. The two paths merge at `CandidateSource` Protocol so the data model has no manual-vs-ATS bifurcation.

## Goals

- One read-only Ceipal inbound integration end-to-end: connect → sync clients → sync users → sync jobs → sync applicants → sync submissions → surface in dashboard.
- Adapter Protocol shape designed for Ceipal first, with the same surface intended to fit Greenhouse and Workday when their adapters are implemented (verification deferred until those adapters are scoped).
- Per-tenant credential storage with encryption at rest and proactive token refresh.
- Polling scheduler that is restart-safe, has no leader-election problem, and works identically on Railway (MVP) and AWS ECS Fargate (enterprise).
- Recruiter UI: connect an ATS, see sync status, complete imported client-account profiles, map ATS users to ProjectX users.
- Audit trail and observability hooks consistent with the rest of the codebase.

## Non-goals

- **Outbound sync** (push interview outcome back into Ceipal). Deferred; the Protocol surface has a forward-compat slot.
- **Resume sync.** The product does not currently consume resume content in any AI/scoring pipeline, so resume retrieval is deferred — the `resume_token` and `Documents[]` fields are preserved in `source_metadata` so enablement later is additive.
- **Job Requisitions sync.** Postings only at MVP. Requisitions are Ceipal's pre-approval state and don't have public descriptions; interview pipelines run against Postings.
- **Interviews / Placements / Talent Bench / Leads / Vendors endpoints.** Out of MVP scope. Interviews specifically excluded because ProjectX is the source of truth for interview state.
- **Auto-mapping Ceipal users to ProjectX users by email match.** Same email across two HR systems doesn't guarantee same person; manual mapping by recruiter is the MVP path.
- **Auto-detection of upstream deletions.** If Ceipal deletes a client/job, MVP leaves the mapping in place. Recruiter manually archives.
- **AI-synthesized company profiles for imported clients.** Stub-and-flag-incomplete is the chosen posture (32-clients-at-a-time would burn LLM cost on profiles the recruiter has to verify anyway).

## Background

Current state in the codebase (`/home/ishant/Projects/ProjectX/backend/nexus/`):

- `app/modules/ats/adapter.py` declares a minimal `ATSAdapter` Protocol with three async methods (`fetch_new_jobs`, `fetch_new_candidates`, `push_interview_outcome`). Insufficient for delta sync, pagination, or client/user/submission entities.
- `app/modules/ats/router.py` registers `GET /api/ats/connections` returning `{"status": "not_implemented"}`.
- `app/modules/ats/{schemas,service,__init__}.py` are placeholder/empty.
- **Candidate side is already plumbed for ATS plug-in.** `app/modules/candidates/sources.py` defines a `CandidateSource` Protocol with one method `normalize(raw) → SourcedCandidate`. `create_candidate(db, request, source, user, tenant_id)` takes a `CandidateSource` impl. Columns `source`, `external_id`, `source_metadata` already exist on `candidates`.
- `app/modules/jd/models.py:32-33` — `job_postings` has `source` and `external_id` columns.
- No `ats_*` tables exist yet. No per-tenant secret storage exists yet (all existing secrets are deploy-wide via `app/config.py`).
- No scheduling layer exists. Dramatiq actors run on demand; nothing fires periodically.

Reference patterns mirrored throughout this design:

| Pattern | Lives at | Mirrored where |
|---|---|---|
| Provider-agnostic factory (Resend / DryRun) | `app/modules/notifications/service.py:41-89` | Adapter registry (Section: Registry) |
| Realtime AI plugin dispatcher (Sarvam / Deepgram / OpenAI) | `app/ai/realtime.py` | Adapter selection by `state.vendor` |
| `_PERMANENT_EXCEPTIONS` tuple in Dramatiq actor | `app/modules/jd/actors.py:57-63` | `ats.errors` permanent / transient hierarchy |
| Bypass-RLS session + `SET LOCAL app.current_tenant` | `app/database.py:99-113`, `app/modules/jd/actors.py:429-551` | `poll_ats_connection` actor |
| Explicit `tenant_id` filtering on every query under bypass-RLS | `app/modules/interview_runtime/service.py` | `ATSImporter` |
| Env-driven secret + `field_validator` requirement | `app/config.py:108-118` (`candidate_jwt_secret`) | `ats_credentials_encryption_keys` |

## Architecture overview

```
┌────────────────────────────────────────────────────────────────┐
│ External cron  (Railway Cron Jobs / AWS EventBridge Scheduler) │
└────────────────────────┬───────────────────────────────────────┘
                         │ every 5 minutes
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ app/cli/ats_tick.py                                            │
│   - SELECT due ats_connections                                 │
│   - enqueue one poll_ats_connection per tenant                 │
└────────────────────────┬───────────────────────────────────────┘
                         │ Dramatiq messages
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ app/modules/ats/actors.py :: poll_ats_connection               │
│   - load + decrypt ATSConnectionState                          │
│   - ensure_authenticated() (refresh tokens if needed)          │
│   - ATSImporter().sync_tenant(adapter)                         │
│   - persist mutated state                                      │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ app/modules/ats/importer.py :: ATSImporter                     │
│   Phase 1: Clients      → client_account org_units (stub)      │
│   Phase 2: Users        → ats_user_mappings                    │
│   Phase 3: Jobs         → job_postings                         │
│   Phase 4: Applicants   → candidates (import_candidate)        │
│   Phase 5: Submissions  → candidate_job_assignments            │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ app/modules/ats/adapters/ceipal.py :: CeipalAdapter            │
│   - holds ATSConnectionState                                   │
│   - HTTP calls to Ceipal API                                   │
│   - vendor-specific → canonical DTOs                           │
│   - AsyncIterator per list method, pagination internal         │
│   - typed exceptions on auth / rate-limit / transient / perm   │
└────────────────────────────────────────────────────────────────┘
```

## Polling scheduler

### Picked: external cron + CLI tick + per-tenant fan-out

Rejected alternatives (full analysis in the brainstorming transcript):

- **APScheduler with Postgres jobstore** — 4.0 still alpha after five years; 3.x has no leader election. Two worker replicas would double-fire unless we bolt on advisory locks.
- **dramatiq-crontab** — Django-only (requires `INSTALLED_APPS` / `manage.py crontab`). We are on FastAPI.
- **periodiq** — unmaintained, single-process by design.
- **In-process scheduler in the worker** — leader election problem at HA.
- **pg_cron + outbox** — credible alternative, but spreads scheduling logic into SQL `cron.schedule()` calls and `cron.job_run_details` table maintenance. Keeping the scheduling contract inside Python (versioned + testable + observable) is cleaner.
- **HTTP "tick" endpoint** — couples scheduling to API uptime and pollutes API latency metrics.

### How it works

The cron fires every 5 minutes. The tick is a stateless ~200ms Python script. Per-tenant cadence is governed by `ats_connections.next_poll_at`, **not by the cron firing rate** — this is the load-bearing invariant.

```python
# app/cli/ats_tick.py
async def main() -> None:
    structlog + OpenTelemetry + Sentry init (mirrors app/worker.py)

    async with get_bypass_session() as db:
        due = await db.execute(text("""
            SELECT id, tenant_id FROM ats_connections
            WHERE active
              AND next_poll_at <= now()
              AND (poll_lock_acquired_at IS NULL
                   OR poll_lock_acquired_at < now() - interval '20 minutes')
            ORDER BY next_poll_at ASC
            LIMIT 500
            FOR UPDATE SKIP LOCKED
        """))
        for connection_id, tenant_id in due:
            # Soft lease — prevents next tick from re-enqueueing this row
            await db.execute(
                text("UPDATE ats_connections SET poll_lock_acquired_at = now() "
                     "WHERE id = :id"),
                {"id": connection_id},
            )
            poll_ats_connection.send(str(connection_id), str(tenant_id))
        await db.commit()
```

Deploy targets:

- **Railway:** new "ats-scheduler" service in the same project, same Docker image, command `python -m app.cli.ats_tick`, cron `*/5 * * * *`. Railway's 5-min floor is irrelevant because per-tenant cadence is governed by `poll_interval_seconds` (default 900s = 15 min).
- **AWS ECS:** EventBridge Scheduler → ECS RunTask, same image, command override.
- **Local dev:** `nexus-scheduler` compose service that runs `python -m app.cli.ats_tick` in a sleep loop.

### Per-tenant fan-out actor

```python
# app/modules/ats/actors.py
@dramatiq.actor(
    max_retries=3,
    min_backoff=30_000,
    max_backoff=600_000,
    queue_name="ats_poll",
)
async def poll_ats_connection(connection_id: str, tenant_id: str) -> None:
    """
    Phase A: load + decrypt state, open sync_log row
    Phase B: adapter.ensure_authenticated() (may refresh tokens)
            on success: persist refreshed tokens immediately
            on ATSCredentialsInvalidError: disable connection, raise
    Phase C: ATSImporter().sync_tenant(adapter)
            on ATSRateLimitedError: advance next_poll_at = now()+retry_after, return cleanly
            on ATSPermanentError: write sync_log status='failed', raise (DLQ)
            on ATSTransientError: propagate (Dramatiq retries)
    Phase D: persist final state, advance next_poll_at with jitter,
            close sync_log status='success'
    """
```

Three transaction boundaries: load short, sync long with its own short transactions per importer phase, persist short. The actor never holds a single DB transaction across the full sync.

### Backpressure / Ceipal rate limits

Ceipal's rate-limit numbers are not documented (the portal page says *"contact your CEIPAL account representative"*). The 429 response does not include a `Retry-After` header.

Defaults:

- **`poll_interval_seconds = 900`** (15 min) per connection, configurable per row.
- **Jitter on `next_poll_at`:** `now() + poll_interval_seconds + random(0, 60s)`. Prevents thundering-herd at the minute after a deploy.
- **On 429:** adapter raises `ATSRateLimitedError(retry_after_seconds=60)` (configurable default). Actor sets `next_poll_at = now() + retry_after` and exits cleanly. No Dramatiq retry — next tick resumes naturally.
- **Per-connection config field `rate_limit_qps`** on `ats_connections` for when the actual Ceipal limit is known. Adapter throttles outgoing requests with `asyncio.Semaphore` based on this.

## Adapter Protocol

### Shape choice

Per-tenant **instance** (not stateless functions). All `list_*` methods return **`AsyncIterator[T]`** (not `list[T]`). DTOs are **canonical / vendor-agnostic Pydantic** models (not raw vendor dicts).

Rationale:
- Instance — Ceipal's auth model has mid-call state (access token may expire mid-page-fetch and need refresh). Encapsulating that in an instance is cleaner than mutating a passed-in object on every call.
- AsyncIterator — streaming matches data volume. A tenant with 10K candidates on first sync can be processed page-by-page; no need to buffer 10K rows in memory.
- Canonical DTOs — the whole "plug-and-play" wedge. Orchestrator code stays vendor-free.

### The Protocol

```python
# app/modules/ats/adapter.py
from typing import AsyncIterator, ClassVar, Protocol
from datetime import datetime
from app.modules.ats.schemas import (
    ATSClientPayload, ATSUserPayload, ATSJobPayload,
    ATSApplicantPayload, ATSSubmissionPayload,
)
from app.modules.ats.connection import ATSConnectionState


class ATSAdapter(Protocol):
    """Per-tenant ATS adapter.

    Constructed via app.modules.ats.registry.get_ats_adapter(state).
    Holds a reference to ATSConnectionState (credentials + token state).
    Owns its own auth/refresh logic. Short-lived: one instance per sync run.
    Not thread-safe.
    """

    vendor: ClassVar[str]           # 'ceipal', 'greenhouse', 'workday'
    state: ATSConnectionState       # mutable; orchestrator persists after sync

    async def ensure_authenticated(self) -> None:
        """Refresh tokens if expired or near-expiry (proactive at 80% lifetime).
        Idempotent. Raises ATSCredentialsInvalidError if stored credentials no
        longer work."""

    async def list_clients(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]: ...

    async def list_users(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]: ...

    async def list_jobs(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]: ...

    async def list_applicants(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]: ...

    async def list_submissions(
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]: ...
```

Six methods (one auth, five list). Pagination handled internally. The `since` parameter is `datetime | None` — caller passes a timestamp; adapter translates to whatever the vendor wants (Ceipal uses `modifiedAfter`).

### Canonical DTOs

```python
# app/modules/ats/schemas.py
class ATSClientPayload(BaseModel):
    external_id: str              # vendor's stable ID (Ceipal: opaque hash)
    name: str
    website: str | None = None
    industry: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    address: str | None = None
    status: str | None = None     # vendor lifecycle ('Active', …)
    contacts: list[dict] = Field(default_factory=list)
    raw: dict
    fetched_at: datetime


class ATSUserPayload(BaseModel):
    external_id: str
    email: str
    display_name: str
    role: str | None = None
    status: str | None = None
    raw: dict
    fetched_at: datetime


class ATSJobPayload(BaseModel):
    external_id: str
    external_client_id: str       # routing key → ats_client_mappings
    title: str
    description: str | None = None
    status: str | None = None     # mirrors to job_postings.external_status
    location: str | None = None
    skills: list[str] = Field(default_factory=list)
    employment_type: str | None = None
    work_arrangement: str | None = None
    salary_range_min: int | None = None
    salary_range_max: int | None = None
    salary_currency: str | None = None
    assigned_recruiter_external_ids: list[str] = Field(default_factory=list)
    raw: dict
    fetched_at: datetime


class ATSApplicantPayload(BaseModel):
    external_id: str
    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    current_title: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    raw: dict
    fetched_at: datetime


class ATSSubmissionPayload(BaseModel):
    external_id: str
    applicant_external_id: str
    job_external_id: str
    submission_status: str | None = None   # 'Internal Interview Scheduled', …
    pipeline_status: str | None = None
    source: str | None = None              # 'Naukri', 'LinkedIn', etc.
    submitted_on: datetime | None = None
    submitted_by_external_id: str | None = None
    pay_rate: Decimal | None = None
    employment_type: str | None = None
    raw: dict                              # carries resume_token, Documents[], etc.
    fetched_at: datetime
```

Every DTO carries a `raw: dict` of the verbatim vendor payload. Three reasons: future-proofing (start using `business_unit_id` later without re-sync), audit forensics ("what did Ceipal tell us about this candidate at import time?"), and cheap (Postgres JSONB compression handles it). The PII-discipline rule ("no raw PII in logs") is unaffected — `raw` lives in DB columns, never log fields.

### Exception hierarchy

```python
# app/modules/ats/errors.py
class ATSError(Exception): ...

class ATSPermanentError(ATSError): ...
class ATSCredentialsInvalidError(ATSPermanentError): ...    # auth failed even after refresh
class ATSAuthorizationError(ATSPermanentError): ...         # 403 — scope insufficient
class ATSVendorContractError(ATSPermanentError): ...        # vendor returned malformed response

class ATSTransientError(ATSError): ...
class ATSNetworkError(ATSTransientError): ...               # 5xx, connection failure
class ATSRateLimitedError(ATSTransientError):
    def __init__(self, retry_after_seconds: int, message: str = ""):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

class ATSUnknownVendorError(ATSPermanentError): ...
class ATSConnectionNotFoundError(ATSPermanentError): ...
```

Mirrors `_PERMANENT_EXCEPTIONS` pattern in `app/modules/jd/actors.py:57-63`. Actor decides: permanent → disable connection + raise (DLQ); rate-limited → advance next_poll_at, return cleanly; transient → re-raise so Dramatiq retries.

## Connection state and credential encryption

### `ATSConnectionState` vs `ATSConnection` ORM row

Two distinct things, separated deliberately:

| Layer | What | Purpose |
|---|---|---|
| `ATSConnection` ORM | DB row, credentials + tokens encrypted | Persistence |
| `ATSConnectionState` | In-memory working copy, decrypted, mutable | What the adapter holds |

Boundary: `load → decrypt → ATSConnectionState → adapter mutates → encrypt → persist`. The adapter never touches the ORM.

```python
# app/modules/ats/connection.py
@dataclass
class ATSConnectionState:
    id: UUID
    tenant_id: UUID
    vendor: str
    credentials: dict[str, Any]                              # vendor-specific, decrypted
    access_token: str | None = None
    refresh_token: str | None = None
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    last_synced_cursors: dict[str, datetime] = field(default_factory=dict)
    poll_interval_seconds: int = 900
```

`last_synced_cursors` is JSONB keyed by entity type: `{"clients": "2026-05-12T08:30:00Z", "jobs": …}`. Resumability: if phase 4 fails, phases 1–3's cursors have advanced; phase 4 retries from the last cursor on next run.

### Encryption: Fernet + MultiFernet from day 1

```python
# app/modules/ats/crypto.py
def encrypt_secret(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())

def decrypt_secret(ciphertext: bytes) -> str:
    return _get_fernet().decrypt(ciphertext).decode()

def encrypt_credentials_blob(plaintext: dict) -> bytes:
    return _get_fernet().encrypt(json.dumps(plaintext).encode())

def decrypt_credentials_blob(ciphertext: bytes) -> dict:
    return json.loads(_get_fernet().decrypt(ciphertext).decode())
```

`MultiFernet` from day 1 (not plain `Fernet`). The setting is `ats_credentials_encryption_keys: list[str]` — first key encrypts; all keys try for decrypt. Rotation = prepend a new key + backfill, no schema migration.

Tokens are also encrypted, not just credentials. A leaked refresh token gives 7 days of Ceipal access; same threat model as credentials.

Key location: env var at MVP → AWS Secrets Manager at enterprise. Application code unchanged; only `app/config.py` source changes. Rotation runbook at `docs/security/ats-credentials-rotation.md` (precondition for production, per root `CLAUDE.md` rotation rule).

### Settings additions

```python
# app/config.py
ats_credentials_encryption_keys: list[str] = []

@field_validator("ats_credentials_encryption_keys")
@classmethod
def _ats_keys_required(cls, v, info):
    env = info.data.get("environment", "development")
    if not v and env != "test":
        raise ValueError(
            "ATS_CREDENTIALS_ENCRYPTION_KEYS is required (comma-separated, "
            "first key is active; generate one with: `python -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key()"
            ".decode())\"`)."
        )
    return v
```

Mirrors the `candidate_jwt_secret` validator at `app/config.py:108-118`.

## Adapter registry

```python
# app/modules/ats/registry.py
from app.modules.ats.adapters.ceipal import CeipalAdapter

_REGISTRY: dict[str, Type[ATSAdapter]] = {
    CeipalAdapter.vendor: CeipalAdapter,
    # GreenhouseAdapter.vendor: GreenhouseAdapter,    # future
    # WorkdayAdapter.vendor: WorkdayAdapter,          # future
}

SUPPORTED_VENDORS = frozenset(_REGISTRY.keys())

def get_ats_adapter(state: ATSConnectionState) -> ATSAdapter:
    cls = _REGISTRY.get(state.vendor)
    if cls is None:
        raise ATSUnknownVendorError(state.vendor)
    return cls(state)
```

Adding a vendor is purely additive: implement adapter class, register one line, define vendor's credential schema (Pydantic), update the recruiter-side connection form. No core code changes.

**Key difference from `notifications/_create_provider()`:** vendor selection is **per-connection** (data — `state.vendor`), not **per-deployment** (env). Different tenants can use different ATSes simultaneously.

## Importer — five-phase sequential

```python
# app/modules/ats/importer.py
class ATSImporter:
    async def sync_tenant(self, adapter: ATSAdapter) -> SyncResult:
        result = SyncResult()
        result.clients     = await self._run_phase("clients",     self._sync_clients,     adapter)
        result.users       = await self._run_phase("users",       self._sync_users,       adapter)
        result.jobs        = await self._run_phase("jobs",        self._sync_jobs,        adapter)
        result.applicants  = await self._run_phase("applicants",  self._sync_applicants,  adapter)
        result.submissions = await self._run_phase("submissions", self._sync_submissions, adapter)
        return result

    async def _run_phase(self, name, fn, adapter):
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{adapter.state.tenant_id}'"))
            with tracer.start_as_current_span(f"ats.sync.{name}"):
                phase_result = await fn(db, adapter)
            await db.commit()
        adapter.state.last_synced_cursors[name] = phase_result.sync_started_at
        return phase_result
```

Sequential by necessity (jobs need client mappings; submissions need job mappings). Per-phase transaction = partial-failure tolerance: failure in phase 5 doesn't undo phases 1–4.

### Phase 1 — Clients → `client_account` org_units

For each `ATSClientPayload`:

1. Lookup `ats_client_mappings` by `(tenant_id, vendor, external_id)`.
2. **If not found:**
   - Build stub `company_profile` from Ceipal fields (name, website, industry, country/state/city, address — direct field copy, no AI).
   - Call `org_units.create_org_unit(parent_unit_id=<root>, unit_type='client_account', name=payload.name, company_profile=stub, company_profile_completion_status='pending', created_by=connection.created_by)`.
   - Insert mapping row with `source_metadata = {"contacts": payload.contacts, "raw": payload.raw}`.
3. **If found:** update `external_client_name`, refresh `source_metadata`, advance `last_synced_at`. Do NOT rename the org_unit (recruiter may have manually edited).

Root company unit (`is_root=true`) lookup cached at phase start.

### Phase 2 — ATS users → `ats_user_mappings`

Simplest phase. For each `ATSUserPayload`, upsert keyed on `(tenant_id, vendor, external_user_id)`. `internal_user_id` stays NULL — recruiter explicitly maps via UI later.

⚠️ Ceipal's `getUsersList` doesn't document `modifiedAfter`. Phase 2 is full-sync per run (small list; cheap).

### Phase 3 — Jobs → `job_postings`

For each `ATSJobPayload`:

1. Resolve `external_client_id` → `org_unit_id` via `ats_client_mappings`. Mapping missing (race: phase 1 hasn't completed for this client) → skip + log; next run picks up.
2. Read `org_unit.company_profile_completion_status`:
   - `complete` → target state `draft`; enqueue `extract_and_enhance_jd` actor after commit.
   - `pending` → target state `blocked_pending_client_setup`; do NOT enqueue extraction.
3. Upsert `job_postings` keyed on `(tenant_id, source='ats_ceipal', external_id)`:
   - `title`, `description_raw ← payload.description`, `org_unit_id`, `source = 'ats_ceipal'`, `external_id`, `external_status ← payload.status`
   - `location`, `employment_type`, `work_arrangement`, salary fields
   - `created_by = connection.created_by`
4. For each `external_recruiter_id` in `payload.assigned_recruiter_external_ids`: upsert `ats_job_recruiter_assignments`.

**Edge cases:**
- `created_by` is non-nullable. We use `ats_connections.created_by`. The user who connected Ceipal is the audit attribution; FK resolves even if that user is later deactivated.
- Ceipal updates a job's text → we update the row but do **not** auto-trigger signal re-extraction (would burn LLM cost on minor field changes). Recruiter manually re-extracts via existing endpoint.
- Ceipal sets `job_status='Closed'` → we mirror to `external_status='Closed'` but do **not** auto-archive (recruiter may still be running interviews on shortlisted candidates). UI surfaces "Ceipal closed this job" as a banner.

### Phase 4 — Applicants → `candidates`

For each `ATSApplicantPayload`:

1. Construct `SourcedCandidate` via `ATSImportSource(vendor='ceipal').normalize(payload)` (lives in `app/modules/ats/sources.py` to keep cross-module import acyclic — see Module boundary section).
2. Call new `candidates.service.import_candidate(db, sourced, tenant_id, created_by=connection.created_by)`.
3. On `DuplicateEmailError`: existing candidate from manual flow → link `external_id` + `source_metadata` onto the existing row but do NOT overwrite `name/phone/etc.` (recruiter may have edited them).

New function in `candidates.service`:

```python
async def import_candidate(
    db: AsyncSession,
    sourced: SourcedCandidate,
    tenant_id: UUID,
    created_by: UUID,
) -> Candidate:
    """Upsert a candidate from a non-form source (ATS import, CSV bulk).
    Idempotency: (tenant_id, source, external_id) when external_id set;
    falls back to (tenant_id, email) for source='manual'.
    On duplicate-email collision with manual: link external_id + source_metadata
    onto the existing row; do NOT overwrite editable fields."""
```

Requires a new partial-unique index on `candidates`:
```
(tenant_id, source, external_id) WHERE pii_redacted_at IS NULL AND external_id IS NOT NULL
```

### Phase 5 — Submissions → `candidate_job_assignments`

For each job touched in phase 3 (or any job with active assignments), iterate `adapter.list_submissions(job_external_id, since=...)`.

For each `ATSSubmissionPayload`:

1. Resolve `applicant_external_id` → `candidate_id` via `candidates` (keyed on `source='ats_ceipal', external_id`). Missing → log + skip (phase 4 race).
2. Resolve `job_external_id` → `job_posting_id` via `job_postings`. Missing → log + skip.
3. Upsert `candidate_job_assignments` keyed on `(tenant_id, source='ats_ceipal', external_id=submission_external_id)`:
   - Existing columns: `candidate_id`, `job_posting_id`, stage progression fields
   - New columns (this migration): `source`, `external_id`, `source_metadata`
   - `source_metadata` carries `submission_status`, `pipeline_status`, `pay_rate`, `resume_token`, `Documents[]`, full `raw` payload

Why no separate `ats_submissions` table: `candidate_job_assignments` *is* the submission for our purposes; we just tag it with external info — same pattern as `candidates` and `job_postings`. One fewer table to migrate.

### Unblock trigger on profile completion

When a recruiter completes a `pending` company profile, `org_units.service.update_company_profile` triggers `_unblock_pending_jobs_for_org_unit`:

```python
async def _unblock_pending_jobs_for_org_unit(db, org_unit_id, tenant_id):
    blocked = await db.execute(
        select(JobPosting).where(
            JobPosting.tenant_id == tenant_id,
            JobPosting.org_unit_id == org_unit_id,
            JobPosting.status == 'blocked_pending_client_setup',
        )
    )
    for job in blocked.scalars().all():
        job.status = 'draft'
        await log_event(db, action='jd.unblocked_by_profile_completion', ...)
    await db.flush()
    # Caller commits, then enqueues extract_and_enhance_jd for each unblocked job
```

## Ceipal adapter implementation specifics

### Authentication

Ceipal uses an unusual auth model (not OAuth2):

- **Initial auth:** `POST https://api.ceipal.com/v2/createAuthtoken/` with body `{"email", "password", "apiKey"}` returns `access_token` (1h) + `refresh_token` (7d).
- **Refresh:** `POST https://api.ceipal.com/v2/refreshToken/` with header `Token: Bearer <expired_access_token>`. Returns new access_token. **The refresh requires the OLD access token in the header** — never discard the access_token until after successful refresh.
- **Refresh-token expired (7d):** transparent re-auth from stored email/password/apiKey. Recruiter never sees a "reconnect" prompt unless their *credentials* stop working (password rotated upstream, key revoked).

Refresh strategy: **proactive at 80% of access-token lifetime** (~48 min) when the adapter is constructed and at the top of `ensure_authenticated()`. Lazy 401-retry as fallback for any race.

### List endpoints + URL patterns

```
GET /v2/getClientsList/         ?modifiedAfter=, modifiedBefore=, limit=, status=
GET /v2/getClientDetails/<id>
GET /v2/getUsersList            (no modifiedAfter; full sync every run)
GET /v2/getUserDetails/<id>
GET /v2/getJobPostingsList/     ?modifiedAfter=, modifiedBefore=, limit=, jobStatus=, client=
GET /v2/getJobPostingDetails/<id>
GET /v2/getApplicantsList/      ?modifiedAfter=, modifiedBefore=, limit=, source=
GET /v2/getApplicantDetails/<id>
GET /v2/getSubmissionsList/     ?jobId=<REQUIRED>, modifiedAfter=, modifiedBefore=, limit=
GET /v2/getSubmissionDetails/<id>
```

Pagination is uniform across all list endpoints: response envelope `{count, num_pages, limit, page_number, next, previous, results: [...]}`. `limit` is 5–50. Adapter walks pages until `next` is empty.

Some URL paths are not explicitly stated in the docs portal (the renderer strips them); the patterns above are inferred from naming convention + sample responses. Postman-confirmable during CeipalAdapter implementation.

### Error envelope mapping

Ceipal returns a uniform error envelope across every endpoint:

| HTTP | Body | Maps to |
|---|---|---|
| 400 | `"Invalid parameters or filters."` | `ATSVendorContractError` — engineering bug; log full request |
| 401 | `"Please provide the access token."` | After unsuccessful refresh: `ATSCredentialsInvalidError` |
| 403 | `"Your company access is temporarily disabled."` | `ATSAuthorizationError` |
| 404 | `"Data not found."` | Treat as empty list for list endpoints; raise for detail endpoints |
| 429 | `"Request limit exceeded. Please try again later."` | `ATSRateLimitedError(retry_after_seconds=settings.ats_default_retry_after)` |
| 5xx / network | — | `ATSNetworkError` (transient) |

No `Retry-After` header on 429. Default backoff: 60s, overridable per connection via `rate_limit_qps` + per-error backoff config.

### Date format inconsistencies

Real responses show two date formats in the **same** payload:
- `"modified": "2026-05-12T06:38:35Z"` — ISO 8601 with UTC marker
- `"submitted_on": "2026-05-12 06:31:23"` — space-separated, no timezone

Adapter's date parser handles both. For timezone-less strings, **assume UTC** (Ceipal API base is UTC per docs; if a future bug surfaces a different convention, this is a one-line fix).

### `pay_rate` type ambiguity

Docs say `string`; real response shows `40.0` (numeric). DTO accepts `Union[str, float, int]` → coerce to `Decimal | None`.

### Resume sync — deferred from MVP

The Submission payload carries:
- `resume_token` — opaque (looks like AES + base64). Not a URL or file path.
- `Documents` — array of document references.
- `selected_submission_documents`, `merged_pdf_document`, `merge_document_path` — string references.

All of these are preserved in `candidate_job_assignments.source_metadata` JSONB. Not consumed by any MVP code path.

**To enable resume sync later** (one method + one DTO + one importer phase):

1. Add `fetch_resume_for_submission(submission_external_id) → ATSResumePayload | None` to `ATSAdapter` Protocol.
2. Add `ATSResumePayload(BaseModel)` with `submission_external_id`, `filename`, `mime_type`, `content: bytes`, `fetched_at`.
3. Discover the Ceipal endpoint that accepts `resume_token` (likely `getResume`/`downloadResume`/`getDocument` under "Custom API"; Postman-confirmable).
4. Add Phase 6 to the importer: for each new submission with `resume_token` and `candidate.resume_s3_key IS NULL`, fetch via adapter, upload to S3 via `resume_service`, update `candidates.resume_s3_key`.
5. Track via `resume_fetched_at` column on `candidate_job_assignments` (so retries don't re-fetch).

Until then, ATS-imported candidates have `candidates.resume_s3_key = NULL`. Recruiters can still manually upload via the existing flow.

## Data model — Alembic migration `0029_ats_core`

### New tables

```sql
CREATE TABLE ats_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    vendor TEXT NOT NULL,                           -- 'ceipal'
    credentials_ciphertext BYTEA NOT NULL,
    access_token_ciphertext BYTEA NULL,
    refresh_token_ciphertext BYTEA NULL,
    access_token_expires_at TIMESTAMPTZ NULL,
    refresh_token_expires_at TIMESTAMPTZ NULL,
    last_synced_cursors JSONB NOT NULL DEFAULT '{}'::jsonb,
    poll_interval_seconds INTEGER NOT NULL DEFAULT 900,
    next_poll_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    poll_lock_acquired_at TIMESTAMPTZ NULL,
    last_poll_started_at TIMESTAMPTZ NULL,
    last_poll_completed_at TIMESTAMPTZ NULL,
    last_poll_error TEXT NULL,
    rate_limit_qps NUMERIC NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    disabled_reason TEXT NULL,
    disabled_at TIMESTAMPTZ NULL,
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, vendor)
);

CREATE TABLE ats_client_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ats_vendor TEXT NOT NULL,
    external_client_id TEXT NOT NULL,
    external_client_name TEXT NOT NULL,
    org_unit_id UUID NOT NULL REFERENCES organizational_units(id) ON DELETE CASCADE,
    source_metadata JSONB NULL,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, ats_vendor, external_client_id)
);

CREATE TABLE ats_user_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ats_vendor TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    external_user_email TEXT NOT NULL,
    external_user_display_name TEXT NOT NULL,
    external_user_role TEXT NULL,
    external_user_status TEXT NULL,
    external_user_metadata JSONB NULL,
    internal_user_id UUID NULL REFERENCES users(id),
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mapped_at TIMESTAMPTZ NULL,
    mapped_by UUID NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, ats_vendor, external_user_id)
);

CREATE TABLE ats_job_recruiter_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    job_posting_id UUID NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    ats_vendor TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_posting_id, external_user_id)
);

CREATE TABLE ats_sync_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES ats_connections(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL,                            -- 'running' | 'success' | 'partial' | 'failed'
    entity_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_phase TEXT NULL,
    error_summary TEXT NULL,
    correlation_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Column additions to existing tables

```sql
-- Org units gain a completion-status flag
ALTER TABLE organizational_units ADD COLUMN company_profile_completion_status TEXT
    NOT NULL DEFAULT 'complete'
    CHECK (company_profile_completion_status IN ('pending', 'complete'));

-- Job postings gain Ceipal lifecycle mirror + new status value
ALTER TABLE job_postings ADD COLUMN external_status TEXT NULL;
-- Broaden status CHECK constraint to include 'blocked_pending_client_setup':
ALTER TABLE job_postings DROP CONSTRAINT job_postings_status_check;
ALTER TABLE job_postings ADD CONSTRAINT job_postings_status_check
    CHECK (status IN ('draft', 'signals_extracting', 'signals_extraction_failed',
                      'signals_extracted', 'pipeline_built', 'active', 'archived',
                      'blocked_pending_client_setup'));

-- Candidate job assignments gain source typing (becomes Submission mirror)
ALTER TABLE candidate_job_assignments ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE candidate_job_assignments ADD COLUMN external_id TEXT NULL;
ALTER TABLE candidate_job_assignments ADD COLUMN source_metadata JSONB NULL;
CREATE UNIQUE INDEX candidate_job_assignments_external_idx
    ON candidate_job_assignments (tenant_id, source, external_id)
    WHERE external_id IS NOT NULL;

-- Candidates gain partial unique on external_id for ATS-import idempotency
CREATE UNIQUE INDEX candidates_tenant_source_external_idx
    ON candidates (tenant_id, source, external_id)
    WHERE pii_redacted_at IS NULL AND external_id IS NOT NULL;
```

### RLS policies on every new tenant-scoped table

Canonical pair per backend `CLAUDE.md` → "RLS Pattern — Always Applied":

```sql
CREATE POLICY tenant_isolation ON <table>
  USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

CREATE POLICY service_bypass ON <table>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

Applied to: `ats_connections`, `ats_client_mappings`, `ats_user_mappings`, `ats_job_recruiter_assignments`, `ats_sync_logs`. All five added to `_TENANT_SCOPED_TABLES` in `app/main.py` so the startup `_assert_rls_completeness` check verifies them.

### Migration ordering and rollback

Pre-deployment Alembic run. Rollback script drops all five new tables and reverses the four ALTERs (in reverse order). Required per backend `CLAUDE.md` migration rule.

## Recruiter-side router

```python
# app/modules/ats/router.py
router = APIRouter(prefix="/api/ats", tags=["ats"])

GET    /api/ats/connections                                  list this tenant's connections
POST   /api/ats/connections                                  create + test + persist
GET    /api/ats/connections/{id}                             show metadata (no credentials)
DELETE /api/ats/connections/{id}                             disable + delete
POST   /api/ats/connections/{id}/sync                        manually trigger a sync
GET    /api/ats/connections/{id}/sync-logs                   recent sync history
GET    /api/ats/connections/{id}/unmapped-users              ATS users without internal_user_id
POST   /api/ats/connections/{id}/users/{external_user_id}/map   map ATS user → ProjectX user
```

### Create flow

```python
@router.post("/connections", status_code=201)
async def create_connection(
    body: ConnectionCreateRequest,                # discriminated union by vendor
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_super_admin),
):
    # 1. Build temporary ATSConnectionState (no DB persist yet)
    # 2. Construct adapter, await adapter.ensure_authenticated() — tests credentials
    # 3. On ATSCredentialsInvalidError: 422 {"code": "ATS_CREDENTIALS_INVALID", ...}
    # 4. On success: encrypt credentials + tokens, insert ats_connections row
    # 5. Audit log: ats.connection.created (vendor only, no credentials)
    # 6. Fire-and-forget initial sync (poll_ats_connection.send())
```

### Pydantic discriminated union

```python
class CeipalCredentials(BaseModel):
    email: str
    password: str = Field(..., repr=False)        # never appears in repr/logs
    api_key: str = Field(..., repr=False)

class CeipalConnectionRequest(BaseModel):
    vendor: Literal['ceipal'] = 'ceipal'
    credentials: CeipalCredentials

ConnectionCreateRequest = Annotated[
    CeipalConnectionRequest,
    Field(discriminator='vendor'),
]
```

OpenAPI schema, validation, and wire format are all unambiguous. Adding a new vendor = one more union member.

### Rate limits (per root `CLAUDE.md`)

| Endpoint class | Per-IP | Per-tenant |
|---|---|---|
| `POST /api/ats/connections` | 10/min | 5/hour |
| `POST /api/ats/connections/{id}/sync` | 30/min | 12/hour |
| All other authenticated `/api/ats/*` | 600/min | 10k/min |

### Authorization

All write endpoints require `require_super_admin`. Reads require any role with the new permission `ats_view` (added to the seeded permission set). The user mapping endpoints (`/users/{external_user_id}/map`) require `super_admin` or new permission `ats_admin`.

## Audit and observability

### Audit log actions

Written via existing `audit_log` (`app/modules/audit/`):

- `ats.connection.created` — payload `{"vendor": "ceipal"}`. Never credentials.
- `ats.connection.disabled` — payload `{"reason": "<scrubbed>"}`.
- `ats.connection.deleted`
- `ats.sync.started` — once per actor invocation
- `ats.sync.completed` — payload includes entity counts
- `ats.sync.failed` — payload `{"phase": "...", "error_code": "..."}`. No PII in error message.
- `ats.user_mapping.created` — when a recruiter maps a Ceipal user
- `jd.unblocked_by_profile_completion` — when the unblock cascade fires

### OpenTelemetry

- Root span per `poll_ats_connection` invocation: `ats.poll`.
- Child spans per importer phase: `ats.sync.clients`, `ats.sync.users`, `ats.sync.jobs`, `ats.sync.applicants`, `ats.sync.submissions`.
- Grandchild spans per adapter HTTP call: `ats.ceipal.getClientsList page=1`.

### structlog

- `correlation_id` (`ats-<uuid4>`) bound at actor entry via `bind_contextvars`. Mirrors `app/worker.py:55-60` pattern.
- Every log line for the sync carries `connection_id`, `tenant_id`, `vendor`, `correlation_id`.

### Sentry

- Tags on every event: `connection_id`, `tenant_id`, `vendor`.
- `ATSPermanentError` → `level=error`.
- `ATSRateLimitedError` → `level=warning` (informational; expected).
- `ATSTransientError` after max retries (DLQ) → `level=error`.

### PII discipline (concrete rules)

| Allowed in logs | Forbidden in logs |
|---|---|
| `connection_id`, `tenant_id`, `vendor`, `correlation_id` | candidate name, candidate email |
| entity counts (`clients_new: 2`, `jobs_updated: 30`) | JD body, resume bytes |
| durations, retry counts | access_token, refresh_token, password, api_key |
| Ceipal external_id (opaque hash; not PII) | mapped internal user emails |
| `phase`, `error_code` (mapped from typed exceptions) | raw Ceipal payload (lives in DB `source_metadata`, never logs) |

The adapter's `httpx` client adds an explicit log-redactor that strips `Authorization` headers and request/response bodies from any log records. `httpx`'s default behavior dumps bodies otherwise.

## Module boundary discipline

Per backend `CLAUDE.md` → "Module public API":

- `app/modules/ats/__init__.py` declares `__all__` exporting: `ATSAdapter`, `get_ats_adapter`, `ATSConnectionState`, all `ATS*Payload` DTOs, all exception classes. **Cross-module callers MUST import through `__init__.py`**, never deep-import.
- The two cross-module callers of this module are:
  - `app/worker.py` — registers actors (`from app.modules.ats import actors`); allowed deep import per the "actors registered by worker.py" rule.
  - `app/main.py` — registers router; same allowance.
- The `ATSImportSource` bridge lives in `app/modules/ats/sources.py`, **not** in `app/modules/candidates/sources.py`. The cross-module import direction is `ats → candidates` only (importer calls `candidates.service.import_candidate`), preserving acyclicity. Putting `ATSImportSource` in `candidates/sources.py` would force `candidates → ats` import for the `ATSCandidatePayload` type, creating a cycle.
- New domain module `ats` added to `KNOWN_DOMAIN_MODULES` in `tests/test_module_boundaries.py`.

## Frontend touchpoints

New route tree under `frontend/app/app/(dashboard)/settings/integrations/` (does not exist today; `/settings/team` and `/settings/org-units` are the only existing settings subtrees):

| Route | Purpose |
|---|---|
| `/settings/integrations` | List connected ATSes; "Connect ATS" CTA. Status badge per connection (Active / Disabled / Last sync N min ago). |
| `/settings/integrations/connect` | Vendor picker → vendor-specific credential form (Ceipal: email + password + api_key). |
| `/settings/integrations/[connectionId]` | Detail page: sync history, blocked-job count, unmapped-user count, manual "Sync now". |
| `/settings/integrations/[connectionId]/users` | User mapping table — unmapped Ceipal users with "Map to ProjectX user" and "Send invite" actions. |

Plus enhancements to existing surfaces:

- **Org visualizer** (`/settings/org-units`): badge on `client_account` units with `company_profile_completion_status='pending'`. Clicking jumps to a pre-populated "complete profile" form.
- **JDs index** (`/jobs`): chip on imported jobs ("From Ceipal"); filter pill for `status='blocked_pending_client_setup'` with a count.
- **Candidate cards**: source badge ("Imported from Ceipal") when `source.startswith('ats_')`. Also displays Ceipal `submission_status` (e.g. "Ceipal: Internal Interview Scheduled") as a read-only secondary badge.

All components follow `frontend/app/CLAUDE.md` conventions: `components/px/` primitives on `@base-ui-components/react` (not shadcn), React Hook Form + Zod, `useMutation` + `apiFetch` + sonner toasts + `queryClient.invalidateQueries`. Credential forms never log or echo input values.

Per root `CLAUDE.md`, the recruiter dashboard app MUST NOT import `livekit-*` packages — unaffected by this change. ATS UI uses only px primitives + existing infra.

## File layout

```
backend/nexus/app/
├── cli/
│   └── ats_tick.py                    ← scheduler tick CLI
├── config.py                          ← + ats_credentials_encryption_keys
├── modules/
│   ├── ats/
│   │   ├── __init__.py                ← public API (__all__)
│   │   ├── adapter.py                 ← ATSAdapter Protocol
│   │   ├── schemas.py                 ← canonical DTOs
│   │   ├── errors.py                  ← exception hierarchy
│   │   ├── connection.py              ← ATSConnectionState + load/persist
│   │   ├── crypto.py                  ← Fernet/MultiFernet
│   │   ├── registry.py                ← get_ats_adapter()
│   │   ├── models.py                  ← ORM (ATSConnection, ATSClientMapping,
│   │   │                                ATSUserMapping, ATSJobRecruiterAssignment,
│   │   │                                ATSSyncLog)
│   │   ├── sources.py                 ← ATSImportSource (candidates bridge)
│   │   ├── importer.py                ← ATSImporter (5 phases)
│   │   ├── actors.py                  ← poll_ats_connection Dramatiq actor
│   │   ├── service.py                 ← connection mgmt + manual-sync
│   │   ├── router.py                  ← /api/ats/* routes
│   │   ├── authz.py                   ← require_ats_admin guard
│   │   └── adapters/
│   │       ├── __init__.py
│   │       └── ceipal.py              ← CeipalAdapter
│   ├── candidates/
│   │   └── service.py                 ← + import_candidate()
│   └── org_units/
│       └── service.py                 ← + _unblock_pending_jobs_for_org_unit
├── main.py                            ← register ats router + add new tables
│                                        to _TENANT_SCOPED_TABLES
└── worker.py                          ← import app.modules.ats.actors

backend/nexus/migrations/versions/
└── 0029_ats_core.py                   ← all new tables + columns + RLS + indexes
                                         + CHECK constraint broadening

backend/nexus/docs/security/
└── ats-credentials-rotation.md        ← rotation runbook (prod precondition)

backend/nexus/docker-compose.yml       ← + nexus-scheduler service for dev

frontend/app/app/(dashboard)/settings/integrations/
├── page.tsx                           ← list
├── connect/page.tsx                   ← vendor-specific create form
└── [connectionId]/
    ├── page.tsx                       ← detail + sync history
    └── users/page.tsx                 ← user mapping

frontend/app/lib/api/ats.ts            ← apiFetch wrappers
```

## Testing strategy

Per backend `CLAUDE.md` → "Test Coverage Gates":

- **`app/modules/ats/crypto.py`** — 100% branch coverage. Round-trip tests for `encrypt_credentials_blob`/`decrypt_credentials_blob` and `encrypt_secret`/`decrypt_secret`. Rotation tests with `MultiFernet` (encrypt with key A, decrypt after prepending key B).
- **`app/modules/ats/adapters/ceipal.py`** — adapter methods tested against a fake httpx transport. Cover: token refresh on 401, rate-limit on 429, network failure, pagination across multiple pages, delta-cursor handling, date-format-inconsistency parsing, `pay_rate` type coercion.
- **`app/modules/ats/importer.py`** — each phase tested in isolation with a fake adapter. Cover: new vs existing client mapping, jobs landing in `blocked_pending_client_setup` when org_unit is `pending`, jobs landing in `draft` when complete, candidate duplicate-email collision linking, submission upsert idempotency.
- **`app/modules/ats/actors.py`** — `poll_ats_connection` end-to-end with mock CeipalAdapter. Cover: `ATSPermanentError` → disable + DLQ, `ATSRateLimitedError` → advance next_poll_at, `ATSTransientError` → propagate (Dramatiq retries).
- **Cross-tenant isolation** — for each of the five new tables, a test that confirms `SET LOCAL app.current_tenant = '<tenant_a>'` returns 0 rows when the row belongs to `<tenant_b>`.
- **Scheduler tick** — test that confirms the `FOR UPDATE SKIP LOCKED` query coexists with a parallel tick without double-enqueueing.
- **Unblock trigger** — completing a `pending` company profile transitions all `blocked_pending_client_setup` JDs in that org_unit to `draft` and enqueues `extract_and_enhance_jd` for each.
- **Frontend `/settings/integrations/*`** — Vitest coverage on the credential-form Zod validators and the `apiFetch` wrappers in `lib/api/ats.ts`.

Project-wide line coverage target stays 80%; the credential-encryption + RLS-on-new-tables paths target 100% branch (per root `CLAUDE.md` test gate).

## Rollout plan

1. **Migration 0029** ships first as a separate PR — schema changes (all tables, columns, RLS, indexes, CHECK broadening) reviewable in isolation. Rollback script included.
2. **Backend module + actor + tick + Ceipal adapter** ship next. Behind a settings flag `ats_enabled` (default `false`) until the integrations UI is live so the `/api/ats/*` router only serves to authenticated super-admins of opt-in tenants.
3. **Frontend `/settings/integrations/*`** ships once the backend is stable.
4. **Dogfood phase:** first connection is our own Ceipal account (or a Ceipal sandbox if one exists). Monitor `ats_sync_logs` and error rates for at least one week.
5. **GA:** drop the `ats_enabled` flag; document the connection process in the customer-facing docs.

## Open questions and risks

- **Ceipal rate-limit numbers.** Not in docs; default 60s `retry_after` + `rate_limit_qps` per-connection knob. Worth pinging Ceipal account rep to get actual numbers; not blocking implementation.
- **Resume retrieval endpoint.** Out of MVP scope but worth Postman-confirming during the dogfood phase so the "enable resume sync" follow-up has a known target.
- **Ceipal exact URL paths.** Pattern-inferred for some endpoints; first Postman test during CeipalAdapter implementation will confirm.
- **Auto-mapping by email.** Deferred from MVP (security-conservative). If recruiters complain about manual mapping friction at GA, consider a soft "matched by email — confirm?" UI hint, not a silent auto-map.
- **Per-entity audit rows.** `ats_sync_logs.entity_counts` is a JSONB summary today; at high volume we'd want one row per imported entity for searchable forensics. Tracked as future work; not blocking.
- **Pre-MVP test corpus.** Ceipal sandbox/test accounts aren't documented in the portal; we'll need to seed a test Ceipal account or rely on the dogfood connection for end-to-end coverage. If a sandbox exists, document it for future engineering onboarding.

## Future surfaces (deferred from MVP)

- **Outbound sync** — push interview outcome from ProjectX → Ceipal. Adapter Protocol gains `push_interview_outcome(submission_external_id, outcome) → None`. Drives the existing `app/modules/analysis` and `app/modules/reporting` stubs into completion.
- **Resume sync** — see "Resume sync — deferred from MVP" section above.
- **Greenhouse / Workday adapters** — implement the same Protocol; add to registry; one module file per vendor.
- **Job Requisition sync** — separate Protocol method `list_job_requisitions`; new state on `job_postings` to represent requisition-before-posting; UI flag.
- **Interview entity mirroring (outbound)** — push our session outcomes to Ceipal's Interviews endpoint as a record.
- **Auto-detect upstream deletions** — periodic full-sync that flags mappings whose source entity no longer exists.
- **Recruiter user auto-mapping** — soft match by email with confirmation UI.
- **Webhook receiver** — if a future ATS provides webhooks (Greenhouse does), the pattern is a new router `app/modules/ats/webhooks.py` with vendor-specific signature verification, enqueueing a `process_ats_webhook` actor that runs through the same importer phases.
