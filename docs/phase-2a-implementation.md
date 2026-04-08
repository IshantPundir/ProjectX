# Phase 2A — JD Pipeline & Signal Extraction

Implementation walkthrough for Phase 2A. See also:
- Design spec: `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-09-phase-2a-implementation.md`

---

## What This Phase Built

1. **Company Profile capture** — strict 4-field schema (`about`, `industry`, `company_stage`, `hiring_bar`) stored on `organizational_units.company_profile`, with `company_profile_completed_at/by` tracking stamps on save.
2. **Raw JD upload** — plain text paste form feeds the new `job_postings` table, with a pre-check gate that blocks creation until the target org unit's ancestry has a completed company profile.
3. **Call 1 signal extraction** — async Dramatiq actor that calls `gpt-5.2` via `instructor` (structured output enforcement) and `langfuse.openai` (LLM tracing, no-op when `LANGFUSE_HOST` unset). Writes an immutable `job_posting_signal_snapshots` row with per-chip provenance (`ai_extracted` vs `ai_inferred`).
4. **`jobs.view` permission** — new canonical permission seeded into `Admin`, `Recruiter`, and `Hiring Manager` system roles (migration 3).
5. **Reusable `updated_at` trigger function** — `public.set_updated_at()` created in migration 2 and applied to `job_postings`. Phase 1 tables have a latent gap — they don't have the trigger; retrofitting is a cross-cutting cleanup tracked in Known Gaps.

---

## Module Layout

New / modified files relative to Phase 1:

```
backend/nexus/
├── app/
│   ├── config.py                                      ← +openai_* settings, -anthropic_api_key
│   ├── main.py                                        ← +exception handlers (409, 422)
│   ├── models.py                                      ← +JobPosting, JobPostingSignalSnapshot, Session
│   ├── worker.py                                      ← NEW — Dramatiq entrypoint
│   ├── ai/                                            ← NEW package
│   │   ├── __init__.py
│   │   ├── config.py                                  ← AIConfig (env-driven)
│   │   ├── client.py                                  ← get_openai_client() — instructor + langfuse
│   │   ├── prompts.py                                 ← PromptLoader
│   │   └── schemas.py                                 ← ExtractionOutput + provenance validators
│   └── modules/
│       ├── auth/permissions.py                        ← +jobs.view
│       ├── jd/                                        ← fleshed from Phase 1 stub
│       │   ├── __init__.py
│       │   ├── actors.py                              ← extract_and_enhance_jd Dramatiq actor
│       │   ├── authz.py                               ← require_job_access() ancestry walk
│       │   ├── errors.py                              ← IllegalTransitionError, CompanyProfileIncompleteError, sanitize_error_for_user
│       │   ├── router.py                              ← 5 endpoints under /api/jobs
│       │   ├── schemas.py                             ← Pydantic request/response
│       │   ├── service.py                             ← create_job_posting, list, get, retry, status
│       │   ├── sse.py                                 ← job_status_event_generator
│       │   └── state_machine.py                       ← LEGAL_TRANSITIONS + transition() helper
│       └── org_units/
│           ├── company_profile.py                     ← NEW — strict Pydantic schema
│           └── service.py                             ← +find_company_profile_in_ancestry, profile validation, completed_at/by stamps
├── prompts/
│   └── v1/
│       └── jd_enhancement.txt                         ← NEW — Call 1 system prompt
├── tests/
│   └── fixtures/
│       └── company_profile_enums.json                 ← NEW — enum parity source of truth
└── docker-compose.yml                                 ← +nexus-worker service
```

---

## Data Flow — Call 1

1. Recruiter POSTs `/api/jobs` with a title and raw JD via the authenticated frontend.
2. `create_job_posting()` walks the org unit ancestry via `find_company_profile_in_ancestry()` looking for a completed `company_profile`. If none is found, raises `CompanyProfileIncompleteError` → router returns HTTP 422 with `org_unit_id` in the body for frontend deep-linking.
3. Profile found → INSERT a `job_postings` row in `status='draft'`. Flush.
4. `state_machine.transition()` moves `draft → signals_extracting`. The helper writes an `audit_log` row via `log_event()` containing `{from, to, correlation_id}`.
5. Service calls `extract_and_enhance_jd.send(...)` via Dramatiq (lazy import to break a circular dependency).
6. Service `db.commit()` — the HTTP 201 response returns with `status: signals_extracting` and `latest_snapshot: null`.
7. A separate `nexus-worker` container picks up the message from the `jd_extraction` queue.
8. The actor opens a `get_bypass_session()` DB session, sets `app.current_tenant` via raw `SET LOCAL` (no HTTP context, so RLS must be re-established), and delegates to `_run_extraction()`.
9. `_run_extraction()`: idempotency guard (`if job.status != 'signals_extracting': return`), loads the profile, builds the user message in mandatory order (**company profile → raw JD → project scope**), calls OpenAI via `instructor` with `response_model=ExtractionOutput` and `reasoning_effort='medium'`.
10. On success: persists `description_enriched` + writes a `job_posting_signal_snapshots` row (version=1), transitions `signals_extracting → signals_extracted`, commits.
11. On failure: Dramatiq's retry middleware retries up to 3 times with exponential backoff. Intermediate retries leave state unchanged. On the final retry, `sanitize_error_for_user()` maps the exception type to a fixed safe string, writes it to `job_postings.status_error`, transitions `signals_extracting → signals_extraction_failed`, commits.

