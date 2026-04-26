# Tenant Hard Delete — Design

**Status:** Approved, ready for implementation plan
**Author:** ProjectX team
**Date:** 2026-04-26
**Related:** Tenant lifecycle work in [`backend/nexus/CLAUDE.md`], existing soft-delete + block in `app/modules/admin/`

---

## 1. Problem statement

Today the admin app has two destructive states for a tenant:

- **Block** — pause access; reversible from UI; data preserved.
- **Soft delete** — lock out + free admin email; cascade-soft-deletes `users` + revokes pending `user_invites`; not reversible from UI; **operational data is preserved**.

Soft delete does not actually remove customer data. For an enterprise B2B
platform where customers eventually demand GDPR-style "right to be forgotten,"
we need a true purge. This design adds a third operation, **Hard Delete**,
that drops all operational data while preserving the audit trail per
compliance norms.

A grace-period worker (auto-purge after N days) is **deferred to a later
phase** — Hard Delete is a manual operator-driven action for now.

## 2. Goals and non-goals

**Goals**

- Add a Hard Delete admin operation that purges every tenant-scoped row
  except the audit log.
- Preserve audit trail integrity: rows in `audit_log` for the deleted tenant
  must survive, including their `actor_email` denormalization, so forensic
  queries continue to work.
- Best-effort delete of Supabase Auth identities for users in the deleted
  tenant — partial success must be reported, not silently swallowed.
- Two-step destruction: Hard Delete is only callable on already-soft-deleted
  tenants. Forces a pause between "lock them out" and "purge."
- Type-the-tenant-name confirmation, server-side enforced.
- Use database-level cascades (`ON DELETE CASCADE`) so the operation is
  atomic and any future tenant-scoped table inherits the behavior by default.

**Non-goals**

- Grace period / scheduled auto-purge.
- UI-driven restore of a hard-deleted tenant. (Impossible — the data is gone.)
- S3 object cleanup. Phase 3 introduces resumes/recordings; cleanup hooks
  will be added then.
- Redis state cleanup. Phase 3 introduces session state; cleanup hooks
  added then.
- Bulk delete (one tenant at a time).
- Audit log archival to a separate table.

## 3. Lifecycle state model

| State | `blocked_at` | `deleted_at` | Visible in admin? | Reversible from UI? | Frees admin email? |
|---|---|---|---|---|---|
| Active | NULL | NULL | yes | n/a | no |
| Blocked | set | NULL | yes (badge) | yes (Unblock) | no |
| Soft-deleted | any | set | yes (badge) | no — DB only | yes |
| Hard-deleted | — | — | **gone** (row purged) | impossible | yes |

```
Active ─Block─▶ Blocked ─Unblock─▶ Active
   │
   └─Delete──▶ Soft-deleted ─Hard delete──▶ (gone)
```

## 4. Cascade scope — what gets deleted, what survives

**Deleted (via DB-level CASCADE):**

| Category | Tables |
|---|---|
| Tenant root | `clients` (the trigger row) |
| Phase 1 | `users`, `user_invites`, `user_role_assignments`, `roles` (tenant-custom only — system roles have `tenant_id IS NULL`), `organizational_units` |
| Phase 2A | `job_postings`, `job_posting_signal_snapshots`, `sessions` |
| Phase 2C.1 | `pipeline_templates`, `pipeline_template_stages`, `job_pipeline_instances`, `job_pipeline_stages`, `pipeline_stage_participants` |
| Phase 2C.2 | `stage_question_banks`, `stage_questions` |
| Phase 3B | `candidates`, `candidate_job_assignments`, `candidate_stage_progress` |
| Phase 3C | `candidate_session_tokens` |

**Preserved:**

- `audit_log` rows for the deleted tenant — FKs to `clients` and `users` are
  dropped (one-time migration). Rows persist with intact `tenant_id` /
  `actor_id` UUID values; `actor_email` is denormalized in the row, so
  attribution queries continue to work.

