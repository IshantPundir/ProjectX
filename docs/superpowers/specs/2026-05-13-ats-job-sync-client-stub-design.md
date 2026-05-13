# ATS job sync — auto-create stub `client_account` org_units when client unknown

**Date:** 2026-05-13
**Status:** Approved (design)
**Scope:** Backend only — `app/modules/ats/importer.py`. No schema migration. No frontend code changes.

---

## Problem

Ceipal's `getJobPostingDetails/{id}` response identifies a job's client by **name only** (e.g. `"client": "Oracle"`) — no stable external id. The current importer matches that name against `ats_client_mappings.external_client_name`. When the lookup misses (because `getClientsList` doesn't return that client, or it hasn't been synced yet, or the recruiter is running the jobs phase in isolation), the job lands with `org_unit_id=NULL` + `status='blocked_pending_client_setup'`. The `/jobs` page renders a "Not set up" chip; there is no UI to resolve it.

We already auto-create stub `client_account` org_units inside `_sync_clients` (Phase 1 of the importer) when `getClientsList` returns a previously-unknown client. The same mechanism should apply during the jobs phase so an out-of-band or partial sync produces stubs that the recruiter can act on via the existing `/settings/org-units` flow.

## Decision

When `_sync_jobs` processes a payload with a non-empty `external_client_name` and no matching mapping:

1. Auto-create a stub `client_account` org_unit under the tenant's root, with `company_profile_completion_status='pending'`.
2. Auto-create an `ats_client_mappings` row keyed by a **synthetic external_id** of the form `"name:" + external_client_name` (verbatim, no normalization).
3. Link the job to the stub org_unit. Status stays `'blocked_pending_client_setup'` — the recruiter must complete the company profile to unblock.

When `_sync_clients` later returns the same client by its real Ceipal id, the stub is **promoted**: the mapping's `external_client_id` is rewritten to the real id and `source_metadata` replaced with Ceipal metadata. The org_unit is not touched — the recruiter's pending-profile work-item stays valid through the promotion.

## Why this shape

- **No schema migration.** `ats_client_mappings.external_client_id` is plain `Text NOT NULL`; the existing unique `(tenant_id, vendor, external_client_id)` constraint naturally prevents duplicate stubs. `OrganizationalUnit.company_profile_completion_status='pending'` is the existing "needs work" marker — both `/settings/org-units` (`OrgUnitNode.tsx:200-212`, list page line 347) and the unblock cascade in `_upsert_job_payload` (lines 524–528) already key off it.
- **No frontend changes.** The `/settings/org-units` page already badges client_account stubs with `completion_status='pending'`. The `/jobs` page's StatusPill already renders "Awaiting setup" for `status='blocked_pending_client_setup'`. The existing "Not set up" chip (triggered by `org_unit_id IS NULL`) continues to render for the narrow residual case of Ceipal jobs with no `client` field at all.
- **Synthetic-id prefix is collision-free.** Real Ceipal ids are URL-safe base64 (no colons); the `"name:"` prefix uniquely identifies stubs eligible for promotion. The `LIKE 'name:%'` filter is precise.
- **Exact-match name lookup.** Per explicit user decision: no case-folding, no whitespace normalization. "Oracle" and "oracle" are distinct clients; the recruiter merges manually if a real-world collision arises. Ceipal's adapter already `.strip()`s the name (`ceipal.py:474`).
- **Promotion preserves the org_unit.** Recruiter-side completion of the company profile happens against the org_unit row, not the mapping row. Rewriting the mapping's external_client_id but not the org_unit lets the recruiter's in-flight profile work survive the promotion.

## Idempotency matrix

| Sequence | Result |
|---|---|
| `_sync_jobs` (Oracle, name only) | Creates stub mapping `("name:Oracle", "Oracle")` + stub org_unit + job linked |
| `_sync_jobs` (Oracle, name only) ×2 | Helper short-circuits on existing-stub lookup; no duplicate |
| `_sync_jobs` (Oracle, name only) → `_sync_clients` (Oracle real id `ABC123`) | Stub promoted to `("ABC123", "Oracle")`; org_unit untouched |
| `_sync_clients` (Oracle real id `ABC123`) → `_sync_jobs` (Oracle, name only) | Jobs phase's existing name-based lookup at `importer.py:499` matches the real mapping; no stub created |
| `_sync_jobs` → `_sync_clients` → `_sync_jobs` | Second jobs run matches the (now-promoted) real mapping by name; links to the same org_unit |
| `_sync_clients` (Oracle) ×2 | Existing-by-id short-circuit at `importer.py:162` updates metadata; promotion check is bypassed |
| `_sync_clients` with BOTH a stub `("name:Oracle", "Oracle")` AND a real `("ABC123", "Oracle")` already present | Existing-by-id short-circuit fires for the real mapping; the stub is left alone (orphan). Out of scope to merge; recruiter resolves manually. |

