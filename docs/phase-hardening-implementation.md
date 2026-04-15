# Post-Phase-2C Hardening — Developer Documentation

**Scope:** Batches D/F/G — RLS runtime role, NULLIF policies, startup assertion, JWT hardening, SSE RLS fix, security headers, FRONTEND_BASE_URL, correlation-ID validation, and misc front/back fixes
**Status:** Complete and shipped through 2026-04-15
**Last updated:** 2026-04-15

See also:
- Phase 1 walkthrough: `docs/phase-1-implementation.md`
- Phase 2A walkthrough: `docs/phase-2a-implementation.md`
- Phase 2B walkthrough: `docs/phase-2b-implementation.md`
- Phase 2C.1 walkthrough: `docs/phase-2c1-implementation.md` (Section 2 flags 0004's ship-time RLS drift; this doc closes the loop)
- Phase 2C.2 walkthrough: `docs/phase-2c2-implementation.md` (Section 2 flags 0006's ship-time RLS drift)

---

## Table of Contents

1. [What This Covers](#1-what-this-covers)
2. [RLS Runtime Role (Migration 0010)](#2-rls-runtime-role-migration-0010)
3. [NULLIF Cast on Tenant Policies (Migration 0011)](#3-nullif-cast-on-tenant-policies-migration-0011)
4. [Phase 1 Full-Command Policies (Migration 0009)](#4-phase-1-full-command-policies-migration-0009)
5. [Audit Log INSERT Fix (Migration 0008)](#5-audit-log-insert-fix-migration-0008)
6. [Policy Rename (Migration 0012)](#6-policy-rename-migration-0012)
7. [Startup RLS Completeness Check](#7-startup-rls-completeness-check)
8. [JWT Hardening](#8-jwt-hardening)
9. [CORS-on-401 Fix](#9-cors-on-401-fix)
10. [SSE RLS Fix](#10-sse-rls-fix)
11. [Security Headers](#11-security-headers)
12. [Configurable FRONTEND_BASE_URL](#12-configurable-frontend_base_url)
13. [Correlation-ID Header Validation](#13-correlation-id-header-validation)
14. [Misc Fixes](#14-misc-fixes)
15. [Remaining Pre-Phase-3 Hardening](#15-remaining-pre-phase-3-hardening)
16. [Cross-references](#16-cross-references)

---

## 1. What This Covers

This doc walks the post-Phase-2C hardening work — everything between commit `380fbf2` (JWT tightening, the first Batch G fix) and `HEAD`. It is the companion to the sibling phase docs, which flag individual drift points under "Spec drift" headings and delegate the full story to this file.

Three audit-round batches are described here:

| Batch | Focus | Load-bearing migrations | Load-bearing code |
|---|---|---|---|
| **Batch D (Round 1 security fixes)** | Audit log RLS, SSE disconnect, Redis safe-dispatch, post-commit query bug, candidate JWT secret validation, correlation-ID validation, CORS cleanup | `0008_audit_log_tenant_insert` | `jd/router.py::_get_correlation_id`, `audit/service.py`, `question_bank/router.py` |
| **Batch E/F (RLS enforcement + SSE fix)** | Phase 1 full-command policies, `nexus_app` runtime role, NULLIF cast, SSE routed through `get_tenant_session` | `0009_phase1_rls_full_command`, `0010_create_nexus_app_role`, `0011_rls_nullif_tenant` | `database.py::_apply_runtime_role`, `jd/sse.py`, `question_bank/sse.py` |
| **Batch G (Round 2 hardening)** | JWT ES256+audience+issuer, policy rename, startup RLS check, CORS-on-401, FRONTEND_BASE_URL, security headers, misc front-end fixes | `0012_rename_service_role_bypass` | `auth/service.py`, `main.py::_assert_rls_completeness`, `next.config.ts`, `config.py::frontend_base_url`, `invite/page.tsx` |

A handful of the fixes are mechanism-dependent on each other:

- Migration **0009** fixes Phase 1 policy shape, but is a no-op until the app stops connecting as `postgres` — migration **0010** is what makes the fix take effect.
- Migration **0011** (NULLIF) is invisible under `postgres` for the same reason; it only surfaced as a crash after 0010 flipped runtime enforcement on.
- The **startup assertion** in `app/main.py` is the safety net that prevents a future drop-through regression of either 0009 or 0011.

Every section below calls out the exact commit hash(es) that made the fix — these are load-bearing for this doc in a way they aren't for the sibling phase docs.

---

## 2. RLS Runtime Role (Migration 0010)

**Commit:** `3e38981` — `fix(rls): enforce policies via dedicated NOBYPASSRLS runtime role`
**Migration:** `backend/nexus/migrations/versions/0010_create_nexus_app_role.py`
**Cross-reference:** Phase 2C.1 Section 2 and Phase 2C.2 Section 2 both flag "ship-time RLS is a no-op" without explaining why; this is the reason.

### What was broken (mechanism)

The Supabase local `postgres` role — and Supabase Cloud's `postgres`, for that matter — has `rolbypassrls=true`. A role with that attribute **unconditionally ignores every RLS policy on every table**, regardless of what the policies say. Connecting as `postgres` alone gives you zero tenant isolation at the database layer: the only thing keeping tenants apart is the application's `WHERE` clauses, with no database backstop.

All Phase 1 / 2A / 2B / 2C migrations had been shipping `tenant_isolation` + `service_bypass` policies in good faith. Under the old `postgres` connection, none of them were evaluated at runtime. A forgotten `WHERE tenant_id = :tenant_id` in any service function would have been an immediate cross-tenant leak with no layer-below catch.

### What's fixed

Migration 0010 creates a dedicated PostgreSQL role `nexus_app` with `NOBYPASSRLS` set. The application's per-request sessions now switch to that role via `SET LOCAL ROLE nexus_app` at the top of every transaction, so every subsequent statement in the session is subject to RLS.

The role is created deliberately locked down — it cannot log in directly, cannot create schema objects, cannot modify roles, and has only the minimum data-plane grants:

```python
# backend/nexus/migrations/versions/0010_create_nexus_app_role.py (upgrade())

op.execute(
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
            CREATE ROLE nexus_app NOLOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;
        END IF;
    END
    $$;
    """
)

op.execute("GRANT nexus_app TO postgres")

op.execute("GRANT USAGE ON SCHEMA public TO nexus_app")
op.execute(
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexus_app"
)
op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nexus_app")

op.execute(
    """
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexus_app
    """
)
op.execute(
    """
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO nexus_app
    """
)
```

A few details are load-bearing:

- **`NOLOGIN`** — nobody can connect directly as `nexus_app`; it is only reached via a role switch from an already-authenticated session. This keeps the attack surface of "compromised app credentials" exactly one role worth — `postgres`'s — rather than widening it.
- **`NOBYPASSRLS`** — the whole point. Without this attribute on the runtime role, every `tenant_isolation` and `service_bypass` policy remains a silent no-op.
- **`GRANT nexus_app TO postgres`** — without this, `SET LOCAL ROLE nexus_app` inside a postgres-authenticated session fails with `permission denied to set role`. PostgreSQL requires the current role to be a member of (or an admin of) the target role.
- **`ALTER DEFAULT PRIVILEGES`** — future tables added by later migrations automatically inherit the same grants. Without this, every new Alembic migration would need to remember to grant `nexus_app` access.
- **No `CREATE` grant** on the schema — `nexus_app` cannot add, drop, or modify tables. Only `postgres` (the migration runner) can touch schema. This means Alembic continues to run as `postgres`, which is still fine because migrations are DDL-only and DDL isn't what the RLS policies are meant to police.

### The session-level switch

The app side lives in `backend/nexus/app/database.py`. A new helper `_apply_runtime_role` runs at the top of every `get_tenant_db`, `get_bypass_db`, `get_tenant_session`, and `get_bypass_session`:

```python
# backend/nexus/app/database.py

async def _apply_runtime_role(session: AsyncSession) -> None:
    """Switch the session's PG role to `settings.db_runtime_role` if configured.

    The `postgres` role in Supabase has rolbypassrls=true, which makes every
    tenant_isolation / service_bypass policy a no-op. Switching to a role
    without that attribute (e.g. `nexus_app`, created by migration 0010) is
    the only way to actually enforce RLS at runtime.

    SET LOCAL ROLE is scoped to the current transaction and auto-reverts
    on commit/rollback, so pooled connections don't cross-contaminate.
    """
    role = settings.db_runtime_role
    if role is None:
        return
    await session.execute(sqlalchemy.text(f"SET LOCAL ROLE {role}"))
```

Three consequences of the implementation choices:

1. **`SET LOCAL ROLE`** (not `SET ROLE`) means the role switch is scoped to the current transaction. On commit/rollback the role auto-reverts to `postgres`, so the pooled asyncpg connection is safe to hand to the next request without cross-contaminating role state.
2. **Ordering matters inside the transaction.** `_apply_runtime_role` runs **before** `SET LOCAL app.current_tenant = '<uuid>'`. If it ran after, the tenant filter GUC would be set on the `postgres` role (which would ignore policies anyway) and the subsequent `SET LOCAL ROLE` would not change that behaviour.
3. **`settings.db_runtime_role` is gated behind an identifier validator.** Because asyncpg cannot parameterise DDL-like commands, the role name is interpolated into the statement string. The config validator in `app/config.py` rejects any value that doesn't match `^[a-zA-Z_][a-zA-Z0-9_]*$`, so the interpolation is SQL-injection-safe by construction:

```python
# backend/nexus/app/config.py

@field_validator("db_runtime_role")
@classmethod
def _validate_db_runtime_role(cls, v: str | None) -> str | None:
    if v is None or v == "":
        return None
    import re
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
        raise ValueError(
            f"DB_RUNTIME_ROLE must be a PostgreSQL identifier "
            f"([a-zA-Z_][a-zA-Z0-9_]*), got: {v!r}"
        )
    return v
```

### Opt-in gate

`DB_RUNTIME_ROLE` defaults to `None`. Leaving it empty disables `_apply_runtime_role` entirely — the connection stays on `postgres`, and RLS is back to being a no-op. Two places still use that mode:

- **Tests.** `backend/nexus/tests/conftest.py` explicitly sets `DB_RUNTIME_ROLE=""` because the test DB is built via SQLAlchemy `Base.metadata.create_all` rather than Alembic migrations. `nexus_app` doesn't exist in the test cluster, so the role switch would fail.
- **Bootstrap.** Any environment running a fresh Supabase cluster before migration 0010 has been applied. Once 0010 lands, operators must set `DB_RUNTIME_ROLE=nexus_app` in the environment and restart the app.

This gate is paired with the startup assertion in Section 7 — the assertion is also skipped when `DB_RUNTIME_ROLE` is empty, so the two co-evolve.

### End-to-end verification

The commit message documents the verification the author ran against the real dev DB after applying 0010:

1. `nexus_app` role exists with `rolbypassrls=false`.
2. `SET LOCAL ROLE nexus_app` successfully switches `current_user`.
3. A tenant-scoped `SELECT` returns only own-tenant rows (the test cluster had 8 org units for the tenant).
4. A tenant-scoped `INSERT` on own tenant succeeds.
5. A cross-tenant `INSERT` raises `InsufficientPrivilegeError` — the first actual database-level isolation this repo has ever had.

---

## 3. NULLIF Cast on Tenant Policies (Migration 0011)

**Commit:** `f6cd25e` — `fix(rls): NULL-safe current_tenant cast in every tenant_isolation policy`
**Migration:** `backend/nexus/migrations/versions/0011_rls_null_safe_current_tenant.py`

### What was broken (mechanism)

Immediately after migration 0010 landed and the app started running as `nexus_app`, real requests began crashing with:

```
invalid input syntax for type uuid: ""
```

inside queries that touched any tenant-scoped table. The cause was a latent PostgreSQL quirk that had been lurking since Phase 1, invisible only because `postgres` never evaluated the policies:

1. `get_tenant_db` issues `SET LOCAL app.current_tenant = '<uuid>'` inside its transaction.
2. When the transaction commits, `SET LOCAL` reverts the value. **For a custom GUC (one starting with `app.`) that was never declared/initialised at session boot, PostgreSQL restores it to the empty string `""` — not NULL.**
3. A subsequent `get_bypass_db` request on the same pooled asyncpg connection sets `app.bypass_rls = 'true'` but never touches `app.current_tenant`. The empty string persists on the connection.
4. Any `SELECT` on a table with a `tenant_isolation` policy now evaluates `<col> = current_setting('app.current_tenant', true)::uuid`. The `''::uuid` cast blows up, and the whole query 500s.

Under the old `postgres` connection, step 4 never ran — the policy expression was skipped entirely. Under `nexus_app`, every pooled connection that served a tenant_db request followed by a bypass_db request crashed on the second request.

### What's fixed

Migration 0011 wraps every `current_setting('app.current_tenant', true)::uuid` expression in `NULLIF(..., '')::uuid`. `NULLIF` returns NULL for the empty string, `NULL::uuid` is NULL, and `<col> = NULL` evaluates to NULL, which is treated as false inside a policy expression. The net effect is: **under an unset/empty `current_tenant`, `tenant_isolation` matches no rows**, which is the intended semantics — `service_bypass` remains the only path that grants access in that case.

### Before/after

```sql
-- Before (every tenant_isolation policy in the schema):
CREATE POLICY "tenant_isolation" ON <table>
    USING      (<col> = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (<col> = current_setting('app.current_tenant', true)::uuid);

-- After (migration 0011):
CREATE POLICY "tenant_isolation" ON <table>
    USING      (<col> = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (<col> = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
```

### Tables touched

The migration enumerates every table that carries a current_tenant-casting policy:

```python
# backend/nexus/migrations/versions/0011_rls_null_safe_current_tenant.py

TENANT_ISOLATION_TABLES = [
    ("clients", "id"),
    ("users", "tenant_id"),
    ("organizational_units", "client_id"),
    ("user_role_assignments", "tenant_id"),
    ("user_invites", "tenant_id"),
    ("audit_log", "tenant_id"),
    ("job_postings", "tenant_id"),
    ("job_posting_signal_snapshots", "tenant_id"),
    ("sessions", "tenant_id"),
    ("pipeline_templates", "tenant_id"),
    ("pipeline_template_stages", "tenant_id"),
    ("job_pipeline_instances", "tenant_id"),
    ("job_pipeline_stages", "tenant_id"),
    ("stage_question_banks", "tenant_id"),
    ("stage_questions", "tenant_id"),
]
```

`service_bypass` policies are deliberately left alone — they compare a text GUC against the literal `'true'`, and the empty-string case is already a no-op false, so there is nothing to fix on that side.

Two bespoke policies also get patched:

- **`audit_log`**'s dedicated `tenant_isolation_insert` policy (added by migration 0008 — see [Section 5](#5-audit-log-insert-fix-migration-0008)) is dropped and recreated with the `NULLIF` wrap.
- **`roles`**'s `roles_visibility` policy uses `tenant_id IS NULL OR tenant_id = current_setting(...)` to make system roles visible to everyone; only the second half needs the `NULLIF` wrap.

### Why `true` matters in `current_setting('app.current_tenant', true)`

The second argument to `current_setting` is `missing_ok`. When `true`, the function returns NULL for a truly unset GUC rather than raising `unrecognized configuration parameter`. All of our policies pass `true` because we want the "never-set" path to return NULL, not error. The `""` quirk is a *different* bug from the "unset GUC raises" bug that `missing_ok` would have protected against — it's the "reset-to-previous" behaviour of `SET LOCAL`, which pre-dates the custom-GUC declaration and restores empty string instead of NULL.

### Going forward

Any new tenant-scoped migration **must** use the `NULLIF(current_setting('app.current_tenant', true), '')::uuid` form. The canonical pattern in root `CLAUDE.md` already reflects this, and the startup assertion (Section 7) will refuse to boot if a new migration drops the `WITH CHECK` half, though it does not catch the raw-cast regression — that's relied on convention + CI code review.

---

## 4. Phase 1 Full-Command Policies (Migration 0009)

**Commit:** `5414bf5` — `fix(rls): full-command tenant_isolation on Phase 1 tables`
**Migration:** `backend/nexus/migrations/versions/0009_phase1_rls_full_command.py`

### What was broken (mechanism)

Phase 1 tables (`clients`, `users`, `organizational_units`, `user_role_assignments`, `user_invites`) were originally created in the Supabase SQL migration `20260405000001_rls_policies.sql` with `tenant_isolation` declared as `FOR SELECT USING (...)`. The shape looked fine at a glance but was silently broken:

- `FOR SELECT USING (...)` applies only to `SELECT` statements. The `USING` clause filters rows on read; it is not consulted on `INSERT`/`UPDATE`/`DELETE`.
- Companion `service_bypass` policies were defined as `FOR ALL USING (current_setting('app.bypass_rls', true) = 'true')`. Under `FOR ALL`, PostgreSQL uses the `USING` expression as the implicit `WITH CHECK` for writes — which under a tenant-scoped session evaluates to `false` (because `app.bypass_rls` is unset in tenant sessions).
- Net effect: under real RLS enforcement, Phase 1 tables accept **no writes at all from tenant-scoped sessions**. Every `INSERT` and `UPDATE` from `get_tenant_db` would be blocked by the database.

This bug did not manifest in practice during Phase 1 because the app was still running as `postgres` (`rolbypassrls=true`), so none of the policies ever ran. It would have turned into a hard production blocker the instant migration 0010 flipped the runtime role — which is why both migrations were batched together in the same audit round.

Phase 2A/2B/2C tables were never affected because they used the canonical full-command form from the start.

### What's fixed

Migration 0009 drops the broken `FOR SELECT USING (...)` form on each of the five Phase 1 tables and recreates the policy as the canonical full-command variant with both `USING` and `WITH CHECK`:

```python
# backend/nexus/migrations/versions/0009_phase1_rls_full_command.py

TABLE_POLICIES = [
    ("clients", "id", "tenant_read"),
    ("users", "tenant_id", "tenant_isolation"),
    ("organizational_units", "client_id", "tenant_isolation"),
    ("user_role_assignments", "tenant_id", "tenant_isolation"),
    ("user_invites", "tenant_id", "tenant_isolation"),
]


def upgrade() -> None:
    for table, col, old_name in TABLE_POLICIES:
        op.execute(f'DROP POLICY IF EXISTS "{old_name}" ON public.{table}')
        op.execute(
            f"""
            CREATE POLICY "tenant_isolation" ON public.{table}
                USING ({col} = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK ({col} = current_setting('app.current_tenant', true)::uuid)
            """
        )
```

### Before/after (users table example)

```sql
-- Before (from supabase/migrations/20260405000001_rls_policies.sql):
CREATE POLICY "tenant_isolation" ON users
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- After (migration 0009):
CREATE POLICY "tenant_isolation" ON users
    USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

Note that 0009 still uses the raw `::uuid` cast. That crash path is invisible in 0009's own commit because `nexus_app` isn't yet in play — migration **0011** layers the `NULLIF` wrap on top. 0009 on its own would have surfaced the empty-string crash from Section 3 immediately; the audit sequenced them so 0010 + 0011 landed together.

### Tables NOT in scope

- **`audit_log`** has its own migration (0008) because the fix shape is different — audit_log had a distinct `FOR INSERT` gap, not the general full-command fix. See [Section 5](#5-audit-log-insert-fix-migration-0008).
- **`roles`** keeps its bespoke `roles_visibility` policy (the `tenant_id IS NULL OR ...` variant) because system roles need to remain visible across tenants. Only the `NULLIF` patch in 0011 touches it.

### Why the commit message's "this migration is a no-op today" note matters

The 0009 commit message explicitly says: "This migration corrects the policies so that they will Do The Right Thing once the application switches to a role without the BYPASSRLS attribute." That statement is only true because 0010 hadn't landed yet when 0009 was authored. Reading 0009 in isolation looks like a cosmetic change; reading it alongside 0010 is what makes it load-bearing. This is the kind of detail that the sibling phase docs (2C.1 Section 2, 2C.2 Section 2) abbreviate and delegate here.

---

## 5. Audit Log INSERT Fix (Migration 0008)

**Commit:** `5f4fb02` — `fix(backend): 8 critical security + correctness fixes (Batch A)` (audit_log migration is #1 in the batch)
**Migration:** `backend/nexus/migrations/versions/0008_audit_log_tenant_insert.py`

### What was broken (mechanism)

The same `FOR SELECT USING (...)` trap described in Section 4 also afflicted `audit_log`, but with a worse consequence: every `log_event()` call from a tenant-scoped path (which is most of them — JD state transitions, signal confirmations, pipeline auto-apply, team invites) was silently being rejected by the database. Because `audit/service.py::log_event` wrapped the write in a try/except to prevent logging failures from breaking business logic, the rejection was swallowed and the mutation silently never landed. The compliance audit trail had been losing tenant-scoped events since Phase 1 — invisibly.

There were actually two bugs fused here:

1. The policy was `FOR SELECT`, so the insert was rejected.
2. `audit/service.py::log_event` caught *all* exceptions and logged them at INFO level without `exc_info`, so nothing in the Sentry stream pointed at the root cause. The same Batch A commit also hardened `log_event` to log at ERROR with `exc_info` and the full audit payload, so any future regression surfaces loudly.

### What's fixed

Migration 0008 adds a dedicated `tenant_isolation_insert` policy on `audit_log` permitting INSERTs where `tenant_id` matches the session's `app.current_tenant`:

```python
# backend/nexus/migrations/versions/0008_audit_log_tenant_insert.py

def upgrade() -> None:
    op.execute("""
        CREATE POLICY "tenant_isolation_insert" ON audit_log
          FOR INSERT
          WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS "tenant_isolation_insert" ON audit_log')
```

The existing `tenant_isolation` SELECT policy on `audit_log` is left alone by 0008. Migration 0011 later patches both policies to use the `NULLIF` cast.

### Before/after

```sql
-- Before (from the Phase 1 supabase migration):
CREATE POLICY "tenant_isolation" ON audit_log
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_bypass" ON audit_log
    FOR ALL USING (current_setting('app.bypass_rls', true) = 'true');

-- After 0008:
CREATE POLICY "tenant_isolation" ON audit_log
    FOR SELECT USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "tenant_isolation_insert" ON audit_log   -- NEW
    FOR INSERT WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_bypass" ON audit_log
    FOR ALL USING (current_setting('app.bypass_rls', true) = 'true');

-- After 0011 (NULLIF-wrapped, both policies shown):
CREATE POLICY "tenant_isolation" ON audit_log
    USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
CREATE POLICY "tenant_isolation_insert" ON audit_log
    FOR INSERT
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
```

Note that after 0011 the SELECT-side `tenant_isolation` policy moves to full-command form (because 0011 drops and recreates it without a `FOR` clause), which is consistent with every other tenant-scoped table in the schema.

### Why audit_log got its own migration instead of folding into 0009

Two practical reasons:

1. **Scope separation.** 0008 was part of Batch A (Round 1 critical fixes), authored before the broader Phase 1 RLS audit. Folding the `audit_log` fix into 0009 would have delayed the audit-trail repair by a week.
2. **Shape.** For `audit_log` the fix was narrow — add a `FOR INSERT` policy, leave the SELECT policy alone. For the other Phase 1 tables, the fix was broad — replace the whole `FOR SELECT USING` with the full-command form. Two different shapes, two different migrations.

The net result after 0011 is that both forms converge on the canonical full-command pattern, plus `audit_log` retains its dedicated `FOR INSERT` policy as belt-and-braces — no harm in overlapping policies because PostgreSQL OR's them.

---

## 6. Policy Rename (Migration 0012)

**Commit:** `72689b9` — `chore(migrations): 0012 rename service_role_bypass → service_bypass`
**Migration:** `backend/nexus/migrations/versions/0012_rename_service_role_bypass.py`

### What was broken (mechanism)

Root `CLAUDE.md` canonicalises the bypass policy name as `service_bypass`. Phase 1 tables shipped with that name. But a handful of Phase 2A/2C migrations — Alembic `0004_pipeline_builder`, `0006_question_banks`, and the Supabase SQL migration `20260410000001_phase_2a_job_postings.sql` — shipped with the older variant `service_role_bypass` instead. Both names work at runtime because they compare the same GUC expression; the problem is tooling convergence: the startup RLS completeness check ([Section 7](#7-startup-rls-completeness-check)) expects a single canonical bypass policy name per tenant-scoped table.

This was cosmetic drift at the runtime level but load-bearing for the new static check.

### What's fixed

Migration 0012 drops `service_role_bypass` and recreates it as `service_bypass` on every affected table. The upgrade is idempotent: every DROP uses `IF EXISTS`, and the policy with the new name is also dropped first in case it already exists from a fresh Supabase install provisioned via the canonical SQL migration.

```python
# backend/nexus/migrations/versions/0012_rename_service_role_bypass.py

AFFECTED_TABLES = [
    "pipeline_templates",
    "pipeline_template_stages",
    "job_pipeline_instances",
    "job_pipeline_stages",
    "stage_question_banks",
    "stage_questions",
    "job_postings",
    "job_posting_signal_snapshots",
    "sessions",
]


def upgrade() -> None:
    for table in AFFECTED_TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS "service_role_bypass" ON public.{table}'
        )
        op.execute(
            f'DROP POLICY IF EXISTS "service_bypass" ON public.{table}'
        )
        op.execute(
            f"""
            CREATE POLICY "service_bypass" ON public.{table}
                USING (current_setting('app.bypass_rls', true) = 'true')
            """
        )
```

The historical migration files (`0004_pipeline_builder.py`, `0006_question_banks.py`, the Supabase SQL) are **not** edited — migration history is append-only. 0012 adjusts the live database only.

### What 0012 explicitly does NOT do

- It does not add `NULLIF` wrapping to the `service_bypass` policy. That policy compares a text GUC (`app.bypass_rls`) against the literal `'true'`; the empty-string case is already a no-op false. `NULLIF` is purely about the `::uuid` cast on `app.current_tenant`, which `service_bypass` never touches.
- It does not alter the `tenant_isolation` policies on these tables — those were handled by migration 0011.

---

## 7. Startup RLS Completeness Check

**Commit:** `bd83cf7` — `feat(rls): startup RLS completeness check — abort on missing policies`
**Code:** `backend/nexus/app/main.py::_assert_rls_completeness`

### What was broken (mechanism)

Even with 0009/0010/0011/0012 all landed, the system was still one forgetful migration away from a regression. A future Alembic revision could add a new tenant-scoped table and forget to `ENABLE ROW LEVEL SECURITY`, or forget the `WITH CHECK` clause, or use the wrong policy name. Without runtime verification, the regression would silently ship and only be caught if a reviewer noticed the missing SQL. The problem this check solves is "how do you make a partial rollout fail loudly at boot instead of quietly in production."

### What's fixed

`app/main.py` gains an async startup hook, `_assert_rls_completeness`, wired into the FastAPI `lifespan` context manager. On every app boot it:

1. Loads every row from `pg_policies` where `schemaname = 'public'` and `policyname IN ('tenant_isolation', 'service_bypass')`.
2. Cross-references the result against an enumerated list of tenant-scoped tables, `_TENANT_SCOPED_TABLES`.
3. For each expected table, checks:
   - a `tenant_isolation` policy exists,
   - its `with_check` column is non-NULL (i.e. the policy was created with the full-command form — the "`FOR SELECT` trap" is exactly what a NULL `with_check` looks like in `pg_policies`),
   - a `service_bypass` policy exists.
4. If anything is missing, logs CRITICAL with a structured diff of what's missing and raises `RuntimeError`, aborting startup.

Two skip conditions keep tests and bootstrap deployments working:

- **`ENVIRONMENT=test`** — the test suite uses `Base.metadata.create_all`, not real Alembic migrations, so the policies don't exist at the test DB level.
- **`DB_RUNTIME_ROLE` unset** — the role switch is disabled, so every connection runs as `postgres` (BYPASSRLS). There is nothing to enforce, so checking the policies would be misleading. This is the bootstrap configuration before migration 0010 has run.

### The enumerated table list

This list is kept in sync with `app/models.py` and the migration history by hand. Any new tenant-scoped table must be added here explicitly — the check is opinionated on purpose.

```python
# backend/nexus/app/main.py

_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "clients",
    "users",
    "organizational_units",
    "user_role_assignments",
    "user_invites",
    "audit_log",
    "job_postings",
    "job_posting_signal_snapshots",
    "sessions",
    "pipeline_templates",
    "pipeline_template_stages",
    "job_pipeline_instances",
    "job_pipeline_stages",
    "stage_question_banks",
    "stage_questions",
)
```

### The check itself

```python
async def _assert_rls_completeness() -> None:
    if settings.environment == "test":
        return
    if not settings.db_runtime_role:
        return

    from app.database import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            sqlalchemy.text(
                """
                SELECT tablename, policyname, with_check
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND policyname IN ('tenant_isolation', 'service_bypass')
                """
            )
        )
        rows = result.all()

    found_tenant_isolation: dict[str, object] = {}
    found_service_bypass: set[str] = set()
    for tablename, policyname, with_check in rows:
        if policyname == "tenant_isolation":
            found_tenant_isolation[tablename] = with_check
        elif policyname == "service_bypass":
            found_service_bypass.add(tablename)

    missing_isolation: list[str] = []
    missing_check: list[str] = []
    missing_bypass: list[str] = []

    for table in _TENANT_SCOPED_TABLES:
        if table not in found_tenant_isolation:
            missing_isolation.append(table)
        else:
            if found_tenant_isolation[table] is None:
                missing_check.append(table)
        if table not in found_service_bypass:
            missing_bypass.append(table)

    if missing_isolation or missing_check or missing_bypass:
        logger.critical(
            "rls.completeness_check_failed",
            missing_tenant_isolation=missing_isolation,
            missing_with_check=missing_check,
            missing_service_bypass=missing_bypass,
            tenant_scoped_tables=list(_TENANT_SCOPED_TABLES),
        )
        raise RuntimeError(
            "RLS completeness check failed — refusing to start. "
            f"missing tenant_isolation: {missing_isolation!r}; "
            f"tenant_isolation without WITH CHECK: {missing_check!r}; "
            f"missing service_bypass: {missing_bypass!r}. "
            "This means a migration shipped partially-applied RLS — fix "
            "the corresponding migration and redeploy."
        )
```

### What the check does and does not catch

**Catches:**
- Missing `tenant_isolation` policy on an enumerated table.
- `tenant_isolation` policy with a NULL `with_check` (i.e. `FOR SELECT USING`, `FOR INSERT USING`, or any other half-shape).
- Missing `service_bypass` policy.
- A new table added to `_TENANT_SCOPED_TABLES` but not given policies by its migration.

**Does not catch:**
- The raw `::uuid` cast regression from Section 3. `pg_policies` does not expose the predicate expression in a form that's easy to assert on, and a text comparison would be fragile. Relied on code review + `CLAUDE.md` documentation.
- A new tenant-scoped table added to the DB but not added to `_TENANT_SCOPED_TABLES`. The check only verifies what it's told to verify. Keeping the list in sync is on the author of any new migration.

### Verification during the commit

The commit author manually tested the failure path: dropping `tenant_isolation` on `sessions` in the dev DB caused the check to fail with a precise listing; restoring the policy made it pass. This is the kind of check that's only valuable if it has actually been exercised in the failure direction.

---

## 8. JWT Hardening

**Commits:**
- `380fbf2` — `fix(auth): tighten JWT verification — ES256 only + audience/issuer check` (original hardening)
- `c79682d` — `fix(auth): repair two regressions from Batch G — issuer mismatch + CORS on 401` (follow-up)

**Code:** `backend/nexus/app/modules/auth/service.py::verify_access_token`

### What was broken (mechanism)

Before Batch G, `verify_access_token` was laxer than it needed to be:

1. **Algorithm allowlist included RS256.** Supabase's GoTrue signs access tokens with ES256 only. Accepting RS256 as well widens the attack surface — if any JWKS entry were ever compromised and re-used under a different alg, PyJWT would accept it. There is never a legitimate RS256 Supabase access token in this stack, so accepting RS256 is pure downside.
2. **No audience check.** Supabase signs user tokens with `aud = "authenticated"` as a GoTrue invariant. Not checking it meant that any token PyJWT could verify via the JWKS path would authenticate, regardless of what audience it was minted for.
3. **No issuer check.** The JWKS URL is scoped to a specific Supabase project, but PyJWT doesn't enforce that by itself. A token minted by a different Supabase project that happened to share the same JWKS hosting path (or a compromised JWKS entry from a different Supabase deployment) would have authenticated. Binding `iss` to the expected `{supabase_url}/auth/v1` closes that window.

### What's fixed

Commit `380fbf2` tightens `verify_access_token` on all three axes; commit `c79682d` fixes a deployment-environment regression where the derived issuer didn't match what Supabase local under Docker was actually advertising.

### The algorithm pin

```python
# backend/nexus/app/modules/auth/service.py (verify_access_token)

decode_kwargs: dict = {
    "algorithms": ["ES256"],
    "audience": "authenticated",
    "options": {"verify_exp": True, "verify_aud": True},
}
```

`algorithms=["ES256"]` is a hard allowlist. Any token with `alg` != `ES256` fails verification regardless of JWKS content. This closes the "alg confusion" family of attacks where a compromised JWKS entry under a different alg could be accepted. The `options={"verify_aud": True}` flag makes the audience check a hard failure rather than a soft warning.

### The audience check

`aud = "authenticated"` is a literal string, not a setting. Supabase GoTrue stamps this claim on every access token for a logged-in user; it is *not* a per-deployment value. The check is performed inside PyJWT's `jwt.decode` via the `audience` kwarg. A token with any other audience — `"service_role"`, `"anon"`, a custom value — is rejected.

### The issuer check

```python
expected_issuer: str | None = None
if settings.supabase_jwt_issuer:
    expected_issuer = settings.supabase_jwt_issuer
elif settings.supabase_url:
    expected_issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
if expected_issuer:
    decode_kwargs["issuer"] = expected_issuer
```

The check is layered:

1. **Explicit override via `SUPABASE_JWT_ISSUER` env var.** This exists because of the Batch G regression — see below.
2. **Derived from `SUPABASE_URL`** otherwise. On Supabase Cloud, the backend's network-reachable Supabase URL and the issuer GoTrue advertises are the same string, so `{supabase_url}/auth/v1` matches the `iss` claim exactly.
3. **Disabled** if both are empty — only safe in tests / JWKS-mocked CI.

What this prevents: a token signed by a different Supabase project that shares access to (or compromised) our JWKS hosting path would carry a different `iss` claim, and would fail verification even with a valid signature.

### The c79682d regression fix — why it had to exist

Commit `380fbf2` introduced the issuer check, which broke Supabase-local-under-Docker immediately. The reason: in Supabase local, the backend container reaches Supabase via `host.docker.internal:54321` (the Docker bridge), so `SUPABASE_URL=http://host.docker.internal:54321`. But Supabase's GoTrue process, which runs as a separate container, stamps `iss` with `http://127.0.0.1:54321/auth/v1` — its *own* self-view. The network-reachable URL and the issuer GoTrue advertises diverge.

The derived issuer `{supabase_url}/auth/v1` therefore becomes `http://host.docker.internal:54321/auth/v1`, which is **not** what any token actually carries. Every request fails with "Invalid issuer".

The fix introduces `SUPABASE_JWT_ISSUER` as an explicit override. In Supabase local dev, operators set `SUPABASE_JWT_ISSUER=http://127.0.0.1:54321/auth/v1` to match what GoTrue actually stamps. In Supabase Cloud deployments the two URLs coincide, so the override is unnecessary and the derived fallback path works.

### JWKS caching

`PyJWKClient(settings.supabase_jwks_url, cache_keys=True)` is a module-level singleton. Keys are fetched once per process lifetime; the client handles key rotation internally by refreshing when it sees a `kid` it doesn't recognise. This is unchanged from before Batch G — not a fix, but important context for the "JWT section" story.

### Candidate JWT path — unchanged but documented

`verify_candidate_token` is a separate function with its own algorithm hardcoded to `HS256`. It is **not** read from config:

```python
# backend/nexus/app/modules/auth/service.py (verify_candidate_token)

def verify_candidate_token(token: str) -> CandidateTokenPayload | None:
    """Verify a single-use candidate session JWT.

    Signing algorithm is hardcoded to HS256 as a policy decision — never
    read from config. A misconfigured environment variable must not be
    able to weaken verification (e.g., accept 'none' or swap to a weaker
    HMAC variant).
    """
    try:
        payload = jwt.decode(
            token,
            settings.candidate_jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )
        ...
```

The hardcoding is a deliberate policy decision: a signing algorithm must never be a deployment-flag-switchable value. Batch A (commit `5f4fb02`) also deleted the `candidate_jwt_algorithm` config setting and removed the `"change-me-candidate-secret"` default, so `CANDIDATE_JWT_SECRET` must now be set explicitly in any non-test environment — enforced by a `@field_validator` on `Settings.candidate_jwt_secret` that raises if the value is empty outside `ENVIRONMENT=test`.

### What's still missing

Candidate JWT **single-use enforcement** is a TODO in `app/middleware/auth.py`. See [Section 15](#15-remaining-pre-phase-3-hardening). This is not exploitable today because every `/api/candidate-session/*` endpoint is still stubbed, but it must land before any Phase 3 session endpoint ships.

---

## 9. CORS-on-401 Fix

**Commit:** `c79682d` — `fix(auth): repair two regressions from Batch G — issuer mismatch + CORS on 401`
**Code:** `backend/nexus/app/main.py::create_app`

### What was broken (mechanism)

Pre-Batch-G, the FastAPI app factory added middleware in this order:

```python
application.add_middleware(CORSMiddleware, ...)
application.add_middleware(TenantMiddleware)
application.add_middleware(AuthMiddleware)
```

Starlette's `add_middleware` inserts at position 0, so the **last added** is the **outermost** layer. Under that order, `AuthMiddleware` was the outermost layer. When auth short-circuited a request with a 401 `JSONResponse`, the response bypassed `CORSMiddleware` entirely — it never traversed the CORS layer on the way out, so it carried no `Access-Control-Allow-Origin` header. Browsers then blocked the response as a CORS violation and surfaced it client-side as `TypeError: Failed to fetch`. The dashboard saw what looked like network failures instead of ordinary HTTP 401s, and `ApiError` never reached the error-handling code in `lib/api/client.ts`.

Pre-Batch-G this was mostly invisible because tokens validated successfully and 401s were rare. Once `380fbf2` tightened JWT verification, 401s became common (especially in dev, where the Docker issuer mismatch triggered one per request) and the symptom finally surfaced.

### What's fixed

Reorder the middleware so that `CORSMiddleware` is added **last**, making it the outermost layer:

```python
# backend/nexus/app/main.py (create_app)

from app.middleware.auth import AuthMiddleware
from app.middleware.tenant import TenantMiddleware

application.add_middleware(TenantMiddleware)
application.add_middleware(AuthMiddleware)

# CORS goes LAST so it ends up as the outermost middleware. Always use
# the explicit settings.cors_origins list. A wildcard
# (`allow_origins=["*"]`) combined with `allow_credentials=True` is
# rejected by all modern browsers, so the old "debug = wildcard"
# shortcut never actually worked for credentialed requests — it only
# masked configuration mistakes.
application.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-correlation-id"],
)
```

With this order, every response — including auth rejections from `AuthMiddleware` — traverses `CORSMiddleware` on the way out and picks up `Access-Control-Allow-Origin`. The dashboard can now read 401 error detail via the normal `ApiError.status === 401` branch.

A secondary cleanup in Batch A (commit `5f4fb02`) had already removed the old `allow_origins=["*"]` + `allow_credentials=True` combo that browsers reject anyway — the old "debug = wildcard" shortcut never actually worked for credentialed requests. It only masked configuration mistakes.

---

## 10. SSE RLS Fix

**Commit:** `bd4b6bb` — `fix(sse): route SSE sessions through get_tenant_session (RLS enforcement)`
**Code:** `backend/nexus/app/modules/jd/sse.py`, `backend/nexus/app/modules/question_bank/sse.py`

### What was broken (mechanism)

Both SSE generators — `jd/sse.py::job_status_event_generator` and `question_bank/sse.py::stream_question_bank_status` — were opening DB sessions the wrong way. Before this fix they used `async_session_factory()` directly, a raw factory call that does not run `_apply_runtime_role`, does not set `app.current_tenant`, and does not even wrap the session in an explicit `session.begin()` block:

```python
# Pre-fix (approximate shape):
async with async_session_factory() as db:
    await db.execute(sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
    event = await get_job_status(db, job_id)
```

Two overlapping bugs:

1. **No role switch.** Because `_apply_runtime_role` was never called, the SSE connection stayed on the `postgres` role (`rolbypassrls=true`), and every `tenant_isolation` policy on the tables it touched was a silent no-op. RLS was bypassed on streaming paths entirely. Under Batch E/F RLS hardening this became a live exploit window: a crafted `jobId` / `stageId` in the URL that slipped past the router's `require_*_access` dependency would return cross-tenant rows because the database layer was not filtering.
2. **Implicit transactions.** Without `session.begin()`, `SET LOCAL app.current_tenant` and the subsequent `SELECT` could land in *separate* implicit transactions under SQLAlchemy's autobegin. `SET LOCAL` is scoped to the transaction that issues it; if the cursor implicitly committed after the `SET`, the `SELECT` would run with `app.current_tenant` reset and RLS — if it had been enforced — would see an empty GUC and return zero rows. Latent bug masked by bug #1.

### What's fixed

Both generators now use `get_tenant_session(tenant_id)` per poll iteration. That context manager is the same one used by `get_tenant_db` but decoupled from the FastAPI request: it opens a session, calls `session.begin()`, runs `_apply_runtime_role` (which issues `SET LOCAL ROLE nexus_app`), and sets `SET LOCAL app.current_tenant`, all inside a single explicit transaction.

```python
# backend/nexus/app/database.py

@asynccontextmanager
async def get_tenant_session(tenant_id: str) -> AsyncGenerator[AsyncSession]:
    """Yield a session with RLS tenant context set."""
    safe_tenant_id = _coerce_tenant_id(tenant_id)
    async with async_session_factory() as session:
        async with session.begin():
            await _apply_runtime_role(session)
            await session.execute(
                sqlalchemy.text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
            )
            yield session
```

### jd/sse.py — now correct

```python
# backend/nexus/app/modules/jd/sse.py

async def job_status_event_generator(
    tenant_id: str,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    safe_tenant_id = str(uuid.UUID(str(tenant_id)))
    last_status: str | None = None
    last_enrichment_status: str | None = None
    while True:
        if await request.is_disconnected():
            return

        # get_tenant_session handles SET LOCAL ROLE nexus_app + SET LOCAL
        # app.current_tenant inside a single explicit transaction. Opening
        # the session raw via async_session_factory() would skip the role
        # switch and RLS would be silently bypassed on streaming paths.
        async with get_tenant_session(safe_tenant_id) as db:
            event = await get_job_status(db, job_id)

        if event is None:
            return
        ...
```

### question_bank/sse.py — also fixed a pool-hold bug

In `question_bank/sse.py` the generator not only switched to `get_tenant_session` but also moved event yields **outside** the session block. Before the fix, it yielded SSE events from inside the session context, holding an asyncpg pool slot across every `yield` — 15-20 orphaned browser tabs could pin the pool and block all other requests. Now collected events are appended to `events_to_emit` inside the session, and the session is released before the yields happen:

```python
# backend/nexus/app/modules/question_bank/sse.py

events_to_emit: list[str] = []
should_terminate = False
...

async with get_tenant_session(safe_tenant_id) as db:
    # ... load pipeline, stages, banks, questions, build events_to_emit ...
    pass

# Session released — yield events without holding a pool slot.
for ev in events_to_emit:
    yield ev
```

Tenant-ID coercion via `uuid.UUID(str(tenant_id))` appears at the top of both generators as a defense-in-depth match against `get_tenant_db`'s `_coerce_tenant_id` — a malformed tenant claim fails fast with a canonicalisation error rather than landing in the `SET LOCAL` string.

### Cross-reference

- Audit round 2 C1 (SSE RLS bypass) and I4 (question_bank/sse missing explicit session.begin) were the original findings that drove this fix.
- `backend/nexus/CLAUDE.md` — the "SSE → get_tenant_session" rule is now codified in the `jd` and `question_bank` module responsibility tables.
- Phase 2C.2 Section 11 (SSE walkthrough) flags this behaviour as a Batch F lesson and cross-links here.

---

## 11. Security Headers

**Commit:** `f9dc628` — `fix(security): add baseline security headers to both surfaces`
**Code:** `frontend/app/next.config.ts`, `frontend/admin/next.config.ts`

### What was broken (mechanism)

Neither Next.js surface was setting any security headers. Missing headers don't "break" anything the way an RLS bug does, but they leave standard browser-level mitigations unused:

- **Clickjacking** — no `X-Frame-Options`, so the dashboard could be loaded in an `<iframe>` and a candidate could be tricked into clicking through a ghost UI.
- **MIME sniffing** — no `X-Content-Type-Options: nosniff`, so a browser might treat an uploaded asset as executable JavaScript based on content inspection.
- **Referrer leakage** — no `Referrer-Policy`, so full URLs (including query-string tokens) would leak to any cross-origin link clicked from the app.
- **Feature policy** — no `Permissions-Policy`, so any embedded script could activate camera/mic/geolocation without same-origin scoping.

### What's fixed

Both surfaces now emit four headers on every response via the Next.js `headers()` handler in their respective `next.config.ts`. The configurations are intentionally symmetric except for the `Permissions-Policy` feature list.

#### Dashboard app (`frontend/app/next.config.ts`)

```typescript
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=()",
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
      },
    ];
  },
};
```

The dashboard's `Permissions-Policy` permits camera and microphone from same-origin (`camera=(self), microphone=(self)`) because candidate interview sessions will need them for Phase 3; `geolocation=()` is an empty allowlist — blocked entirely.

#### Admin app (`frontend/admin/next.config.ts`)

```typescript
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=()",
  },
];
```

The admin app has no legitimate need for camera, mic, or geolocation and denies all three explicitly.

### What's NOT set

**CSP is deliberately out of scope for this batch.** Setting a meaningful Content Security Policy requires nonce wiring across server components, inline scripts (Next.js bootstrap, React Query devtools, Tailwind v4), and third-party SDKs (Supabase, LiveKit). That work is a planned follow-up. Until then, the four headers above give clickjacking, MIME-sniffing, referrer-leak, and feature-policy defense.

### Cross-reference

- `frontend/app/CLAUDE.md` — "Security" section lists these headers as canonical and notes CSP as a pending follow-up.
- `frontend/admin/CLAUDE.md` — same.

---

## 12. Configurable FRONTEND_BASE_URL

**Commit:** `07cf0b6` — `fix(config): configurable FRONTEND_BASE_URL for invite links`
**Code:** `backend/nexus/app/config.py`, `backend/nexus/app/modules/admin/service.py`, `backend/nexus/app/modules/settings/router.py`

### What was broken (mechanism)

Invite-link construction was hardcoded with a `debug ? localhost : app.projectx.com` ternary:

```python
# Pre-fix shape:
base_url = "http://localhost:3000" if settings.debug else "https://app.projectx.com"
invite_url = f"{base_url}/invite?token={raw_token}"
```

This branching silently sent staging invites to production. Any staging deploy that runs with `DEBUG=false` (which is the correct value for a non-local environment) would mint invite links pointing at `app.projectx.com` — i.e., production. A new team member invited from a staging tenant would click through to production, land on a login page for a tenant that doesn't exist there, and get a confused error. The error surfaces as "invalid invite" even though the invite token was perfectly valid in staging.

### What's fixed

Introduce `FRONTEND_BASE_URL` as a first-class config setting. Every environment must set it explicitly in `.env`; the default only exists for local development:

```python
# backend/nexus/app/config.py

# Frontend base URL — used to build invite/confirmation links in emails.
# Previously hardcoded with a `debug ? localhost : app.projectx.com`
# ternary, which meant a staging deploy with DEBUG=false would mint
# invite links that point at production. Now every environment must
# set FRONTEND_BASE_URL explicitly.
frontend_base_url: str = "http://localhost:3000"

@field_validator("frontend_base_url")
@classmethod
def _strip_trailing_slash(cls, v: str) -> str:
    return v.rstrip("/")
```

The `@field_validator` strips trailing slashes so concatenation produces clean URLs regardless of operator input — `https://staging.projectx.com/` and `https://staging.projectx.com` both produce `.../invite?token=...`.

### Call sites

Every invite-URL construction now reads `settings.frontend_base_url`:

```python
# backend/nexus/app/modules/admin/service.py (line 57)
invite_url = f"{settings.frontend_base_url}/invite?token={raw_token}"

# backend/nexus/app/modules/settings/router.py (lines 38, 83, 135)
invite_url = f"{settings.frontend_base_url}/invite?token={raw_token}"
```

Four call sites in total: `admin.provision_client` (Company Admin invite), `settings.create_team_invite`, `settings.resend_team_invite`, and `settings.resend_company_admin_invite`. None of them branch on `settings.debug` any more.

### Deployment requirement

`.env.example` ships with the placeholder and a comment; every dev/staging/prod `.env` must set `FRONTEND_BASE_URL` explicitly. The default of `http://localhost:3000` is convenient for local development but wrong in every other environment. Forgetting to set it in staging or production would mean invite links point at localhost — a different kind of broken, but at least loud rather than silent.

### Cross-reference

`backend/nexus/CLAUDE.md` "Invite / confirmation link URLs" subsection documents the pattern and calls out the debug-branching pitfall explicitly.

---

## 13. Correlation-ID Header Validation

**Commit:** `5aa27ef` — `fix(jd): validate x-correlation-id header before propagating to logs`
**Code:** `backend/nexus/app/modules/jd/router.py::_get_correlation_id`

### What was broken (mechanism)

The JD router used to trust the inbound `x-correlation-id` header verbatim at five call sites:

```python
# Pre-fix:
correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
```

The correlation ID flows into structlog records, Langfuse trace tags, and Dramatiq actor kwargs. Because none of those sinks validate their inputs — structlog serialises arbitrary strings, Langfuse accepts them as tag values, Dramatiq pickles them into the broker payload — an attacker-controlled header could inject unbounded or non-printable content into observability pipelines. A 1MB `x-correlation-id` header would bloat every subsequent log line and trace event; embedded newlines or ANSI escape sequences could poison structured log consumers; embedded null bytes could break string-based log aggregators.

This is log-injection, the same class of bug that motivates `printf(user_input)` warnings.

### What's fixed

A local helper `_get_correlation_id` validates the inbound header before propagating it, and falls back to a fresh `uuid4` for any invalid value:

```python
# backend/nexus/app/modules/jd/router.py

# Max length for an inbound x-correlation-id header. 128 is generous — uuid4
# is 36 chars — but caps log-field growth and blocks pathological values.
_MAX_CORRELATION_ID_LEN = 128


def _get_correlation_id(request: Request) -> str:
    """Extract x-correlation-id or mint a fresh uuid4.

    The header is untrusted input, so we validate before propagating it to
    logs, Langfuse tags, and actor kwargs:
      - Non-empty
      - ≤ 128 characters
      - ASCII only
      - Printable

    Invalid values are discarded and replaced with a fresh uuid4 so a
    forensic trail is still preserved per-request.
    """
    raw = request.headers.get("x-correlation-id")
    if raw and 0 < len(raw) <= _MAX_CORRELATION_ID_LEN and raw.isascii() and raw.isprintable():
        return raw
    return str(uuid.uuid4())
```

Four validations in one expression:

1. **Non-empty** — any empty or missing header falls through to uuid4.
2. **Length ≤ 128** — 128 chars is generous (uuid4 is 36) but caps log-field growth against pathological inputs.
3. **ASCII only** (`raw.isascii()`) — blocks Unicode escapes, high-byte sequences, and anything a terminal renderer might interpret as control.
4. **Printable** (`raw.isprintable()`) — blocks newlines, tabs, null bytes, ANSI escapes, and every other control character. A valid uuid4 is trivially printable.

Every invalid value is silently replaced with a fresh uuid4, so a forensic trail is still preserved per-request — the caller just doesn't get to choose the ID.

### Call sites

All five inline `request.headers.get("x-correlation-id", ...)` call sites in `jd/router.py` are converted to `_get_correlation_id(request)`:

- `create_job` — the initial JD submission
- `retry_failed_extraction` — retry from the extraction failure state
- `save_signals` (PATCH) — signal edit
- `confirm_signals` (POST) — signal confirmation
- `trigger_reenrichment` (POST) — Call 2 re-enrichment

Each call site also forwards the validated correlation_id to `_safe_dispatch_extraction` / `_safe_dispatch_reenrichment` (the FastAPI BackgroundTasks wrappers that enqueue the Dramatiq actors) via keyword argument — the validation happens exactly once per request.

### Not validated yet

Other routers (`question_bank`, `pipelines`, `settings`, `admin`) don't currently read `x-correlation-id` headers — they don't need validation because they're not propagating untrusted input. If a future change adds correlation-ID extraction to another router, it must use the same helper (or a shared helper moved to a common place).

---

## 14. Misc Fixes

Short-form notes on the non-RLS, non-auth hardening commits. Each one is small enough to describe in a paragraph but each closed a distinct failure mode.

### 14.1 Same-origin redirect allowlist on invite completion

**Commit:** `b23f6df` — `fix(invite): allowlist same-origin redirect after completion`
**Code:** `frontend/app/app/(auth)/invite/page.tsx`

The invite completion handler receives a `redirect_to` field from the backend's `POST /api/auth/complete-invite` response and navigates to it on success. The only legitimate values are `"/"` and `"/onboarding"`, but nothing in the client validated that. A compromised or MITM'd response could return `"https://evil.com"` or `"//evil.com"` and the freshly-authenticated user would be redirected off-site to an attacker-controlled origin — an open-redirect exploit against a just-authenticated session.

The fix validates the redirect as same-origin by shape: starts with `/`, does not start with `//` (which browsers treat as a protocol-relative URL):

```typescript
// frontend/app/app/(auth)/invite/page.tsx

// Guard against open-redirect — the backend-returned `redirect_to`
// must be a same-origin relative path. The only legitimate values
// today are `/` and `/onboarding`; a malicious/MITM'd response
// sending `https://evil.com` or `//evil.com` would otherwise
// navigate a freshly-authenticated user off-site.
const safeRedirect = result.redirect_to?.startsWith("/") &&
  !result.redirect_to.startsWith("//")
  ? result.redirect_to
  : "/";
router.push(safeRedirect);
router.refresh();
```

The `frontend/app/CLAUDE.md` "Security" section codifies this pattern: "Post-auth redirects must be allowlisted. Any `router.push(urlFromBackend)` where the URL is controlled by a mutation response must validate that the value starts with `/` (and does not start with `//`) before navigating."

### 14.2 Jobs list query key discipline

**Commit:** `1369b42` — `fix(jobs): narrow list query key to avoid clobbering detail caches`
**Code:** `frontend/app/app/(dashboard)/jobs/page.tsx`

The jobs list used `useQuery({ queryKey: ['jobs'], ... })`. TanStack Query's `invalidateQueries` does prefix matching by default, so any `invalidateQueries({ queryKey: ['jobs', jobId] })` from elsewhere in the app also invalidated `['jobs']`. Every save/confirm/enrich/SSE event on any job detail page was silently blowing the jobs list cache and forcing a refetch.

The fix renames the list key from `['jobs']` to `['jobs-list']`:

- `['jobs-list']` — the list query only.
- `['jobs', jobId]` — that specific job's detail only.

Invalidations at detail-page sites unchanged; the delete mutation's `onSuccess` is updated to invalidate the new key. `frontend/app/CLAUDE.md` "State Management" section documents the discipline explicitly: "list endpoints use distinct keys from their detail siblings."

### 14.3 Focus management on pipeline dialogs

**Commit:** `dd2f528` — `fix(a11y): focus management on pipeline dialogs`
**Code:** `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`, `.../TemplatePickerDialog.tsx`

WCAG 2.4.3 compliance: when a modal opens, keyboard focus must move into the dialog instead of staying on the trigger element. Both dialogs in the pipeline builder were failing this.

Fix: use a `ref` + `useEffect` pattern on mount.

- `StageConfigDrawer` focuses the name input on mount. The parent renders the drawer conditionally, so mount-time equals open-time.
- `TemplatePickerDialog` focuses the close button on open. The template cards load asynchronously, so focusing one would be racy; the close button is always present.

`frontend/app/CLAUDE.md` "Accessibility" section codifies the pattern: "Dialogs and drawers must move focus on open. Use a `ref` + `useEffect(() => { if (open) ref.current?.focus() }, [open])` pattern."

### 14.4 Stable keys — `PipelineFunnel` and `EditableChipRow`

**Commits:** `475df30` — `fix(pipeline): stable key for PipelineFunnel stages`; `9dac616` — `fix(jd-panels): stable key for EditableChipRow`

Both components were using a composite key based on array index + label (`${i}-${stage.name}`), which aliases into each other on reorder or rename and causes spurious remounts — losing form state, animation state, focus.

- `PipelineFunnel.tsx` — key is now `stage.id ?? scratch-${i}`, so saved stages use their stable UUID; only unsaved scratch stages fall back to array index. Widens the prop type to accept stages with optional `id` for backwards compatibility with both the scratch template form and the saved template edit form.
- `EditableSignalsPanel.tsx` (`EditableChipRow`) — key is now `${realIndex}-${item.value}`, composing array index with signal value. `SignalItem` has no server-assigned UID, so this composite is the best available stable identity; `realIndex` is the live post-reorder position, not the original index.

### 14.5 `confirm_signals` — `PipelineAlreadyExistsError` idempotent

**Commit:** `a7ba2ea` — `fix(jd): treat PipelineAlreadyExistsError as idempotent in confirm_signals`
**Code:** `backend/nexus/app/modules/jd/service.py::confirm_signals`

`confirm_signals` calls `auto_apply_pipeline_on_confirmation` after the status transition. Pre-fix, every auto-apply failure was buried under a single error log + audit event, including the very common `PipelineAlreadyExistsError` that fires on every re-confirm of a job that already has a pipeline. Real failures were drowning in the noise floor.

Fix: catch `PipelineAlreadyExistsError` separately at debug level; every other exception still logs at error and writes a `job_pipeline.auto_apply_failed` audit event.

```python
# backend/nexus/app/modules/jd/service.py (confirm_signals)

try:
    from app.modules.pipelines.errors import PipelineAlreadyExistsError
    from app.modules.pipelines.service import auto_apply_pipeline_on_confirmation

    await auto_apply_pipeline_on_confirmation(
        db, job=job, actor_id=actor_id,
    )
except PipelineAlreadyExistsError:
    logger.debug(
        "jd.pipeline_auto_apply_skipped_existing",
        job_posting_id=str(job.id),
        reason="pipeline_already_exists",
    )
except Exception as exc:
    logger.error(
        "jd.pipeline_auto_apply_failed",
        job_posting_id=str(job.id),
        exc_info=exc,
    )
    # ... audit event write ...
```

Cross-reference: Phase 2C.1 Section 6 walks the auto-apply contract end-to-end, including the `PipelineAlreadyExistsError` idempotency case.

### 14.6 `question_bank` actor — commit scoping on failure

**Commit:** `1a0b847` — `fix(question_bank): only commit 'failed' status on actor exception`
**Code:** `backend/nexus/app/modules/question_bank/actors.py`

`generate_question_bank_stage` used to call `db.commit()` in its catch-all except clause regardless of whether the bank was actually transitioned to `'failed'` inside `_generate_one_bank`. If an exception originated outside `_generate_one_bank`'s own except branch (e.g. a DB outage between the LLM call and the status write, or a bug upstream), the actor was committing partially-written state.

Fix: commit only when `bank.status == "failed"`. Otherwise log a warning with the observed status and roll back so Dramatiq can retry or dead-letter cleanly:

```python
# backend/nexus/app/modules/question_bank/actors.py

except Exception:
    # Only commit 'failed' status if we actually transitioned to failed
    # inside _generate_one_bank's except branch. An exception from
    # anywhere else (a DB outage between the LLM call and the status write,
    # or a bug higher up in the stack) would commit partially-
    # written state. Roll back and re-raise so Dramatiq can retry
    # or dead-letter the task cleanly.
    if bank.status == "failed":
        await db.commit()
    else:
        logger.warning(
            "question_bank.stage_actor_rollback",
            bank_id=str(bank.id),
            bank_status=bank.status,
            reason="exception_outside_failed_transition",
        )
        await db.rollback()
    raise
```

### 14.7 `list_banks` GET read-idempotent

**Commit:** `23e78bc` — `fix(question_bank): make list_banks GET read-idempotent`
**Code:** `backend/nexus/app/modules/question_bank/router.py`, `.../schemas.py`

`GET /api/jobs/{id}/banks` used to call `ensure_bank_exists()` in a loop, writing a draft `StageQuestionBank` row for every stage on every request. An 8-stage pipeline leaked 8 rows per poll — violating HTTP GET semantics and slowly filling `stage_question_banks` with placeholder drafts that the UI never touched.

Fix: return a real `BankResponse` for stages that already have a bank row, and a synthetic `PlaceholderBankResponse` (with `status="not_generated"`) for stages that don't. The `POST /questions/generate` path is still the only legal write and still calls `ensure_bank_exists` lazily on first generation request. A regression test builds a 3-stage pipeline with zero banks, hits GET twice, and confirms the bank count stays at zero.

Cross-reference: Phase 2C.2 walkthrough describes the read-idempotent list contract in its bank-state-machine section.

### 14.8 `use-job-status-stream` — absolute reconnect ceiling

**Commit:** `4dc26b9` — `fix(sse): add absolute reconnect ceiling to useJobStatusStream`
**Code:** `frontend/app/lib/hooks/use-job-status-stream.ts`

`useJobStatusStream` already had `MAX_AUTH_RETRIES=2` to prevent infinite auth loops when the refresh token is expired. But non-auth reconnect paths — transient `onerror` bubbles, server 5xx, library-internal retry storms — had no absolute cap. A persistently broken upstream could drive the hook to reconnect indefinitely.

Fix: introduce `MAX_TOTAL_RETRIES = 20`, an absolute ceiling counted across every reconnection path:

```typescript
// frontend/app/lib/hooks/use-job-status-stream.ts

/** Absolute ceiling on reconnection attempts across the effect's lifetime,
 *  counting auth retries, transient errors, and every other reason a
 *  connect() might recurse. Once hit, the stream stops permanently and
 *  the hook surfaces an error — TanStack Query's polling fallback on
 *  useJob() still keeps the page usable. */
const MAX_TOTAL_RETRIES = 20
```

Both the transient `onerror` path (inside `fetchEventSource`) and the outer `connect()` recursion path increment `totalRetries`. When the ceiling is hit, the stream aborts permanently, `isStreaming` flips to false, and the hook surfaces `'Live updates unavailable — reconnection limit reached.'`. TanStack Query polling on `useJob()` still keeps the page functional; only live updates are lost.

### 14.9 `use-questions-status-stream` — ref-mirroring across stage changes

**Commit:** `2dfa766` — `fix(sse): stabilize useQuestionsStatusStream connection across stage changes`
**Code:** `frontend/app/lib/hooks/use-questions-status-stream.ts`

`useQuestionsStatusStream` took `selectedStageId` as an argument and used it inside `onmessage` to decide whether to invalidate the bank-detail cache. Pre-fix, `selectedStageId` was both closure-captured and listed in the `useEffect` dep array. Every stage click tore down and reopened the SSE connection (which is expensive, and causes a brief gap in live updates each time the user clicks a stage).

Fix: mirror `selectedStageId` into a ref. The effect dep array drops it; the `onmessage` handler reads the ref, which is always current. The SSE connection is now stable across stage selections.

```typescript
// frontend/app/lib/hooks/use-questions-status-stream.ts

// Mirror selectedStageId into a ref so the onmessage handler below reads
// the latest value without re-running the effect. Without this, every
// stage selection tears down + reopens the SSE connection (because
// selectedStageId would be in the dep array), which is wasteful and
// causes a brief gap in live updates each time the user clicks a stage.
const selectedStageIdRef = useRef(selectedStageId)
useEffect(() => {
  selectedStageIdRef.current = selectedStageId
})
```

Cross-reference: `frontend/app/CLAUDE.md` "Tech Stack" section now calls out that both SSE hooks use a ref-mirroring pattern and that `useJobStatusStream` also caps total reconnect attempts via `MAX_TOTAL_RETRIES`.

---

## 15. Remaining Pre-Phase-3 Hardening

### 15.1 Candidate JWT single-use enforcement (blocker)

**Status:** TODO in `backend/nexus/app/middleware/auth.py`.
**Urgency:** Must land before any Phase 3 session endpoint (`/start`, `/consent`, `/transcript`, `/token`) becomes live.

`verify_candidate_token` currently only checks signature + expiry + the hardcoded HS256 algorithm. It does **not** mark the token as used on first verification, so a candidate JWT that leaks in a URL (history, analytics, referrer) can be replayed until it expires. This is explicitly not exploitable **today** because every `/api/candidate-session/*` endpoint is still a stubbed `not_implemented`, but that will change the moment Phase 3 starts shipping session code.

The required fix shape is atomic mark-used on first verification. Two acceptable implementations:

- **Redis-backed.** `SET NX session:<token_id> used EX <ttl>` — atomic across concurrent verifications. Reject if the key already exists. TTL matches token expiry so the set doesn't grow unbounded.
- **Database-backed.** Add a `used_at TIMESTAMPTZ NULL` column to the sessions table and issue `UPDATE sessions SET used_at = now() WHERE id = :id AND used_at IS NULL RETURNING id`. If the UPDATE returns zero rows, the token has already been used — reject with 401.

Either approach must execute atomically with the signature verification in the middleware — there is no safe place to do it in the handler, because the race window between middleware and handler is wide enough to matter.

`backend/nexus/CLAUDE.md` calls this out explicitly in the "Auth Abstraction" section: "Phase 3 prerequisite: candidate-JWT single-use enforcement is currently a TODO (`middleware/auth.py`). Not exploitable today because session endpoints are stubbed, but it MUST land before any Phase 3 session endpoint (start / consent / transcript) becomes live."

### 15.2 CSP (Content Security Policy) — follow-up, not a blocker

Section 11 describes the four baseline security headers already shipped. CSP remains deferred until the wiring work (nonce propagation through server components, inline scripts, and third-party SDKs) can be tackled as its own batch. The absence of CSP does not block Phase 3 — it is a defense-in-depth improvement, not a prerequisite.

### 15.3 `x-correlation-id` validation in other routers — as needed

Section 13 covered the `jd` router. The helper `_get_correlation_id` is currently local to `jd/router.py`. If any future router starts propagating `x-correlation-id` into logs or actor kwargs, it must reuse the same validation logic — either by importing the helper or moving it to a shared location. This is a convention, not a blocker.

---

## 16. Cross-references

- **Phase 1 walkthrough (`docs/phase-1-implementation.md`)** — origin of the Phase 1 tables whose RLS was repaired by 0008, 0009, and 0011. The "Spec drift" call-outs in Section 2 on `clients`/`users`/`organizational_units`/`user_role_assignments`/`user_invites` point here for the full story.
- **Phase 2C.1 walkthrough (`docs/phase-2c1-implementation.md`)** — Section 2 "Spec drift — RLS pattern at ship time vs. canonical form" flags migration 0004's `USING`-only policies, raw `::uuid` cast, and `service_role_bypass` alias. Delegated to Sections 2/3/6 here.
- **Phase 2C.2 walkthrough (`docs/phase-2c2-implementation.md`)** — Section 2 has the same three-way drift flag for migration 0006 (`stage_question_banks`, `stage_questions`); delegates here. Section 11 (SSE walkthrough) points at [Section 10](#10-sse-rls-fix) for the `get_tenant_session` discipline.
- **Root `CLAUDE.md`** — canonical RLS policy pair, `NULLIF` requirement, `FOR SELECT USING` trap, RLS runtime-role rule, `auth.jwt()` warning.
- **`backend/nexus/CLAUDE.md`** — RLS runtime role section ("Load-Bearing"), RLS Pattern section ("Two traps to avoid"), Auth Abstraction section (JWT ES256 + audience + issuer + candidate single-use TODO), Invite / confirmation link URLs subsection (FRONTEND_BASE_URL), Database Migrations section (0008–0012 annotations).
- **`frontend/app/CLAUDE.md`** — Security section (headers + same-origin redirect allowlist), Accessibility section (dialog focus management), State Management section (query key discipline), Tech Stack section (`MAX_TOTAL_RETRIES` + ref-mirroring SSE hooks).
- **`frontend/admin/CLAUDE.md`** — Security headers subset (no camera/mic/geolocation grants).