**External — best-effort, after the DB cascade commits:**

- Supabase Auth users for every user in the tenant (via Admin API
  `provider.delete_user(id)`). Failures are logged, partial-success state
  is recorded in the audit log, and the response reports counts.
- (Phase 3 hook only) S3 objects, Redis state — not implemented now.

## 5. API surface

```
POST /api/admin/clients/{id}/hard-delete
Headers: Authorization: Bearer <projectx_admin_jwt>
Body: { "confirmation_name": "<exact tenant name>" }

200 → HardDeleteResponse:
        { "client_id": "<uuid>",
          "purged_at": "<iso>",            // computed `now()` on success
          "auth_users_purged": <int>,
          "auth_users_failed": <int> }

403 — caller lacks `is_projectx_admin`
404 — client doesn't exist (or already hard-deleted)
409 — client is not in `deleted` state (must be soft-deleted first)
422 — confirmation_name does not match client.name exactly
```

`HardDeleteResponse` is a **new Pydantic schema** distinct from the
existing `ClientStatusResponse` used by block/unblock/soft-delete — it
carries auth-purge counts and `purged_at` is synthesized at response time
(no DB column persists it; the row is gone).

Body-level validation enforces the typed-name confirmation server-side, so
direct API calls are subject to the same gate as the UI.

The endpoint sits next to the existing block / unblock / delete operations
in `app/modules/admin/router.py`.

## 6. Migration `0023_tenant_hard_delete_cascade`

Two pieces:

**A. Drop audit_log foreign keys.** Audit logs are append-only historical
records — FKs to mutable tables are an anti-pattern in audit design. Without
this drop, hard delete is impossible because `audit_log.tenant_id` and
`audit_log.actor_id` would block the cascade.

```sql
ALTER TABLE audit_log DROP CONSTRAINT audit_log_tenant_id_fkey;
ALTER TABLE audit_log DROP CONSTRAINT audit_log_actor_id_fkey;
```

**B. Convert 14 tenant-scoped FKs to `ON DELETE CASCADE`.** Per
`pg_constraint`, the existing state has these FKs on `clients(id)` without a
cascade clause (default = `NO ACTION`):

| FK constraint | Table | Notes |
|---|---|---|
| `users_tenant_id_fkey` | users | |
| `user_invites_tenant_id_fkey` | user_invites | |
| `user_role_assignments_tenant_id_fkey` | user_role_assignments | |
| `organizational_units_client_id_fkey` | organizational_units | column is `client_id`, not `tenant_id` |
| `roles_tenant_id_fkey` | roles | tenant-custom rows only — system roles have NULL `tenant_id` and won't match |
| `job_postings_tenant_id_fkey` | job_postings | |
| `job_posting_signal_snapshots_tenant_id_fkey` | job_posting_signal_snapshots | |
| `sessions_tenant_id_fkey` | sessions | |
| `pipeline_templates_tenant_id_fkey` | pipeline_templates | |
| `pipeline_template_stages_tenant_id_fkey` | pipeline_template_stages | |
| `job_pipeline_instances_tenant_id_fkey` | job_pipeline_instances | |
| `job_pipeline_stages_tenant_id_fkey` | job_pipeline_stages | |
| `fk_stage_question_banks_tenant` | stage_question_banks | non-standard name (older naming convention) |
| `fk_stage_questions_tenant` | stage_questions | non-standard name |

Pattern per FK: `ALTER TABLE <t> DROP CONSTRAINT <c>; ALTER TABLE <t> ADD
CONSTRAINT <c> FOREIGN KEY (<col>) REFERENCES clients(id) ON DELETE CASCADE`.

The 5 Phase-3 tables already have `ON DELETE CASCADE` and are skipped
(`candidates`, `candidate_job_assignments`, `candidate_stage_progress`,
`candidate_session_tokens`, `pipeline_stage_participants`).

