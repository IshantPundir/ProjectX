# Disaster Recovery — runbook & drill log

This directory holds the DR runbook and the quarterly restore-drill logs required
by the root `CLAUDE.md` → "Backup, Restore & Disaster Recovery".

> **Status (2026-06-03):** runbook seeded. **No restore drill has been logged yet** —
> the first quarterly drill is an open action item (see below). This honesty note exists
> so the gap is visible rather than implied-covered.

---

## Targets

| Metric | Target |
|---|---|
| Postgres RPO | ≤ 15 min (PITR) |
| Postgres RTO | ≤ 4 h |
| Backup cadence | Managed daily backups + continuous PITR (Supabase) |

Redis (Dramatiq broker + any transient session signal) is **ephemeral by design** —
nothing that must survive a flush lives only in Redis. The durable record of a session
is in Postgres (`sessions`, `session_reports`, audit log) plus the event-log envelope.

Object storage:
- **AWS S3 (resumes):** versioning ON + MFA-delete ON — recover by object version.
- **Cloudflare R2 (recordings / reels / thumbnails):** no object versioning. These are
  reproducible artifacts — a lost reel/thumbnail is regenerated from the recording +
  report; a lost recording is unrecoverable (accepted residual, see threat-model).

## What can fail, and the recovery path

| Failure | Recovery |
|---|---|
| Bad migration / data corruption | PITR to just before the event; replay forward if needed. |
| Full DB loss | Restore latest managed backup; verify RLS + run tests (drill procedure below). |
| Redis flush | None needed — broker is ephemeral; in-flight Dramatiq jobs are idempotent and re-enqueued. |
| R2 object loss (reel/thumbnail) | Regenerate via the reel/vision actors from the source recording + report. |
| S3 object loss (resume) | Restore prior object version. |

## Quarterly restore drill (required)

1. Trigger a restore of the latest backup into a **scratch** database (never production).
2. Point a throwaway app/test config at the scratch DB with `DB_RUNTIME_ROLE=nexus_app`.
3. Run the boot-time RLS assertion — start the app (or invoke `_assert_rls_completeness`)
   and confirm it does **not** abort (every tenant-scoped table has its policy pair).
4. Run `pytest` against the scratch DB and confirm green.
5. Record timings (restore start → app-ready) to validate RTO ≤ 4 h and RPO ≤ 15 min.
6. **Drop the scratch DB.**
7. Log the result as `docs/dr/YYYY-MM-DD-restore-drill.md` using the template below.

### Drill log template

```markdown
# Restore drill — YYYY-MM-DD
- Backup restored (timestamp / PITR target):
- Scratch DB:
- RLS assertion (_assert_rls_completeness): PASS / FAIL
- pytest: PASS / FAIL (N passed, M failed)
- Measured RTO (restore → app-ready):
- Measured RPO (data-loss window):
- Anomalies / follow-ups:
- Scratch DB dropped: yes
```

## Open action items

- [ ] Run + log the **first** quarterly restore drill (`docs/dr/<date>-restore-drill.md`).
- [ ] Confirm Supabase PITR retention window satisfies RPO ≤ 15 min on the current plan.
