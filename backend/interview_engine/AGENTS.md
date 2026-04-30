# Interview Engine — Agent Instructions

## What this container is

A LiveKit Agent worker that conducts the structured AI-led interview.
It is **not** a standalone service — it is wired into the Nexus
backend's session lifecycle and is dispatched on demand when a
candidate clicks Start.

Single deployable container. One process, one agent, one room at a
time per dispatch (the worker pool concurrency is set by LiveKit).

## How it boots

1. The candidate clicks Start → Nexus's `/api/candidate-session/{token}/start`
   mints a LiveKit candidate access token, mints a single-use HS256
   engine dispatch JWT (`purpose='engine_dispatch'`), records both,
   then dispatches the agent into the room with the JWT in dispatch
   metadata.
2. The engine worker (this container) sees the dispatch, joins the
   room as the agent participant, and reads the JWT out of the
   dispatch metadata.
3. The engine calls `GET /api/internal/sessions/{id}/config` on Nexus
   with the JWT as the Authorization bearer. Nexus's
   `verify_engine_token` validates the JWT (HS256 pinning, purpose
   claim, atomic single-use INSERT into `engine_token_uses`) and
   responds with the full `SessionConfig` (job, candidate, stage,
   questions, signals, ancestry-walked company profile).
4. The engine instantiates an `InterviewerAgent` with that config and
   starts the structured-interview state machine.
5. On `Action.CLOSE`, the engine compiles a `SessionResult` and
   `POST`s it to `/api/internal/sessions/{id}/results` (using the
   same JWT against a different `(jti, endpoint)` slot, so single-use
   per-endpoint).

## Path-dep on Nexus

The engine imports types directly from Nexus to keep the wire
contract single-sourced:

```python
from app.modules.interview_runtime.schemas import (
    SessionConfig,
    SessionResult,
    QuestionConfig,
    # …
)
```

These come from the Nexus path-dep declared in `pyproject.toml` and
are mounted into the container at `/app/nexus`. The engine never
defines its own copy of these schemas — when the contract evolves
on the Nexus side, the engine picks it up automatically (modulo a
rebuild).

## Two virtualenvs

The image installs Nexus and `livekit-agents` into separate venvs
because Nexus pins `openai<2` (langfuse 2.x constraint) and
`livekit-agents` requires `openai>=2`. The Dockerfile lays
`PYTHONPATH` so the engine venv resolves first, the Nexus venv
second. See the long Dockerfile comment for the resolution order.

This is tracked as tech-debt; the cleanup is to land
`langfuse>=3` and re-unify into a single venv.

## Tests

Engine pytest tests live under `backend/interview_engine/tests/`.
Pytest is **not** baked into either venv — install it on demand:

```bash
docker compose run --rm --entrypoint bash interview-engine \
  -c "uv pip install --python /venv/engine/bin/python pytest pytest-asyncio respx --quiet \
      && cd /app/interview_engine \
      && PYTHONPATH=/app/interview_engine:/app/nexus /venv/engine/bin/python -m pytest tests/ -v"
```

For host-mounted iteration on agent code (the engine's source is
`COPY`'d at image-build, not volume-mounted), pass
`-v $(pwd)/backend/interview_engine:/app/interview_engine` to the
`docker compose run` command.

## Source notes

- `agent.py` — entrypoint. Registers plugins, fetches config via
  `nexus_client.fetch_session_config`, instantiates the agent, joins
  the room.
- `agents/interviewer.py` — `InterviewerAgent(Agent)`. Owns the
  state machine, the `record_observation` `@function_tool` (the core
  per-turn loop), and `_publish_progress_attributes` (which writes
  `current_question_index`, `total_questions`, `time_remaining_seconds`
  to the agent participant's LiveKit attributes so the candidate's
  ProgressBanner can render `Q3 of 9 · 11 min remaining`).
- `state_machine.py` — `InterviewStateMachine` + `Action` enum
  (`PROBE | ADVANCE | SKIP | CLOSE`). Pure, no IO.
- `nexus_client.py` — httpx wrapper for the two internal-API calls.
  Retries on 5xx + network. Permanent errors (auth, validation)
  raise typed exceptions and the engine writes a JSON fallback file
  to `engine_config.results_fallback_dir` rather than losing the
  result.
- `prompt_builder.py` — builds the system prompt from a versioned
  template at `backend/nexus/prompts/v1/interview/interviewer.txt`
  (loaded via `app.ai.prompts.prompt_loader`).

## Reference: LiveKit docs

LiveKit is a fast-evolving project — refer to the latest
documentation at `https://docs.livekit.io/`. The LiveKit MCP server
at `https://docs.livekit.io/mcp` provides browsing and search tools
(`get_docs_overview`, `get_pages`, `docs_search`, `code_search`,
`get_changelog`, `get_pricing_info`). Prefer browsing
(`get_docs_overview`, `get_pages`) over search, and `docs_search`
over `code_search`.

## Graceful close + rejoin (shipped 2026-04-30)

- The agent publishes a `session_outcome` participant attribute via
  `set_attributes` immediately before shutdown. The candidate's frontend
  reads this on the `Disconnected` event to route between
  `CompletionScreen` (`outcome='completed'`) and `DisconnectError` with
  code `ENGINE_ERROR` (`outcome='error'`). See
  `agents/interviewer.py::_publish_session_outcome`.
- Two paths reach close: (a) state machine emits `Action.CLOSE` →
  `record_observation` persists + publishes; (b) candidate disconnects
  mid-session → `session.on('close')` in `agent.py` persists a partial
  result and publishes `'completed'` so Nexus transitions
  `session.state` to `'completed'` and the wizard's pre-check on next
  visit doesn't show the rejoin path for an effectively-ended session.
- Mid-session rejoin: the candidate's frontend wizard sees
  `state='active'` from `/pre-check`, mounts `<App mode='rejoin'>`,
  which calls `POST /api/candidate-session/{token}/rejoin` (mints a
  fresh LiveKit token for the same room without re-dispatching the
  engine — see `app/modules/session/service.py::rejoin_session`).

## Out of scope (Phase 3D follow-ups)

- LiveKit Egress recording pipeline.
- Real-time scoring + probe selection (Phase 3D `analysis` module).
