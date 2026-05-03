# --- Stage 1: Build dependencies ---
FROM python:3.13-slim AS deps
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[dev]"

# --- Stage 2: Production image ---
FROM python:3.13-slim AS runner
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 nexus && \
    useradd --system --uid 1001 --gid nexus nexus

COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY . .

# HF_HOME tells huggingface_hub where to cache model files. Set as a
# permanent ENV so both the build-time download-files step below and the
# runtime nexus-engine process use the same path.
ENV HF_HOME=/app/.cache/huggingface

# Pre-download AI plugin model files at build time so nexus-engine doesn't
# need network access on first dispatch. Especially load-bearing for the
# multilingual turn-detector plugin which instantiates with
# local_files_only=True and would otherwise crash with "Could not find
# file 'model_q8.onnx'". The download-files subcommand iterates registered
# plugins (silero, turn-detector) and pulls each plugin's
# HF / ONNX assets.
#
# Inherited by all three services from the same image (nexus, nexus-worker,
# nexus-engine). The cost is ~300MB of image bloat on services that don't
# use the models, accepted in exchange for not branching the Dockerfile.
#
# ENVIRONMENT=test bypasses Settings-level required-secret validators —
# real secrets arrive at runtime via compose env_file.
RUN ENVIRONMENT=test python -m app.modules.interview_engine download-files \
    && chown -R nexus:nexus /app/.cache

USER nexus

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
