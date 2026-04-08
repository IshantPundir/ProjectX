# Phase 2A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first end-to-end slice of the Phase 2 interview instrument — Company Profile capture, raw JD upload, AI signal extraction via Dramatiq + OpenAI + instructor + Langfuse, and the read-only three-panel review UI.

**Architecture:** New `app/ai/` provider-agnostic layer (AIConfig, PromptLoader, OpenAI client factory). New `app/modules/jd/` module with service, Dramatiq actor, state machine, authz, SSE event generator, and custom exception handlers. New `nexus-worker` docker-compose service. Frontend: shadcn/ui bootstrap, TanStack Query + React Hook Form + Zod + `@microsoft/fetch-event-source`, new `/jobs` route group with three-panel review page, rewritten Company Profile form shared between onboarding and org unit settings.

**Tech Stack:**
- Backend: FastAPI, SQLAlchemy async + asyncpg, Dramatiq + Redis, `openai`, `instructor`, `langfuse.openai`, `sse-starlette`, structlog
- Frontend: Next.js 16 App Router, Tailwind v4 (CSS `@theme` config), shadcn/ui, TanStack Query, React Hook Form + Zod, `@microsoft/fetch-event-source`
- Database: Supabase-managed Postgres with RLS via `app.current_tenant`; Supabase SQL migrations only (no Alembic)

**Reference spec:** `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`

**Important conventions to match:**
- Flat test file layout — `backend/nexus/tests/test_jd_*.py`, not `tests/modules/jd/test_*.py`
- Existing conftest fixtures: `db`, `client`, `create_test_client`, `create_test_user`, `create_test_org_unit`
- Async throughout — no sync blocking calls
- Structlog with bound context for correlation IDs
- Tailwind v4 uses `@theme` directive in `app/globals.css`, NOT `tailwind.config.ts`
- Frontend CLAUDE.md AGENTS.md rule: consult `node_modules/next/dist/docs/` before writing new App Router files

---

## Task ordering rationale

Tasks 0–5 are non-code verification tasks that inform the rest. Tasks 6–14 establish backend foundations (config, migrations, models) so downstream code compiles. Tasks 15–41 build the backend feature stack bottom-up (AI layer → errors → state machine → service → actor → SSE → router → worker). Tasks 42–44 update backend docs. Tasks 45–68 build the frontend top-down (deps → providers → forms → pages). Tasks 69–71 finalize docs and run manual E2E acceptance.

**Pre-flight:** Before starting Task 0, ensure the local Supabase stack is running (`supabase start` from `backend/supabase/`) and that a fresh copy of the repo is checked out on a dedicated feature branch.

---

## Task 0: Create feature branch and verify pre-flight state

**Files:**
- None — git/setup only

- [ ] **Step 1: Create feature branch from main**

```bash
cd /home/ishant/Projects/ProjectX
git checkout main
git pull
git checkout -b phase-2a-jd-pipeline
git status
```

Expected: on branch `phase-2a-jd-pipeline`, clean working tree.

- [ ] **Step 2: Verify backend starts cleanly against current migrations**

```bash
cd backend/nexus
docker compose up --build -d
docker compose logs nexus --tail 30
curl -s http://127.0.0.1:8000/health
```

Expected: `{"status":"ok"}`, no errors in logs.

- [ ] **Step 3: Verify backend tests pass on a clean checkout**

```bash
docker compose run --rm nexus pytest -x
```

Expected: all tests green. If any fail, STOP — do not proceed until the baseline is clean.

- [ ] **Step 4: Verify frontend builds**

```bash
cd ../../frontend/app
npm install
npm run lint
npm run build
```

Expected: no lint errors, clean build.

- [ ] **Step 5: Tear down docker, commit nothing**

```bash
cd ../../backend/nexus
docker compose down
```

No commit — Task 0 is verification only.

---

## Task 1: Verify `UserContext.has_permission_in_unit()` ancestry behavior

**Why this is Day-1:** Getting this wrong means correct permission grants silently 403 on JD endpoints. The result dictates whether `require_job_access()` is belt-and-braces or the primary enforcement path.

**Files:**
- Read: `backend/nexus/app/modules/auth/context.py`
- Create: `backend/nexus/tests/test_has_permission_in_unit_ancestry.py`

- [ ] **Step 1: Read the current implementation**

```bash
docker compose up -d
```

Read the file via your editor or `Read` tool: `backend/nexus/app/modules/auth/context.py`. Look specifically at `has_permission_in_unit()` — does it check ONLY the exact `unit_id` argument, or does it walk the `parent_unit_id` chain?

- [ ] **Step 2: Write the verification test**

Create `backend/nexus/tests/test_has_permission_in_unit_ancestry.py`:

```python
"""Day-1 verification: does has_permission_in_unit() inherit permissions
from ancestor units, or only check the exact unit ID?

The answer dictates whether require_job_access() in app/modules/jd/authz.py
needs to walk ancestry itself (primary) or can rely on the helper
(belt-and-braces). See spec Task 1."""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.auth.context import RoleAssignment, UserContext
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


@pytest.mark.asyncio
async def test_ancestry_inheritance_behavior(db):
    """Create a parent → child org unit hierarchy, grant a recruiter a role
    on the PARENT, and check whether has_permission_in_unit(child, ...)
    returns True (ancestry inheritance) or False (exact-match only)."""

    tenant = await create_test_client(db)
    await db.flush()

    user = await create_test_user(db, tenant.id)

    parent_unit = await create_test_org_unit(
        db, tenant.id, name="Parent Division", unit_type="division"
    )
    child_unit = await create_test_org_unit(
        db,
        tenant.id,
        name="Child Team",
        unit_type="team",
        parent_unit_id=parent_unit.id,
    )
    await db.flush()

    ctx = UserContext(
        user_id=user.id,
        tenant_id=tenant.id,
        email=user.email,
        is_super_admin=False,
        is_projectx_admin=False,
        assignments=[
            RoleAssignment(
                org_unit_id=parent_unit.id,
                org_unit_name=parent_unit.name,
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["jobs.view"],
            ),
        ],
    )

    # Grant is on parent_unit only
    assert ctx.has_permission_in_unit(parent_unit.id, "jobs.view") is True

    # Does it inherit to child_unit?
    inherits = ctx.has_permission_in_unit(child_unit.id, "jobs.view")

    # ASSERTION: print the result — this test is a probe, not an assertion.
    # The test should ALWAYS PASS; we read its output to learn the answer.
    print(f"\n\n=== DAY-1 TASK 1 RESULT ===")
    print(f"has_permission_in_unit(child, 'jobs.view') = {inherits}")
    print(f"Parent grant inherits to child: {inherits}")
    print(f"==========================\n")

    # No assertion on inherits — we want to see the value regardless
```

- [ ] **Step 3: Run the probe test**

```bash
docker compose run --rm nexus pytest tests/test_has_permission_in_unit_ancestry.py -v -s
```

Expected: test PASSES, and the `-s` flag lets the `print()` output surface. Record the `inherits` value.

- [ ] **Step 4: Record the finding in the spec**

Edit `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md` — find the "Day-1 Verification Tasks" section. Under Task 1, append:

```markdown
**Verification result (2026-04-09):** has_permission_in_unit ancestry inheritance = <True|False>
Implication: require_job_access() will be <belt-and-braces|primary enforcement>.
```

Fill in the actual values.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/tests/test_has_permission_in_unit_ancestry.py docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md
git commit -m "test: Day-1 probe — verify has_permission_in_unit ancestry behavior"
```

---

## Task 2: Verify OpenAI model access and pick the real model ID

**Files:**
- Modify: `backend/nexus/.env.example` (add OPENAI_* vars)
- Note: don't commit actual API keys to `.env`

- [ ] **Step 1: Confirm an OpenAI API key exists in the local environment**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -c "^OPENAI_API_KEY=" .env || echo "MISSING"
```

If MISSING, prompt the user to add one to `.env` before proceeding. Do not fabricate a key.

- [ ] **Step 2: List available models via the OpenAI API**

```bash
docker compose run --rm nexus sh -c '
python -c "
import os, openai
c = openai.OpenAI(api_key=os.environ[\"OPENAI_API_KEY\"])
for m in c.models.list().data:
    if m.id.startswith(\"gpt-\"):
        print(m.id)
" | sort
'
```

Expected: a list of `gpt-*` model IDs available to the key. Pick the model that most closely matches the spec's intent (a GPT-5-class model capable of `reasoning_effort=medium` structured output). If none match, use the strongest available `gpt-4*` model and note the substitution.

- [ ] **Step 3: Record the chosen model ID**

Open the spec and find the Day-1 Task 2 section. Append:

```markdown
**Verification result (2026-04-09):** OPENAI_EXTRACTION_MODEL = <chosen model ID>
(Substitution from "gpt-5.2" placeholder documented here.)
```

- [ ] **Step 4: Add OpenAI env vars to .env.example**

Edit `backend/nexus/.env.example`. Add (after the existing AI-related block):

```bash
# --- OpenAI (Phase 2A) ---
OPENAI_API_KEY=
OPENAI_EXTRACTION_MODEL=gpt-5.2
OPENAI_EXTRACTION_EFFORT=medium
```

(Leave the placeholder `gpt-5.2` in .env.example — actual value lives in `.env`.)

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/.env.example docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md
git commit -m "chore: add OPENAI_* env vars to .env.example and record Day-1 Task 2 finding"
```

---

## Task 3: Verify `langfuse.openai` drop-in import path

**Files:**
- None — verification only, findings recorded in the spec

- [ ] **Step 1: Probe the installed langfuse version**

```bash
docker compose run --rm nexus python -c "
import langfuse
print('version:', langfuse.__version__)
try:
    from langfuse.openai import AsyncOpenAI
    print('IMPORT_PATH: langfuse.openai.AsyncOpenAI OK')
except ImportError as e:
    print('IMPORT_PATH_FAIL:', e)
"
```

Expected: prints the version and whether the import path works.

- [ ] **Step 2: If the import failed, probe alternatives**

If Step 1 printed `IMPORT_PATH_FAIL`, try:

```bash
docker compose run --rm nexus python -c "
import pkgutil, langfuse
for m in pkgutil.iter_modules(langfuse.__path__, langfuse.__name__ + '.'):
    print(m.name)
"
```

Look for a module named something like `langfuse.openai` or `langfuse.integrations.openai`.

- [ ] **Step 3: Record the finding**

Append to the spec under Day-1 Task 3:

```markdown
**Verification result (2026-04-09):**
langfuse version: <version>
Working import: `from <module> import AsyncOpenAI`
```

- [ ] **Step 4: If the import path differs from the spec, update all affected sketches**

The spec's `app/ai/client.py` sketch uses `from langfuse.openai import AsyncOpenAI`. If that's wrong, update every occurrence in the spec before Task 17.

- [ ] **Step 5: Commit (if spec changed)**

```bash
git add docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md
git commit -m "docs: record Day-1 Task 3 finding — langfuse.openai import path"
```

If no spec changes: skip the commit.

---

## Task 4: Verify `reasoning_effort` parameter shape

**Files:**
- None — verification only

- [ ] **Step 1: Probe the target model with a top-level `reasoning_effort` kwarg**

```bash
docker compose run --rm nexus python -c "
import os, asyncio, openai

async def probe():
    c = openai.AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'])
    try:
        r = await c.chat.completions.create(
            model=os.environ.get('OPENAI_EXTRACTION_MODEL', 'gpt-5.2'),
            reasoning_effort='medium',
            messages=[{'role': 'user', 'content': 'Say the word ready and nothing else.'}],
        )
        print('SHAPE: top-level kwarg works')
        print(r.choices[0].message.content)
    except TypeError as e:
        print('SHAPE_FAIL_TYPE:', e)
    except openai.BadRequestError as e:
        print('SHAPE_FAIL_400:', e)

asyncio.run(probe())
"
```

Expected one of:
- `SHAPE: top-level kwarg works` — good, spec is correct
- `SHAPE_FAIL_TYPE` — SDK doesn't accept the kwarg; try extra_body
- `SHAPE_FAIL_400` — API rejected the param; try extra_body or responses endpoint

- [ ] **Step 2: If Step 1 failed, probe `extra_body` variant**

```bash
docker compose run --rm nexus python -c "
import os, asyncio, openai

async def probe():
    c = openai.AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'])
    try:
        r = await c.chat.completions.create(
            model=os.environ.get('OPENAI_EXTRACTION_MODEL', 'gpt-5.2'),
            extra_body={'reasoning_effort': 'medium'},
            messages=[{'role': 'user', 'content': 'Say ready.'}],
        )
        print('SHAPE: extra_body works')
    except Exception as e:
        print('EXTRA_BODY_FAIL:', type(e).__name__, e)

asyncio.run(probe())
"
```

- [ ] **Step 3: Record finding in spec**

Under Day-1 Task 4, append:

```markdown
**Verification result (2026-04-09):**
Working shape: <top-level kwarg | extra_body={'reasoning_effort': ...} | responses endpoint>
```

- [ ] **Step 4: Update actor sketch if needed**

If the shape is NOT a top-level kwarg, edit the spec's `app/modules/jd/actors.py` sketch (the `client.chat.completions.create(...)` call) to use the correct shape. Every task that references it must be consistent.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md
git commit -m "docs: record Day-1 Task 4 finding — reasoning_effort parameter shape"
```

Skip if no spec changes were needed.

---

## Task 5: Verify `instructor` exception class name

**Files:**
- None — verification only

- [ ] **Step 1: Probe instructor.exceptions**

```bash
docker compose run --rm nexus python -c "
try:
    import instructor.exceptions as ie
    print('MODULE OK')
    print('Classes:', [n for n in dir(ie) if not n.startswith('_')])
except ImportError as e:
    print('NO_EXCEPTIONS_MODULE:', e)

import instructor
print('\ninstructor root attrs:', [n for n in dir(instructor) if 'xcept' in n.lower() or 'etry' in n.lower()])
"
```

Expected: prints the list of exception class names. Look for one named like `InstructorRetryException`, `RetryException`, `IncompleteOutputException`, or `ValidationError`.

- [ ] **Step 2: Identify the class raised after retry exhaustion**

Read the instructor source to confirm which class is raised when `max_retries` is exceeded:

```bash
docker compose run --rm nexus python -c "
import inspect, instructor
print(inspect.getsourcefile(instructor))
" | xargs dirname
```

Then `cat` the `exceptions.py` or `retry.py` file in that directory and find the retry-exhausted raise site.

- [ ] **Step 3: Record the correct name in the spec**

Under Day-1 Task 5, append:

```markdown
**Verification result (2026-04-09):**
Retry-exhausted class: `instructor.exceptions.<ClassName>` (or wherever it lives)
Update errors.py _SAFE_MESSAGES key to match before Task 21.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md
git commit -m "docs: record Day-1 Task 5 finding — instructor exception class name"
```

---

## Task 6: Add OpenAI dependencies and remove Anthropic