Throughout: the frontend is watching the SSE status stream at `GET /api/jobs/{id}/status/stream`. Every 1.5s the backend polls the row and emits a `status` event on change. Terminal states close the stream.

---

## How to Add a New Prompt Version

1. Create `backend/nexus/prompts/v2/` and copy + edit the prompt file.
2. Instantiate `PromptLoader(version="v2")` in the code path that should use the new version (or switch the default in `app/ai/prompts.py`).
3. Restart the worker: `docker compose restart nexus-worker`.
4. The next Call 1 dispatch picks up the new prompt.

A hot-reload endpoint is deferred — see the design spec's Deferred Hardening section.

---

## How to Swap the OpenAI Model for a Task

1. Edit `.env`: set `OPENAI_EXTRACTION_MODEL=<new-model-id>` (and optionally `OPENAI_EXTRACTION_EFFORT=<effort>`).
2. Restart the worker: `docker compose restart nexus-worker`.
3. The next dispatch uses the new model. No code change, no redeploy.

`AIConfig` properties read from `settings` on every access, so restarting the worker is sufficient — no need to rebuild the image.

---

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Job stuck in `signals_extracting` forever | Dramatiq enqueue succeeded but no worker consumed the message (worker down, Redis unreachable when `.send` was called) | `docker compose ps nexus-worker`; `docker compose logs nexus-worker`. No automatic recovery in 2A — operator must manually `UPDATE job_postings SET status = 'signals_extraction_failed' WHERE id = ...` then use the retry button. |
| All Call 1 attempts fail with `signals_extraction_failed` | Wrong model ID in `.env`, or `reasoning_effort` parameter shape mismatch for the model | `docker compose logs nexus-worker \| grep jd.actor.call1_failed` — the structlog `exc_info` will show the exception type. Check `.env` `OPENAI_EXTRACTION_MODEL`. |
| Langfuse trace not appearing | `LANGFUSE_HOST` empty or Langfuse instance unreachable | Langfuse is intentionally a no-op when the host is unset; set `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` in `.env` to enable. |
| 422 on JD creation | Target org unit has no ancestor with a completed profile | Visit Settings → Org Units → [company] → Company Profile tab and fill all four fields. |
| 409 Conflict on retry | Job is not in `signals_extraction_failed` state | Only failed jobs can be retried. The retry endpoint's precondition is enforced by the state machine. |
| Dramatiq worker exits on boot with `--watch` error | `--watch` flag requires `watchdog` dependency not installed in 2A | The docker-compose command was trimmed to `dramatiq app.worker --processes 2 --threads 4`. For dev hot-reload, add `watchdog` to `pyproject.toml` extras and re-add `--watch /app/app`. |
| `supabase db reset` wipes `projectx_test` database | The reset command drops all databases; `projectx_test` isn't recreated automatically | After `supabase db reset`, run `docker exec supabase_db_<project> psql -U postgres -c "CREATE DATABASE projectx_test;"` before running pytest. |
| `pytest` in container fails with `ConnectionRefusedError` to `127.0.0.1:54322` | Phase 1 had this as the default, unreachable from inside Docker | Phase 2A fixed the default in `conftest.py` to `host.docker.internal:54322`. Override via `TEST_DATABASE_URL` env var for non-container runs. |

---

## Known Gaps

See the Deferred Hardening section of the design spec for the full list. The most important for operators:

1. **Dual-write risk**: if Redis is down when a job is created, the row sits in `signals_extracting` with no automatic recovery in 2A. Manual fix: update the row to `signals_extraction_failed` and use the retry button.
2. **`updated_at` trigger only on Phase 2A tables**: Phase 1 tables (`clients`, `users`, etc.) don't have the trigger. `public.set_updated_at()` is defined globally in migration `20260410000001` and can be applied to Phase 1 tables in a future cleanup.
3. **No frontend tests**: Vitest is deferred to Phase 2B.
4. **`--watch` hot-reload not enabled in dev**: requires adding `watchdog` to `pyproject.toml` and re-adding the flag to the docker-compose worker command.
5. **Prompt hot-reload endpoint not built**: restart the worker to pick up new prompt files.