## Data model — fields used (no migration)

| Field | Stub value at creation | Promotion value (in `_sync_clients`) |
|---|---|---|
| `ats_client_mappings.external_client_id` | `"name:" + name` (verbatim) | `payload.external_id` (real Ceipal id) |
| `ats_client_mappings.external_client_name` | `name` (already stripped by adapter) | unchanged |
| `ats_client_mappings.source_metadata` | `{"stub": true, "origin": "jobs_phase"}` | `{"contacts": payload.contacts, "raw": payload.raw}` |
| `ats_client_mappings.last_synced_at` | `now()` | `now()` |
| `ats_client_mappings.org_unit_id` | new stub org_unit id | unchanged |
| `organizational_units.unit_type` | `"client_account"` | n/a |
| `organizational_units.parent_unit_id` | tenant's root company unit id | n/a |
| `organizational_units.name` | `payload.external_client_name` | n/a |
| `organizational_units.company_profile` | `{"name": name}` (minimal — matches `_sync_clients` pattern of populating non-NULL JSON without strict 4-field validation) | unchanged |
| `organizational_units.company_profile_completion_status` | `'pending'` | unchanged |
| `organizational_units.created_by` | `ATSConnection.created_by` | n/a |
| `organizational_units.is_root` | `False` | n/a |
| `job_postings.org_unit_id` | stub org_unit id | n/a (job sync handles this) |
| `job_postings.status` | `'blocked_pending_client_setup'` | n/a |

## Implementation — code shape

### New helper on `ATSImporter`

```python
async def _get_or_create_client_stub_by_name(
    self,
    db: AsyncSession,
    *,
    tenant_id: UUID,
    vendor: str,
    external_client_name: str,
    created_by: UUID,
    root_org_unit_id: UUID,
) -> tuple[OrganizationalUnit, ATSClientMapping]:
    """Idempotent: returns the existing stub if one matches the synthetic
    id, otherwise creates org_unit + mapping + audit row in one go."""
```

Steps:

1. `SELECT` mapping where `tenant_id=?`, `ats_vendor=?`, `external_client_id = 'name:' + external_client_name`. If found, `get(OrgUnit, mapping.org_unit_id)` and return the pair.
2. Otherwise: build `OrganizationalUnit(client_id=tenant_id, parent_unit_id=root_org_unit_id, name=external_client_name, unit_type="client_account", is_root=False, company_profile={"name": external_client_name}, company_profile_completion_status="pending", created_by=created_by)`, `db.add`, `db.flush`.
3. Build `ATSClientMapping(tenant_id=tenant_id, ats_vendor=vendor, external_client_id=f"name:{external_client_name}", external_client_name=external_client_name, org_unit_id=new_unit.id, source_metadata={"stub": True, "origin": "jobs_phase"})`, `db.add`.
4. `await log_event(...)` with `action="ats.client_mapping.created"`, `resource="ats_client_mapping"`, `resource_id=new_unit.id`, `payload={"vendor": vendor, "external_client_id": f"name:{external_client_name}", "org_unit_id": str(new_unit.id), "stub": True, "origin": "jobs_phase"}`. Reuses the same action string as `_sync_clients`; the `stub`/`origin` payload keys disambiguate.
5. Return `(new_unit, mapping)`.

### Change to `_upsert_job_payload`

After the existing two lookups (by external_client_id, then by external_client_name) fail and `payload.external_client_name` is non-empty, call the helper:

```python
if mapping is None and payload.external_client_name:
    root = await db.scalar(
        select(OrganizationalUnit).where(
            OrganizationalUnit.client_id == tenant_id,
            OrganizationalUnit.is_root.is_(True),
        )
    )
    org_unit, mapping = await self._get_or_create_client_stub_by_name(
        db,
        tenant_id=tenant_id,
        vendor=adapter.vendor,
        external_client_name=payload.external_client_name,
        created_by=created_by,
        root_org_unit_id=root.id,
    )
```

The existing `if mapping is None:` block (lines 513–521) becomes the residual no-name path: Ceipal jobs with no `client` field at all land unlinked (`org_unit_id=NULL`, `status='blocked_pending_client_setup'`) with the existing `logger.info("ats.sync.jobs.imported_unlinked", ...)` line preserved.

### Change to `_sync_clients`

Between the "existing is not None" branch and the "Create the org_unit with stub profile" block (around line 168), add the promotion check:

```python
promotable = await db.scalar(
    select(ATSClientMapping).where(
        ATSClientMapping.tenant_id == tenant_id,
        ATSClientMapping.ats_vendor == adapter.vendor,
        ATSClientMapping.external_client_name == payload.name,
        ATSClientMapping.external_client_id.like("name:%"),
    )
)
if promotable is not None:
    promotable.external_client_id = payload.external_id
    promotable.source_metadata = {"contacts": payload.contacts, "raw": payload.raw}
    promotable.last_synced_at = datetime.now(tz=UTC)
    await log_event(
        db, tenant_id=tenant_id, actor_id=created_by,
        actor_email="ats-import",
        action="ats.client_mapping.promoted",
        resource="ats_client_mapping",
        resource_id=promotable.org_unit_id,
        payload={"vendor": adapter.vendor,
                 "from_external_client_id": f"name:{payload.name}",
                 "to_external_client_id": payload.external_id},
    )
    result.updated += 1
    continue
```

`created_by` and `root` are already resolved at the top of `_sync_clients`.

## Tests

New tests live alongside the existing ATS importer suite. Cover at minimum:

1. `test_sync_jobs_creates_stub_for_unknown_client` — single payload with `external_client_id=""`, `external_client_name="Oracle"`. Asserts: one new `client_account` org_unit under root, `completion_status='pending'`, mapping with synthetic id `"name:Oracle"` and `source_metadata={"stub": True, "origin": "jobs_phase"}`, job linked with `status='blocked_pending_client_setup'`, audit row with `action='ats.client_mapping.created'` and `payload.stub=True`.
2. `test_sync_jobs_idempotent_stub_creation` — same payload twice; one org_unit, one mapping, no duplicates.
3. `test_sync_jobs_uses_existing_real_mapping` — pre-seed `("ABC123", "Oracle")`; jobs sync links to it without creating a stub.
4. `test_sync_jobs_no_client_name_stays_unlinked` — `external_client_name=None`; job lands with `org_unit_id=NULL`; no stub created; `logger.info("ats.sync.jobs.imported_unlinked", …)` fires (assert via log capture or just behavior).
5. `test_sync_clients_promotes_stub` — pre-seed stub `("name:Oracle", "Oracle")`; `_sync_clients` returns Oracle with real id `"ABC123"`. Asserts: mapping's id becomes `"ABC123"`, `source_metadata` becomes the real payload, org_unit untouched (same id, `completion_status` still `'pending'`), audit row `ats.client_mapping.promoted`, `result.updated=1`, `result.new=0`.
6. `test_sync_clients_no_promotion_when_real_mapping_exists` — stub AND real mapping both pre-seeded; `_sync_clients` returns Oracle with id `"ABC123"`; existing-by-id short-circuit fires, stub untouched, no promotion event. (Documents the edge case; recruiter resolves duplicate.)
7. `test_sync_jobs_then_sync_clients_then_sync_jobs` — full interleave; exactly one org_unit and one mapping at the end; mapping has real id; second jobs run matches by name.

## Edge cases

- **Case differences.** `"Oracle"` and `"oracle"` are distinct (exact-equality decision). Recruiter merges manually.
- **Name contains a colon.** Synthetic id `"name:Acme: Subdivision"`. `LIKE 'name:%'` still matches; equality on `external_client_name` is exact.
- **Empty / None `external_client_name`.** Adapter normalizes both to `None` already (`ceipal.py:474`). Helper is only called when truthy.
- **Concurrent syncs against the same tenant.** Single-dev MVP — router gates one sync at a time per tenant. The `(tenant_id, vendor, external_client_id)` unique constraint backstops any race as an `IntegrityError`; no row-level locking added.
- **Backfilling pre-existing NULL rows.** Out of scope. Migration 0033's NULL rows stay as they are. A separate one-off SQL script or admin endpoint can backfill later if needed. Note: re-running jobs sync after this lands does NOT auto-backfill because Pass 2's missing-detect skips IDs already in the local store.

## Out of scope

- Merging duplicate org_units (real + stub for the same conceptual client).
- A `/jobs`-page UI flow to reassign a job's `org_unit_id` post-hoc.
- Backfilling existing `job_postings.org_unit_id IS NULL` rows.
- Loosening exact-equality name matching.

## Validation gates

- `docker compose run --rm nexus pytest tests/modules/ats -q` — all green.
- `docker compose run --rm nexus ruff check app/modules/ats` — clean.
- `docker compose run --rm nexus mypy app/modules/ats` — clean.
- Manual smoke: connect Ceipal, run the jobs-phase sync from `/jobs` "Sync jobs from ATS" button, confirm previously-NULL-org_unit jobs now have a stub `client_account` org_unit at `/settings/org-units` with the "profile incomplete" badge, and the linked job's StatusPill reads "Awaiting setup".