**Files:**
- Modify: `backend/nexus/pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml dependencies**

In `backend/nexus/pyproject.toml`, find the `dependencies = [` block. Remove the Anthropic line (if present) and add these entries:

```toml
    # --- AI provider (Phase 2A) ---
    "openai>=1.60,<2",
    "instructor>=1.7,<2",
    "sse-starlette>=2.1,<3",
```

Remove any line like `"anthropic>=...,<..."` if it exists.

- [ ] **Step 2: Rebuild the image to install the new deps**

```bash
cd backend/nexus
docker compose build nexus
```

Expected: `openai`, `instructor`, `sse-starlette` install cleanly.

- [ ] **Step 3: Verify imports work**

```bash
docker compose run --rm nexus python -c "
import openai, instructor
from sse_starlette.sse import EventSourceResponse
print('openai:', openai.__version__)
print('instructor:', instructor.__version__)
print('sse-starlette: OK')
"
```

Expected: prints versions, no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/pyproject.toml
git commit -m "chore(backend): add openai, instructor, sse-starlette deps; drop anthropic"
```

---

## Task 7: Replace `anthropic_api_key` with OpenAI settings

**Files:**
- Modify: `backend/nexus/app/config.py`

- [ ] **Step 1: Read current config.py**

Open `backend/nexus/app/config.py` and locate the `anthropic_api_key` field (around line 39 per spec).

- [ ] **Step 2: Replace the Anthropic block with OpenAI settings**

In `backend/nexus/app/config.py`, replace:

```python
    # AI — Anthropic
    anthropic_api_key: str = ""
```

with:

```python
    # --- AI — OpenAI (Phase 2A) ---
    openai_api_key: str = ""

    # Model selection — env-driven, swappable without code changes.
    # See app/ai/config.py for usage. Default placeholders; real values
    # come from .env or deployment config.
    openai_extraction_model: str = "gpt-5.2"
    openai_extraction_effort: str = "medium"

    # OpenAI request tuning
    openai_request_timeout_seconds: float = 120.0
    openai_max_retries: int = 2    # instructor-level schema retries; actor-level retries are separate
```

- [ ] **Step 3: Verify the app still imports**

```bash
docker compose run --rm nexus python -c "from app.config import settings; print(settings.openai_extraction_model)"
```

Expected: prints `gpt-5.2` (or the verified ID from Task 2 if .env is set).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/config.py
git commit -m "chore(config): replace anthropic_api_key with openai_* settings"
```

---

## Task 8: Add `jobs.view` to `ALL_PERMISSIONS`

**Files:**
- Modify: `backend/nexus/app/modules/auth/permissions.py`
- Modify: `backend/nexus/tests/test_permissions.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/nexus/tests/test_permissions.py`:

```python
def test_jobs_view_permission_exists():
    """Phase 2A adds jobs.view as a new canonical permission."""
    from app.modules.auth.permissions import ALL_PERMISSIONS
    assert "jobs.view" in ALL_PERMISSIONS
```

- [ ] **Step 2: Run the test — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_permissions.py::test_jobs_view_permission_exists -v
```

Expected: FAIL — `"jobs.view" not in ALL_PERMISSIONS`.

- [ ] **Step 3: Add `jobs.view` to the frozenset**

In `backend/nexus/app/modules/auth/permissions.py`, add `"jobs.view",` to the frozenset literal (in alphabetical order, between `interviews.conduct` and the existing `jobs.create`):

```python
ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "users.invite_admins",
        "users.invite_users",
        "users.deactivate",
        "org_units.create",
        "org_units.manage",
        "jobs.view",
        "jobs.create",
        "jobs.manage",
        "candidates.view",
        "candidates.evaluate",
        "candidates.advance",
        "interviews.schedule",
        "interviews.conduct",
        "reports.view",
        "reports.export",
        "settings.client",
        "settings.integrations",
    }
)
```

- [ ] **Step 4: Run the test — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_permissions.py::test_jobs_view_permission_exists -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/auth/permissions.py backend/nexus/tests/test_permissions.py
git commit -m "feat(auth): add jobs.view to ALL_PERMISSIONS"
```

---

## Task 9: Migration 1 — Company Profile reset + tracking columns

**Files:**
- Create: `backend/supabase/migrations/20260410000000_phase_2a_company_profile_reset.sql`

- [ ] **Step 1: Create the migration file**

Create `backend/supabase/migrations/20260410000000_phase_2a_company_profile_reset.sql`:

```sql
-- =============================================================
-- Phase 2A — Company Profile hard cutover
-- Adds tracking columns and nulls any existing company_profile
-- that doesn't match the new 4-field shape.
-- =============================================================

ALTER TABLE organizational_units
    ADD COLUMN company_profile_completed_at TIMESTAMPTZ,
    ADD COLUMN company_profile_completed_by UUID REFERENCES users(id);

-- Hard cutover: null any profile that doesn't carry all four new fields.
-- Pre-MVP dev data only. App-layer validation enforces character limits
-- and enum values (see app/modules/org_units/schemas.py in Task 39).
UPDATE organizational_units
   SET company_profile = NULL
 WHERE company_profile IS NOT NULL
   AND NOT (
        company_profile ? 'about'
    AND company_profile ? 'industry'
    AND company_profile ? 'company_stage'
    AND company_profile ? 'hiring_bar'
   );

-- We deliberately do NOT add a CHECK constraint on the JSONB structure.
-- Constraints on JSONB are too brittle for future schema evolution.
```

- [ ] **Step 2: Apply the migration locally**

```bash
cd backend
supabase db reset
```

Expected: all migrations re-run cleanly. Watch for any errors on the new migration.

- [ ] **Step 3: Verify the new columns exist**

```bash
supabase db --help >/dev/null 2>&1 && \
  PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'organizational_units'
  AND column_name IN ('company_profile_completed_at','company_profile_completed_by');
"
```

Expected: two rows returned.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/supabase/migrations/20260410000000_phase_2a_company_profile_reset.sql
git commit -m "feat(db): Phase 2A migration 1 — company profile reset + tracking columns"
```

---

## Task 10: Migration 2 — `job_postings`, snapshots, sessions stub, updated_at trigger

**Files:**
- Create: `backend/supabase/migrations/20260410000001_phase_2a_job_postings.sql`

- [ ] **Step 1: Create the migration file**

Create `backend/supabase/migrations/20260410000001_phase_2a_job_postings.sql`:

```sql
-- =============================================================
-- Phase 2A — job_postings, job_posting_signal_snapshots,
--            sessions stub, set_updated_at() trigger function
-- =============================================================

-- ------------------------------------------------------------
-- Reusable updated_at trigger function.
-- Phase 1 never created one, so Phase 1 tables' updated_at columns
-- are frozen at creation time. Retrofitting Phase 1 tables is a
-- separate cross-cutting cleanup (see Deferred Hardening #10).
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------
-- job_postings
-- State machine values in 2A:
--   draft, signals_extracting, signals_extraction_failed, signals_extracted
-- ------------------------------------------------------------

CREATE TABLE job_postings (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES clients(id),
    org_unit_id               UUID NOT NULL REFERENCES organizational_units(id),
    title                     TEXT NOT NULL,
    description_raw           TEXT NOT NULL,
    project_scope_raw         TEXT,
    description_enriched      TEXT,
    enriched_manually_edited  BOOLEAN NOT NULL DEFAULT FALSE,
    status                    TEXT NOT NULL DEFAULT 'draft',
    status_error              TEXT,
    source                    TEXT NOT NULL DEFAULT 'native',
    external_id               TEXT,
    target_headcount          INTEGER,
    deadline                  DATE,
    created_by                UUID NOT NULL REFERENCES users(id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_job_postings_tenant_org_unit ON job_postings (tenant_id, org_unit_id);
CREATE INDEX idx_job_postings_status          ON job_postings (tenant_id, status);
CREATE INDEX idx_job_postings_created_at      ON job_postings (tenant_id, created_at DESC);

CREATE TRIGGER set_job_postings_updated_at
    BEFORE UPDATE ON job_postings
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE job_postings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON job_postings
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON job_postings
  USING (current_setting('app.bypass_rls', true) = 'true');

-- ------------------------------------------------------------
-- job_posting_signal_snapshots
-- ------------------------------------------------------------

CREATE TABLE job_posting_signal_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES clients(id),
    job_posting_id        UUID NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    version               INTEGER NOT NULL,
    required_skills       JSONB NOT NULL,
    preferred_skills      JSONB NOT NULL,
    must_haves            JSONB NOT NULL,
    good_to_haves         JSONB NOT NULL,
    min_experience_years  INTEGER NOT NULL,
    seniority_level       TEXT NOT NULL,
    role_summary          TEXT NOT NULL,
    confirmed_by          UUID REFERENCES users(id),
    confirmed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (job_posting_id, version)
);

CREATE INDEX idx_signal_snapshots_job_posting
    ON job_posting_signal_snapshots (job_posting_id, version DESC);

ALTER TABLE job_posting_signal_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON job_posting_signal_snapshots
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON job_posting_signal_snapshots
  USING (current_setting('app.bypass_rls', true) = 'true');

-- ------------------------------------------------------------
-- sessions stub — Phase 3 FK parent; candidate_id has NO FK in 2A
-- ------------------------------------------------------------

CREATE TABLE sessions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES clients(id),
    job_posting_id            UUID NOT NULL REFERENCES job_postings(id),
    candidate_id              UUID,  -- FK deferred to Phase 3
    status                    TEXT NOT NULL DEFAULT 'scheduled',
    started_at                TIMESTAMPTZ,
    completed_at              TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON sessions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON sessions
  USING (current_setting('app.bypass_rls', true) = 'true');
```

- [ ] **Step 2: Apply the migration**

```bash
cd backend
supabase db reset
```

Expected: all migrations run cleanly including the new one.

- [ ] **Step 3: Verify the tables and trigger exist**

```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "
SELECT table_name FROM information_schema.tables
  WHERE table_schema = 'public' AND table_name IN ('job_postings','job_posting_signal_snapshots','sessions');

SELECT trigger_name FROM information_schema.triggers
  WHERE event_object_table = 'job_postings';

SELECT routine_name FROM information_schema.routines
  WHERE routine_name = 'set_updated_at';
"
```

Expected: 3 tables, `set_job_postings_updated_at` trigger, `set_updated_at` function.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/supabase/migrations/20260410000001_phase_2a_job_postings.sql
git commit -m "feat(db): Phase 2A migration 2 — job_postings, snapshots, sessions, set_updated_at()"
```

---

## Task 11: Migration 3 — seed `jobs.view` into system roles

**Files:**
- Create: `backend/supabase/migrations/20260410000002_phase_2a_jobs_view_permission.sql`

- [ ] **Step 1: Create the migration**

Create `backend/supabase/migrations/20260410000002_phase_2a_jobs_view_permission.sql`:

```sql
-- =============================================================
-- Phase 2A — Seed jobs.view into Admin, Recruiter, Hiring Manager
-- Matches the ALL_PERMISSIONS frozenset updated in Task 8.
-- =============================================================

UPDATE roles
   SET permissions = permissions || '["jobs.view"]'::jsonb
 WHERE is_system = TRUE
   AND name IN ('Admin', 'Recruiter', 'Hiring Manager')
   AND NOT (permissions ? 'jobs.view');
```

- [ ] **Step 2: Apply and verify**

```bash
cd backend
supabase db reset

PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "
SELECT name, permissions ? 'jobs.view' AS has_jobs_view
FROM roles WHERE is_system = TRUE ORDER BY name;
"
```

Expected: Admin / Recruiter / Hiring Manager rows all show `has_jobs_view = t`.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/supabase/migrations/20260410000002_phase_2a_jobs_view_permission.sql
git commit -m "feat(db): Phase 2A migration 3 — seed jobs.view into system roles"
```

---

## Task 12: Add SQLAlchemy models for JobPosting, Snapshot, Session

**Files:**
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Append the three new models to models.py**

In `backend/nexus/app/models.py`, after the existing `AuditLog` class, add:

```python
class JobPosting(Base):
    """Phase 2A — the raw-JD-to-enriched-JD-to-signals instrument.
    State machine states: draft, signals_extracting,
    signals_extraction_failed, signals_extracted. Mutations go through
    app.modules.jd.state_machine.transition()."""
    __tablename__ = "job_postings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    project_scope_raw: Mapped[str | None] = mapped_column(Text)
    description_enriched: Mapped[str | None] = mapped_column(Text)
    enriched_manually_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="'draft'")
    status_error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="'native'")
    external_id: Mapped[str | None] = mapped_column(Text)
    target_headcount: Mapped[int | None] = mapped_column()
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class JobPostingSignalSnapshot(Base):
    """Phase 2A — immutable versioned snapshot of extracted+inferred signals
    for a job posting. Written by the Dramatiq actor after a successful
    Call 1. version=1 is the initial extraction. 2B+ will add confirmed_by/at."""
    __tablename__ = "job_posting_signal_snapshots"
    __table_args__ = (
        UniqueConstraint("job_posting_id", "version", name="uq_snapshot_job_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    required_skills: Mapped[list] = mapped_column(JSONB, nullable=False)
    preferred_skills: Mapped[list] = mapped_column(JSONB, nullable=False)
    must_haves: Mapped[list] = mapped_column(JSONB, nullable=False)
    good_to_haves: Mapped[list] = mapped_column(JSONB, nullable=False)
    min_experience_years: Mapped[int] = mapped_column(nullable=False)
    seniority_level: Mapped[str] = mapped_column(String, nullable=False)
    role_summary: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Session(Base):
    """Phase 3 stub. Defined in 2A so Phase 3 FKs have a parent.
    candidate_id column exists but NO FK constraint until Phase 3
    creates the candidates table."""
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # FK deferred to Phase 3
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="'scheduled'")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

Also add `Integer` to the SQLAlchemy import if not already present at the top of the file:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text, UniqueConstraint
```

- [ ] **Step 2: Verify models import cleanly**

```bash
cd backend/nexus
docker compose run --rm nexus python -c "
from app.models import JobPosting, JobPostingSignalSnapshot, Session
print('models loaded:', JobPosting.__tablename__, JobPostingSignalSnapshot.__tablename__, Session.__tablename__)
"
```

Expected: prints three table names.

- [ ] **Step 3: Run full existing test suite to check for regressions**

```bash
docker compose run --rm nexus pytest -x
```

Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/models.py
git commit -m "feat(models): add JobPosting, JobPostingSignalSnapshot, Session ORM classes"
```

---

## Task 13: Create `prompts/v1/jd_enhancement.txt`

**Files:**
- Create: `backend/nexus/prompts/v1/jd_enhancement.txt`

- [ ] **Step 1: Create the prompts directory and file**

```bash
mkdir -p backend/nexus/prompts/v1
```

Create `backend/nexus/prompts/v1/jd_enhancement.txt`:

```
You are an enterprise hiring intelligence system that enriches raw job descriptions and extracts structured hiring signals for downstream interview question generation.

# Your Task

You will receive a user message containing, IN THIS ORDER:
  1. The hiring company's profile (stable context — about, industry, company_stage, hiring_bar)
  2. The raw job description (the document being enriched)
  3. Optionally, a project scope paragraph

Read the context BEFORE reading the document. Context-first primes your understanding of what "strong" means for this role in this specific company's environment.

Produce a single structured output with two coupled parts:
  - `enriched_jd`: the rewritten JD following the canonical seven-section structure below
  - `signals`: the structured hiring signals extracted from the enriched JD

# The Dual-Audience Rule

The enriched JD serves two audiences at once:
  - A) The AI downstream (question bank generation) — needs precise role framing, clear must-haves, structured requirements
  - B) Candidates reading the posted job — needs full picture, company culture, perks, compensation

Your job is NOT to rewrite the original JD. It is to IMPROVE the sections that carry evaluation signal while PRESERVING the sections that belong to the recruiter/employer. If a section exists purely for candidate attraction (benefits, perks, equal opportunity legal text), pass it through verbatim. If a section carries signal (must-haves, responsibilities, role summary), enrich it for precision.

# Canonical Section Order (apply to output `enriched_jd`)

  1. Header (title, location, work arrangement, experience range) — Structure
  2. About the Company — Preserve if present; OMIT if absent (never populate from company profile)
  3. The Role (role summary, 2–3 sentences) — Enrich using company profile
  4. What You'll Do (responsibilities) — Restructure and clarify
  5. What We're Looking For (Must-Haves) — Strengthen with verifiable thresholds
  6. Good to Have (Nice-to-Have) — Trim to technical differentiators only; remove generic soft skills
  7. Qualifications — Preserve if present; omit if absent
  8. Benefits & Perks / Compensation / Equal Opportunity / Application Instructions — Preserve verbatim, zero modifications

# Signal Provenance

Every signal chip you produce MUST carry a `source` field and an `inference_basis` field:

  - `source = "ai_extracted"`: directly stated in the raw JD text → `inference_basis` = null
  - `source = "ai_inferred"`: NOT stated but logically implied → `inference_basis` = a short explanation of why

You may only infer from three legitimate sources, in descending confidence:
  1. Role title + seniority (highest confidence) — e.g. "Sr. X" implies architectural ownership and mentoring
  2. Technology adjacency (high confidence) — e.g. "MuleSoft" implies REST/SOAP API knowledge
  3. Company profile + project scope (medium confidence) — e.g. "fintech" implies data security awareness

HARD RULES — NEVER infer:
  - Specific certifications unless strongly implied by a named technology
  - Years of experience in domains not mentioned
  - Industry regulatory knowledge without a clear industry signal
  - Leadership scope beyond what the title explicitly states
  - Anything that could create a discriminatory screening criterion
  - Compensation or team structure

# Rules for the Enriched JD Body

  - PRESERVE: About the Company, Benefits & Perks, Compensation, Equal Opportunity, Application Instructions. Verbatim. Zero modifications.
  - ENRICH: Role Summary (apply company context), Must-Haves (verifiable thresholds: "5+ years hands-on in X", not "strong experience"), Good to Have (technical differentiators only — strip generic soft skills)
  - RESTRUCTURE: What You'll Do (group under 3–4 theme headers, concrete active-voice bullets, no fluff)
  - STRUCTURE: Header, Qualifications (light reformat only)
  - NEVER fabricate benefits, compensation, company mission, or equal-opportunity language the original JD did not contain

# Soft Skills Rule

Generic soft skills ("strong communicator", "fast learner", "proactive mindset", "excellent problem solver") are NOT signal chips. They are universal expectations. Strip them from Good to Have. If a soft skill is role-specific ("client-facing presence required for enterprise engagements"), fold it into the Role Summary as tone/context, not as a must-have chip.

# Output Constraints

  - `enriched_jd` MUST be at least 50 characters
  - `min_experience_years` MUST be 0–50
  - `seniority_level` MUST be one of: junior, mid, senior, lead, principal
  - `role_summary` MUST be 10–2000 characters
  - Every `SignalItem` with `source = "ai_inferred"` MUST have a non-null `inference_basis`
  - Every `SignalItem` with `source = "ai_extracted"` MUST have `inference_basis = null`

Return only the structured JSON output. Do not include any preamble, commentary, or markdown fencing.
```

- [ ] **Step 2: Verify the file is readable**

```bash
wc -l backend/nexus/prompts/v1/jd_enhancement.txt
```

Expected: ~60+ lines.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/prompts/v1/jd_enhancement.txt
git commit -m "feat(prompts): add jd_enhancement.txt for Phase 2A Call 1"
```

---

## Task 14: Create `app/ai/config.py` — AIConfig

**Files:**
- Create: `backend/nexus/app/ai/__init__.py`
- Create: `backend/nexus/app/ai/config.py`

- [ ] **Step 1: Create package init**

Create `backend/nexus/app/ai/__init__.py`:

```python
"""Provider-agnostic AI layer.

Business logic imports from this package — never from openai/instructor/langfuse
directly. This is the load-bearing abstraction that makes a future model or
provider swap a config change, not a code rewrite."""
```

- [ ] **Step 2: Create AIConfig**

Create `backend/nexus/app/ai/config.py`:

```python
"""Env-driven AI configuration.

Single source of truth for model IDs and reasoning_effort values. Never
hardcode a model name or effort level anywhere else. Swapping a model for a
specific task is a .env change + restart, no code change.

Future phase properties (reenrichment, generation, session, scoring) are
added to this class as each phase lands — not speculatively in 2A."""

from app.config import settings


class AIConfig:
    @property
    def extraction_model(self) -> str:
        return settings.openai_extraction_model

    @property
    def extraction_effort(self) -> str:
        return settings.openai_extraction_effort

    @property
    def request_timeout_seconds(self) -> float:
        return settings.openai_request_timeout_seconds

    @property
    def max_schema_retries(self) -> int:
        return settings.openai_max_retries


ai_config = AIConfig()
```

- [ ] **Step 3: Verify import**

```bash
docker compose run --rm nexus python -c "
from app.ai.config import ai_config
print('model:', ai_config.extraction_model)
print('effort:', ai_config.extraction_effort)
"
```

Expected: prints the configured model and effort.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/ai/__init__.py backend/nexus/app/ai/config.py
git commit -m "feat(ai): AIConfig env-driven model/effort registry"
```

---

## Task 15: Create `app/ai/prompts.py` — PromptLoader

**Files:**
- Create: `backend/nexus/app/ai/prompts.py`
- Create: `backend/nexus/tests/test_prompt_loader.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_prompt_loader.py`:

```python
"""Tests for the PromptLoader — file-system-based prompt versioning."""

import pytest

from app.ai.prompts import PromptLoader, prompt_loader


def test_loads_jd_enhancement_prompt():
    """The canonical Phase 2A prompt must be loadable by name."""
    content = prompt_loader.get("jd_enhancement")
    assert len(content) > 100
    assert "enriched_jd" in content
    assert "signals" in content
    assert "ai_extracted" in content


def test_caches_repeated_reads():
    """Second call for the same prompt returns the cached value without
    re-reading the file."""
    loader = PromptLoader(version="v1")
    first = loader.get("jd_enhancement")
    second = loader.get("jd_enhancement")
    assert first is second  # identity, not just equality — cached


def test_missing_prompt_raises():
    """Unknown prompt name raises FileNotFoundError."""
    loader = PromptLoader(version="v1")
    with pytest.raises(FileNotFoundError):
        loader.get("nonexistent_prompt_name")
```

- [ ] **Step 2: Run the test — expect FAIL (module doesn't exist)**

```bash
docker compose run --rm nexus pytest tests/test_prompt_loader.py -v
```

Expected: FAIL — ImportError on `app.ai.prompts`.

- [ ] **Step 3: Implement the PromptLoader**

Create `backend/nexus/app/ai/prompts.py`:

```python
"""PromptLoader — reads prompts/v{N}/<name>.txt at first access, caches in memory.

A future /api/admin/prompts/reload endpoint can bust the cache without a restart
(not in 2A). Failures to load are loud: the caller gets FileNotFoundError,
not a silent empty string."""

from pathlib import Path

import structlog

logger = structlog.get_logger()

# Repository layout: backend/nexus/prompts/v{version}/<name>.txt
# __file__ is backend/nexus/app/ai/prompts.py → parents[2] == backend/nexus
PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "prompts"


class PromptLoader:
    def __init__(self, version: str = "v1") -> None:
        self._version = version
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = PROMPTS_ROOT / self._version / f"{name}.txt"
            if not path.exists():
                raise FileNotFoundError(
                    f"Prompt not found: version={self._version} name={name} "
                    f"expected at {path}"
                )
            content = path.read_text(encoding="utf-8")
            self._cache[name] = content
            logger.info(
                "prompts.loaded",
                name=name,
                version=self._version,
                chars=len(content),
            )
        return self._cache[name]


prompt_loader = PromptLoader()
```

- [ ] **Step 4: Run the test — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_prompt_loader.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/ai/prompts.py backend/nexus/tests/test_prompt_loader.py
git commit -m "feat(ai): PromptLoader — file-system prompt versioning with memo cache"
```

---

## Task 16: Create `app/ai/schemas.py` — ExtractionOutput

**Files:**
- Create: `backend/nexus/app/ai/schemas.py`
- Create: `backend/nexus/tests/test_ai_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_ai_schemas.py`:

```python
"""Tests for the Call 1 structured output schemas."""

import pytest
from pydantic import ValidationError

from app.ai.schemas import ExtractedSignals, ExtractionOutput, SignalItem


def test_extracted_signal_item_no_basis():
    item = SignalItem(value="Kafka", source="ai_extracted", inference_basis=None)
    assert item.value == "Kafka"
    assert item.source == "ai_extracted"
    assert item.inference_basis is None


def test_inferred_signal_item_has_basis():
    item = SignalItem(
        value="REST/SOAP APIs",
        source="ai_inferred",
        inference_basis="MuleSoft adjacency — REST/SOAP is a prerequisite",
    )
    assert item.source == "ai_inferred"
    assert item.inference_basis is not None


def test_invalid_source_rejected():
    with pytest.raises(ValidationError):
        SignalItem(value="Anything", source="recruiter", inference_basis=None)


def test_extraction_output_minimum_fields():
    out = ExtractionOutput(
        enriched_jd="A" * 60,
        signals=ExtractedSignals(
            required_skills=[SignalItem(value="Python", source="ai_extracted", inference_basis=None)],
            preferred_skills=[],
            must_haves=[],
            good_to_haves=[],
            min_experience_years=5,
            seniority_level="senior",
            role_summary="A senior Python engineer building a scalable ingestion pipeline.",
        ),
    )
    assert out.signals.min_experience_years == 5
    assert out.signals.seniority_level == "senior"


def test_min_experience_out_of_range():
    with pytest.raises(ValidationError):
        ExtractedSignals(
            required_skills=[],
            preferred_skills=[],
            must_haves=[],
            good_to_haves=[],
            min_experience_years=-1,
            seniority_level="senior",
            role_summary="Something reasonable here.",
        )


def test_enriched_jd_too_short():
    with pytest.raises(ValidationError):
        ExtractionOutput(
            enriched_jd="too short",
            signals=ExtractedSignals(
                required_skills=[],
                preferred_skills=[],
                must_haves=[],
                good_to_haves=[],
                min_experience_years=0,
                seniority_level="junior",
                role_summary="A valid role summary that meets the minimum length requirement.",
            ),
        )
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_ai_schemas.py -v
```

Expected: all fail — ImportError.

- [ ] **Step 3: Implement schemas**

Create `backend/nexus/app/ai/schemas.py`:

```python
"""Call 1 structured output schemas — strict Pydantic models.

These are the exact shape returned by the gpt-5.2 extraction call via
instructor. Field names match job_posting_signal_snapshots column names.
Validators enforce the provenance rule: ai_inferred requires inference_basis,
ai_extracted requires it to be null."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SignalItem(BaseModel):
    value: str = Field(min_length=1)
    source: Literal["ai_extracted", "ai_inferred"]
    inference_basis: str | None = Field(
        default=None,
        description="Required when source='ai_inferred', else null",
    )

    @model_validator(mode="after")
    def check_basis_matches_source(self) -> "SignalItem":
        if self.source == "ai_inferred" and not self.inference_basis:
            raise ValueError(
                "SignalItem with source='ai_inferred' must have an inference_basis"
            )
        if self.source == "ai_extracted" and self.inference_basis is not None:
            raise ValueError(
                "SignalItem with source='ai_extracted' must have inference_basis=null"
            )
        return self


class ExtractedSignals(BaseModel):
    required_skills: list[SignalItem]
    preferred_skills: list[SignalItem]
    must_haves: list[SignalItem]
    good_to_haves: list[SignalItem]
    min_experience_years: int = Field(ge=0, le=50)
    seniority_level: Literal["junior", "mid", "senior", "lead", "principal"]
    role_summary: str = Field(min_length=10, max_length=2000)


class ExtractionOutput(BaseModel):
    enriched_jd: str = Field(min_length=50)
    signals: ExtractedSignals
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_ai_schemas.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/ai/schemas.py backend/nexus/tests/test_ai_schemas.py
git commit -m "feat(ai): Call 1 structured output schemas with provenance validators"
```