**No change needed for `users(id)` FKs** in tenant-scoped tables. Per
PostgreSQL 17 docs, the default `ON DELETE` is `NO ACTION` (not `RESTRICT`),
which is checked at end-of-statement. Within a single cascading `DELETE`
statement, by the time `NO ACTION` fires its check, every row that would
have referenced the deleted user is also gone (because every such row's
`tenant_id` FK is now `CASCADE`). The constraint trivially passes.

**No change needed for `clients.super_admin_id`.** Already
`DEFERRABLE INITIALLY DEFERRED`; the check fires at commit, by which time
both `clients` and `users` rows are gone.

## 7. Service flow

`app/modules/admin/service.py::hard_delete_client`:

```
1. Load client by id.
2. Reject if state != "deleted" — raise InvalidClientStateError(current, "purged").
3. Reject if confirmation_name != client.name — raise ConfirmationMismatchError.
4. Snapshot for audit:
     - SELECT auth_user_id FROM users WHERE tenant_id = ?
     - record user_count + client name
5. Write audit event `client.hard_deleted` BEFORE the cascade
   (this row's FKs to clients/users still resolve at this moment;
   after step 6 they wouldn't, but per migration 0023 the FKs are dropped).
6. DELETE FROM clients WHERE id = ?  — Postgres unwinds the cascade.
7. Flush + commit (the @asynccontextmanager around get_bypass_db handles this).
8. Best-effort _purge_auth_users(auth_user_ids):
     for each id, await provider.delete_user(id), per-call try/except.
     Returns (purged: list[str], failed: list[(id, reason)])
9. If failed:
     write audit event `client.hard_delete_auth_partial`
     (the FK from audit_log to clients is dropped, so this is allowed
      even though the clients row is gone)
10. Return HardDeleteResponse with purged/failed counts.
```

**Transactional discipline:**
- The pre-cascade audit event AND the `DELETE FROM clients` run in the
  same transaction (managed by `get_bypass_db`). Either both happen or
  neither does.
- After that transaction commits, control returns to the endpoint with
  the DB cascade complete. Supabase Auth purge runs **after** the commit,
  using a fresh DB session for any partial-success audit event.
- Rationale: a network failure to the Supabase Admin API must not roll
  back the DB delete. The DB is the source of truth for "tenant gone."
  Auth-side stragglers are recoverable from the partial-success audit
  event.

**Why best-effort for Supabase Auth?**
- If we tried to make it transactional, a Supabase outage during the call
  would force a rollback of the whole hard delete — leaving the tenant in a
  partially-deleted limbo. That's worse than "DB is gone, auth users will be
  cleaned up later from the partial-state audit log."

## 8. Auth provider purge helper

New helper in `app/modules/admin/service.py`:

```python
async def _purge_auth_users(auth_user_ids: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Best-effort bulk delete of Supabase Auth users.

    Returns (purged, failed). `failed` is [(auth_user_id, reason_str), ...].
    Each call is independently try/excepted so one failure does not abort
    the rest.
    """
    provider = get_auth_provider()
    purged, failed = [], []
    for uid in auth_user_ids:
        try:
            await provider.delete_user(uid)
            purged.append(uid)
        except AuthProviderError as e:
            failed.append((uid, str(e)))
            logger.warning("admin.hard_delete.auth_user_purge_failed", auth_user_id=uid, error=str(e))
    return purged, failed
```

`provider.delete_user` is already idempotent (logs `already_absent` on 404)
per `app/modules/auth/admin/supabase.py:219`. Reusing the existing
provider abstraction means a future Cognito swap is unaffected.

## 9. Audit trail

Two new constants in `app/modules/audit/actions.py`:

```python
CLIENT_HARD_DELETED = "client.hard_deleted"
CLIENT_HARD_DELETE_AUTH_PARTIAL = "client.hard_delete_auth_partial"
```

