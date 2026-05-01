# Interview Engine — End-to-End Runbook

This runbook walks a new engineer (or yourself, returning to this code
after time away) through bringing up the full interview pipeline
locally and validating that a candidate can complete a screening
session from invite link to recorded result.

If any step fails, that's the fault. Don't paper over — fix it before
moving to the next step.

---

## Stack overview

Three Docker containers + Supabase + the Next.js dev server:

| Component | What it does |
|---|---|
| `nexus` | FastAPI backend, candidate-session API, `/start` LiveKit provisioning, internal API for the engine. |
| `nexus-worker` | Dramatiq worker for JD enrichment + question bank generation. |
| `interview-engine` | LiveKit Agent worker. Joins candidate rooms when dispatched, runs the structured interview state machine, posts the result back via the internal API. |
| `supabase` (local) | Postgres + Auth + Inbucket (mock SMTP). |
| Next.js `frontend/app` | Recruiter dashboard + candidate interview surface. |

The engine container ships with two virtualenvs (`/venv/nexus` and
`/venv/engine`) layered via `PYTHONPATH` because Nexus pins
`openai<2` (langfuse 2.x constraint) and `livekit-agents` requires
`openai>=2`. See `backend/interview_engine/Dockerfile` for the
detailed comment.

---

## Manual end-to-end runbook

1. `cp backend/nexus/.env.example backend/nexus/.env`; fill
   `LIVEKIT_*`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`,
   `CARTESIA_API_KEY`, `INTERVIEW_ENGINE_JWT_SECRET`,
   `CANDIDATE_JWT_SECRET`, `FRONTEND_BASE_URL`,
   `NEXUS_INTERNAL_BASE_URL=http://nexus:8000`.
2. `supabase start`.
3. `cd backend/nexus && docker compose up --build` — three containers
   boot: `nexus`, `nexus-worker`, `interview-engine`.
4. Verify `interview-engine` logs show
   `engine.worker.registered agent_name=Dakota-1785` (or whichever
   agent name your `.env` has set).
5. Frontend: `cd frontend/app && npm install && npm run dev`.
6. In the recruiter dashboard, create a job → confirm signals → wait
   for the per-stage question bank to reach `confirmed`.
7. Add a candidate, send an invite, open Inbucket
   (`http://localhost:54324`) for the candidate link.
8. Walk the candidate wizard: Consent → OTP (if enabled for the
   stage) → Camera/Mic → Start.
9. **Verify (UI):** within ~3 seconds of clicking Start, the
   candidate hears the agent's greeting; the progress banner
   advances on every turn (`Q3 of 9 · 11 min remaining`); the
   transcript pane populates with both speakers; the agent
   eventually reaches `Action.CLOSE` and disconnects; the candidate
   sees the completion screen.
10. **Verify (Postgres):**
    ```sql
    SELECT state, questions_asked, probes_fired,
           agent_completed_at, jsonb_array_length(transcript)
    FROM sessions WHERE id = '<session_id>';
    ```
    Expect `state = 'completed'`, `transcript` populated, and the
    counters non-zero.
11. **Verify (Langfuse):** the trace appears in the self-hosted
    Langfuse instance with the session's `correlation_id`.

---

## Known limitations (Phase 3C.2 — to be addressed before GA)

These are documented gaps that the chunk-final review surfaced. They
do not block local end-to-end validation but should not ship to a
production tenant.

- **No graceful-vs-error disconnect distinction.** The candidate's
  `LiveSessionShell.onDisconnected` flips outcome to `'completed'`
  for every disconnect reason — a clean agent-driven CLOSE, a
  candidate network drop, an agent crash, and a server-side kick all
  route to the same "Thanks for completing your interview" screen.
  A structured close signal from the engine (LiveKit data message or
  room metadata) would let the UI distinguish graceful end from
  error. Tracked for Phase 3D (when reporting needs a definitive
  session-end signal anyway).
- **No rejoin flow.** A candidate who refreshes the tab or loses
  network mid-session sees the existing `AlreadyStartedPanel`
  ("If you were disconnected, the rejoin flow will be available in
  the next release."). Same root cause as the disconnect-signal gap
  above; same Phase 3D follow-up.
- **No LiveKit Egress (recording) pipeline.** The candidate's audio
  and the agent's audio are NOT recorded today. Required before any
  external client can audit a session, and required to support the
  recruiter "review session" flow.

---

## Common bringup failures and fixes

- **`engine.worker.registered` log line never appears.** Check that
  `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` resolve a valid project
  with a worker pool. The engine retries connection forever — kill
  the container and inspect.
- **Cartesia TTS errors mid-session ("quota_exceeded").** Top up the
  Cartesia account. If you need to keep working without spend, the
  workaround is to swap `app/ai/realtime.py::build_tts_plugin` to
  OpenAI TTS (\~5 line edit). Don't permanently swap — coordinate
  with the team before merging that.
- **Candidate JWT is single-use.** Once `/start` succeeds the token
  is consumed. For repeated UI testing, send a fresh invite each
  pass. The recruiter dashboard's resend supersedes the previous
  token automatically.
- **The engine container has its source baked at image-build time
  via `COPY`, not via volume mount.** If you edit
  `backend/interview_engine/*.py` you must rebuild the engine image
  (`docker compose build interview-engine`) before the change takes
  effect.
- **Tests inside the engine container** require pytest and
  pytest-asyncio installed at runtime — they're not baked in. From
  inside the container: `uv pip install --python /venv/engine/bin/python pytest pytest-asyncio`.