---

## Task 17: Create `app/ai/client.py` — OpenAI client factory

**Files:**
- Create: `backend/nexus/app/ai/client.py`

**Note:** If Task 3 found that `langfuse.openai` lives at a different path, use that path instead of the one below.

- [ ] **Step 1: Implement the client factory**

Create `backend/nexus/app/ai/client.py`:

```python
"""OpenAI client factory wrapped with instructor (structured output) and
langfuse (LLM observability).

Business logic imports get_openai_client() — never openai or langfuse.openai
directly. This is the single swap point for a future provider change.

Langfuse behavior:
  - When LANGFUSE_HOST is set and keys are configured, every call is traced.
  - When LANGFUSE_HOST is empty, the wrapper degrades to a transparent
    passthrough — no network calls, no state, no errors.

Instructor behavior:
  - mode=TOOLS_STRICT uses OpenAI function-calling with strict schema
    enforcement. If the model returns a malformed payload, instructor
    retries up to max_schema_retries times before raising.
"""

from functools import lru_cache

import instructor
from langfuse.openai import AsyncOpenAI

from app.ai.config import ai_config
from app.config import settings


@lru_cache(maxsize=1)
def get_openai_client() -> instructor.AsyncInstructor:
    """Return a memoized async OpenAI client wrapped with instructor.

    Memoization is safe because the client is stateless across calls and
    the underlying httpx pool is managed by openai SDK internals."""
    raw = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
    )
    return instructor.from_openai(
        raw,
        mode=instructor.Mode.TOOLS_STRICT,
        max_retries=ai_config.max_schema_retries,
    )
```

- [ ] **Step 2: Smoke test the factory (no network call)**

```bash
docker compose run --rm nexus python -c "
from app.ai.client import get_openai_client
c = get_openai_client()
print('client type:', type(c).__name__)
"
```

Expected: prints a class name starting with `AsyncInstructor`.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/ai/client.py
git commit -m "feat(ai): get_openai_client() — instructor + langfuse.openai factory"
```

---

## Task 18: Create `app/modules/jd/errors.py` — exceptions + sanitizer

**Files:**
- Create: `backend/nexus/app/modules/jd/errors.py`
- Create: `backend/nexus/tests/test_jd_errors.py`

**Note:** If Task 5 found a different name for the instructor retry-exhausted exception, update the `_SAFE_MESSAGES` key below accordingly.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_errors.py`:

```python
"""Tests for JD exceptions and status_error sanitization."""

import uuid
import pytest
import openai

from app.modules.jd.errors import (
    CompanyProfileIncompleteError,
    IllegalTransitionError,
    sanitize_error_for_user,
)


def test_illegal_transition_error_fields():
    exc = IllegalTransitionError("draft", "signals_extracted")
    assert exc.from_state == "draft"
    assert exc.to_state == "signals_extracted"
    assert "draft" in str(exc)
    assert "signals_extracted" in str(exc)


def test_company_profile_incomplete_carries_org_unit_id():
    unit_id = uuid.uuid4()
    exc = CompanyProfileIncompleteError(unit_id)
    assert exc.org_unit_id == unit_id


def test_sanitize_openai_rate_limit():
    # Build a minimal mock rate-limit error (openai.RateLimitError takes a response)
    class FakeResponse:
        request = None
        status_code = 429
        headers = {}
    exc = openai.RateLimitError("rate limit hit with sensitive key sk-abc", response=FakeResponse(), body=None)
    msg = sanitize_error_for_user(exc)
    assert "rate-limiting" in msg
    assert "sk-abc" not in msg
    assert "sensitive" not in msg


def test_sanitize_unknown_exception_returns_default():
    class WeirdError(Exception):
        pass
    msg = sanitize_error_for_user(WeirdError("internal path /app/secrets/key.pem"))
    assert "please retry" in msg.lower()
    assert "/app/secrets" not in msg
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_jd_errors.py -v
```

Expected: all fail — ImportError.

- [ ] **Step 3: Create the module**

Create `backend/nexus/app/modules/jd/errors.py`:

```python
"""JD module exceptions + user-facing error sanitization.

Three responsibilities co-located in one file:
  1. IllegalTransitionError — raised by state_machine.transition(),
     mapped to HTTP 409 Conflict at the router layer.
  2. CompanyProfileIncompleteError — raised by create_job_posting() when
     no ancestor has a completed company profile. Mapped to HTTP 422 at
     the router layer, with org_unit_id in the body so the frontend can
     deep-link to the Company Profile tab.
  3. sanitize_error_for_user() — maps third-party exception TYPES to
     fixed safe user-facing strings. The raw str(exc) from an OpenAI or
     instructor failure may leak API URLs, keys, request IDs, file paths,
     or prompt payloads — none of which should reach job_posting.status_error
     or the frontend.

Rich exception detail is still captured in structlog / Sentry — we only
sanitize what reaches the DB and the frontend."""

from typing import Final
from uuid import UUID

import openai

# Day-1 Task 5 verification: instructor 1.12.0 deprecates instructor.exceptions
# in favor of instructor.core. Both expose the same class object, but the new
# path avoids a startup DeprecationWarning that would pollute production logs.
from instructor.core import InstructorRetryException


# --- Exception classes ----------------------------------------------------

class IllegalTransitionError(Exception):
    """Raised when code attempts an illegal job_posting.status transition.
    Mapped to HTTP 409 Conflict at the router layer with a state-specific
    message (see app/main.py exception handler)."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Illegal transition: {from_state} → {to_state}")


class CompanyProfileIncompleteError(Exception):
    """Raised by create_job_posting() when no ancestor of the target org unit
    has a completed company_profile. Mapped to HTTP 422 Unprocessable Entity
    at the router layer, with org_unit_id in the body."""

    def __init__(self, org_unit_id: UUID) -> None:
        self.org_unit_id = org_unit_id
        super().__init__(
            f"Org unit {org_unit_id} has no ancestor with a completed company profile"
        )


# --- Error sanitization --------------------------------------------------

# Day-1 Task 5 verified: instructor 1.12.0 raises InstructorRetryException
# from instructor.core when max_retries is exceeded. The legacy
# instructor.exceptions path still works but emits a DeprecationWarning.
# Use the canonical core path.

_SAFE_MESSAGES: Final[dict[type[Exception], str]] = {
    openai.RateLimitError:
        "Our AI provider is rate-limiting us. Please retry in a minute.",
    openai.APITimeoutError:
        "The AI provider timed out. Please retry.",
    openai.APIConnectionError:
        "Could not reach the AI provider. Please retry.",
    openai.AuthenticationError:
        "AI provider authentication failed. Contact support.",
    openai.BadRequestError:
        "The job description could not be processed. Please check the input and retry.",
    InstructorRetryException:
        "The AI response did not match the expected format after retries. Please retry.",
}

_DEFAULT_MESSAGE: Final[str] = (
    "Extraction failed — please retry. Contact support if this persists."
)


def sanitize_error_for_user(exc: Exception) -> str:
    """Return a safe user-facing message for the given exception.

    NEVER returns str(exc) or any fragment of the exception's args —
    only fixed strings from _SAFE_MESSAGES or the default."""
    for exc_type, message in _SAFE_MESSAGES.items():
        if isinstance(exc, exc_type):
            return message
    return _DEFAULT_MESSAGE
```

Also create `backend/nexus/app/modules/jd/__init__.py` if it doesn't already exist with content. (The Phase 1 stub already has an empty init — leave it.)

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_errors.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/errors.py backend/nexus/tests/test_jd_errors.py
git commit -m "feat(jd): exceptions + sanitize_error_for_user for safe status_error strings"
```

---

## Task 19: Create `app/modules/jd/state_machine.py`

**Files:**
- Create: `backend/nexus/app/modules/jd/state_machine.py`
- Create: `backend/nexus/tests/test_jd_state_machine.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_state_machine.py`:

```python
"""Tests for the JD state machine — legal and illegal transitions."""

import pytest

from app.modules.jd.errors import IllegalTransitionError
from app.modules.jd.state_machine import LEGAL_TRANSITIONS, is_legal_transition


def test_draft_to_signals_extracting_legal():
    assert is_legal_transition("draft", "signals_extracting")


def test_signals_extracting_to_extracted_legal():
    assert is_legal_transition("signals_extracting", "signals_extracted")


def test_signals_extracting_to_failed_legal():
    assert is_legal_transition("signals_extracting", "signals_extraction_failed")


def test_failed_to_extracting_retry_legal():
    assert is_legal_transition("signals_extraction_failed", "signals_extracting")


def test_draft_to_extracted_illegal():
    assert not is_legal_transition("draft", "signals_extracted")


def test_extracted_to_extracting_illegal():
    """Retrying a successfully extracted job is not allowed."""
    assert not is_legal_transition("signals_extracted", "signals_extracting")


def test_extracted_is_terminal_in_2a():
    assert LEGAL_TRANSITIONS["signals_extracted"] == set()


def test_unknown_from_state_is_illegal():
    assert not is_legal_transition("made_up_state", "signals_extracting")
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_jd_state_machine.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the state machine**

Create `backend/nexus/app/modules/jd/state_machine.py`:

```python
"""Single source of truth for job_posting.status transitions.

Every code path that mutates job_posting.status MUST go through
transition() in this module — including the Dramatiq actor.

LEGAL_TRANSITIONS is the canonical set. New states (2B's signals_confirmed,
2C's template_draft etc.) are added here and the corresponding 409 message
mapping is added in app/main.py's exception handler."""

from typing import Final
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jd.errors import IllegalTransitionError

logger = structlog.get_logger()


LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},  # retry
    "signals_extracted": set(),                            # terminal in 2A
    # Future states added here as phases land:
    # "signals_confirmed", "template_generating", ...
}


def is_legal_transition(from_state: str, to_state: str) -> bool:
    """Pure function — no DB access. Useful for unit tests and dispatch logic."""
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


async def transition(
    db: AsyncSession,
    job,  # JobPosting — typed via Protocol below if needed later
    *,
    to_state: str,
    actor_id: UUID | None,
    correlation_id: str,
) -> None:
    """Atomically update job.status and write an audit_log row.

    Caller is responsible for db.commit() / rollback. This function only
    flushes the model change — the outer transaction decides whether it
    persists.

    Raises:
        IllegalTransitionError: if the transition is not in LEGAL_TRANSITIONS.
    """
    from_state = job.status
    if not is_legal_transition(from_state, to_state):
        raise IllegalTransitionError(from_state, to_state)

    job.status = to_state

    # Audit log: lazy import to avoid cycles
    from app.modules.audit.service import write_audit_log

    await write_audit_log(
        db,
        action="job_posting.status_changed",
        resource="job_posting",
        resource_id=job.id,
        actor_id=actor_id,
        payload={
            "from": from_state,
            "to": to_state,
            "correlation_id": correlation_id,
        },
    )

    logger.info(
        "jd.state_machine.transition",
        job_posting_id=str(job.id),
        from_state=from_state,
        to_state=to_state,
        correlation_id=correlation_id,
    )
```

**IMPORTANT**: If `app.modules.audit.service.write_audit_log` does not exist with this signature, read `backend/nexus/app/modules/audit/service.py` and either (a) adapt the call to match the existing signature OR (b) add a new helper function there. Do NOT just make up the signature — the existing audit module is Phase 1 code with a specific shape.

- [ ] **Step 4: Inspect the existing audit module and adapt the call**

```bash
docker compose run --rm nexus python -c "
from app.modules.audit import service
import inspect
print(inspect.getsource(service))
" | head -60
```

Read the existing functions and update the `transition()` call to match. If there's no async `write_audit_log` with that shape, use whichever existing function Phase 1 provides and pass equivalent arguments. Commit the adaptation as part of the same task.

- [ ] **Step 5: Run tests — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_state_machine.py -v
```

Expected: all 8 tests pass. (These test pure `is_legal_transition()`; async `transition()` is integration-tested in Task 25.)

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/jd/state_machine.py backend/nexus/tests/test_jd_state_machine.py
git commit -m "feat(jd): state machine with transition() helper and audit trail"
```

---

## Task 20: Create `app/modules/jd/authz.py` — `require_job_access()`

**Files:**
- Create: `backend/nexus/app/modules/jd/authz.py`
- Create: `backend/nexus/tests/test_jd_authz.py`

**Depends on Task 1 findings:** if `has_permission_in_unit()` does NOT inherit from ancestors, this helper's local ancestry walk is the primary enforcement. If it does inherit, the walk is belt-and-braces but still correct.

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/test_jd_authz.py`:

```python
"""Tests for require_job_access() — org unit ancestry permission check."""

import uuid
import pytest
from fastapi import HTTPException

from app.models import JobPosting
from app.modules.auth.context import RoleAssignment, UserContext
from app.modules.jd.authz import require_job_access
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


def _make_ctx(user, tenant, assignments, is_super=False):
    return UserContext(
        user_id=user.id,
        tenant_id=tenant.id,
        email=user.email,
        is_super_admin=is_super,
        is_projectx_admin=False,
        assignments=assignments,
    )


