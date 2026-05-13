# ATS Job Sync — Auto-Create Stub `client_account` Org Units Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `_sync_jobs` processes a Ceipal job whose `client` name has no matching `ats_client_mappings` row, auto-create a stub `client_account` org_unit (`completion_status='pending'`) plus a paired stub mapping keyed by synthetic `external_client_id = "name:<name>"`. When `_sync_clients` later returns the real Ceipal id for that client, promote the stub mapping (rewrite external_client_id, refresh source_metadata, leave org_unit untouched).

**Architecture:** Backend-only change in `app/modules/ats/importer.py`. One new private helper on `ATSImporter`. Two edits: `_upsert_job_payload` calls the helper when both existing lookups miss and a name is present; `_sync_clients` runs a promotion check before its existing "create new" branch. No schema migration. No frontend changes — existing UI surfaces (`/settings/org-units` "profile incomplete" badge, `/jobs` StatusPill "Awaiting setup") already cover the recruiter signal.

**Tech Stack:** Python 3.13, SQLAlchemy 2 async (asyncpg), Dramatiq actors, pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-13-ats-job-sync-client-stub-design.md`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `backend/nexus/app/modules/ats/importer.py` | Modify | Add `_get_or_create_client_stub_by_name` helper. Insert helper call in `_upsert_job_payload` between existing-mapping lookups and the unlinked-fallback. Insert promotion check at top of `_sync_clients` per-payload loop. |
| `backend/nexus/tests/modules/ats/test_importer_jobs.py` | Modify | Add four new tests (stub creation, idempotency, origin metadata, full-interleave). Update two existing test docstrings to disambiguate new vs old precondition. |
| `backend/nexus/tests/modules/ats/test_importer_clients_users.py` | Modify | Add two new tests (promotion of stub, no-promotion when real mapping exists). |

No new files. No `conftest.py` changes — existing `jobs_fixture` and `importer_fixture` cover seed needs.

---

### Task 1: RED — failing test for stub creation in `_sync_jobs`

**Files:**
- Modify: `backend/nexus/tests/modules/ats/test_importer_jobs.py` (append at end of file)

- [ ] **Step 1: Write the failing test**

Append this test to `tests/modules/ats/test_importer_jobs.py`:

```python
@pytest.mark.asyncio
async def test_sync_jobs_creates_stub_for_unknown_client_name(db, jobs_fixture):
    """A Ceipal job whose `client` name has no matching ats_client_mappings
    row triggers auto-creation of a stub client_account org_unit + paired
    stub mapping. The job is linked to the new stub with
    status='blocked_pending_client_setup'.

    Synthetic external_client_id format: 'name:' + external_client_name.
    Stub mapping carries source_metadata={"stub": True, "origin": "jobs_phase"}.
    Stub org_unit carries company_profile_completion_status='pending' and
    company_profile={"name": <name>}.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id, _pending_unit, _complete_unit = jobs_fixture
    job = ATSJobPayload(
        external_id="j-stub",
        external_client_id="",  # Ceipal jobs never carry a stable client id
        external_client_name="Oracle",  # not in seeded mappings (P, C)
        title="Java Engineer",
        status="Active",
        raw={"client": "Oracle"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.new == 1
    assert result.skipped == 0

    # Stub org_unit was created under the tenant's root.
    unit_row = await db.execute(text(
        "SELECT id::text AS id, name, unit_type, parent_unit_id::text AS parent_id, "
        "company_profile, company_profile_completion_status "
        "FROM organizational_units "
        "WHERE client_id = :t AND name = 'Oracle'"
    ), {"t": tenant_id})
    u = unit_row.one()
    assert u.unit_type == "client_account"
    assert u.parent_id == root_unit_id
    assert u.company_profile_completion_status == "pending"
    assert u.company_profile == {"name": "Oracle"}

    # Paired stub mapping with synthetic id and origin metadata.
    mapping_row = await db.execute(text(
        "SELECT external_client_id, external_client_name, source_metadata, "
        "org_unit_id::text AS org_unit_id "
        "FROM ats_client_mappings "
        "WHERE tenant_id = :t AND ats_vendor = 'ceipal' "
        "AND external_client_name = 'Oracle'"
    ), {"t": tenant_id})
    m = mapping_row.one()
    assert m.external_client_id == "name:Oracle"
    assert m.source_metadata == {"stub": True, "origin": "jobs_phase"}
    assert m.org_unit_id == u.id

    # Job linked to the stub with the blocked status.
    job_row = await db.execute(text(
        "SELECT status, org_unit_id::text AS org_unit_id "
        "FROM job_postings WHERE tenant_id = :t AND external_id = 'j-stub'"
    ), {"t": tenant_id})
    j = job_row.one()
    assert j.status == "blocked_pending_client_setup"
    assert j.org_unit_id == u.id

    # Audit log row written with stub origin marker.
    audit_row = await db.execute(text(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.client_mapping.created' "
        "ORDER BY created_at DESC LIMIT 1"
    ), {"t": tenant_id})
    a = audit_row.one()
    assert a.payload["stub"] is True
    assert a.payload["origin"] == "jobs_phase"
    assert a.payload["external_client_id"] == "name:Oracle"
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_jobs.py::test_sync_jobs_creates_stub_for_unknown_client_name -v
```

Expected: FAIL. The assertion most likely to fire first is `unit_row.one()` raising `NoResultFound` (no Oracle org_unit was created by the current code path; the job lands with `org_unit_id=NULL`).

- [ ] **Step 3: Do NOT commit yet** — Task 2 implements the helper that makes this test pass. Keep the failing test in your working tree.

---

### Task 2: GREEN — implement helper + wire into `_upsert_job_payload`

**Files:**
- Modify: `backend/nexus/app/modules/ats/importer.py`

- [ ] **Step 1: Add the new helper method on `ATSImporter`**

Add this method to `ATSImporter` in `app/modules/ats/importer.py`. Place it **after** `_upsert_job_payload` (around line 597, before `_write_jobs_progress`) so related job-phase code stays together.

```python
    async def _get_or_create_client_stub_by_name(
        self,
        db,
        *,
        tenant_id: UUID,
        vendor: str,
        external_client_name: str,
        created_by: UUID,
        root_org_unit_id: UUID,
    ):
        """Look up or create a stub client_account org_unit + mapping pair
        for a Ceipal job whose ``client`` field is a name with no matching
        ``ats_client_mappings`` row.

        Idempotent: returns the existing stub if one already exists under
        the synthetic id ``"name:" + external_client_name``. Otherwise
        inserts a new ``OrganizationalUnit`` (parented at the tenant's
        root) and a paired ``ATSClientMapping`` and writes an audit row.

        Returns ``(org_unit, mapping)``.

        Called from ``_upsert_job_payload`` when both existing mapping
        lookups miss and the payload carries a non-empty
        ``external_client_name``. ``_sync_clients`` does not use this
        helper — it already has its own create path and follows up with
        a promotion check (see ``_sync_clients``).
        """
        from app.modules.ats.models import ATSClientMapping
        from app.modules.org_units.models import OrganizationalUnit

        synthetic_id = f"name:{external_client_name}"

        existing = await db.scalar(
            select(ATSClientMapping).where(
                ATSClientMapping.tenant_id == tenant_id,
                ATSClientMapping.ats_vendor == vendor,
                ATSClientMapping.external_client_id == synthetic_id,
            )
        )
        if existing is not None:
            org_unit = await db.get(OrganizationalUnit, existing.org_unit_id)
            return org_unit, existing

        org_unit = OrganizationalUnit(
            client_id=tenant_id,
            parent_unit_id=root_org_unit_id,
            name=external_client_name,
            unit_type="client_account",
            is_root=False,
            company_profile={"name": external_client_name},
            company_profile_completion_status="pending",
            created_by=created_by,
        )
        db.add(org_unit)
        await db.flush()

        mapping = ATSClientMapping(
            tenant_id=tenant_id,
            ats_vendor=vendor,
            external_client_id=synthetic_id,
            external_client_name=external_client_name,
            org_unit_id=org_unit.id,
            source_metadata={"stub": True, "origin": "jobs_phase"},
        )
        db.add(mapping)

        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=created_by,
            actor_email="ats-import",
            action="ats.client_mapping.created",
            resource="ats_client_mapping",
            resource_id=org_unit.id,
            payload={
                "vendor": vendor,
                "external_client_id": synthetic_id,
                "org_unit_id": str(org_unit.id),
                "stub": True,
                "origin": "jobs_phase",
            },
        )
        return org_unit, mapping
```

- [ ] **Step 2: Wire the helper into `_upsert_job_payload`**

Open `backend/nexus/app/modules/ats/importer.py` and find the existing block at lines 499–521 (the second mapping lookup followed by the unlinked-fallback). Replace **only** the gap between the second lookup and the existing `if mapping is None:` fallback with a new lookup-then-stub-create path.

The current code reads (around lines 499–522):

```python
        if mapping is None and payload.external_client_name:
            mapping = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_name == payload.external_client_name,
                )
            )
        # No client mapping → import the job unlinked. ...
        if mapping is None:
            logger.info(
                "ats.sync.jobs.imported_unlinked",
                external_job_id=payload.external_id,
                external_client_id=payload.external_client_id,
                external_client_name=payload.external_client_name,
            )
            org_unit_id_for_insert: UUID | None = None
            target_status = "blocked_pending_client_setup"
        else:
            org_unit = await db.get(OrganizationalUnit, mapping.org_unit_id)
            target_status = (
                "blocked_pending_client_setup"
                if org_unit.company_profile_completion_status == "pending"
                else "draft"
            )
            org_unit_id_for_insert = org_unit.id
```

Change it to insert a third resolution step **before** the unlinked fallback. The new step: when both id-based and name-based lookups have failed AND the payload carries a non-empty `external_client_name`, call the helper to create (or reuse) a stub. Only fall through to the unlinked branch when there is no name at all.

Replace the block above with:

```python
        if mapping is None and payload.external_client_name:
            mapping = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_name == payload.external_client_name,
                )
            )

        # Stub-creation step: a job with a client NAME but no matching
        # mapping triggers auto-creation of a stub client_account org_unit
        # so the recruiter has a row to act on (complete the company
        # profile) under /settings/org-units. The job is linked to the
        # stub; status stays 'blocked_pending_client_setup' until the
        # profile is completed.
        if mapping is None and payload.external_client_name:
            root = await db.scalar(
                select(OrganizationalUnit).where(
                    OrganizationalUnit.client_id == tenant_id,
                    OrganizationalUnit.is_root.is_(True),
                )
            )
            if root is None:
                raise RuntimeError(
                    f"tenant {tenant_id} has no root company org_unit"
                )
            org_unit, mapping = await self._get_or_create_client_stub_by_name(
                db,
                tenant_id=tenant_id,
                vendor=adapter.vendor,
                external_client_name=payload.external_client_name,
                created_by=created_by,
                root_org_unit_id=root.id,
            )

        # No client info at all (empty/None name) → import unlinked.
        # org_unit_id stays NULL; the /jobs page's 'Not set up' chip
        # renders for this narrow case only. The recruiter wires the
        # job to an org_unit later via a separate flow.
        if mapping is None:
            logger.info(
                "ats.sync.jobs.imported_unlinked",
                external_job_id=payload.external_id,
                external_client_id=payload.external_client_id,
                external_client_name=payload.external_client_name,
            )
            org_unit_id_for_insert: UUID | None = None
            target_status = "blocked_pending_client_setup"
        else:
            org_unit = await db.get(OrganizationalUnit, mapping.org_unit_id)
            target_status = (
                "blocked_pending_client_setup"
                if org_unit.company_profile_completion_status == "pending"
                else "draft"
            )
            org_unit_id_for_insert = org_unit.id
```

Note: the new block references `OrganizationalUnit` and `select`, both already imported at the top of `_upsert_job_payload` (lines 487–488) and the module respectively. No new imports needed.

- [ ] **Step 3: Run the failing test from Task 1 — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_jobs.py::test_sync_jobs_creates_stub_for_unknown_client_name -v
```

Expected: PASS.

- [ ] **Step 4: Run the full ATS importer test suite to catch regressions**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats -q
```

Expected: all 117+ tests pass (one new test added). If any pre-existing test fails, the most likely cause is a side-effect of the new code path catching a case the test didn't account for — re-read the failing test carefully and either fix the production code or update the test docstring + assertion if the new behavior is correct.

- [ ] **Step 5: Lint + type-check**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  ruff check app/modules/ats/importer.py
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  mypy app/modules/ats/importer.py
```

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/ats/importer.py \
        backend/nexus/tests/modules/ats/test_importer_jobs.py
git commit -m "$(cat <<'EOF'
feat(ats): auto-create stub client_account org_unit when job sync sees unknown client name

Ceipal's getJobPostingDetails returns the client by name only ("Oracle"),
no stable id. When the name has no matching ats_client_mappings row,
_upsert_job_payload now creates a stub client_account org_unit (parented
at root, company_profile={"name": <n>}, completion_status='pending') plus
a paired mapping with synthetic external_client_id="name:<n>" and
source_metadata={"stub": True, "origin": "jobs_phase"}. The job is linked
to the stub with status='blocked_pending_client_setup'.

Idempotent by construction: the synthetic id is the lookup key on re-sync.

The narrow NULL-org_unit_id branch is preserved for jobs with no client
name at all (the /jobs page "Not set up" chip's last remaining case).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Idempotency + origin-metadata tests

**Files:**
- Modify: `backend/nexus/tests/modules/ats/test_importer_jobs.py`

These tests validate behavior already implemented in Task 2; both should pass without any production-code change.

- [ ] **Step 1: Add the idempotency test**

Append to `tests/modules/ats/test_importer_jobs.py`:

```python
@pytest.mark.asyncio
async def test_sync_jobs_stub_creation_is_idempotent(db, jobs_fixture):
    """Running _sync_jobs twice for the same unknown-client-name payload
    creates exactly one org_unit + one stub mapping. The second run hits
    the existing-stub short-circuit in _get_or_create_client_stub_by_name.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j-idem",
        external_client_id="",
        external_client_name="Acme Corp",
        title="t",
        status="Active",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )

    importer = ATSImporter()
    # First sync — creates stub.
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))
    # Second sync — same payload (Pass 2 missing-detect won't fire because
    # the job already exists locally, so this exercises Pass 1's update path).
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))

    unit_count = await db.execute(text(
        "SELECT COUNT(*) FROM organizational_units "
        "WHERE client_id = :t AND name = 'Acme Corp'"
    ), {"t": tenant_id})
    assert unit_count.scalar_one() == 1

    mapping_count = await db.execute(text(
        "SELECT COUNT(*) FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_id = 'name:Acme Corp'"
    ), {"t": tenant_id})
    assert mapping_count.scalar_one() == 1
