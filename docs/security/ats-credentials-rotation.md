# ATS Credentials — Key Rotation Runbook

**Last reviewed:** 2026-05-12

## Scope

This runbook covers rotation of the `ATS_CREDENTIALS_ENCRYPTION_KEYS` setting
that protects per-tenant ATS credentials (email, password, API key) and
OAuth-style tokens (access_token, refresh_token) at rest.

It does NOT cover rotation of tenants' upstream credentials in Ceipal /
Greenhouse / Workday themselves — those are tenant-owned and rotated via
each vendor's console.

## When to rotate

- **Routine:** every 90 days.
- **Personnel change:** any engineer with access to prod env vars / AWS
  Secrets Manager leaves the team or changes role.
- **Incident:** any signal of key compromise (leaked log, lost dev machine,
  third-party breach affecting our key custodian).

## Pre-conditions

- [ ] You have write access to the prod env var store (Railway env or AWS
      Secrets Manager) AND the staging equivalent.
- [ ] You have the existing `ATS_CREDENTIALS_ENCRYPTION_KEYS` value (or at
      least confirmation it is non-empty).
- [ ] No active ATS sync is in flight (check `ats_sync_logs.status='running'`).

## Procedure

### 1. Generate a new key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Save the output to your password manager labeled `ats-encryption-key-<YYYY-MM-DD>`.

### 2. Prepend the new key to the active list

`ATS_CREDENTIALS_ENCRYPTION_KEYS` is comma-separated; the FIRST key encrypts
new values, all keys are tried for decrypt. Append, not replace:

```
ATS_CREDENTIALS_ENCRYPTION_KEYS=<NEW_KEY>,<OLD_KEY>
```

Update **staging first**, deploy, smoke-test:
- POST /api/ats/connections (create a test connection) — succeeds.
- GET /api/ats/connections/{id} — returns metadata.
- Wait one scheduler tick — sync_logs row appears with status='success'.

If staging is clean, repeat on prod.

### 3. Backfill (re-encrypt with the new key)

> **MVP limitation:** the backfill CLI `app/cli/ats_reencrypt.py` is **not
> yet shipped**. Until it lands, MultiFernet handles the asymmetric state
> transparently: new writes use the new key; reads decrypt under either.
> Step 3 is therefore a no-op for MVP — proceed directly to Step 4 ONLY when
> you are willing to let stale ciphertexts age out naturally (next sync
> rewrites tokens; credentials_ciphertext lingers until the recruiter
> reconnects). For an incident-driven rotation where the old key must be
> revoked immediately, hold both keys in the env until the backfill script
> is written, OR force a reconnect of every connection (DELETE + recreate
> via the API).

When the script exists:

```bash
docker compose run --rm nexus python -m app.cli.ats_reencrypt
```

(The reencrypt CLI iterates every ats_connections row, decrypts under
MultiFernet, re-encrypts under the new key. Idempotent.) Tracking row count
in stdout; should match `SELECT COUNT(*) FROM ats_connections`.

### 4. Drop the old key

After the backfill confirms 100% coverage (OR after the natural-churn window
for incident-free rotations), remove the OLD key from the env:

```
ATS_CREDENTIALS_ENCRYPTION_KEYS=<NEW_KEY>
```

Deploy, smoke-test once more. The system should be operating exclusively on
the new key.

### 5. Log + close

- Update this runbook's "Last reviewed" date.
- Record the rotation in your team's security log:
  - date, operator, reason (routine / personnel / incident), affected envs.

## Rollback

If decrypt errors appear in `ats_sync_logs.error_summary` matching
`InvalidToken` after Step 4:
- Re-add the old key to the front of the list: `<NEW>,<OLD>`.
- Investigate which connection's ciphertext is still on the old key (likely
  a row created during the deploy window before backfill completed).
- Re-run Step 3's backfill (or, until that script ships, DELETE + recreate
  the affected connection via `POST /api/ats/connections`).

## Audit trail

Every rotation must produce an audit_log row of `action='ats.encryption_key.rotated'`
with payload `{"operator": "<email>", "reason": "<routine|personnel|incident>"}`.
This is the trail SOC 2 reviewers expect.

(Implementation note: the reencrypt CLI will emit this audit row at completion.
Until the CLI ships, write the audit row manually via a one-off psql session
on rotation.)