@pytest.mark.asyncio
async def test_super_admin_bypasses_ancestry_check(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=unit.id,
        title="T", description_raw="R",
        created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    ctx = _make_ctx(user, tenant, assignments=[], is_super=True)
    # Should NOT raise
    result = await require_job_access(db, job.id, ctx, "view")
    assert result.id == job.id


@pytest.mark.asyncio
async def test_grant_on_parent_allows_access_to_child_unit_job(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    parent = await create_test_org_unit(db, tenant.id, name="Parent", unit_type="division")
    child = await create_test_org_unit(db, tenant.id, name="Child", unit_type="team", parent_unit_id=parent.id)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=child.id,
        title="T", description_raw="R",
        created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    ctx = _make_ctx(
        user, tenant,
        assignments=[
            RoleAssignment(
                org_unit_id=parent.id,
                org_unit_name="Parent",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["jobs.view"],
            ),
        ],
    )
    result = await require_job_access(db, job.id, ctx, "view")
    assert result.id == job.id


@pytest.mark.asyncio
async def test_grant_on_sibling_unit_does_not_allow_access(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit_a = await create_test_org_unit(db, tenant.id, name="A")
    unit_b = await create_test_org_unit(db, tenant.id, name="B")
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=unit_a.id,
        title="T", description_raw="R",
        created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    ctx = _make_ctx(
        user, tenant,
        assignments=[
            RoleAssignment(
                org_unit_id=unit_b.id,
                org_unit_name="B",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["jobs.view"],
            ),
        ],
    )
    with pytest.raises(HTTPException) as exc_info:
        await require_job_access(db, job.id, ctx, "view")
    assert exc_info.value.status_code == 403
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_jd_authz.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement authz.py**

Create `backend/nexus/app/modules/jd/authz.py`:

```python
"""Authorization helpers for the JD module.

require_job_access() loads the job, walks the org unit ancestry from the
job's unit up to the root, and checks whether the user holds the required
permission on any ancestor. If Task 1 (Day-1 verification) found that
UserContext.has_permission_in_unit() already inherits from ancestors, the
walk is defensive (still correct). If it doesn't inherit, the walk is the
PRIMARY enforcement."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobPosting, OrganizationalUnit
from app.modules.auth.context import UserContext


async def _get_org_unit_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> list[OrganizationalUnit]:
    """Walk parent_unit_id chain from the given unit up to the root.
    Returns units in order: [starting_unit, parent, grandparent, ..., root]."""
    chain: list[OrganizationalUnit] = []
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            break  # defensive: avoid infinite loop on corrupted data
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            break
        chain.append(unit)
        current_id = unit.parent_unit_id
    return chain


async def require_job_access(
    db: AsyncSession,
    job_id: UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> JobPosting:
    """Load the job and enforce ancestry-based RBAC.

    Raises HTTPException(404) if the job doesn't exist in the current tenant
    (RLS scope). Raises HTTPException(403) if the user lacks the required
    permission in any ancestor of the job's org unit. Returns the loaded
    JobPosting row on success so callers don't re-fetch."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Super admin short-circuit — matches Phase 1 pattern
    if user.is_super_admin:
        return job

    permission = f"jobs.{action}"
    ancestry = await _get_org_unit_ancestry(db, job.org_unit_id)
    for unit in ancestry:
        if user.has_permission_in_unit(unit.id, permission):
            return job

    raise HTTPException(
        status_code=403,
        detail=f"Missing {permission} in job's org unit ancestry",
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_authz.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/authz.py backend/nexus/tests/test_jd_authz.py
git commit -m "feat(jd): require_job_access() with local ancestry walk"
```

---

## Task 21: Strict Company Profile schema + ancestry helper

**Files:**
- Create: `backend/nexus/app/modules/org_units/company_profile.py`
- Create: `backend/nexus/tests/test_company_profile_schema.py`
- Create: `backend/nexus/tests/fixtures/company_profile_enums.json`

- [ ] **Step 1: Create the enum parity fixture**

Create `backend/nexus/tests/fixtures/company_profile_enums.json`:

```json
{
  "industry": [
    "fintech_financial_services",
    "healthcare_medtech",
    "ecommerce_retail",
    "ai_ml_products",
    "saas_enterprise_software",
    "developer_tools_infrastructure",
    "agency_consulting_staffing",
    "media_content",
    "logistics_supply_chain",
    "other"
  ],
  "company_stage": [
    "pre_seed_seed",
    "series_a_b",
    "series_c_plus",
    "large_enterprise"
  ]
}
```

- [ ] **Step 2: Write the failing tests**

Create `backend/nexus/tests/test_company_profile_schema.py`:

```python
"""Tests for the strict Company Profile Pydantic schema and enum parity
with the frontend Zod enum. The fixture file is the single source of truth
for both sides."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.org_units.company_profile import (
    CompanyProfile,
    COMPANY_STAGE_VALUES,
    INDUSTRY_VALUES,
)

FIXTURE = Path(__file__).parent / "fixtures" / "company_profile_enums.json"


def test_enum_parity_with_frontend_fixture():
    """The Python enum values must match the fixture exactly. The frontend
    Zod schema reads the same fixture (via a build-time check in 2B+ or
    manual sync in 2A)."""
    with FIXTURE.open() as f:
        expected = json.load(f)
    assert list(INDUSTRY_VALUES) == expected["industry"]
    assert list(COMPANY_STAGE_VALUES) == expected["company_stage"]


def test_valid_profile():
    profile = CompanyProfile(
        about="We build real-time risk scoring infrastructure for mid-market lenders.",
        industry="fintech_financial_services",
        company_stage="series_a_b",
        hiring_bar="Engineers who own problems end-to-end.",
    )
    assert profile.industry == "fintech_financial_services"


def test_about_too_short():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="Too short",
            industry="fintech_financial_services",
            company_stage="series_a_b",
            hiring_bar="Strong engineers who own problems end-to-end.",
        )


def test_about_too_long():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="A" * 501,
            industry="fintech_financial_services",
            company_stage="series_a_b",
            hiring_bar="Strong engineers who own problems end-to-end.",
        )


def test_hiring_bar_too_long():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="A real description of what this fintech company builds and for whom.",
            industry="fintech_financial_services",
            company_stage="series_a_b",
            hiring_bar="H" * 281,
        )


def test_invalid_industry():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="A real description of what this fintech company builds and for whom.",
            industry="not_a_valid_industry",
            company_stage="series_a_b",
            hiring_bar="Strong engineers who own problems end-to-end.",
        )
```

- [ ] **Step 3: Run — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_company_profile_schema.py -v
```

Expected: all fail.

- [ ] **Step 4: Implement the schema**

Create `backend/nexus/app/modules/org_units/company_profile.py`:

```python
"""Strict Company Profile schema and helpers.

The 4-field Phase 2A shape is the single source of truth for both the
JSONB validation on org units (when unit_type is company/client_account)
and the input the Call 1 prompt receives from find_company_profile_in_ancestry().

Enum values are duplicated in the frontend Zod schema
(frontend/app/components/dashboard/company-profile-form.tsx). A test in
tests/test_company_profile_schema.py enforces parity with
tests/fixtures/company_profile_enums.json — the fixture is the canonical
definition; both sides import from it (backend via this module, frontend
via a constant that MUST match)."""

from typing import Final, Literal

from pydantic import BaseModel, Field


INDUSTRY_VALUES: Final[tuple[str, ...]] = (
    "fintech_financial_services",
    "healthcare_medtech",
    "ecommerce_retail",
    "ai_ml_products",
    "saas_enterprise_software",
    "developer_tools_infrastructure",
    "agency_consulting_staffing",
    "media_content",
    "logistics_supply_chain",
    "other",
)

COMPANY_STAGE_VALUES: Final[tuple[str, ...]] = (
    "pre_seed_seed",
    "series_a_b",
    "series_c_plus",
    "large_enterprise",
)

IndustryEnum = Literal[
    "fintech_financial_services",
    "healthcare_medtech",
    "ecommerce_retail",
    "ai_ml_products",
    "saas_enterprise_software",
    "developer_tools_infrastructure",
    "agency_consulting_staffing",
    "media_content",
    "logistics_supply_chain",
    "other",
]

CompanyStageEnum = Literal[
    "pre_seed_seed",
    "series_a_b",
    "series_c_plus",
    "large_enterprise",
]


class CompanyProfile(BaseModel):
    about: str = Field(
        min_length=30,
        max_length=500,
        description="Operational description of what the company builds. Not a mission statement.",
    )
    industry: IndustryEnum
    company_stage: CompanyStageEnum
    hiring_bar: str = Field(
        min_length=20,
        max_length=280,
        description="What a strong hire looks like at this company.",
    )
```

- [ ] **Step 5: Run — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_company_profile_schema.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/org_units/company_profile.py backend/nexus/tests/test_company_profile_schema.py backend/nexus/tests/fixtures/company_profile_enums.json
git commit -m "feat(org_units): strict CompanyProfile schema + enum parity fixture"
```

---

## Task 22: Add `find_company_profile_in_ancestry()` and profile completion tracking

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`
- Modify: `backend/nexus/app/modules/org_units/schemas.py`
- Create: `backend/nexus/tests/test_find_company_profile_in_ancestry.py`

- [ ] **Step 1: Read the existing org_units service to understand conventions**

Read `backend/nexus/app/modules/org_units/service.py` — note the existing `create_org_unit` and `update_org_unit` functions and how they handle `company_profile`.

- [ ] **Step 2: Write the failing test**

Create `backend/nexus/tests/test_find_company_profile_in_ancestry.py`:

```python
"""Tests for find_company_profile_in_ancestry() — walks up parent_unit_id
looking for the first ancestor with a completed company_profile."""

import pytest

from app.modules.org_units.service import find_company_profile_in_ancestry
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


_VALID_PROFILE = {
    "about": "We build real-time risk scoring infrastructure for mid-market lenders.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


@pytest.mark.asyncio
async def test_returns_profile_from_direct_unit(db):
    tenant = await create_test_client(db)
    await db.flush()
    unit = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    await db.flush()

    result = await find_company_profile_in_ancestry(db, unit.id)
    assert result == _VALID_PROFILE


@pytest.mark.asyncio
async def test_returns_profile_from_ancestor(db):
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE, name="Acme",
    )
    division = await create_test_org_unit(
        db, tenant.id, unit_type="division", parent_unit_id=company.id, name="Eng",
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=division.id, name="Platform",
    )
    await db.flush()

    result = await find_company_profile_in_ancestry(db, team.id)
    assert result == _VALID_PROFILE


@pytest.mark.asyncio
async def test_returns_none_when_no_ancestor_has_profile(db):
    tenant = await create_test_client(db)
    await db.flush()
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    await db.flush()

    result = await find_company_profile_in_ancestry(db, division.id)
    assert result is None
```

- [ ] **Step 3: Run — expect FAIL (function doesn't exist)**

```bash
docker compose run --rm nexus pytest tests/test_find_company_profile_in_ancestry.py -v
```

Expected: ImportError.

- [ ] **Step 4: Add the helper to org_units/service.py**

Append to `backend/nexus/app/modules/org_units/service.py`:

```python
async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Walk parent_unit_id chain from the given unit up to root.
    Return the first company_profile dict encountered. None if no ancestor
    has one.

    Used by create_job_posting() to decide whether a JD can be created
    under a given org unit and by _build_user_message() to pass company
    context into Call 1."""
    from app.models import OrganizationalUnit
    from sqlalchemy import select

    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            return None  # defensive: corrupted data loop
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            return None
        if unit.company_profile:
            return unit.company_profile
        current_id = unit.parent_unit_id
    return None
```

If the module doesn't already import `UUID`, add `from uuid import UUID` at the top.

- [ ] **Step 5: Run — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_find_company_profile_in_ancestry.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 6: Wire company_profile validation + completion tracking into create/update**

Find the existing `create_org_unit` and `update_org_unit` functions in `backend/nexus/app/modules/org_units/service.py`. Where they currently accept `company_profile: dict | None` and do rudimentary validation, add a strict validation pass using `CompanyProfile`:

```python
from app.modules.org_units.company_profile import CompanyProfile
from pydantic import ValidationError
from datetime import UTC, datetime

def _validate_and_normalize_company_profile(profile: dict | None) -> dict | None:
    """Strict validation of the 4-field Phase 2A company profile shape.
    Returns the validated dict (Pydantic round-trip) or raises ValueError
    with a user-facing message."""
    if profile is None:
        return None
    try:
        return CompanyProfile(**profile).model_dump()
    except ValidationError as e:
        raise ValueError(
            "Company profile validation failed: "
            + "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
        )
```

Then in `create_org_unit`, replace the existing "Rule 3: company_profile required for company and client_account" block with:

```python
    # Rule 3: company_profile required for company and client_account,
    # and must match the Phase 2A strict schema when provided.
    if unit_type in ("company", "client_account"):
        if not company_profile:
            raise ValueError(
                f"A company_profile is required for units of type '{unit_type}'."
            )
    company_profile = _validate_and_normalize_company_profile(company_profile)
```

And after the `await db.flush()` for the unit insert, add completion tracking:

```python
    if company_profile is not None:
        unit.company_profile_completed_at = datetime.now(UTC)
        unit.company_profile_completed_by = created_by_user_id  # whatever the local param name is
```

(Adjust `created_by_user_id` to whatever the existing function signature calls the user param.)

In `update_org_unit`, when `set_company_profile=True`, run the same validation and also stamp `company_profile_completed_at` / `company_profile_completed_by = actor_user_id` on successful change.

- [ ] **Step 7: Run the full org_units test suite to catch regressions**

```bash
docker compose run --rm nexus pytest tests/test_org_units.py tests/test_org_unit_types.py tests/test_find_company_profile_in_ancestry.py -v
```

Expected: all pass. Phase 1 tests may need adjustment if they used old-shape profile dicts — update them to use the new 4-field shape. Show the failing test names and fix in-place.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/org_units/service.py backend/nexus/app/modules/org_units/schemas.py backend/nexus/tests/test_find_company_profile_in_ancestry.py backend/nexus/tests/test_org_units.py backend/nexus/tests/test_org_unit_types.py
git commit -m "feat(org_units): strict company profile validation + completed_at/by tracking + ancestry helper"
```

---

## Task 23: JD Pydantic schemas (request/response)

**Files:**
- Rewrite: `backend/nexus/app/modules/jd/schemas.py`

- [ ] **Step 1: Replace the stub schemas**

Overwrite `backend/nexus/app/modules/jd/schemas.py` with:

```python
"""Pydantic request / response schemas for the JD module.

Shape is the HTTP surface; the internal ORM models live in app/models.py.
Conversions between them live in service.py."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal[
    "draft",
    "signals_extracting",
    "signals_extraction_failed",
    "signals_extracted",
]


class SignalItemResponse(BaseModel):
    value: str
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None


class SignalSnapshotResponse(BaseModel):
    version: int
    required_skills: list[SignalItemResponse]
    preferred_skills: list[SignalItemResponse]
    must_haves: list[SignalItemResponse]
    good_to_haves: list[SignalItemResponse]
    min_experience_years: int
    seniority_level: str
    role_summary: str


class JobPostingCreate(BaseModel):
    """POST /api/jobs request body."""
    model_config = ConfigDict(extra="forbid")

    org_unit_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description_raw: str = Field(min_length=50, max_length=50_000)
    project_scope_raw: str | None = Field(default=None, max_length=20_000)
    target_headcount: int | None = Field(default=None, ge=1, le=10_000)
    deadline: date | None = None


class JobPostingSummary(BaseModel):
    """Row shape for GET /api/jobs (list view)."""
    id: UUID
    title: str
    org_unit_id: UUID
    status: JobStatus
    status_error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobPostingWithSnapshot(BaseModel):
    """Row shape for GET /api/jobs/{id} — full payload with latest snapshot."""
    id: UUID
    title: str
    org_unit_id: UUID
    description_raw: str
    project_scope_raw: str | None = None
    description_enriched: str | None = None
    status: JobStatus
    status_error: str | None = None
    target_headcount: int | None = None
    deadline: date | None = None
    created_at: datetime
    updated_at: datetime
    latest_snapshot: SignalSnapshotResponse | None = None


class JobStatusEvent(BaseModel):
    """SSE event payload shape (serialized to JSON in the event data field)."""
    job_id: UUID
    status: JobStatus
    error: str | None = None
    signal_snapshot_version: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"signals_extracted", "signals_extraction_failed"}
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
docker compose run --rm nexus python -c "
from app.modules.jd.schemas import JobPostingCreate, JobPostingWithSnapshot, JobStatusEvent
print('JD schemas loaded OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/jd/schemas.py
git commit -m "feat(jd): request/response Pydantic schemas"
```

---

## Task 24: JD service — `create_job_posting()` + helpers

**Files:**
- Rewrite: `backend/nexus/app/modules/jd/service.py`
- Create: `backend/nexus/tests/test_jd_service_create.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_service_create.py`:

```python
"""Tests for JD service create_job_posting — happy path + profile-gate failure."""

import pytest

from app.modules.jd.errors import CompanyProfileIncompleteError
from app.modules.jd.service import create_job_posting
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


@pytest.mark.asyncio
async def test_create_job_posting_happy_path(db, monkeypatch):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=company.id,
    )
    await db.flush()

    # Stub Dramatiq dispatch so the service doesn't actually enqueue
    dispatched = []
    def fake_send(*args, **kwargs):
        dispatched.append((args, kwargs))
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        fake_send,
    )

    job = await create_job_posting(
        db,
        tenant_id=tenant.id,
        created_by=user.id,
        org_unit_id=team.id,
        title="Sr. Integration Engineer",
        description_raw="A" * 200,  # meets min_length
        project_scope_raw=None,
        target_headcount=1,
        deadline=None,
        correlation_id="test-corr-1",
    )
    await db.flush()

    assert job.status == "signals_extracting"
    assert job.title == "Sr. Integration Engineer"
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_create_job_posting_blocks_without_profile(db, monkeypatch):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    # division has NO company_profile and no ancestor with one
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    await db.flush()

    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    with pytest.raises(CompanyProfileIncompleteError):
        await create_job_posting(
            db,
            tenant_id=tenant.id,
            created_by=user.id,
            org_unit_id=division.id,
            title="Test Role",
            description_raw="A" * 200,
            project_scope_raw=None,
            target_headcount=None,
            deadline=None,
            correlation_id="test-corr-2",
        )
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_jd_service_create.py -v
```

Expected: ImportError on `create_job_posting`.

- [ ] **Step 3: Implement the service**

Overwrite `backend/nexus/app/modules/jd/service.py`:

```python
"""JD module business logic.

All mutations to job_postings.status go through state_machine.transition().
The Dramatiq actor is imported lazily inside create_job_posting() to avoid
a circular import (actors.py imports service.py for the snapshot persist)."""

from datetime import date
from uuid import UUID

import structlog
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.errors import CompanyProfileIncompleteError
from app.modules.jd.schemas import JobStatusEvent
from app.modules.jd.state_machine import transition
from app.modules.org_units.service import find_company_profile_in_ancestry

logger = structlog.get_logger()


async def create_job_posting(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    created_by: UUID,
    org_unit_id: UUID,
    title: str,
    description_raw: str,
    project_scope_raw: str | None,
    target_headcount: int | None,
    deadline: date | None,
    correlation_id: str,
) -> JobPosting:
    """Atomic:
      1. Validate company_profile completeness via ancestry walk.
      2. INSERT job_postings row in 'draft'.
      3. Flush so the row has an ID.
      4. Transition draft → signals_extracting via state_machine.transition().
      5. Enqueue the Dramatiq actor (lazy import to break cycle).
    Caller is responsible for db.commit().

    Raises:
        CompanyProfileIncompleteError: no ancestor has a completed profile.
    """
    profile = await find_company_profile_in_ancestry(db, org_unit_id)
    if profile is None:
        raise CompanyProfileIncompleteError(org_unit_id)

    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title=title,
        description_raw=description_raw,
        project_scope_raw=project_scope_raw,
        target_headcount=target_headcount,
        deadline=deadline,
        status="draft",
        source="native",
        created_by=created_by,
    )
    db.add(job)
    await db.flush()

    await transition(
        db,
        job,
        to_state="signals_extracting",
        actor_id=created_by,
        correlation_id=correlation_id,
    )
    await db.flush()

    # Lazy import to avoid circular dependency (actors → service for persist)
    from app.modules.jd.actors import extract_and_enhance_jd

    extract_and_enhance_jd.send(
        job_posting_id=str(job.id),
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )

    logger.info(
        "jd.service.created",
        job_posting_id=str(job.id),
        org_unit_id=str(org_unit_id),
        correlation_id=correlation_id,
    )
    return job


async def get_job_posting_with_latest_snapshot(
    db: AsyncSession, job_id: UUID
) -> tuple[JobPosting | None, JobPostingSignalSnapshot | None]:
    """Load a job and its latest snapshot in a single call. RLS scopes
    the query to the current tenant. Returns (None, None) if not found."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None, None

    snap_result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job_id)
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    return job, snapshot


async def list_job_postings(
    db: AsyncSession,
    *,
    visible_org_unit_ids: list[UUID] | None,
    org_unit_filter: UUID | None = None,
    status_filter: str | None = None,
) -> list[JobPosting]:
    """List jobs in the current tenant (RLS) optionally constrained to a
    set of visible org unit IDs.

    IMPLEMENTATION NOTE: visible_org_unit_ids carries the pre-computed
    union of all org units where the user has jobs.view permission in
    ancestry. If None, the caller is a super admin and all tenant rows
    are returned."""
    stmt = select(JobPosting)
    if visible_org_unit_ids is not None:
        stmt = stmt.where(JobPosting.org_unit_id.in_(visible_org_unit_ids))
    if org_unit_filter is not None:
        stmt = stmt.where(JobPosting.org_unit_id == org_unit_filter)
    if status_filter is not None:
        stmt = stmt.where(JobPosting.status == status_filter)
    stmt = stmt.order_by(desc(JobPosting.created_at))

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_job_status(db: AsyncSession, job_id: UUID) -> JobStatusEvent | None:
    """Build a JobStatusEvent from the current DB state. Used by sse.py."""
    job, snapshot = await get_job_posting_with_latest_snapshot(db, job_id)
    if job is None:
        return None
    return JobStatusEvent(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        error=job.status_error,
        signal_snapshot_version=snapshot.version if snapshot else None,
    )


async def retry_failed_extraction(
    db: AsyncSession,
    *,
    job_id: UUID,
    actor_id: UUID,
    correlation_id: str,
) -> JobPosting:
    """Precondition: job.status == 'signals_extraction_failed'.
    Transitions via state_machine (which enforces the precondition) and
    re-enqueues the actor. Caller commits."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one()

    await transition(
        db,
        job,
        to_state="signals_extracting",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    job.status_error = None  # clear the previous error message
    await db.flush()

    from app.modules.jd.actors import extract_and_enhance_jd

    extract_and_enhance_jd.send(
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
    )
    return job
```

- [ ] **Step 4: Run the service tests — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_service_create.py -v
```

Expected: both tests pass. (The test monkeypatches `extract_and_enhance_jd.send` — the actor module must already be importable even though its full implementation comes in Task 26. Create a minimal stub now if needed:)

- [ ] **Step 5: If actors.py doesn't exist yet, create a minimal stub**

If Step 4 fails with `ModuleNotFoundError: app.modules.jd.actors`, create `backend/nexus/app/modules/jd/actors.py` as a stub:

```python
"""Phase 2A Dramatiq actors — extract_and_enhance_jd implementation in Task 26.

This stub exists so service.py's lazy import works during tests before Task 26 lands."""

import dramatiq


@dramatiq.actor(queue_name="jd_extraction")
async def extract_and_enhance_jd(
    job_posting_id: str, tenant_id: str, correlation_id: str
) -> None:
    """Stub — real implementation in Task 26."""
    raise NotImplementedError("extract_and_enhance_jd implementation comes in Task 26")
```

Re-run the test.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/jd/service.py backend/nexus/app/modules/jd/actors.py backend/nexus/tests/test_jd_service_create.py
git commit -m "feat(jd): create_job_posting service with profile gate + actor dispatch"
```

---

## Task 25: JD service — state-machine integration tests

**Files:**
- Create: `backend/nexus/tests/test_jd_state_transitions_integration.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_state_transitions_integration.py`:

```python
"""Integration tests for state_machine.transition() against a real DB.
Verifies that transitions write audit_log rows and respect legality."""

import uuid
import pytest

from app.models import AuditLog, JobPosting
from app.modules.jd.errors import IllegalTransitionError
from app.modules.jd.state_machine import transition
from sqlalchemy import select
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


async def _make_job(db, status="draft"):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=unit.id,
        title="T", description_raw="R",
        created_by=user.id, status=status,
    )
    db.add(job)
    await db.flush()
    return tenant, user, job


@pytest.mark.asyncio
async def test_draft_to_extracting_writes_audit_row(db):
    tenant, user, job = await _make_job(db, status="draft")

    await transition(
        db, job,
        to_state="signals_extracting",
        actor_id=user.id,
        correlation_id="corr-1",
    )
    await db.flush()

    assert job.status == "signals_extracting"

    audit = await db.execute(
        select(AuditLog).where(AuditLog.resource_id == job.id)
    )
    rows = list(audit.scalars().all())
    assert len(rows) == 1
    assert rows[0].action == "job_posting.status_changed"


@pytest.mark.asyncio
async def test_illegal_transition_raises(db):
    tenant, user, job = await _make_job(db, status="draft")
    with pytest.raises(IllegalTransitionError):
        await transition(
            db, job,
            to_state="signals_extracted",
            actor_id=user.id,
            correlation_id="corr-2",
        )


@pytest.mark.asyncio
async def test_retry_from_failed_legal(db):
    tenant, user, job = await _make_job(db, status="signals_extraction_failed")
    await transition(
        db, job,
        to_state="signals_extracting",
        actor_id=user.id,
        correlation_id="corr-retry",
    )
    await db.flush()
    assert job.status == "signals_extracting"
```

- [ ] **Step 2: Run — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_state_transitions_integration.py -v
```

Expected: all 3 pass. If the audit log row count is not 1, inspect the audit service signature — you may have called `write_audit_log` with the wrong signature in Task 19. Fix the call, not the test.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_jd_state_transitions_integration.py
git commit -m "test(jd): integration tests for state_machine transitions + audit"
```

---

## Task 26: Dramatiq actor — `extract_and_enhance_jd`

**Files:**
- Rewrite: `backend/nexus/app/modules/jd/actors.py`
- Create: `backend/nexus/tests/test_jd_actor.py`

**Day-1 Task 4 dependency:** if the verification found that `reasoning_effort` needs `extra_body` instead of a top-level kwarg, update the actor's `client.chat.completions.create(...)` call accordingly BEFORE running the test.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_actor.py`:

```python
"""Tests for extract_and_enhance_jd actor — happy path + failure transitions.

The OpenAI client is mocked. Tests exercise the full DB + state machine
integration but never hit the network."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from app.ai.schemas import ExtractedSignals, ExtractionOutput, SignalItem
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.actors import _run_extraction
from sqlalchemy import select
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


async def _make_extracting_job(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Sr Engineer",
        description_raw="A" * 200,
        status="signals_extracting",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()
    return tenant, user, job


def _fake_extraction_output() -> ExtractionOutput:
    return ExtractionOutput(
        enriched_jd="A" * 80,
        signals=ExtractedSignals(
            required_skills=[
                SignalItem(value="Python", source="ai_extracted", inference_basis=None),
            ],
            preferred_skills=[],
            must_haves=[
                SignalItem(value="5+ years backend", source="ai_extracted", inference_basis=None),
            ],
            good_to_haves=[],
            min_experience_years=5,
            seniority_level="senior",
            role_summary="A senior backend engineer at a Series A fintech. Owns end-to-end.",
        ),
    )


@pytest.mark.asyncio
async def test_actor_happy_path_persists_snapshot(db, monkeypatch):
    tenant, user, job = await _make_extracting_job(db)

    # Mock the OpenAI client call
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_fake_extraction_output())
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    await _run_extraction(
        db,
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="corr-happy",
        retries_so_far=0,
    )
    await db.flush()

    await db.refresh(job)
    assert job.status == "signals_extracted"
    assert job.description_enriched is not None
    assert len(job.description_enriched) >= 50

    snap_result = await db.execute(
        select(JobPostingSignalSnapshot).where(JobPostingSignalSnapshot.job_posting_id == job.id)
    )
    snap = snap_result.scalar_one()
    assert snap.version == 1
    assert snap.seniority_level == "senior"


@pytest.mark.asyncio
async def test_actor_final_retry_failure_sanitizes(db, monkeypatch):
    tenant, user, job = await _make_extracting_job(db)

    class FakeResponse:
        status_code = 429
        headers = {}
        request = None

    def raise_rate_limit(*a, **k):
        raise openai.RateLimitError("boom with sensitive key sk-abc", response=FakeResponse(), body=None)

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=raise_rate_limit)
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    with pytest.raises(openai.RateLimitError):
        await _run_extraction(
            db,
            job_posting_id=str(job.id),
            tenant_id=str(tenant.id),
            correlation_id="corr-fail",
            retries_so_far=2,  # final retry
        )
    await db.flush()

    await db.refresh(job)
    assert job.status == "signals_extraction_failed"
    assert job.status_error is not None
    assert "sk-abc" not in job.status_error
    assert "rate-limiting" in job.status_error


@pytest.mark.asyncio
async def test_actor_intermediate_retry_does_not_flip_state(db, monkeypatch):
    tenant, user, job = await _make_extracting_job(db)

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=openai.APITimeoutError("timeout"))
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    with pytest.raises(openai.APITimeoutError):
        await _run_extraction(
            db,
            job_posting_id=str(job.id),
            tenant_id=str(tenant.id),
            correlation_id="corr-intermediate",
            retries_so_far=0,  # not final
        )
    await db.refresh(job)
    assert job.status == "signals_extracting"  # unchanged
    assert job.status_error is None
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_jd_actor.py -v
```