Pre-cascade event payload:
```json
{ "client_name": "BinQle", "user_count": 1 }
```

Partial-success event payload (only emitted if `failed` is non-empty —
shape uses objects, not tuples, for stable JSON consumers):
```json
{
  "purged": ["uuid", "..."],
  "failed": [{ "auth_user_id": "uuid", "reason": "..." }, ...]
}
```

The service helper returns `(list[str], list[tuple[str, str]])` for
ergonomic Python; the router converts the tuples into the object shape
above before passing them to `log_event`.

Forensic queries that survive a hard delete:
- `SELECT * FROM audit_log WHERE tenant_id = '<uuid>'` — full history of the
  deleted tenant.
- `SELECT actor_email, created_at, payload FROM audit_log WHERE
  action = 'client.hard_deleted' AND resource_id = '<uuid>'` — who deleted
  this tenant, when, with what surface area.

## 10. Admin UI flow

In `frontend/admin/app/(admin)/dashboard/page.tsx`:

1. The actions menu (`⋯`) on a soft-deleted row currently shows a disabled
   button. **Re-enable it** for soft-deleted rows but show only one option:
   **"Permanently delete"** in red. Block / Unblock / Delete remain hidden
   in this state.
2. Click "Permanently delete" → modal:
   ```
   ┌────────────────────────────────────────────────┐
   │ Permanently delete {client_name}?              │
   │                                                │
   │ This will erase:                               │
   │ • All operational data (users, jobs, …)        │
   │ • All Supabase Auth identities                 │
   │ • The tenant record itself                     │
   │                                                │
   │ The audit log will be preserved.               │
   │ This cannot be undone.                         │
   │                                                │
   │ Type {client_name} to confirm:                 │
   │ [______________________________]               │
   │                                                │
   │       [ Cancel ] [ Permanently delete ]        │
   └────────────────────────────────────────────────┘
   ```
3. The "Permanently delete" button stays disabled until the typed input
   exactly matches `client.name`.
4. On 200: row disappears from the list (the GET endpoint already excludes
   purged tenants because the row no longer exists).
5. On 422: red inline error "Confirmation does not match." Do not close
   modal.
6. On 200 with `auth_users_failed > 0`: warning toast "Tenant deleted, but
   N Supabase Auth identities failed to purge — see logs."
7. On 5xx: red inline error with the response detail.

## 11. Error contracts

New typed exceptions in `app/modules/admin/service.py`:

```python
# Already exists in admin/service.py (defined for the soft-delete
# block/unblock state machine). Reused here — `requested="purged"` is
# the new transition value.
class InvalidClientStateError(Exception): ...

# New.
class ConfirmationMismatchError(Exception):
    """Raised when the typed confirmation name does not match
    client.name exactly."""
```

`ConfirmationMismatchError` gets a handler in `app/main.py` returning 422
with `code = "CONFIRMATION_MISMATCH"`. Frontend pattern-matches this
specifically so it can highlight the input field rather than show a
generic toast.

## 12. Testing approach

Test file: `backend/nexus/tests/test_admin_hard_delete.py` (new).

**Unit / service-level (using `db` fixture):**

1. `test_hard_delete_requires_soft_delete_first` — active tenant, expect
   `InvalidClientStateError`.
2. `test_hard_delete_requires_blocked_tenant_to_be_deleted_first` — blocked
   tenant, expect `InvalidClientStateError`.
3. `test_hard_delete_requires_name_match` — soft-deleted, wrong name,
   expect `ConfirmationMismatchError`.
4. `test_hard_delete_purges_all_tenant_rows` — populate one tenant with
   rows in every cascading table (users, role_assignments, org_units, JD,
   pipeline templates, instances, candidates, sessions, etc.), soft-delete,
   hard-delete, then assert each table has zero rows for that tenant_id.