```

- [ ] **Step 2: Add the colon-in-name test**

Append (same file):

```python
@pytest.mark.asyncio
async def test_sync_jobs_stub_handles_colon_in_client_name(db, jobs_fixture):
    """A client name that itself contains a colon (e.g. 'Acme: West Region')
    produces synthetic id 'name:Acme: West Region'. The LIKE 'name:%' filter
    in _sync_clients still matches and the exact-equality lookup on
    external_client_name still works for re-sync."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j-colon",
        external_client_id="",
        external_client_name="Acme: West Region",
        title="t",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    importer = ATSImporter()
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))

    row = await db.execute(text(
        "SELECT external_client_id, external_client_name FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_name = 'Acme: West Region'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.external_client_id == "name:Acme: West Region"
    assert r.external_client_name == "Acme: West Region"
```

- [ ] **Step 3: Polish docstring on `test_job_with_unknown_client_mapping_is_imported_unlinked`**

The current docstring on this test (around line 130) says "No matching ats_client_mappings row → insert the job_posting with org_unit_id=NULL". Under the new design, that's only true when the payload also has no name. The test's payload has `external_client_id="not-yet-imported-client"` and no `external_client_name`, so the existing assertion of NULL `org_unit_id` is still correct — but the docstring needs to disambiguate.

Replace the docstring with:

```python
    """No matching mapping AND no external_client_name → insert the
    job_posting with org_unit_id=NULL + status='blocked_pending_client_setup'.

    The 'Not set up' chip on /jobs renders for this case. A job with a
    name but no matching mapping takes the stub-creation path instead
    (covered by test_sync_jobs_creates_stub_for_unknown_client_name)."""
```

- [ ] **Step 4: Polish docstring on `test_job_with_neither_id_nor_name_is_imported_unlinked`**

The docstring at line 196 already correctly describes the no-id-no-name case but ends with "Same handling as a job whose external_client_id misses the mappings table" — that statement is no longer true under the new design. Replace the docstring with:

```python
    """A job with empty external_client_id AND empty external_client_name
    has nothing to link against — inserted with org_unit_id=NULL +
    status='blocked_pending_client_setup'. The stub-creation path requires
    a non-empty external_client_name; without one we cannot fabricate a
    sensible org_unit name, so the row stays unlinked and the recruiter
    handles it via the /jobs 'Not set up' chip."""
```

- [ ] **Step 5: Run the new tests + the docstring-polished tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_jobs.py -v
```

Expected: all tests in the file pass (existing ones unchanged in behavior, two new ones green).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/tests/modules/ats/test_importer_jobs.py
git commit -m "$(cat <<'EOF'
test(ats): cover stub idempotency, colon-in-name, and polish unlinked-job docstrings

Adds:
  - test_sync_jobs_stub_creation_is_idempotent — twice-run sync produces
    one org_unit + one mapping (synthetic-id lookup short-circuits Pass 1).
  - test_sync_jobs_stub_handles_colon_in_client_name — synthetic id format
    'name:<name>' works when <name> itself contains a colon.

Polishes:
  - test_job_with_unknown_client_mapping_is_imported_unlinked docstring —
    disambiguates "no mapping" vs "no mapping AND no name" (only the latter
    leaves org_unit_id NULL after the stub-creation change).
  - test_job_with_neither_id_nor_name_is_imported_unlinked docstring —
    removes outdated "same handling" sentence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: RED — failing test for `_sync_clients` stub promotion

**Files:**
- Modify: `backend/nexus/tests/modules/ats/test_importer_clients_users.py` (append after `test_sync_clients_existing_mapping_updates_dont_rename_org_unit`)

- [ ] **Step 1: Write the failing test**

Append to `tests/modules/ats/test_importer_clients_users.py`:

```python
@pytest.mark.asyncio
async def test_sync_clients_promotes_stub_to_real_external_id(
    db, importer_fixture,
):
    """When _sync_clients later returns a real Ceipal id for a client that
    already exists as a stub (created by an earlier _sync_jobs run), the
    stub mapping is PROMOTED in place: external_client_id is rewritten to
    the real id, source_metadata is replaced with the Ceipal payload's
    contacts+raw, last_synced_at advances. The org_unit is untouched —
    same row, same id, same completion_status='pending', same
    company_profile (the recruiter's in-flight profile work survives).

    Audit row 'ats.client_mapping.promoted' is written with from/to ids.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id = importer_fixture

    # Pre-seed: a stub created by an earlier jobs-phase run for "Oracle".
    stub_unit_id = uuid.uuid4()
    original_synced_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await db.execute(
        text(
            "INSERT INTO organizational_units "
            "(id, client_id, parent_unit_id, name, unit_type, is_root, "
            "company_profile, company_profile_completion_status) "
            "VALUES (:o, :t, :p, 'Oracle', 'client_account', false, "
            "'{\"name\": \"Oracle\"}', 'pending')"
        ),
        {"o": stub_unit_id, "t": tenant_id, "p": root_unit_id},
    )
    await db.execute(
        text(
            "INSERT INTO ats_client_mappings "
            "(tenant_id, ats_vendor, external_client_id, external_client_name, "
            " org_unit_id, source_metadata, last_synced_at) "
            "VALUES (:t, 'ceipal', 'name:Oracle', 'Oracle', :o, "
            " :sm, :s)"
        ),
        {
            "t": tenant_id, "o": stub_unit_id, "s": original_synced_at,
            "sm": '{"stub": true, "origin": "jobs_phase"}',
        },
    )
    await db.flush()

    # _sync_clients now returns Oracle with its real Ceipal id.
    payload = ATSClientPayload(
        external_id="ABC123",
        name="Oracle",
        website="www.oracle.com",
        industry="Computer Software",
        contacts=[{"email": "ops@oracle.com"}],
        raw={"id": "ABC123", "name": "Oracle"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    assert result.updated == 1
    assert result.new == 0

    # Mapping promoted: synthetic id replaced by real id; metadata refreshed.
    mapping_row = await db.execute(text(
        "SELECT external_client_id, external_client_name, source_metadata, "
        "last_synced_at, org_unit_id::text AS org_unit_id "
        "FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_name = 'Oracle'"
    ), {"t": tenant_id})
    rows = mapping_row.all()
    assert len(rows) == 1  # exactly one mapping (no duplicate created)
    m = rows[0]
    assert m.external_client_id == "ABC123"
    assert m.source_metadata == {
        "contacts": [{"email": "ops@oracle.com"}],
        "raw": {"id": "ABC123", "name": "Oracle"},
    }
    assert m.last_synced_at > original_synced_at
    assert m.org_unit_id == str(stub_unit_id)

    # Org_unit untouched: same row, completion_status still pending.
    unit_row = await db.execute(text(
        "SELECT id::text AS id, name, company_profile_completion_status, "
        "company_profile "
        "FROM organizational_units WHERE id = :o"
    ), {"o": stub_unit_id})
    u = unit_row.one()
    assert u.id == str(stub_unit_id)
    assert u.name == "Oracle"
    assert u.company_profile_completion_status == "pending"
    assert u.company_profile == {"name": "Oracle"}

    # Promotion audit row written.
    audit_row = await db.execute(text(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.client_mapping.promoted' "
        "ORDER BY created_at DESC LIMIT 1"
    ), {"t": tenant_id})
    a = audit_row.one()
    assert a.payload["from_external_client_id"] == "name:Oracle"
    assert a.payload["to_external_client_id"] == "ABC123"
    assert a.payload["vendor"] == "ceipal"
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_clients_users.py::test_sync_clients_promotes_stub_to_real_external_id -v
```

Expected: FAIL. The current `_sync_clients` would treat Oracle as a brand-new client, create a SECOND org_unit + mapping with external_client_id="ABC123", and leave the stub orphaned. The `len(rows) == 1` assertion is the most likely to surface this (it would be 2: the stub plus the new one). `result.updated == 1` would also fail (would be 0 with `result.new == 1`).

- [ ] **Step 3: Do NOT commit yet** — Task 5 implements the promotion path.

---

### Task 5: GREEN — implement promotion check in `_sync_clients`

**Files:**
- Modify: `backend/nexus/app/modules/ats/importer.py`

- [ ] **Step 1: Insert the promotion block**

Open `app/modules/ats/importer.py`. Find the `_sync_clients` method (line 123). After the existing `if existing is not None: ... continue` block (around lines 162–168), and **before** the comment block that begins `# Create the org_unit with stub profile` (around line 170), insert the promotion check.

The relevant region currently reads (lines 162–170):

```python
            if existing is not None:
                # Update mapping metadata; do NOT rename the org_unit.
                existing.external_client_name = payload.name
                existing.source_metadata = {"contacts": payload.contacts, "raw": payload.raw}
                existing.last_synced_at = datetime.now(tz=UTC)
                result.updated += 1
                continue

            # Create the org_unit with stub profile
```

Insert the promotion block between the `continue` and the `# Create the org_unit with stub profile` comment:

```python
            if existing is not None:
                # Update mapping metadata; do NOT rename the org_unit.
                existing.external_client_name = payload.name
                existing.source_metadata = {"contacts": payload.contacts, "raw": payload.raw}
                existing.last_synced_at = datetime.now(tz=UTC)
                result.updated += 1
                continue

            # Promotion: a stub mapping (synthetic id "name:<name>") created
            # by an earlier _sync_jobs run is upgraded in place when Ceipal
            # now returns the real client id. We rewrite external_client_id
            # to the real id and refresh source_metadata, but leave the
            # linked org_unit alone so the recruiter's in-flight profile
            # completion work survives the promotion. See spec
            # docs/superpowers/specs/2026-05-13-ats-job-sync-client-stub-design.md.
            promotable = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_name == payload.name,
                    ATSClientMapping.external_client_id.like("name:%"),
                )
            )
            if promotable is not None:
                from_id = promotable.external_client_id
                promotable.external_client_id = payload.external_id
                promotable.source_metadata = {
                    "contacts": payload.contacts,
                    "raw": payload.raw,
                }
                promotable.last_synced_at = datetime.now(tz=UTC)
                await log_event(
                    db,
                    tenant_id=tenant_id,
                    actor_id=created_by,
                    actor_email="ats-import",
                    action="ats.client_mapping.promoted",
                    resource="ats_client_mapping",
                    resource_id=promotable.org_unit_id,
                    payload={
                        "vendor": adapter.vendor,
                        "from_external_client_id": from_id,
                        "to_external_client_id": payload.external_id,
                    },
                )
                result.updated += 1
                continue

            # Create the org_unit with stub profile
```

`ATSClientMapping`, `OrganizationalUnit`, `select`, `log_event`, `datetime`, `UTC`, `text`, and `tenant_id`/`created_by` are all already in scope inside `_sync_clients` (per the function-local imports at lines 128–130 and the function-scoped variable resolution above). No new imports needed.

- [ ] **Step 2: Run the failing test from Task 4 — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_clients_users.py::test_sync_clients_promotes_stub_to_real_external_id -v
```

Expected: PASS.

- [ ] **Step 3: Run the full ATS suite to catch regressions**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats -q
```

Expected: all pass.

- [ ] **Step 4: Lint + type-check**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  ruff check app/modules/ats/importer.py
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  mypy app/modules/ats/importer.py
```

Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/ats/importer.py \
        backend/nexus/tests/modules/ats/test_importer_clients_users.py
git commit -m "$(cat <<'EOF'
feat(ats): promote stub client mappings when _sync_clients returns the real id

When _sync_jobs creates a stub mapping with synthetic external_client_id
"name:<name>" (because Ceipal's job-details endpoint identifies clients
by name only) and _sync_clients later returns the same client with its
real Ceipal id, the stub is now promoted in place rather than producing
a duplicate org_unit.

Promotion semantics:
  - Mapping's external_client_id is rewritten to the real id.
  - source_metadata is replaced with the real Ceipal contacts+raw payload.
  - last_synced_at advances.
  - The linked org_unit is NOT touched — recruiter's in-flight company
    profile completion survives intact.

Audit action: ats.client_mapping.promoted with from/to ids.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Coverage tests — no-promotion edge case + full interleave

**Files:**
- Modify: `backend/nexus/tests/modules/ats/test_importer_clients_users.py`
- Modify: `backend/nexus/tests/modules/ats/test_importer_jobs.py`

Both tests validate behavior already shipped in Tasks 2 + 5; they document edge-case invariants and should pass against the existing implementation without further production code changes.

- [ ] **Step 1: Add the no-promotion-when-real-mapping-exists test**

Append to `tests/modules/ats/test_importer_clients_users.py`:

```python
@pytest.mark.asyncio
async def test_sync_clients_does_not_promote_when_real_mapping_exists(
    db, importer_fixture,
):
    """If BOTH a real mapping ('ABC123', 'Oracle') AND a stub mapping
    ('name:Oracle', 'Oracle') already exist for the same tenant+vendor,
    _sync_clients with payload(external_id='ABC123', name='Oracle')
    short-circuits on the existing-by-id lookup at the top of the loop.
    The promotion check is NOT reached; the stub mapping is left
    untouched (recruiter-resolvable duplicate, out of scope to auto-merge).
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id = importer_fixture

    # Pre-seed: a real mapping + linked org_unit.
    real_unit_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO organizational_units "
        "(id, client_id, parent_unit_id, name, unit_type, is_root, "
        " company_profile, company_profile_completion_status) "
        "VALUES (:o, :t, :p, 'Oracle', 'client_account', false, "
        " '{\"name\":\"Oracle\"}', 'complete')"
    ), {"o": real_unit_id, "t": tenant_id, "p": root_unit_id})
    await db.execute(text(
        "INSERT INTO ats_client_mappings "
        "(tenant_id, ats_vendor, external_client_id, external_client_name, "
        " org_unit_id, source_metadata) "
        "VALUES (:t, 'ceipal', 'ABC123', 'Oracle', :o, '{}')"
    ), {"t": tenant_id, "o": real_unit_id})

    # Pre-seed: a stub mapping + linked stub org_unit (orphan).
    stub_unit_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO organizational_units "
        "(id, client_id, parent_unit_id, name, unit_type, is_root, "
        " company_profile, company_profile_completion_status) "
        "VALUES (:o, :t, :p, 'Oracle', 'client_account', false, "
        " '{\"name\":\"Oracle\"}', 'pending')"
    ), {"o": stub_unit_id, "t": tenant_id, "p": root_unit_id})
    await db.execute(text(
        "INSERT INTO ats_client_mappings "
        "(tenant_id, ats_vendor, external_client_id, external_client_name, "
        " org_unit_id, source_metadata) "
        "VALUES (:t, 'ceipal', 'name:Oracle', 'Oracle', :o, "
        " '{\"stub\":true,\"origin\":\"jobs_phase\"}')"
    ), {"t": tenant_id, "o": stub_unit_id})
    await db.flush()

    payload = ATSClientPayload(
        external_id="ABC123",
        name="Oracle",
        raw={"id": "ABC123"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    # Existing-by-id branch fires (real mapping refreshed); promotion not invoked.
    assert result.updated == 1
    assert result.new == 0

    # Stub mapping untouched.
    stub_row = await db.execute(text(
        "SELECT external_client_id, source_metadata FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_id = 'name:Oracle'"
    ), {"t": tenant_id})
    s = stub_row.one()
    assert s.external_client_id == "name:Oracle"
    assert s.source_metadata == {"stub": True, "origin": "jobs_phase"}

    # No promotion audit row.
    audit_rows = await db.execute(text(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.client_mapping.promoted'"
    ), {"t": tenant_id})
    assert audit_rows.scalar_one() == 0
```

- [ ] **Step 2: Add the full-interleave test**

Append to `tests/modules/ats/test_importer_jobs.py`:

```python
@pytest.mark.asyncio
async def test_jobs_then_clients_then_jobs_converges_to_one_org_unit(
    db, jobs_fixture,
):
    """Full interleave proof. Sequence:
      1. _sync_jobs sees Oracle job (name only) → creates stub.
      2. _sync_clients later returns Oracle with real id → promotes stub.
      3. _sync_jobs runs again → matches by name against the now-promoted
         mapping; no second stub, no second org_unit, no duplicates.
    """
    from app.modules.ats.importer import ATSImporter
    from app.modules.ats.schemas import ATSClientPayload

    def _clients_adapter(tenant_id, client_payloads):
        state = ATSConnectionState(
            id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), vendor="ceipal",
            credentials={},
        )
        adapter = AsyncMock()
        adapter.state = state
        adapter.vendor = "ceipal"
        adapter.list_clients = lambda since=None: _async_iter(client_payloads)
        adapter.list_users = lambda since=None: _async_iter([])
        return adapter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j-interleave",
        external_client_id="",
        external_client_name="Oracle",
        title="t",
        status="Active",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    importer = ATSImporter()

    # Step 1: jobs sync creates the stub.
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))

    # Step 2: clients sync promotes the stub.
    client_payload = ATSClientPayload(
        external_id="ABC123",
        name="Oracle",
        raw={"id": "ABC123"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    await importer._run_phase(
        "clients", importer._sync_clients,
        _clients_adapter(tenant_id, [client_payload]),
    )

    # Step 3: jobs sync again — matches by name against the promoted mapping.
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))

    # Convergence: exactly one Oracle org_unit, one Oracle mapping, real id.
    unit_count = await db.execute(text(
        "SELECT COUNT(*) FROM organizational_units "
        "WHERE client_id = :t AND name = 'Oracle'"
    ), {"t": tenant_id})
    assert unit_count.scalar_one() == 1

    mapping_row = await db.execute(text(
        "SELECT external_client_id, external_client_name FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_name = 'Oracle'"
    ), {"t": tenant_id})
    rows = mapping_row.all()
    assert len(rows) == 1
    assert rows[0].external_client_id == "ABC123"
```

- [ ] **Step 3: Run the new tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_jobs.py::test_jobs_then_clients_then_jobs_converges_to_one_org_unit \
         tests/modules/ats/test_importer_clients_users.py::test_sync_clients_does_not_promote_when_real_mapping_exists \
         -v
```

Expected: both PASS.

- [ ] **Step 4: Run the full ATS suite a final time**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/tests/modules/ats/test_importer_clients_users.py \
        backend/nexus/tests/modules/ats/test_importer_jobs.py
git commit -m "$(cat <<'EOF'
test(ats): cover no-promotion edge case + full jobs/clients/jobs interleave

  - test_sync_clients_does_not_promote_when_real_mapping_exists — when
    BOTH a real and a stub mapping already exist for the same client,
    _sync_clients's existing-by-id short-circuit runs first; the stub
    is left as a recruiter-resolvable orphan (documented out-of-scope).
  - test_jobs_then_clients_then_jobs_converges_to_one_org_unit — full
    interleave proof: jobs → clients → jobs ends with one org_unit, one
    mapping with the real id, no duplicates.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Manual smoke checklist

This is a verification step, not a code change. Run it before considering the work complete.

- [ ] **Step 1: Start the stack**

```bash
docker compose -f backend/nexus/docker-compose.yml up -d
cd frontend/app && npm run dev
```

- [ ] **Step 2: Pre-state inspection (optional, illuminating)**

In Supabase Studio (`http://localhost:54323`), run:

```sql
SELECT id, external_id, status, org_unit_id
FROM job_postings
WHERE source = 'ats_ceipal' AND org_unit_id IS NULL;
```

Note the count — these are pre-existing rows from before this change. They will NOT be backfilled automatically (out of scope).

- [ ] **Step 3: Trigger a jobs-phase sync**

Go to `http://localhost:3000/jobs` and click "Sync jobs from ATS". Watch the SyncProgressBar to completion.

- [ ] **Step 4: Verify on `/settings/org-units`**

Navigate to `http://localhost:3000/settings/org-units`. Expand the tree under the root company unit. Confirm:

- Any new `client_account` org_units are present for client names that previously had no mapping.
- Each carries the "profile incomplete" caution badge (rendered by `OrgUnitNode.tsx:200-212`).
- Clicking into a stub shows `company_profile = {"name": "<name>"}` and `completion_status = 'pending'`.

- [ ] **Step 5: Verify on `/jobs`**

Go back to `/jobs`. Confirm:

- Jobs whose client was previously unmappable now show the StatusPill "Awaiting setup" instead of the "Not set up" chip.
- The "Not set up" chip remains only for the residual case (`org_unit_id IS NULL`) — these are jobs from before this change OR jobs Ceipal returned with no `client` field at all.

- [ ] **Step 6: Verify reconciliation**

If the recruiter's Ceipal account has any of the same clients in `getClientsList`, trigger a clients-phase sync (currently this happens implicitly via the full sync from `/settings/team`'s "Sync users from ATS" button — but that's the users phase; check whether your current entrypoint triggers `_sync_clients`). After clients-phase runs:

```sql
SELECT external_client_id, external_client_name, source_metadata
FROM ats_client_mappings
WHERE tenant_id = '<your-tenant>' AND ats_vendor = 'ceipal'
ORDER BY last_synced_at DESC LIMIT 20;
```

For any client that was a stub and is now in `getClientsList`: `external_client_id` should be the real Ceipal id (not `name:<name>`), and `source_metadata` should contain `contacts`/`raw` (not `{"stub": true, "origin": "jobs_phase"}`).

- [ ] **Step 7: Verify audit trail**

```sql
SELECT action, payload, created_at FROM audit_log
WHERE action IN ('ats.client_mapping.created', 'ats.client_mapping.promoted')
ORDER BY created_at DESC LIMIT 20;
```

Expected: rows for both action types. The `payload.stub = true` flag distinguishes job-phase-created stubs from clients-phase-created mappings.

- [ ] **Step 8: Done**

If everything above checks out, the feature is shipped.

---

## Self-Review

**Spec coverage:**

- ✅ Stub creation in `_sync_jobs` for unknown client name → Task 2 implements, Task 1 + Task 3 test.
- ✅ Synthetic id format `"name:<name>"` → Task 2 implementation; Task 1 + Task 3 (colon test) assert.
- ✅ Stub org_unit shape (`company_profile = {"name": name}`, `completion_status='pending'`, parent = root) → Task 2 implementation; Task 1 asserts.
- ✅ Stub mapping shape (`source_metadata={"stub": True, "origin": "jobs_phase"}`) → Task 2 implementation; Task 1 asserts.
- ✅ Job links to stub with `status='blocked_pending_client_setup'` → Task 2 reuses existing pending→blocked branch; Task 1 asserts.
- ✅ Idempotency on re-run → Task 3 idempotency test.
- ✅ Audit action `ats.client_mapping.created` with `stub`+`origin` payload → Task 2 implementation; Task 1 asserts.
- ✅ Promotion in `_sync_clients` → Task 5 implementation; Task 4 tests.
- ✅ Promotion preserves org_unit → Task 4 asserts (same id, completion_status, company_profile).
- ✅ Promotion audit action `ats.client_mapping.promoted` → Task 5 implementation; Task 4 asserts.
- ✅ No-promotion-when-real-mapping-exists edge case → Task 6 test.
- ✅ Full interleave convergence → Task 6 test.
- ✅ Empty-name fallback to NULL org_unit_id → already covered by existing `test_job_with_neither_id_nor_name_is_imported_unlinked` and `test_job_with_unknown_client_mapping_is_imported_unlinked`; Task 3 polishes docstrings to reflect the new precondition.
- ✅ Colon-in-name handling → Task 3 explicit test.

**Placeholder scan:** no TBDs, no "implement later", every code block is complete and copy-pasteable.

**Type consistency:**
- `_get_or_create_client_stub_by_name` signature in Task 2 matches every call site in Task 2's wiring.
- Synthetic id format `f"name:{external_client_name}"` is identical in helper (Task 2), promotion check (Task 5), and every test assertion.
- `source_metadata` shape on stub creation `{"stub": True, "origin": "jobs_phase"}` is identical between Task 2 implementation, Task 1 assertion, Task 3 assertion, and Task 6 fixture seed.
- `source_metadata` shape on promotion `{"contacts": ..., "raw": ...}` matches the existing `_sync_clients` line 165 update path.
- Audit `action` strings are identical: `"ats.client_mapping.created"` (Task 2), `"ats.client_mapping.promoted"` (Task 5).
- Audit payload keys (`stub`, `origin`, `from_external_client_id`, `to_external_client_id`) are identical between emit (Tasks 2/5) and assertion (Tasks 1/4).