Expected: fails — `_run_extraction` doesn't exist yet (or stub raises NotImplementedError).

- [ ] **Step 3: Implement the actor**

Overwrite `backend/nexus/app/modules/jd/actors.py`:

```python
"""Dramatiq actor for Call 1 (JD enhancement + signal extraction).

The public actor wraps an inner _run_extraction() coroutine that accepts
a DB session. This split makes the coroutine unit-testable without spinning
up Dramatiq's scheduler — tests pass a transactional session directly."""

from uuid import UUID

import dramatiq
import structlog
from dramatiq.middleware import CurrentMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.ai.schemas import ExtractionOutput
from app.database import get_bypass_db
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.errors import sanitize_error_for_user
from app.modules.jd.state_machine import transition
from app.modules.org_units.service import find_company_profile_in_ancestry
from sqlalchemy import select

logger = structlog.get_logger()


def _build_user_message(job: JobPosting, profile: dict) -> str:
    """Build the Call 1 user message in the mandatory ordering:
    company profile → raw JD → project scope.

    The context (profile) MUST come before the document (JD) — this primes
    the model correctly from the first token. See feedback_prompt_context_ordering.md."""
    parts: list[str] = [
        "## Company Profile\n"
        f"- About: {profile['about']}\n"
        f"- Industry: {profile['industry']}\n"
        f"- Company stage: {profile['company_stage']}\n"
        f"- Hiring bar: {profile['hiring_bar']}\n",
        f"## Raw Job Description\n\n{job.description_raw}\n",
    ]
    if job.project_scope_raw:
        parts.append(f"## Project Scope\n\n{job.project_scope_raw}\n")
    return "\n".join(parts)


async def _persist_enriched(
    db: AsyncSession, job: JobPosting, result: ExtractionOutput
) -> None:
    """Write the enriched JD onto the job row and insert a new snapshot."""
    job.description_enriched = result.enriched_jd

    snapshot = JobPostingSignalSnapshot(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        version=1,
        required_skills=[item.model_dump() for item in result.signals.required_skills],
        preferred_skills=[item.model_dump() for item in result.signals.preferred_skills],
        must_haves=[item.model_dump() for item in result.signals.must_haves],
        good_to_haves=[item.model_dump() for item in result.signals.good_to_haves],
        min_experience_years=result.signals.min_experience_years,
        seniority_level=result.signals.seniority_level,
        role_summary=result.signals.role_summary,
    )
    db.add(snapshot)


async def _run_extraction(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Core extraction logic — unit-testable without Dramatiq."""
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.actor.job_not_found")
        return

    if job.status != "signals_extracting":
        log.warn("jd.actor.skip_unexpected_state", state=job.status)
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        # This should never happen — create_job_posting validated it.
        # Defensive: mark as failed.
        job.status_error = "Company profile missing — create_job_posting should have blocked this"
        await transition(
            db, job,
            to_state="signals_extraction_failed",
            actor_id=None,
            correlation_id=correlation_id,
        )
        return

    try:
        client = get_openai_client()
        prompt = prompt_loader.get("jd_enhancement")
        extraction: ExtractionOutput = await client.chat.completions.create(
            model=ai_config.extraction_model,
            reasoning_effort=ai_config.extraction_effort,
            response_model=ExtractionOutput,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": _build_user_message(job, profile)},
            ],
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        log.error("jd.actor.call1_failed", exc_info=exc)
        if retries_so_far >= 2:
            # Final attempt — sanitize and transition to _failed
            job.status_error = sanitize_error_for_user(exc)
            await transition(
                db, job,
                to_state="signals_extraction_failed",
                actor_id=None,
                correlation_id=correlation_id,
            )
        raise  # Dramatiq retries on all non-final exceptions

    # Success path
    await _persist_enriched(db, job, extraction)
    await transition(
        db, job,
        to_state="signals_extracted",
        actor_id=None,
        correlation_id=correlation_id,
    )
    log.info("jd.actor.completed")


@dramatiq.actor(
    max_retries=3,
    min_backoff=2_000,
    max_backoff=60_000,
    queue_name="jd_extraction",
)
async def extract_and_enhance_jd(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Dramatiq entry point. Opens a bypass DB session (no HTTP request
    context), sets app.current_tenant for RLS, delegates to _run_extraction,
    commits on success."""
    current = CurrentMessage.get_current_message()
    retries_so_far = current.options.get("retries", 0) if current else 0

    async with get_bypass_db() as db:
        await db.execute(
            text("SET LOCAL app.current_tenant = :t"),
            {"t": tenant_id},
        )
        try:
            await _run_extraction(
                db,
                job_posting_id=job_posting_id,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                retries_so_far=retries_so_far,
            )
            await db.commit()
        except Exception:
            # _run_extraction already transitioned to _failed on final retry
            # and staged the changes; commit them before re-raising so the
            # user sees the failed state. Intermediate retries rollback
            # silently (unchanged state).
            if retries_so_far >= 2:
                await db.commit()
            else:
                await db.rollback()
            raise
```

- [ ] **Step 4: Run — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_actor.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/actors.py backend/nexus/tests/test_jd_actor.py
git commit -m "feat(jd): extract_and_enhance_jd Dramatiq actor with mocked unit tests"
```

---

## Task 27: SSE event generator — `app/modules/jd/sse.py`

**Files:**
- Create: `backend/nexus/app/modules/jd/sse.py`
- Create: `backend/nexus/tests/test_jd_sse.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_jd_sse.py`:

```python
"""Tests for job_status_event_generator — de-dup, terminal close, disconnect."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.jd.schemas import JobStatusEvent
from app.modules.jd.sse import job_status_event_generator


class FakeRequest:
    def __init__(self, disconnect_after: int = 999) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


@pytest.mark.asyncio
async def test_emits_initial_then_terminal(monkeypatch):
    """Initial status emits once; terminal state emits and closes."""
    events_to_yield = [
        JobStatusEvent(job_id="00000000-0000-0000-0000-000000000001", status="signals_extracting", error=None, signal_snapshot_version=None),
        JobStatusEvent(job_id="00000000-0000-0000-0000-000000000001", status="signals_extracting", error=None, signal_snapshot_version=None),  # dedup
        JobStatusEvent(job_id="00000000-0000-0000-0000-000000000001", status="signals_extracted", error=None, signal_snapshot_version=1),
    ]
    idx = {"i": 0}
    async def fake_get_job_status(db, job_id):
        i = min(idx["i"], len(events_to_yield) - 1)
        idx["i"] += 1
        return events_to_yield[i]

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.01)

    gen = job_status_event_generator(db=None, job_id="00000000-0000-0000-0000-000000000001", request=FakeRequest())
    yielded = [ev async for ev in gen]

    # Should emit: extracting (initial), extracted (terminal). Middle duplicate de-duped.
    assert len(yielded) == 2
    assert "signals_extracting" in yielded[0]["data"]
    assert "signals_extracted" in yielded[1]["data"]


@pytest.mark.asyncio
async def test_terminates_on_disconnect(monkeypatch):
    async def fake_get_job_status(db, job_id):
        return JobStatusEvent(job_id="00000000-0000-0000-0000-000000000001", status="signals_extracting", error=None, signal_snapshot_version=None)

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.01)

    gen = job_status_event_generator(db=None, job_id="00000000-0000-0000-0000-000000000001", request=FakeRequest(disconnect_after=0))
    yielded = [ev async for ev in gen]
    assert yielded == []  # disconnected before first yield
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_jd_sse.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement sse.py**

Create `backend/nexus/app/modules/jd/sse.py`:

```python
"""Server-Sent Events generator for job posting status updates.

Contract:
  - Polls the job_postings row every POLL_INTERVAL_SECONDS.
  - Emits a 'status' event ONLY when job.status changes from the last
    observed value (de-duplication).
  - Terminates and closes the HTTP connection when the job reaches a
    terminal state (signals_extracted or signals_extraction_failed).
  - Terminates immediately if the client disconnects mid-stream.
  - Does NOT enforce RBAC — the router's require_job_access() dependency
    has already validated access before this generator is invoked.
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jd.service import get_job_status

POLL_INTERVAL_SECONDS: float = 1.5
TERMINAL_STATES: frozenset[str] = frozenset({
    "signals_extracted",
    "signals_extraction_failed",
})


async def job_status_event_generator(
    db: AsyncSession,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events until terminal state or client disconnect."""
    last_status: str | None = None
    while True:
        if await request.is_disconnected():
            return

        event = await get_job_status(db, job_id)
        if event is None:
            return  # job disappeared (shouldn't happen under RLS scope)

        if event.status != last_status:
            yield {
                "event": "status",
                "data": event.model_dump_json(),
            }
            last_status = event.status

        if event.status in TERMINAL_STATES:
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

- [ ] **Step 4: Run — expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_jd_sse.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/sse.py backend/nexus/tests/test_jd_sse.py
git commit -m "feat(jd): SSE event generator with dedup and terminal close"
```

---

## Task 28: JD router — full endpoint surface

**Files:**
- Rewrite: `backend/nexus/app/modules/jd/router.py`

- [ ] **Step 1: Replace the stub router**

Overwrite `backend/nexus/app/modules/jd/router.py`:

```python
"""JD module HTTP surface.

All business logic lives in service.py; this module is request/response
orchestration only. API prefix /api/jobs matches the Phase 1 convention
(no /v1/ versioning segment)."""

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_tenant_db
from app.models import JobPostingSignalSnapshot
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.jd.authz import require_job_access
from app.modules.jd.schemas import (
    JobPostingCreate,
    JobPostingSummary,
    JobPostingWithSnapshot,
    SignalItemResponse,
    SignalSnapshotResponse,
)
from app.modules.jd.service import (
    create_job_posting,
    get_job_posting_with_latest_snapshot,
    list_job_postings,
    retry_failed_extraction,
)
from app.modules.jd.sse import job_status_event_generator

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _snapshot_to_response(snap: JobPostingSignalSnapshot | None) -> SignalSnapshotResponse | None:
    if snap is None:
        return None
    return SignalSnapshotResponse(
        version=snap.version,
        required_skills=[SignalItemResponse(**item) for item in snap.required_skills],
        preferred_skills=[SignalItemResponse(**item) for item in snap.preferred_skills],
        must_haves=[SignalItemResponse(**item) for item in snap.must_haves],
        good_to_haves=[SignalItemResponse(**item) for item in snap.good_to_haves],
        min_experience_years=snap.min_experience_years,
        seniority_level=snap.seniority_level,
        role_summary=snap.role_summary,
    )


def _job_to_summary(job) -> JobPostingSummary:
    return JobPostingSummary(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        status=job.status,
        status_error=job.status_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _job_with_snapshot_to_response(job, snap) -> JobPostingWithSnapshot:
    return JobPostingWithSnapshot(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        description_raw=job.description_raw,
        project_scope_raw=job.project_scope_raw,
        description_enriched=job.description_enriched,
        status=job.status,
        status_error=job.status_error,
        target_headcount=job.target_headcount,
        deadline=job.deadline,
        created_at=job.created_at,
        updated_at=job.updated_at,
        latest_snapshot=_snapshot_to_response(snap),
    )


def _visible_unit_ids(user: UserContext, permission: str) -> list[UUID] | None:
    """Return the flat list of org unit IDs where the user holds `permission`,
    or None if the user is a super admin (no filter needed)."""
    if user.is_super_admin:
        return None
    return [a.org_unit_id for a in user.assignments if permission in a.permissions]


@router.post("", status_code=201, response_model=JobPostingWithSnapshot)
async def create_job(
    body: JobPostingCreate,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    # jobs.create is checked at the service layer via find_company_profile gate;
    # also enforce it here at the endpoint boundary.
    if not user.is_super_admin:
        # User must hold jobs.create on the target org unit OR any ancestor
        from app.modules.jd.authz import _get_org_unit_ancestry
        ancestry = await _get_org_unit_ancestry(db, body.org_unit_id)
        if not any(user.has_permission_in_unit(u.id, "jobs.create") for u in ancestry):
            raise HTTPException(status_code=403, detail="Missing jobs.create in ancestry")

    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    job = await create_job_posting(
        db,
        tenant_id=user.tenant_id,
        created_by=user.user_id,
        org_unit_id=body.org_unit_id,
        title=body.title,
        description_raw=body.description_raw,
        project_scope_raw=body.project_scope_raw,
        target_headcount=body.target_headcount,
        deadline=body.deadline,
        correlation_id=correlation_id,
    )
    await db.commit()
    job, snap = await get_job_posting_with_latest_snapshot(db, job.id)
    return _job_with_snapshot_to_response(job, snap)


@router.get("", response_model=list[JobPostingSummary])
async def list_jobs(
    org_unit_id: UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[JobPostingSummary]:
    visible = _visible_unit_ids(user, "jobs.view")
    jobs = await list_job_postings(
        db,
        visible_org_unit_ids=visible,
        org_unit_filter=org_unit_id,
        status_filter=status,
    )
    return [_job_to_summary(j) for j in jobs]


@router.get("/{job_id}", response_model=JobPostingWithSnapshot)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    await require_job_access(db, job_id, user, "view")
    job, snap = await get_job_posting_with_latest_snapshot(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_with_snapshot_to_response(job, snap)


@router.get("/{job_id}/status/stream")
async def stream_status(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> EventSourceResponse:
    await require_job_access(db, job_id, user, "view")
    return EventSourceResponse(
        job_status_event_generator(db, job_id, request)
    )


@router.post("/{job_id}/retry", status_code=202, response_model=JobPostingSummary)
async def retry_extraction(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingSummary:
    await require_job_access(db, job_id, user, "manage")
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    job = await retry_failed_extraction(
        db,
        job_id=job_id,
        actor_id=user.user_id,
        correlation_id=correlation_id,
    )
    await db.commit()
    return _job_to_summary(job)
```

- [ ] **Step 2: Verify the router imports in main.py still work**

```bash
docker compose run --rm nexus python -c "
from app.modules.jd.router import router
print('router prefix:', router.prefix)
print('routes:', [r.path for r in router.routes])
"
```

Expected: prints 5 routes (`POST /`, `GET /`, `GET /{job_id}`, `GET /{job_id}/status/stream`, `POST /{job_id}/retry`).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py
git commit -m "feat(jd): full router surface — create, list, get, stream, retry"
```

---

## Task 29: Register JD exception handlers in main.py

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Add the exception handlers**

In `backend/nexus/app/main.py`, inside `create_app()` after the router registration block, add:

```python
    # --- Exception handlers (Phase 2A — JD module) ---
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from app.modules.jd.errors import (
        CompanyProfileIncompleteError,
        IllegalTransitionError,
    )

    _ILLEGAL_TRANSITION_MESSAGES: dict[tuple[str, str], str] = {
        ("signals_extracting", "signals_extracting"):
            "Job is already being processed",
        ("signals_extracted", "signals_extracting"):
            "This job has already been extracted successfully — "
            "retry is only valid after an extraction failure",
        ("draft", "signals_extracted"):
            "Job cannot transition directly from draft to extracted",
    }

    @application.exception_handler(IllegalTransitionError)
    async def illegal_transition_handler(
        request: Request, exc: IllegalTransitionError
    ) -> JSONResponse:
        key = (exc.from_state, exc.to_state)
        detail = _ILLEGAL_TRANSITION_MESSAGES.get(
            key,
            f"Cannot transition job from {exc.from_state} to {exc.to_state}",
        )
        return JSONResponse(status_code=409, content={"detail": detail})

    @application.exception_handler(CompanyProfileIncompleteError)
    async def company_profile_incomplete_handler(
        request: Request, exc: CompanyProfileIncompleteError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Company profile must be completed before creating a job description. "
                    "Visit Settings → Org Units → [your company] → Company Profile to finish setup."
                ),
                "org_unit_id": str(exc.org_unit_id),
            },
        )
```

- [ ] **Step 2: Smoke test**

```bash
docker compose run --rm nexus python -c "
from app.main import app
print('app ok, routes:', len(app.routes))
"
```

Expected: prints the total route count, no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/main.py
git commit -m "feat(main): register IllegalTransitionError (409) and CompanyProfileIncompleteError (422) handlers"
```

---

## Task 30: Router integration tests

**Files:**
- Create: `backend/nexus/tests/test_jd_router.py`

- [ ] **Step 1: Write the integration tests**

Create `backend/nexus/tests/test_jd_router.py`:

```python
"""End-to-end router tests for the JD module.

Uses the existing async httpx client fixture from conftest. Creates a
tenant + super admin user, authenticates the request via a JWT issued
in the test (following the pattern in test_auth_endpoints.py and
test_settings.py), and exercises the five endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from app.ai.schemas import ExtractedSignals, ExtractionOutput, SignalItem
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


def _fake_extraction() -> ExtractionOutput:
    return ExtractionOutput(
        enriched_jd="A" * 80,
        signals=ExtractedSignals(
            required_skills=[
                SignalItem(value="Python", source="ai_extracted", inference_basis=None),
            ],
            preferred_skills=[],
            must_haves=[],
            good_to_haves=[],
            min_experience_years=3,
            seniority_level="mid",
            role_summary="A mid-level Python engineer at a Series A fintech.",
        ),
    )


# The actual auth/JWT fixture pattern lives in tests/test_auth_endpoints.py —
# replicate it here rather than duplicating. If that file has a helper like
# `_auth_headers(user, tenant)` or `_issue_token(...)`, import and reuse it.
# If not, inline the minimal JWT issuance following the pattern in
# tests/test_settings.py.


@pytest.mark.asyncio
async def test_create_job_happy_path(db, client, monkeypatch):
    """Super admin creates a job; response returns 201 with status
    signals_extracting and no snapshot yet."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.commit()

    # Stub the actor dispatch so no real Dramatiq happens
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    headers = {"Authorization": "Bearer test-super-admin-token"}
    # NOTE: this test requires the same JWT-issuance helper used elsewhere
    # in the suite. Copy the pattern from test_auth_endpoints.py::test_me_endpoint.
    # Replace `test-super-admin-token` with a real signed JWT for this user.

    body = {
        "org_unit_id": str(company.id),
        "title": "Sr. Python Engineer",
        "description_raw": "A" * 200,
        "project_scope_raw": None,
        "target_headcount": 1,
        "deadline": None,
    }
    response = await client.post("/api/jobs", json=body, headers=headers)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "signals_extracting"
    assert data["latest_snapshot"] is None
```

**Read first:** `tests/test_auth_endpoints.py` and `tests/test_settings.py` already have a working JWT-issuance helper. Copy its pattern into your new test file (don't duplicate the helper yet; follow the existing approach).

- [ ] **Step 2: Add the JWT helper import or inline copy**

Open `tests/test_auth_endpoints.py` and find the JWT issuance pattern. Replicate it in `test_jd_router.py` so `client.post(...)` uses a valid signed token for the test user.

- [ ] **Step 3: Run**

```bash
docker compose run --rm nexus pytest tests/test_jd_router.py -v
```

Expected: PASS.

- [ ] **Step 4: Add more router tests — list, get, 404, 403**

Expand `test_jd_router.py` with:

```python
@pytest.mark.asyncio
async def test_get_nonexistent_job_returns_404(db, client, ...):
    # super admin GET /api/jobs/<random uuid> → 404
    ...

@pytest.mark.asyncio
async def test_list_jobs_filters_by_visible_units(db, client, ...):
    # recruiter in unit A sees their job, not one in unit B
    ...

@pytest.mark.asyncio
async def test_retry_on_non_failed_job_returns_409(db, client, monkeypatch, ...):
    # POST /api/jobs/{id}/retry on a signals_extracting job → 409
    ...
```

Fill in the `...` bodies following the same JWT + factory patterns.

- [ ] **Step 5: Run all router tests**

```bash
docker compose run --rm nexus pytest tests/test_jd_router.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/tests/test_jd_router.py
git commit -m "test(jd): router integration tests — create/list/get/retry"
```

---

## Task 31: Dramatiq worker entrypoint

**Files:**
- Create: `backend/nexus/app/worker.py`

- [ ] **Step 1: Create the worker module**

Create `backend/nexus/app/worker.py`:

```python
"""Dramatiq worker entry point.

Run in dev via:
    docker compose up nexus-worker

Run directly via:
    dramatiq app.worker --processes 2 --threads 4

Every actor module must be imported here so Dramatiq registers the
actors with the broker at worker startup."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.config import settings

broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)