5. `test_hard_delete_preserves_audit_log_rows` — audit log rows for the
   deleted tenant must still exist after the cascade.
6. `test_hard_delete_writes_pre_cascade_audit_event` — `client.hard_deleted`
   audit event must be present after the operation.
7. `test_hard_delete_idempotent_on_404` — the second hard-delete attempt
   on the same id returns 404, doesn't crash.

**HTTP-level (where feasible):**

8. `test_hard_delete_endpoint_403_without_admin_claim` — non-admin token
   → 403.
9. `test_hard_delete_endpoint_409_when_not_soft_deleted` — endpoint-level
   wiring of `InvalidClientStateError` → 409.

**Auth provider purge:**

10. Mock the provider; one user succeeds, one raises `AuthProviderError`.
    Assert response carries correct counts and the partial-success audit
    event was written.

**Migration safety:**

11. Apply `0023` against the dev DB which currently has BinQle in
    soft-deleted state. Confirm it succeeds without error and the FK list
    matches the spec. (Manual verification step.)

## 13. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Postgres cascade hits a `NO ACTION` constraint that fires before all cascade rows are queued | Low (per Postgres 17 docs, end-of-statement timing) | High (delete fails atomically — no partial state) | Test 4 above explicitly populates every table type and verifies success. If the test surprises us, fallback is a follow-up migration converting `users(id)` FKs in tenant-scoped tables to `ON DELETE SET NULL`. |
| Supabase Auth purge fails for some users | Medium (network blips happen) | Low (audit event captures the partial state; ops can replay manually) | Per-call try/except; counts in response; partial-success audit event. |
| Operator misclicks "Permanently delete" | Low (typed-name confirmation) | Catastrophic (data is gone) | Two-step (must soft-delete first); typed-name confirmation; server-side validation of the same name. |
| Concurrent hard-delete and unblock from two operators | Very low | Low (one wins, one gets 404 or 409) | The transaction model already serializes; no extra locking needed. |
| FK migration leaves a non-CASCADE FK behind because we missed one | Low (verified via `pg_constraint` query in this spec) | High (hard delete fails when that table has rows) | Test 4 covers every cascading table; CI will catch a missed conversion. |
| Audit log accumulates without bound for hard-deleted tenants | Long-term | Low (rows are small; partition by `created_at` later if needed) | Out of scope — handled when audit retention policy is formalized. |

## 14. Out of scope / future work

- **Grace period and auto-purge worker** (Phase 2 lifecycle): scheduled
  Dramatiq actor that hard-deletes any tenant whose `deleted_at >
  NOW() - INTERVAL '30 days'` and whose `clients` row has not been UI-
  restored.
- **UI-driven restore of soft-deleted tenants** within the grace window.
- **S3 object cleanup** when resumes/recordings ship in Phase 3.
- **Redis state cleanup** when session state ships in Phase 3.
- **Bulk hard delete** (no driver — one tenant at a time is fine for ops).
- **Audit log archival / retention policy** — deferred until first
  compliance ask.

## 15. Open questions

None — all design decisions confirmed:

- Hard delete only on soft-deleted tenants ✓
- Type-the-name confirmation ✓
- Audit trail preserved by dropping FKs (not tombstoning) ✓
- DB-level CASCADE rather than application-level explicit DELETEs ✓
- Supabase Auth identities purged best-effort ✓

## 16. References

- Existing `delete_client` (soft delete) — `backend/nexus/app/modules/admin/service.py`
- Auth provider abstraction — `backend/nexus/app/modules/auth/admin/`
- Tenant-scoped tables registry — `backend/nexus/app/main.py:31`
  (`_TENANT_SCOPED_TABLES`)
- PostgreSQL 17 docs: `ON DELETE` actions and `NO ACTION` end-of-statement
  timing — confirmed via context7 query against
  `/websites/postgresql_17/ddl-constraints.md` and
  `/websites/postgresql_17/sql-createtable.md`.
