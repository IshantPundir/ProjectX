# Phase 3C.2 — interview engine container.
#
# Build context = backend/ (set by docker-compose.yml so the COPY
# commands below can reach both `nexus/` and `interview_engine/`).
#
# The engine installs nexus as a path-dep (see pyproject.toml) so it can
# import:
#   - app.modules.interview_runtime.schemas (wire contract)
#   - app.ai.realtime  (LiveKit plugin factories)
#   - app.ai.prompts   (interviewer prompt loader)
#
# Dependency isolation strategy
# ─────────────────────────────
# nexus pins openai<2 (via langfuse 2.x + instructor constraints).
# livekit-agents>=1.5.4 requires openai>=2. A single shared site-packages
# cannot satisfy both. Solution: two virtualenvs.
#
#   /venv/nexus  — nexus + its transitive deps (openai 1.x, langfuse 2.x)
#   /venv/engine — livekit-agents + plugins + openai>=2
#
# At runtime PYTHONPATH is layered so that:
#   1. /app/interview_engine  (engine source, editable-style)
#   2. /app/nexus             (nexus source, editable-style)
#   3. /venv/engine site-packages  (livekit, openai v2, …)
#   4. /venv/nexus  site-packages  (langfuse, instructor, …)
#
# Because /venv/engine comes before /venv/nexus in PYTHONPATH, the engine's
# openai v2 wins when `import openai` is resolved, while nexus-specific
# packages (langfuse, instructor, asyncpg, sqlalchemy, …) resolve from the
# nexus venv.
#
# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy both packages (both live in the backend/ build context).
COPY nexus /app/nexus
COPY interview_engine /app/interview_engine

RUN pip install --no-cache-dir uv

# ── nexus venv ─────────────────────────────────────────────────────────────
# Install nexus + its full dependency tree into an isolated venv.
# This satisfies nexus's openai<2 / langfuse<3 / instructor<2 constraints
# without leaking them into the engine's package set.
RUN uv venv /venv/nexus --python python3.13 \
    && uv pip install --python /venv/nexus/bin/python -e /app/nexus

# ── engine venv ─────────────────────────────────────────────────────────────
# Install the engine's direct dependencies (excluding nexus itself — nexus
# source is imported directly via PYTHONPATH). livekit-agents requires
# openai>=2, which is incompatible with nexus's pin, so it lives here.
RUN uv venv /venv/engine --python python3.13 \
    && uv pip install --python /venv/engine/bin/python \
        "livekit-agents[silero,turn-detector]>=1.5.4" \
        "livekit-plugins-ai-coustics>=0.2.7" \
        "livekit-plugins-noise-cancellation~=0.2" \
        "livekit-plugins-deepgram>=1.5.4" \
        "livekit-plugins-openai>=1.5.4" \
        "livekit-plugins-cartesia>=1.5.4" \
        "pydantic>=2.0" \
        "pydantic-settings>=2.0" \
        "python-dotenv>=1.2.2" \
        "structlog>=24.0" \
        "httpx>=0.27"

WORKDIR /app/interview_engine

# Drop privileges before runtime.
RUN useradd --system --uid 10001 --home /app appuser \
    && chown -R appuser:appuser /app /venv
USER appuser

# Runtime PYTHONPATH layer order (first wins on name collision):
#   engine source → nexus source → engine site-packages → nexus site-packages
#
# This ensures:
#   - agent.py / state_machine.py / etc resolve from /app/interview_engine
#   - app.* imports resolve from /app/nexus
#   - livekit / openai v2 resolve from /venv/engine
#   - langfuse / instructor / asyncpg / sqlalchemy resolve from /venv/nexus
ENV PYTHONPATH="/app/interview_engine:/app/nexus:/venv/engine/lib/python3.13/site-packages:/venv/nexus/lib/python3.13/site-packages"
ENV PATH="/venv/engine/bin:/venv/nexus/bin:$PATH"

CMD ["python", "agent.py", "start"]