# Actor imports — MUST stay after set_broker
from app.modules.jd import actors as _jd_actors  # noqa: F401, E402
```

- [ ] **Step 2: Smoke test the module loads**

```bash
docker compose run --rm nexus python -c "
import app.worker
print('broker:', app.worker.broker)
print('actors registered:', list(app.worker.broker.actors.keys()))
"
```

Expected: broker prints a RedisBroker; actors list includes `extract_and_enhance_jd`.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/worker.py
git commit -m "feat(worker): Dramatiq entrypoint with Redis broker and actor registration"
```

---

## Task 32: Add `nexus-worker` service to docker-compose

**Files:**
- Modify: `backend/nexus/docker-compose.yml`

- [ ] **Step 1: Add the worker service**

In `backend/nexus/docker-compose.yml`, after the `nexus:` service block, add:

```yaml
  nexus-worker:
    build:
      context: .
      dockerfile: Dockerfile
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@host.docker.internal:54322/postgres
      - REDIS_URL=redis://redis:6379/0
      - SUPABASE_JWKS_URL=http://host.docker.internal:54321/auth/v1/.well-known/jwks.json
      - SUPABASE_URL=http://host.docker.internal:54321
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - .:/app
    command: dramatiq app.worker --processes 2 --threads 4 --watch /app/app
```

- [ ] **Step 2: Bring up the full stack**

```bash
cd backend/nexus
docker compose up -d --build
docker compose ps
docker compose logs nexus-worker --tail 20
```

Expected: `nexus`, `nexus-worker`, and `redis` services all Up. Worker logs show "Booting ..." with the jd_extraction queue bound.

- [ ] **Step 3: Tear down**

```bash
docker compose down
```

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/docker-compose.yml
git commit -m "feat(infra): add nexus-worker service to docker-compose"
```

---

## Task 33: Update root CLAUDE.md (Anthropic → OpenAI)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find and replace the Claude/Anthropic references**

In `CLAUDE.md` (root), find the "Two-Tier Architecture Philosophy" table. Change both `LLM async` and `LLM real-time` rows from `Anthropic Claude API` to `OpenAI API`.

- [ ] **Step 2: Add AI provider hard rule**

In the "Hard Rules" section, add a new bullet under security (or create a new "AI Provider" subsection):

```markdown
### AI Provider — Load-Bearing
- AI provider is OpenAI for the entire system (Phase 2A onwards).
- All LLM calls go through the `app/ai/` module. Never call the OpenAI SDK (or `langfuse.openai`, or `instructor`) directly from business logic.
- `AIConfig` in `app/ai/config.py` is the single source of truth for model IDs and reasoning_effort — env-driven, never hardcoded in service files.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(root): replace Claude/Anthropic references with OpenAI; add AI provider hard rule"
```

---

## Task 34: Update backend CLAUDE.md

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1: Replace Anthropic references and add AI Provider section**

In `backend/nexus/CLAUDE.md`:

1. Find any `Anthropic` / `Claude` references in prose and replace with `OpenAI`.
2. Add a new section after "Auth Abstraction — Load-Bearing Constraint":

```markdown
## AI Provider & Prompt Management — Load-Bearing

Phase 2A introduces `app/ai/` as the provider-agnostic AI layer.

- **AIConfig** (`app/ai/config.py`) — env-driven model IDs and reasoning_effort. Never hardcode. Swapping a model for a task is a single `.env` change.
- **PromptLoader** (`app/ai/prompts.py`) — reads versioned prompts from `prompts/v{N}/<name>.txt`, cached in memory. Prompt updates are file changes, not code deploys.
- **OpenAI client factory** (`app/ai/client.py`) — returns an `instructor.AsyncInstructor` wrapped around `langfuse.openai.AsyncOpenAI`. Langfuse tracing is a drop-in — no-op when `LANGFUSE_HOST` is empty.
- **ExtractionOutput** and related schemas (`app/ai/schemas.py`) — strict Pydantic models for structured outputs. `source=ai_inferred` requires `inference_basis`; `ai_extracted` requires it to be null.

Business logic imports `get_openai_client()` and `prompt_loader` from `app.ai.*` — never openai/instructor/langfuse directly. This is the single swap point for a future provider change.
```

3. Update the "Module Structure" tree to include `app/ai/`, `app/worker.py`, `prompts/v1/`, and the new `app/modules/jd/` files (`errors.py`, `state_machine.py`, `authz.py`, `actors.py`, `sse.py`).

4. Update "Phase 1 — Implemented" heading to add a sibling "Phase 2A — Implemented" section listing:
   - `jd` — full module with state machine, service, actor, SSE, router
   - `ai` — provider-agnostic layer
   - Dramatiq worker infrastructure
   - Company Profile validation (strict schema + ancestry helper)

5. Update "Dev Commands" to include the worker:

```bash
# Start worker alongside API
docker compose up nexus-worker

# Run worker directly (no container)
dramatiq app.worker --processes 2 --threads 4
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/CLAUDE.md
git commit -m "docs(backend): AI provider section, Phase 2A module list, worker commands"
```

---

## Task 35: Create `backend/nexus/docs/phase-2a-implementation.md`

**Files:**
- Create: `backend/nexus/docs/phase-2a-implementation.md`

- [ ] **Step 1: Read the existing phase-1 doc to match structure and tone**

Read `backend/nexus/docs/phase-1-implementation.md` or `docs/phase-1-implementation.md` (wherever it lives) and note the section headings and level of detail.

- [ ] **Step 2: Create the phase-2a implementation doc**

Create `backend/nexus/docs/phase-2a-implementation.md`:

```markdown
# Phase 2A — JD Pipeline & Signal Extraction

Implementation walkthrough for Phase 2A. See also:
- Design spec: `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-09-phase-2a-implementation.md`

## What This Phase Built

1. **Company Profile capture** — strict 4-field schema (`about`, `industry`, `company_stage`, `hiring_bar`) stored on `organizational_units.company_profile`.
2. **Raw JD upload** — plain text paste form with a pre-check gate that blocks creation until the target org unit's ancestry has a completed company profile.
3. **Call 1 signal extraction** — async Dramatiq actor that calls GPT-5.2 via `instructor` (structured output) and `langfuse.openai` (tracing). Writes an immutable `job_posting_signal_snapshots` row with per-chip provenance.
4. **Three-panel read-only review UI** — Next.js 16 + shadcn/ui + TanStack Query, driven by an SSE status stream (`@microsoft/fetch-event-source`).
5. **`jobs.view` permission** — new canonical permission seeded into Admin, Recruiter, and Hiring Manager system roles.

## Module Layout

```
backend/nexus/
├── app/
│   ├── ai/                          ← provider-agnostic AI layer
│   │   ├── config.py                  AIConfig (env-driven)
│   │   ├── client.py                  get_openai_client() — instructor + langfuse
│   │   ├── prompts.py                 PromptLoader (file-system, cached)
│   │   └── schemas.py                 ExtractionOutput (Pydantic strict)
│   ├── worker.py                    ← Dramatiq entrypoint
│   ├── modules/
│   │   ├── jd/                      ← JD module (fleshed from Phase 1 stub)
│   │   │   ├── errors.py              IllegalTransitionError, CompanyProfileIncompleteError, sanitize_error_for_user
│   │   │   ├── state_machine.py       LEGAL_TRANSITIONS + transition() helper
│   │   │   ├── authz.py               require_job_access() ancestry walk
│   │   │   ├── schemas.py             HTTP request/response models
│   │   │   ├── service.py             business logic
│   │   │   ├── actors.py              extract_and_enhance_jd Dramatiq actor
│   │   │   ├── sse.py                 job_status_event_generator
│   │   │   └── router.py              5 endpoints
│   │   └── org_units/
│   │       ├── company_profile.py   ← NEW strict Pydantic schema
│   │       └── service.py             find_company_profile_in_ancestry() added
└── prompts/
    └── v1/
        └── jd_enhancement.txt       ← Call 1 system prompt
```

## Data Flow — Call 1

1. Recruiter POSTs `/api/jobs` with a title and raw JD.
2. `create_job_posting()` walks the org unit ancestry looking for a completed `company_profile`. If none is found, raises `CompanyProfileIncompleteError` → router returns HTTP 422 with `org_unit_id` in the body for deep-linking.
3. Profile found → INSERT a `job_postings` row in `status='draft'`, flush, then `transition(..., to_state='signals_extracting', ...)` which writes an `audit_log` row.
4. The service then calls `extract_and_enhance_jd.send(...)` — Dramatiq enqueues the actor message.
5. Service `db.commit()`s and the endpoint returns 201.
6. A separate `nexus-worker` container running `dramatiq app.worker` picks up the message.
7. Actor opens a `get_bypass_db()` session, sets `app.current_tenant` for RLS, calls the OpenAI client with `instructor` + `ExtractionOutput`.
8. On success: persists the enriched JD + snapshot row, transitions `signals_extracting → signals_extracted`, commits.
9. On failure (network, timeout, bad request, schema validation): the actor decides whether to retry. Intermediate retries leave state unchanged. On the final retry, the actor sanitizes the exception via `sanitize_error_for_user()`, stores the safe message in `job_postings.status_error`, transitions `signals_extracting → signals_extraction_failed`, commits.

## Frontend — SSE stream

The `/jobs/[jobId]` page opens two data sources simultaneously:
1. `useJob(jobId)` — TanStack Query fetches `GET /api/jobs/{id}` with `staleTime: 5000`. Query key is `['jobs', jobId]`.
2. `useJobStatusStream(jobId)` — uses `@microsoft/fetch-event-source` to connect to `GET /api/jobs/{id}/status/stream`. On every status event, calls `queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })` so the cached payload refreshes.

The Supabase token is fetched via `getFreshSupabaseToken()` (from `lib/auth/tokens.ts`) BEFORE opening the SSE connection — `await` cannot be used inside a sync object literal. The token is passed in the `Authorization` header.

## How to Add a New Prompt Version

1. Create `backend/nexus/prompts/v2/` and copy + edit the prompt.
2. Update `PromptLoader(version="v2")` in `app/ai/prompts.py` (or add a second instance).
3. Restart the worker: `docker compose restart nexus-worker`.

(A hot-reload endpoint is deferred. See the spec's Deferred Hardening section.)

## How to Swap the OpenAI Model for a Task

1. Edit `.env`: set `OPENAI_EXTRACTION_MODEL=<new-model-id>`.
2. Restart the worker: `docker compose restart nexus-worker`.
3. The next Call 1 dispatch picks up the new model. No code change, no redeploy.

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Job stuck in `signals_extracting` forever | Dramatiq enqueue succeeded but no worker consumed the message | `docker compose ps nexus-worker`, `docker compose logs nexus-worker`; known dual-write risk, see Deferred Hardening #9 |
| All Call 1 attempts fail with `signals_extraction_failed` | Wrong model ID in `.env`, or `reasoning_effort` parameter shape mismatch | `docker compose logs nexus-worker | grep jd.actor.call1_failed` — exception type and message will tell you which |
| Langfuse trace not appearing | `LANGFUSE_HOST` empty or Langfuse instance unreachable | Langfuse is intentionally a no-op when the host is unset |
| 422 Company Profile response on JD creation | Target org unit has no ancestor with a completed profile | Visit Settings → Org Units → [company] → Company Profile tab |
| 409 Conflict on retry | Job is not in `signals_extraction_failed` state | Only failed jobs can be retried; state machine enforces this |

## Known Gaps

See the Deferred Hardening section of the design spec for the full list. The most important for operators:

1. **Dual-write risk**: if Redis is down when a job is created, the row sits in `signals_extracting` with no automatic recovery. Manual fix: `UPDATE job_postings SET status = 'signals_extraction_failed' WHERE id = ...` then use the retry button.
2. **Updated_at trigger only on new 2A tables**: Phase 1 tables (`clients`, `users`, etc.) don't have the trigger. `public.set_updated_at()` is defined globally in migration 20260410000001 and can be applied to Phase 1 tables in a future cleanup.
3. **No frontend tests**: Vitest is deferred to Phase 2B.
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/docs/phase-2a-implementation.md
git commit -m "docs(backend): phase-2a-implementation.md walkthrough"
```

---

## Task 36: Backend checkpoint — run the full test suite

**Files:**
- None

- [ ] **Step 1: Run the complete backend test suite**

```bash
cd backend/nexus
docker compose up -d
docker compose run --rm nexus pytest -v
```

Expected: ALL tests pass (Phase 1 existing tests + all Phase 2A tests added so far).

- [ ] **Step 2: Run the linter**

```bash
docker compose run --rm nexus ruff check .
```

Expected: no errors.

- [ ] **Step 3: Run mypy (if it passes on Phase 1)**

```bash
docker compose run --rm nexus mypy app/
```

Expected: no new errors compared to the Phase 1 baseline. If Phase 1 had some mypy errors, that baseline is acceptable; do not introduce NEW errors.

- [ ] **Step 4: Tear down and push the branch**

```bash
docker compose down
cd /home/ishant/Projects/ProjectX
git push -u origin phase-2a-jd-pipeline
```

This is a checkpoint — no new commit. Backend work is complete up to this point; the rest of the plan is frontend.

---

## Task 37: Install frontend dependencies + bootstrap shadcn/ui

**Files:**
- Modify: `frontend/app/package.json`
- Create: `frontend/app/components.json` (via shadcn init)
- Create: `frontend/app/components/ui/*.tsx` (via shadcn add)
- Modify: `frontend/app/app/globals.css`

- [ ] **Step 1: Install npm dependencies**

```bash
cd frontend/app
npm install @tanstack/react-query @tanstack/react-query-devtools \
            react-hook-form @hookform/resolvers zod \
            @microsoft/fetch-event-source
```

Expected: all five packages install cleanly.

- [ ] **Step 2: Bootstrap shadcn/ui**

```bash
npx shadcn@latest init
```

When prompted:
- Style: **new-york**
- Base color: **neutral** (matches Phase 1's zinc accent)
- Global CSS file: `app/globals.css`
- CSS variables: **yes**
- React Server Components: **yes**
- Components directory: `components` (default)
- Utility path: `@/lib/utils`
- Import aliases: default

Expected: creates `components.json`, updates `app/globals.css` with the shadcn theme block, installs `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`.

- [ ] **Step 3: Add the shadcn primitives needed for Phase 2A**

```bash
npx shadcn@latest add button input textarea select label separator \
                      badge skeleton dialog tooltip sonner tabs card form alert
```

Expected: creates `components/ui/*.tsx` files for each primitive, installs `@radix-ui/*` dependencies.

- [ ] **Step 4: Verify the app still builds**

```bash
npm run build
```

Expected: build succeeds with no errors. Watch for any TypeScript errors from the new components.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/package.json frontend/app/package-lock.json frontend/app/components.json frontend/app/components/ui frontend/app/lib/utils.ts frontend/app/app/globals.css
git commit -m "chore(frontend): install TanStack Query, RHF, Zod, fetch-event-source; bootstrap shadcn/ui"
```

---

## Task 38: Add the `3xl: 1440px` breakpoint to Tailwind v4

**Files:**
- Modify: `frontend/app/app/globals.css`

- [ ] **Step 1: Add the custom breakpoint**

In `frontend/app/app/globals.css`, inside the existing `@theme` block (added by shadcn init), add:

```css
@theme inline {
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);

  /* Phase 2A — three-panel review layout transitions here */
  --breakpoint-3xl: 1440px;
}
```

**IMPORTANT:** Tailwind v4 uses CSS `@theme` directives in globals.css, NOT a `tailwind.config.ts` file. Do not create one.

- [ ] **Step 2: Verify the breakpoint works**

```bash
cd frontend/app
npm run build
```

Expected: build succeeds. The breakpoint is now usable as `3xl:` in className props.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/app/globals.css
git commit -m "feat(frontend): add Tailwind v4 3xl: 1440px breakpoint for three-panel review"
```

---

## Task 39: Create `lib/auth/tokens.ts` and `lib/api/jobs.ts`

**Files:**
- Create: `frontend/app/lib/auth/tokens.ts`
- Create: `frontend/app/lib/api/jobs.ts`

- [ ] **Step 1: Create the token helper**

Create `frontend/app/lib/auth/tokens.ts`:

```typescript
// Fetches the current Supabase access token, refreshing if necessary.
// Used by useJobStatusStream and the typed jobs API client.
//
// No in-memory caching layer. @supabase/ssr already caches the session
// in cookies and auto-refreshes on expiry — re-calling getSession() is
// cheap, and adding a second cache would risk serving a stale token.

import { createClient } from '@/lib/supabase/client'

export async function getFreshSupabaseToken(): Promise<string> {
  const supabase = createClient()
  const { data, error } = await supabase.auth.getSession()
  if (error || !data.session) {
    throw new Error('No active Supabase session')
  }
  return data.session.access_token
}
```

- [ ] **Step 2: Create the typed jobs API client**

Create `frontend/app/lib/api/jobs.ts`:

```typescript
import { apiFetch } from './client'

export type SignalItem = {
  value: string
  source: 'ai_extracted' | 'ai_inferred' | 'recruiter'
  inference_basis: string | null
}

export type SignalSnapshot = {
  version: number
  required_skills: SignalItem[]
  preferred_skills: SignalItem[]
  must_haves: SignalItem[]
  good_to_haves: SignalItem[]
  min_experience_years: number
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}

export type JobStatus =
  | 'draft'
  | 'signals_extracting'
  | 'signals_extraction_failed'
  | 'signals_extracted'

export type JobPostingSummary = {
  id: string
  title: string
  org_unit_id: string
  status: JobStatus
  status_error: string | null
  created_at: string
  updated_at: string
}

export type JobPostingWithSnapshot = JobPostingSummary & {
  description_raw: string
  project_scope_raw: string | null
  description_enriched: string | null
  target_headcount: number | null
  deadline: string | null
  latest_snapshot: SignalSnapshot | null
}

export type JobStatusEvent = {
  job_id: string
  status: JobStatus
  error: string | null
  signal_snapshot_version: number | null
}

export type CreateJobBody = {
  org_unit_id: string
  title: string
  description_raw: string
  project_scope_raw: string | null
  target_headcount: number | null
  deadline: string | null
}

export const jobsApi = {
  list: (token: string, orgUnitId?: string) =>
    apiFetch<JobPostingSummary[]>(
      `/api/jobs${orgUnitId ? `?org_unit_id=${orgUnitId}` : ''}`,
      { token },
    ),

  get: (token: string, id: string) =>
    apiFetch<JobPostingWithSnapshot>(`/api/jobs/${id}`, { token }),

  create: (token: string, body: CreateJobBody) =>
    apiFetch<JobPostingWithSnapshot>('/api/jobs', {
      token,
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json' },
    }),

  retry: (token: string, id: string) =>
    apiFetch<JobPostingSummary>(`/api/jobs/${id}/retry`, {
      token,
      method: 'POST',
    }),
}
```

**Read first:** `frontend/app/lib/api/client.ts` — confirm the exact signature of `apiFetch`. If it takes options differently (e.g., `method`, `body` as separate props rather than a nested object), adjust the calls above to match the Phase 1 convention.

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/lib/auth/tokens.ts frontend/app/lib/api/jobs.ts
git commit -m "feat(frontend): typed jobs API client and getFreshSupabaseToken helper"
```

---

## Task 40: Create `DashboardProviders` client boundary + integrate into layout

**Files:**
- Create: `frontend/app/components/dashboard/providers.tsx`
- Modify: `frontend/app/app/(dashboard)/layout.tsx`

- [ ] **Step 1: Create the providers client component**

Create `frontend/app/components/dashboard/providers.tsx`:

```typescript
'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { useState } from 'react'

export function DashboardProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  )

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {process.env.NODE_ENV === 'development' && <ReactQueryDevtools />}
    </QueryClientProvider>
  )
}
```

- [ ] **Step 2: Wrap the dashboard layout children in the provider**

In `frontend/app/app/(dashboard)/layout.tsx`, change the return block to wrap `children` in `<DashboardProviders>`:

```typescript
import { DashboardProviders } from '@/components/dashboard/providers'

// ... existing server logic unchanged ...

  return (
    <div className="flex flex-1">
      <aside className="w-56 border-r border-zinc-200 bg-white p-4 flex flex-col">
        <h2 className="text-sm font-bold text-zinc-900 mb-6">ProjectX</h2>
        <SidebarNav userEmail={user.email ?? ""} />
      </aside>
      <main className="flex-1 p-6">
        <DashboardProviders>{children}</DashboardProviders>
      </main>
    </div>
  );
```

The layout itself stays a server component; `DashboardProviders` is the client boundary.

- [ ] **Step 3: Verify the dashboard still renders**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/providers.tsx frontend/app/app/\(dashboard\)/layout.tsx
git commit -m "feat(frontend): DashboardProviders client boundary with TanStack Query"
```

---

## Task 41: Create `CompanyProfileForm` component

**Files:**
- Create: `frontend/app/components/dashboard/company-profile-form.tsx`

- [ ] **Step 1: Create the shared form component**

Create `frontend/app/components/dashboard/company-profile-form.tsx`:

```typescript
'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

// These enums MUST match backend/nexus/tests/fixtures/company_profile_enums.json
// A backend unit test enforces parity. If you add/remove/rename a value here,
// also update the fixture AND the Python Literal in
// backend/nexus/app/modules/org_units/company_profile.py.
export const INDUSTRY_OPTIONS = [
  { value: 'fintech_financial_services', label: 'Fintech / Financial Services' },
  { value: 'healthcare_medtech', label: 'Healthcare / Medtech' },
  { value: 'ecommerce_retail', label: 'E-commerce / Retail' },
  { value: 'ai_ml_products', label: 'AI / ML Products' },
  { value: 'saas_enterprise_software', label: 'SaaS / Enterprise Software' },
  { value: 'developer_tools_infrastructure', label: 'Developer Tools / Infrastructure' },
  { value: 'agency_consulting_staffing', label: 'Agency / Consulting / Staffing' },
  { value: 'media_content', label: 'Media / Content' },
  { value: 'logistics_supply_chain', label: 'Logistics / Supply Chain' },
  { value: 'other', label: 'Other' },
] as const

export const COMPANY_STAGE_OPTIONS = [
  { value: 'pre_seed_seed', label: 'Pre-seed / Seed (≤20 people)' },
  { value: 'series_a_b', label: 'Series A–B (20–200 people)' },
  { value: 'series_c_plus', label: 'Series C+ (200–1000 people)' },
  { value: 'large_enterprise', label: 'Large Enterprise (1000+ people)' },
] as const

export const companyProfileSchema = z.object({
  about: z
    .string()
    .min(30, 'Describe what you build in at least a sentence (30+ characters)')
    .max(500, 'Keep it concise — 500 characters max'),
  industry: z.enum(INDUSTRY_OPTIONS.map((o) => o.value) as [string, ...string[]]),
  company_stage: z.enum(
    COMPANY_STAGE_OPTIONS.map((o) => o.value) as [string, ...string[]],
  ),
  hiring_bar: z
    .string()
    .min(20, 'Describe what a strong hire looks like (20+ characters)')
    .max(280, 'Twitter-length — 280 characters max'),
})

export type CompanyProfile = z.infer<typeof companyProfileSchema>

type Props = {
  initialValue?: Partial<CompanyProfile>
  onSubmit: (value: CompanyProfile) => Promise<void>
  submitLabel?: string
}

export function CompanyProfileForm({
  initialValue,
  onSubmit,
  submitLabel = 'Save Company Profile',
}: Props) {
  const form = useForm<CompanyProfile>({
    resolver: zodResolver(companyProfileSchema),
    defaultValues: {
      about: initialValue?.about ?? '',
      industry: (initialValue?.industry as CompanyProfile['industry']) ?? undefined,
      company_stage:
        (initialValue?.company_stage as CompanyProfile['company_stage']) ?? undefined,
      hiring_bar: initialValue?.hiring_bar ?? '',
    },
    mode: 'onChange',
  })

  const aboutValue = form.watch('about') || ''
  const hiringBarValue = form.watch('hiring_bar') || ''

  return (
    <form
      onSubmit={form.handleSubmit(onSubmit)}
      className="space-y-6 max-w-2xl"
    >
      <div>
        <div className="flex items-baseline justify-between">
          <Label htmlFor="about" className="text-sm font-semibold">
            What does your company actually build or do?
          </Label>
          <span className="text-xs text-zinc-400">{aboutValue.length} / 500</span>
        </div>
        <p className="text-xs text-zinc-500 mt-1 mb-2">
          Be specific — what problems, at what scale, for whom?{' '}
          <em>Not your mission statement.</em>
        </p>
        <Textarea id="about" {...form.register('about')} rows={4} />
        {form.formState.errors.about && (
          <p className="text-xs text-red-500 mt-1">
            {form.formState.errors.about.message}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <Label htmlFor="industry" className="text-sm font-semibold">
            Industry
          </Label>
          <Select
            onValueChange={(v) =>
              form.setValue('industry', v as CompanyProfile['industry'], {
                shouldValidate: true,
              })
            }
            defaultValue={form.getValues('industry')}
          >
            <SelectTrigger id="industry" className="mt-2">
              <SelectValue placeholder="Select industry" />
            </SelectTrigger>
            <SelectContent>
              {INDUSTRY_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {form.formState.errors.industry && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.industry.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="company_stage" className="text-sm font-semibold">
            Company stage
          </Label>
          <Select
            onValueChange={(v) =>
              form.setValue('company_stage', v as CompanyProfile['company_stage'], {
                shouldValidate: true,
              })
            }
            defaultValue={form.getValues('company_stage')}
          >
            <SelectTrigger id="company_stage" className="mt-2">
              <SelectValue placeholder="Select stage" />
            </SelectTrigger>
            <SelectContent>
              {COMPANY_STAGE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {form.formState.errors.company_stage && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.company_stage.message}
            </p>
          )}
        </div>
      </div>

      <div>
        <div className="flex items-baseline justify-between">
          <Label htmlFor="hiring_bar" className="text-sm font-semibold">
            What does a strong hire look like here?
          </Label>
          <span className="text-xs text-zinc-400">
            {hiringBarValue.length} / 280
          </span>
        </div>
        <p className="text-xs text-zinc-500 mt-1 mb-2">
          What do you value that a generic JD wouldn&apos;t capture?
        </p>
        <Textarea id="hiring_bar" {...form.register('hiring_bar')} rows={3} />
        {form.formState.errors.hiring_bar && (
          <p className="text-xs text-red-500 mt-1">
            {form.formState.errors.hiring_bar.message}
          </p>
        )}
      </div>

      <Button
        type="submit"
        disabled={!form.formState.isValid || form.formState.isSubmitting}
      >
        {form.formState.isSubmitting ? 'Saving...' : submitLabel}
      </Button>
    </form>
  )
}
```

- [ ] **Step 2: Verify the build**

```bash
cd frontend/app
npm run build
```

Expected: clean build. If the zod enum `map((o) => o.value) as [string, ...string[]]` spread feels awkward, it is the idiomatic way to narrow a `readonly` enum literal for Zod — leave it.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/company-profile-form.tsx
git commit -m "feat(frontend): CompanyProfileForm shared between onboarding and settings"
```

---

## Task 42: Rewrite onboarding step 2 to use CompanyProfileForm

**Files:**
- Modify: `frontend/app/app/onboarding/page.tsx`

- [ ] **Step 1: Read the current onboarding page**

Open `frontend/app/app/onboarding/page.tsx` and find the step 2 block that currently collects the old 6-field profile (`display_name`, `industry`, `company_size`, `culture_summary`, `strong_hire`, `brand_voice`).

- [ ] **Step 2: Replace step 2 with the new 4-field form**

Replace the old step-2 form JSX with:

```typescript
import { CompanyProfileForm, type CompanyProfile } from '@/components/dashboard/company-profile-form'

// ...inside the component, replace the step 2 render branch with:

{step === 2 && (
  <div>
    <h2 className="text-lg font-semibold mb-2">Company Profile</h2>
    <p className="text-sm text-zinc-500 mb-6">
      Four questions about your company. This takes about 2 minutes and
      significantly improves the quality of your AI-generated interview
      questions and rubrics.
    </p>
    <CompanyProfileForm
      onSubmit={async (value: CompanyProfile) => {
        const res = await apiFetch('/api/org-units/' + companyUnitId, {
          method: 'PATCH',
          token: accessToken,
          body: JSON.stringify({
            set_company_profile: true,
            company_profile: value,
          }),
          headers: { 'Content-Type': 'application/json' },
        })
        // Navigate to dashboard after save
        router.push('/')
      }}
      submitLabel="Finish Onboarding"
    />
  </div>
)}
```

You will need to locate where `companyUnitId` and `accessToken` come from in the existing page. Read the surrounding code and adapt the variable names accordingly — the existing onboarding page already has the company unit ID and token in scope because it previously wrote the old profile shape.

Also REMOVE the old 6-field state variables (`display_name`, `company_size`, `culture_summary`, `strong_hire`, `brand_voice`) from the component — they're no longer used.

- [ ] **Step 3: Verify the build**

```bash
cd frontend/app
npm run build
```

Expected: clean build. Any TypeScript errors about unused imports / state should be fixed by removing the dead code left over from the old form.

- [ ] **Step 4: Smoke test manually**

```bash
cd ../../backend/nexus
docker compose up -d
cd ../../frontend/app
npm run dev
```

Navigate to `http://localhost:3000/onboarding` as a fresh super admin user and verify:
- Step 1 (user details) unchanged
- Step 2 renders the new 4-field form
- Character counters update as you type
- Submit button is disabled until valid
- Successful submit transitions to the dashboard

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/app/onboarding/page.tsx
git commit -m "feat(onboarding): rewrite step 2 to use 4-field CompanyProfileForm"
```

---

## Task 43: Create the Company Profile settings tab page

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/page.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`

- [ ] **Step 1: Create the company-profile tab page**

Create `frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/page.tsx`:

```typescript
'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { notFound, useParams, useRouter } from 'next/navigation'
import { toast } from 'sonner'

import { CompanyProfileForm, type CompanyProfile } from '@/components/dashboard/company-profile-form'
import { apiFetch } from '@/lib/api/client'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

type OrgUnit = {
  id: string
  name: string
  unit_type: string
  company_profile: CompanyProfile | null
  company_profile_completed_at: string | null
}

export default function CompanyProfilePage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId
  const queryClient = useQueryClient()
  const router = useRouter()

  const { data: unit, isLoading } = useQuery<OrgUnit>({
    queryKey: ['org-unit', unitId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return apiFetch<OrgUnit>(`/api/org-units/${unitId}`, { token })
    },
  })

  const mutation = useMutation({
    mutationFn: async (profile: CompanyProfile) => {
      const token = await getFreshSupabaseToken()
      return apiFetch(`/api/org-units/${unitId}`, {
        token,
        method: 'PATCH',
        body: JSON.stringify({
          set_company_profile: true,
          company_profile: profile,
        }),
        headers: { 'Content-Type': 'application/json' },
      })
    },
    onSuccess: () => {
      toast.success('Company profile saved')
      queryClient.invalidateQueries({ queryKey: ['org-unit', unitId] })
    },
    onError: (err: Error) => {
      toast.error(`Save failed: ${err.message}`)
    },
  })

  if (isLoading) return <div className="text-sm text-zinc-500">Loading…</div>
  if (!unit) return notFound()

  // Tab is only valid for company / client_account units
  if (!['company', 'client_account'].includes(unit.unit_type)) {
    return (
      <div className="text-sm text-zinc-500">
        Company Profile is only configurable on company and client_account units.
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-2">Company Profile</h2>
      <p className="text-sm text-zinc-500 mb-6">
        Four questions about your company. Required before creating job descriptions.
      </p>
      <CompanyProfileForm
        initialValue={unit.company_profile ?? undefined}
        onSubmit={async (value) => {
          await mutation.mutateAsync(value)
        }}
      />
    </div>
  )
}
```

- [ ] **Step 2: Add the tab link to the parent unit detail page**

Open `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`. Find the section header or metadata block and add a link (or nav row) to the new tab when `unit.unit_type in ['company', 'client_account']`:

```typescript
{['company', 'client_account'].includes(unit.unit_type) && (
  <Link
    href={`/settings/org-units/${unit.id}/company-profile`}
    className="inline-flex items-center px-3 py-1.5 rounded-md text-sm font-medium bg-blue-50 text-blue-700 hover:bg-blue-100"
  >
    Company Profile
  </Link>
)}
```

**Read first:** the existing page structure. Place this link in a sensible spot — probably next to the existing "Edit" or similar action buttons. If the page currently has an inline profile edit form (from Phase 1), remove the old fields AND the old form block since all profile editing now happens on the new tab.

- [ ] **Step 3: Remove the old inline profile editor**

Remove any state + form JSX in the parent page that handled the old 6-field profile (`display_name`, `company_size`, `culture_summary`, `strong_hire`, `brand_voice`). That entire block is replaced by the dedicated tab.

- [ ] **Step 4: Verify the build**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add 'frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/page.tsx' 'frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx'
git commit -m "feat(settings): Company Profile tab on org unit detail page"
```

---

## Task 44: Create the `useJob` and `useJobStatusStream` hooks

**Files:**
- Create: `frontend/app/lib/hooks/use-job.ts`
- Create: `frontend/app/lib/hooks/use-job-status-stream.ts`

- [ ] **Step 1: Create `use-job.ts`**

Create `frontend/app/lib/hooks/use-job.ts`:

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingWithSnapshot } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJob(jobId: string) {
  return useQuery<JobPostingWithSnapshot>({
    queryKey: ['jobs', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.get(token, jobId)
    },
    enabled: !!jobId,
    staleTime: 5_000,
  })
}
```

- [ ] **Step 2: Create `use-job-status-stream.ts`**

Create `frontend/app/lib/hooks/use-job-status-stream.ts`:

```typescript
'use client'

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { type JobStatusEvent } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

/**
 * Opens an SSE connection to /api/jobs/{id}/status/stream and updates
 * local state + the TanStack Query cache on every status event.
 *
 * IMPORTANT: the Supabase token must be fetched BEFORE opening the SSE
 * connection. `await` cannot be used inside a sync object literal, so we
 * use a .then() chain to fetch first, then pass the token into fetchEventSource.
 */
export function useJobStatusStream(jobId: string) {
  const [status, setStatus] = useState<JobStatusEvent | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    const ctrl = new AbortController()

    getFreshSupabaseToken().then((token) => {
      if (ctrl.signal.aborted) return
      fetchEventSource(`${API_URL}/api/jobs/${jobId}/status/stream`, {
        signal: ctrl.signal,
        headers: { Authorization: `Bearer ${token}` },
        onmessage(ev) {
          try {
            const payload = JSON.parse(ev.data) as JobStatusEvent
            setStatus(payload)
            queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
          } catch (e) {
            console.warn('SSE parse error', e)
          }
        },
        onerror(err) {
          // fetch-event-source auto-retries; don't throw unless fatal.
          console.warn('SSE error', err)
        },
      }).catch((err) => {
        console.warn('SSE connection failed', err)
      })
    })

    return () => ctrl.abort()
  }, [jobId, queryClient])

  return status
}
```

- [ ] **Step 3: Verify the build**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/lib/hooks/use-job.ts frontend/app/lib/hooks/use-job-status-stream.ts
git commit -m "feat(frontend): useJob and useJobStatusStream hooks"
```

---

## Task 45: Create the SignalChip component

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/SignalChip.tsx`

- [ ] **Step 1: Create the chip component**

Create `frontend/app/components/dashboard/jd-panels/SignalChip.tsx`:

```typescript
'use client'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { SignalItem } from '@/lib/api/jobs'

/**
 * Provenance-aware chip following Q14 of the brainstorming session:
 * subtle tinted fill + color dot prefix. Inferred chips get a dashed
 * border and an inference_basis tooltip on hover.
 */
export function SignalChip({ item }: { item: SignalItem }) {
  const base = 'inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border font-medium'

  if (item.source === 'ai_extracted') {
    return (
      <span className={`${base} bg-blue-50 text-blue-700 border-blue-200`}>
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
        {item.value}
      </span>
    )
  }

  if (item.source === 'ai_inferred') {
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={`${base} bg-amber-50 text-amber-800 border border-dashed border-amber-400`}>
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              {item.value}
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs text-xs">
            <p className="font-semibold mb-1">AI-inferred signal</p>
            <p className="mb-1">{item.inference_basis || 'No inference basis provided.'}</p>
            <p className="italic text-zinc-400">Verify before confirming.</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  // recruiter — not used in 2A (read-only) but supported for 2B
  return (
    <span className={`${base} bg-emerald-50 text-emerald-700 border-emerald-200`}>
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
      {item.value}
    </span>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/SignalChip.tsx
git commit -m "feat(frontend): SignalChip component with provenance-aware styling"
```

---

## Task 46: Create `OriginalJdPanel`, `EnrichedJdPanel`, `SignalsPanel`

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/OriginalJdPanel.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/EnrichedJdPanel.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/SignalsPanel.tsx`

- [ ] **Step 1: Create OriginalJdPanel**

Create `frontend/app/components/dashboard/jd-panels/OriginalJdPanel.tsx`:

```typescript
'use client'

import { useState } from 'react'

type Props = {
  descriptionRaw: string
  projectScopeRaw?: string | null
}

/**
 * Collapses to a vertical drawer below 1440px (3xl breakpoint).
 * Above 3xl, renders as a full column. Below, renders as a thin side
 * rail with a "View raw JD" label; clicking expands into a full overlay.
 */
export function OriginalJdPanel({ descriptionRaw, projectScopeRaw }: Props) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      {/* Full column — only visible at 3xl and above */}
      <aside className="hidden 3xl:flex 3xl:col-span-1 flex-col bg-white rounded-lg border border-zinc-200 p-5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-3 pb-2 border-b border-zinc-100">
          Original JD
        </h3>
        <pre className="whitespace-pre-wrap text-xs text-zinc-700 font-mono leading-relaxed">
          {descriptionRaw}
        </pre>
        {projectScopeRaw && (
          <>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mt-6 mb-3 pb-2 border-b border-zinc-100">
              Project Scope
            </h3>
            <pre className="whitespace-pre-wrap text-xs text-zinc-700 font-mono leading-relaxed">
              {projectScopeRaw}
            </pre>
          </>
        )}
      </aside>

      {/* Vertical rail — only visible below 3xl */}
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="3xl:hidden w-8 flex items-center justify-center bg-white border border-zinc-200 rounded-lg hover:bg-zinc-50"
        aria-label="View raw JD"
      >
        <span
          className="text-xs text-zinc-500 font-medium whitespace-nowrap"
          style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
        >
          View raw JD
        </span>
      </button>

      {/* Expanded overlay */}
      {expanded && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8"
          onClick={() => setExpanded(false)}
        >
          <div
            className="bg-white rounded-lg max-w-3xl max-h-[80vh] overflow-auto p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold">Original JD</h3>
              <button
                type="button"
                onClick={() => setExpanded(false)}
                className="text-zinc-400 hover:text-zinc-900"
              >
                ×
              </button>
            </div>
            <pre className="whitespace-pre-wrap text-sm text-zinc-700 font-mono">
              {descriptionRaw}
            </pre>
            {projectScopeRaw && (
              <>
                <h3 className="text-sm font-semibold mt-6 mb-2">Project Scope</h3>
                <pre className="whitespace-pre-wrap text-sm text-zinc-700 font-mono">
                  {projectScopeRaw}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
```

- [ ] **Step 2: Create EnrichedJdPanel**

Create `frontend/app/components/dashboard/jd-panels/EnrichedJdPanel.tsx`:

```typescript
'use client'

type Props = {
  enrichedJd: string
}

export function EnrichedJdPanel({ enrichedJd }: Props) {
  return (
    <section className="col-span-1 3xl:col-span-2 bg-white rounded-lg border border-zinc-200 p-6 overflow-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-4 pb-2 border-b border-zinc-100">
        Enriched JD
      </h3>
      <div className="prose prose-sm prose-zinc max-w-none whitespace-pre-wrap">
        {enrichedJd}
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Create SignalsPanel**

Create `frontend/app/components/dashboard/jd-panels/SignalsPanel.tsx`:

```typescript
'use client'

import type { SignalSnapshot } from '@/lib/api/jobs'
import { SignalChip } from './SignalChip'

type Props = {
  snapshot: SignalSnapshot
}

function Section({
  label,
  items,
}: {
  label: string
  items: SignalSnapshot['required_skills']
}) {
  if (items.length === 0) return null
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
        {label}
      </h4>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item, i) => (
          <SignalChip key={`${label}-${i}`} item={item} />
        ))}
      </div>
    </div>
  )
}

export function SignalsPanel({ snapshot }: Props) {
  return (
    <aside className="col-span-1 bg-white rounded-lg border border-zinc-200 p-5 space-y-5 overflow-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 pb-2 border-b border-zinc-100">
        Signals
      </h3>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
          Role Summary
        </h4>
        <p className="text-xs text-zinc-700 leading-relaxed">
          {snapshot.role_summary}
        </p>
      </div>

      <Section label="Required Skills" items={snapshot.required_skills} />
      <Section label="Preferred Skills" items={snapshot.preferred_skills} />
      <Section label="Must Haves" items={snapshot.must_haves} />
      <Section label="Good to Haves" items={snapshot.good_to_haves} />

      <div className="pt-3 border-t border-zinc-100 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-zinc-400 uppercase tracking-wide">Min Experience</div>
          <div className="text-zinc-900 font-semibold mt-0.5">
            {snapshot.min_experience_years} yrs
          </div>
        </div>
        <div>
          <div className="text-zinc-400 uppercase tracking-wide">Seniority</div>
          <div className="text-zinc-900 font-semibold mt-0.5 capitalize">
            {snapshot.seniority_level}
          </div>
        </div>
      </div>
    </aside>
  )
}
```

- [ ] **Step 4: Verify the build**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/components/dashboard/jd-panels/OriginalJdPanel.tsx frontend/app/components/dashboard/jd-panels/EnrichedJdPanel.tsx frontend/app/components/dashboard/jd-panels/SignalsPanel.tsx
git commit -m "feat(frontend): three-panel components — Original, Enriched, Signals"
```

---

## Task 47: Create `LoadingSkeleton` and `ErrorBanner`

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/ErrorBanner.tsx`

- [ ] **Step 1: Create LoadingSkeleton**

Create `frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx`:

```typescript
'use client'

import { Skeleton } from '@/components/ui/skeleton'
import type { JobStatusEvent } from '@/lib/api/jobs'

type Props = {
  status: JobStatusEvent | null
}

/**
 * Content-aware skeleton — status pill bound to SSE events, section
 * labels pre-rendered so the transition to real content feels like
 * filling in blanks.
 */
export function LoadingSkeleton({ status }: Props) {
  const statusText = status?.status === 'signals_extracting'
    ? 'Extracting signals and enriching JD…'
    : 'Dispatching extraction job…'

  return (
    <div className="grid grid-cols-1 3xl:grid-cols-[1fr_2fr_1.2fr] gap-4 min-h-[60vh]">
      <aside className="hidden 3xl:block bg-white rounded-lg border border-zinc-200 p-5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-3">
          Original JD
        </h3>
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[90%] mb-2" />
        <Skeleton className="h-3 w-[75%] mb-2" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[60%]" />
      </aside>

      <section className="bg-white rounded-lg border border-zinc-200 p-6">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-4 pb-2 border-b border-zinc-100">
          Enriched JD
        </h3>
        <div className="inline-flex items-center gap-2 bg-blue-50 text-blue-700 text-xs px-3 py-1.5 rounded-full border border-blue-200 mb-4">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          {statusText}
        </div>
        <Skeleton className="h-4 w-[40%] mb-3" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[90%] mb-2" />
        <Skeleton className="h-3 w-[75%] mb-6" />
        <Skeleton className="h-4 w-[35%] mb-3" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[90%] mb-2" />
        <Skeleton className="h-3 w-full" />
      </section>

      <aside className="bg-white rounded-lg border border-zinc-200 p-5 space-y-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 pb-2 border-b border-zinc-100">
          Signals
        </h3>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
            Role Summary
          </div>
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-[80%]" />
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
            Required Skills
          </div>
          <div className="flex gap-1.5 flex-wrap">
            <Skeleton className="h-5 w-16 rounded-full" />
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-5 w-14 rounded-full" />
          </div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-zinc-400 mb-2">
            Must Haves
          </div>
          <div className="flex gap-1.5 flex-wrap">
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
        </div>
      </aside>
    </div>
  )
}
```

- [ ] **Step 2: Create ErrorBanner**

Create `frontend/app/components/dashboard/jd-panels/ErrorBanner.tsx`:

```typescript
'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

type Props = {
  jobId: string
  error: string | null
}

export function ErrorBanner({ jobId, error }: Props) {
  const [retrying, setRetrying] = useState(false)
  const queryClient = useQueryClient()

  async function handleRetry() {
    setRetrying(true)
    try {
      const token = await getFreshSupabaseToken()
      await jobsApi.retry(token, jobId)
      queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
      toast.success('Retry dispatched')
    } catch (e) {
      toast.error(`Retry failed: ${(e as Error).message}`)
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="bg-red-50 border border-red-200 rounded-lg p-5 mb-4">
      <div className="flex items-start gap-3">
        <div className="text-red-500 text-lg leading-none mt-0.5">!</div>
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-red-900 mb-1">
            Extraction failed
          </h3>
          <p className="text-sm text-red-700 mb-3">
            {error || 'An unknown error occurred. Please retry.'}
          </p>
          <Button
            onClick={handleRetry}
            disabled={retrying}
            variant="outline"
            size="sm"
          >
            {retrying ? 'Retrying…' : 'Retry extraction'}
          </Button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx frontend/app/components/dashboard/jd-panels/ErrorBanner.tsx
git commit -m "feat(frontend): content-aware LoadingSkeleton and ErrorBanner with retry"
```

---

## Task 48: Create `/jobs` list page

**Files:**
- Create: `frontend/app/app/(dashboard)/jobs/page.tsx`

- [ ] **Step 1: Create the list page**

Create `frontend/app/app/(dashboard)/jobs/page.tsx`:

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { jobsApi, type JobPostingSummary, type JobStatus } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const STATUS_LABELS: Record<JobStatus, string> = {
  draft: 'Draft',
  signals_extracting: 'Extracting',
  signals_extraction_failed: 'Failed',
  signals_extracted: 'Ready',
}

const STATUS_VARIANT: Record<JobStatus, 'default' | 'secondary' | 'destructive'> = {
  draft: 'secondary',
  signals_extracting: 'secondary',
  signals_extraction_failed: 'destructive',
  signals_extracted: 'default',
}

export default function JobsListPage() {
  const { data, isLoading, error } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token)
    },
  })

  if (isLoading) return <div className="text-sm text-zinc-500">Loading…</div>
  if (error) return <div className="text-sm text-red-500">Error: {(error as Error).message}</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">Job Descriptions</h1>
        <Link href="/jobs/new">
          <Button>+ New JD</Button>
        </Link>
      </div>

      {(!data || data.length === 0) ? (
        <div className="bg-white border border-zinc-200 rounded-lg p-12 text-center">
          <h2 className="text-lg font-semibold text-zinc-900 mb-2">No JDs yet</h2>
          <p className="text-sm text-zinc-500 mb-6">
            Paste a job description to generate structured interview signals.
          </p>
          <Link href="/jobs/new">
            <Button>Create your first JD</Button>
          </Link>
        </div>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">Title</th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">Status</th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">Created</th>
              </tr>
            </thead>
            <tbody>
              {data.map((job) => (
                <tr key={job.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <td className="px-4 py-3">
                    <Link href={`/jobs/${job.id}`} className="text-sm font-medium text-blue-600 hover:underline">
                      {job.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={STATUS_VARIANT[job.status]}>
                      {STATUS_LABELS[job.status]}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-500">
                    {new Date(job.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify the build**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add 'frontend/app/app/(dashboard)/jobs/page.tsx'
git commit -m "feat(frontend): /jobs list page with status badges and empty state"
```

---

## Task 49: Create `/jobs/new` paste form

**Files:**
- Create: `frontend/app/app/(dashboard)/jobs/new/page.tsx`

- [ ] **Step 1: Create the paste form**

Create `frontend/app/app/(dashboard)/jobs/new/page.tsx`:

```typescript
'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { apiFetch } from '@/lib/api/client'
import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const createJobSchema = z.object({
  org_unit_id: z.string().uuid('Select an org unit'),
  title: z.string().min(1, 'Title is required').max(300),
  description_raw: z.string().min(50, 'JD must be at least 50 characters').max(50_000),
  project_scope_raw: z.string().max(20_000).optional().nullable(),
  target_headcount: z.number().int().min(1).max(10_000).optional().nullable(),
})

type CreateJobForm = z.infer<typeof createJobSchema>

type OrgUnitWithProfile = {
  id: string
  name: string
  unit_type: string
  has_profile_in_ancestry: boolean
}

export default function NewJobPage() {
  const router = useRouter()

  // Fetch org units the user can create jobs in, filtered to those with
  // a completed company_profile in ancestry.
  const { data: units, isLoading: unitsLoading } = useQuery<OrgUnitWithProfile[]>({
    queryKey: ['org-units', 'job-eligible'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      // The backend endpoint for this list lives in the org_units module.
      // If a dedicated "job-eligible" endpoint doesn't exist, fall back to
      // GET /api/org-units and filter client-side.
      return apiFetch<OrgUnitWithProfile[]>('/api/org-units', { token })
    },
  })

  const form = useForm<CreateJobForm>({
    resolver: zodResolver(createJobSchema),
    defaultValues: {
      org_unit_id: '',
      title: '',
      description_raw: '',
      project_scope_raw: '',
      target_headcount: null,
    },
  })

  const createMutation = useMutation({
    mutationFn: async (data: CreateJobForm) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.create(token, {
        org_unit_id: data.org_unit_id,
        title: data.title,
        description_raw: data.description_raw,
        project_scope_raw: data.project_scope_raw || null,
        target_headcount: data.target_headcount || null,
        deadline: null,
      })
    },
    onSuccess: (job) => {
      toast.success('Job created — running extraction')
      router.push(`/jobs/${job.id}`)
    },
    onError: (err: Error) => {
      toast.error(`Create failed: ${err.message}`)
    },
  })

  const eligibleUnits = units || []  // trust backend filter; if none exist, show CTA

  if (!unitsLoading && eligibleUnits.length === 0) {
    return (
      <div className="max-w-xl bg-white border border-zinc-200 rounded-lg p-8">
        <h1 className="text-xl font-semibold text-zinc-900 mb-3">
          Complete your company profile first
        </h1>
        <p className="text-sm text-zinc-600 mb-5">
          You need a completed company profile before creating a job description.
          The AI uses it to calibrate what a strong hire looks like at your company.
        </p>
        <Link href="/settings/org-units">
          <Button>Set up Company Profile</Button>
        </Link>
      </div>
    )
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-zinc-900 mb-6">New Job Description</h1>
      <form
        onSubmit={form.handleSubmit((data) => createMutation.mutate(data))}
        className="space-y-6"
      >
        <div>
          <Label htmlFor="org_unit_id">Org Unit</Label>
          <Select
            onValueChange={(v) => form.setValue('org_unit_id', v, { shouldValidate: true })}
          >
            <SelectTrigger id="org_unit_id" className="mt-2">
              <SelectValue placeholder="Select org unit" />
            </SelectTrigger>
            <SelectContent>
              {eligibleUnits.map((u) => (
                <SelectItem key={u.id} value={u.id}>
                  {u.name} ({u.unit_type})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {form.formState.errors.org_unit_id && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.org_unit_id.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="title">Title</Label>
          <Input id="title" {...form.register('title')} className="mt-2" />
          {form.formState.errors.title && (
            <p className="text-xs text-red-500 mt-1">{form.formState.errors.title.message}</p>
          )}
        </div>

        <div>
          <Label htmlFor="description_raw">Job Description</Label>
          <p className="text-xs text-zinc-500 mt-1 mb-2">
            Paste the full raw JD. The AI will enrich it and extract structured signals.
          </p>
          <Textarea
            id="description_raw"
            {...form.register('description_raw')}
            rows={14}
            className="font-mono text-sm"
          />
          {form.formState.errors.description_raw && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.description_raw.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="project_scope_raw">Project Scope (optional)</Label>
          <p className="text-xs text-zinc-500 mt-1 mb-2">
            What will this hire build in their first 90 days? Significantly improves
            question specificity.
          </p>
          <Textarea
            id="project_scope_raw"
            {...form.register('project_scope_raw')}
            rows={5}
          />
        </div>

        <Button type="submit" disabled={createMutation.isPending}>
          {createMutation.isPending ? 'Creating…' : 'Create and enhance'}
        </Button>
      </form>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add 'frontend/app/app/(dashboard)/jobs/new/page.tsx'
git commit -m "feat(frontend): /jobs/new paste form with RHF + Zod + org unit gate"
```

---

## Task 50: Create `/jobs/[jobId]` three-panel review page

**Files:**
- Create: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

- [ ] **Step 1: Create the review page**

Create `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`:

```typescript
'use client'

import { useParams } from 'next/navigation'

import { EnrichedJdPanel } from '@/components/dashboard/jd-panels/EnrichedJdPanel'
import { ErrorBanner } from '@/components/dashboard/jd-panels/ErrorBanner'
import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
import { OriginalJdPanel } from '@/components/dashboard/jd-panels/OriginalJdPanel'
import { SignalsPanel } from '@/components/dashboard/jd-panels/SignalsPanel'
import { useJob } from '@/lib/hooks/use-job'
import { useJobStatusStream } from '@/lib/hooks/use-job-status-stream'

export default function JobReviewPage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  const { data: job, isLoading } = useJob(jobId)
  const status = useJobStatusStream(jobId)

  if (isLoading || !job) {
    return <LoadingSkeleton status={status} />
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
      </div>

      {(job.status === 'draft' || job.status === 'signals_extracting') && (
        <LoadingSkeleton status={status} />
      )}

      {job.status === 'signals_extraction_failed' && (
        <ErrorBanner jobId={jobId} error={job.status_error} />
      )}

      {job.status === 'signals_extracted' && job.latest_snapshot && job.description_enriched && (
        <div className="grid grid-cols-[auto_1fr] 3xl:grid-cols-[1fr_2fr_1.2fr] gap-4 min-h-[70vh]">
          <OriginalJdPanel
            descriptionRaw={job.description_raw}
            projectScopeRaw={job.project_scope_raw}
          />
          <EnrichedJdPanel enrichedJd={job.description_enriched} />
          <SignalsPanel snapshot={job.latest_snapshot} />
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
cd frontend/app
npm run build
```

Expected: clean build.

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add 'frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx'
git commit -m "feat(frontend): /jobs/[jobId] three-panel review page with SSE integration"
```

---

## Task 51: Add "Jobs" to the sidebar navigation

**Files:**
- Modify: `frontend/app/app/(dashboard)/SidebarNav.tsx`

- [ ] **Step 1: Read current sidebar**

Open `frontend/app/app/(dashboard)/SidebarNav.tsx` and note the existing nav link pattern.

- [ ] **Step 2: Add the Jobs link**

Add a link to `/jobs` using the same pattern as existing nav items (place it prominently — likely just after the dashboard home link). Example:

```typescript
<Link
  href="/jobs"
  className="..."  // same classes as existing nav links
>
  Jobs
</Link>
```

- [ ] **Step 3: Verify the build and manually check the sidebar**

```bash
cd frontend/app
npm run build
```

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add 'frontend/app/app/(dashboard)/SidebarNav.tsx'
git commit -m "feat(frontend): add Jobs link to dashboard sidebar"
```

---

## Task 52: Update frontend CLAUDE.md

**Files:**
- Modify: `frontend/app/CLAUDE.md`

- [ ] **Step 1: Move Phase 2 deps from "Planned" to "Currently Installed"**

In `frontend/app/CLAUDE.md`, find the "Tech Stack" section and move these from "Planned for Phase 2+" to "Currently Installed (Phase 2A)":
- shadcn/ui
- TanStack Query
- React Hook Form + Zod
- @microsoft/fetch-event-source

- [ ] **Step 2: Document new conventions**

Add a section documenting:
- `components/ui/` — shadcn primitives, auto-generated, do not edit
- `components/dashboard/` — dashboard composite components
- `components/dashboard/jd-panels/` — JD three-panel review components
- `lib/auth/tokens.ts` — `getFreshSupabaseToken()` usage
- `lib/api/jobs.ts` — first namespaced typed API client
- `lib/hooks/` — `useJob`, `useJobStatusStream`
- Custom Tailwind v4 breakpoint `3xl: 1440px` in `app/globals.css` (NOT a `tailwind.config.ts`)
- `DashboardProviders` client-boundary for TanStack Query, wraps dashboard children inside the server layout

Add a reminder that the AGENTS.md rule still applies: consult `node_modules/next/dist/docs/` before writing new route or layout files.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/CLAUDE.md
git commit -m "docs(frontend): document Phase 2A conventions — shadcn, TanStack Query, jd-panels, 3xl breakpoint"
```

---

## Task 53: Final acceptance — run the full manual E2E checklist

**Files:**
- None — manual verification

- [ ] **Step 1: Bring up the full stack**

```bash
cd backend/nexus
docker compose up -d --build
cd ../../frontend/app
npm run dev
```

- [ ] **Step 2: Run the acceptance checklist from the design spec**

Follow the "Manual E2E acceptance" section in `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`:

1. Create a tenant, super-admin signs in, completes onboarding with the new 4-field company profile. Verify validation errors fire correctly on short `about`, invalid enum values, etc.
2. Navigate to Settings → Org Units → [company unit] → Company Profile tab. Verify the tab only shows for company/client_account units, loads existing values, and saves successfully.
3. Paste a real JD on `/jobs/new`. Submit. Watch the content-aware skeleton with the status pill cycle through states via SSE.
4. Land on `/jobs/[id]` three-panel view. Verify:
   - Original JD collapses to a vertical drawer below 1440px (resize browser window)
   - Enriched JD renders in center
   - Signal chips render with correct provenance colors (blue solid, amber dashed, green solid)
   - `ai_inferred` chip tooltip shows `inference_basis` on hover
5. Trigger a failure path: stub `OPENAI_API_KEY` to empty in `.env`, restart `nexus-worker`, re-submit a JD. Verify the error banner appears with the sanitized message and a working retry button.
6. Restore `OPENAI_API_KEY`, click retry, verify recovery path works.
7. Close the browser tab, reopen, navigate to `/jobs`, click the JD. Verify the review loads from cached DB state.
8. Create a second user in a sibling org unit via the admin flow. Verify they cannot see the first user's JD (403 when visiting `/jobs/[id]` directly; job not in their list view).
9. Inspect `audit_log` in the DB:

```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "
SELECT action, payload, created_at FROM audit_log
WHERE action = 'job_posting.status_changed'
ORDER BY created_at DESC LIMIT 10;
"
```

Expected: rows for every status transition with `from`, `to`, and `correlation_id` in the payload.

10. Verify Langfuse traces (if `LANGFUSE_HOST` is configured in dev). Optional — skip if no local Langfuse instance.

- [ ] **Step 3: Run the full backend test suite one more time**

```bash
cd backend/nexus
docker compose run --rm nexus pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Run frontend lint + build**

```bash
cd ../../frontend/app
npm run lint
npm run build
```

Expected: no lint errors, clean build.

- [ ] **Step 5: Tear down**

```bash
cd ../../backend/nexus
docker compose down
```

- [ ] **Step 6: Push the branch**

```bash
cd /home/ishant/Projects/ProjectX
git push origin phase-2a-jd-pipeline
```

Phase 2A implementation is complete. Open a PR with a summary linking to the spec and the plan.

---

## Self-review notes (for the executing agent)

This plan decomposes Phase 2A into 53 tasks. Rough distribution:

- **Day-1 verification (Tasks 0–5):** 6 tasks — gate all subsequent work on verified findings.
- **Backend foundation (Tasks 6–12):** 7 tasks — config, deps, migrations, models, permission.
- **Backend AI layer (Tasks 13–17):** 5 tasks — prompt, AIConfig, PromptLoader, schemas, client.
- **Backend JD module (Tasks 18–30):** 13 tasks — errors, state machine, authz, service, actor, SSE, router, handlers.
- **Backend infra + docs (Tasks 31–36):** 6 tasks — worker, docker-compose, CLAUDE.md, phase-2a doc, checkpoint.
- **Frontend (Tasks 37–52):** 16 tasks — deps, shadcn, breakpoint, tokens, API client, providers, forms, panels, hooks, pages, docs.
- **Acceptance (Task 53):** 1 task — manual E2E checklist + full test suite + push.

Total: 54 commits (plus the initial branch-creation in Task 0 which has no commit). Each commit is small, self-contained, and matches a single logical change. Frequent commits make review easy and rollback surgical.

**Spec coverage check:**
- Company Profile capture → Tasks 21, 22, 41, 42, 43 ✓
- Raw JD upload → Tasks 23, 24, 49 ✓
- Call 1 signal extraction → Tasks 13, 14, 15, 16, 17, 26 ✓
- Three-panel review UI → Tasks 45, 46, 47, 50 ✓
- Navigable JD list → Tasks 23, 24, 28, 48 ✓
- Dramatiq worker infra → Tasks 31, 32 ✓
- SSE status stream → Tasks 27, 28, 44 ✓
- `jobs.view` permission → Tasks 8, 11 ✓
- Exception handlers (409/422) → Tasks 18, 29 ✓
- State machine → Tasks 19, 25 ✓
- Authorization → Tasks 20 (ancestry walk) ✓
- Docs (root, backend, frontend, phase-2a) → Tasks 33, 34, 35, 52 ✓
- Day-1 verifications → Tasks 1, 2, 3, 4, 5 ✓
- Updated_at trigger → Task 10 ✓
- Migration 1/2/3 → Tasks 9, 10, 11 ✓
- sanitize_error_for_user → Task 18 ✓
- find_company_profile_in_ancestry → Task 22 ✓

No gaps detected.

**Placeholder scan:** No "TBD", "TODO", "implement later", or "similar to Task N" references. Every code step shows the actual code.

**Type consistency check:**
- `JobStatusEvent` consistent across backend schemas, frontend types, SSE hook, and tests ✓
- `SignalItem.source` literal values (`ai_extracted | ai_inferred | recruiter`) consistent across backend (schemas.py), frontend (jobs.ts), DB (JSONB), and tests ✓
- `INDUSTRY_VALUES` / `COMPANY_STAGE_VALUES` consistent between Python (company_profile.py), frontend (company-profile-form.tsx), and fixture JSON ✓
- `LEGAL_TRANSITIONS` consistent between state_machine.py and main.py exception handler message mapping ✓
- `getFreshSupabaseToken()` signature consistent between tokens.ts, use-job.ts, use-job-status-stream.ts, and page components ✓

Plan is ready for execution.

