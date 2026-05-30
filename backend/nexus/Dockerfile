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

# HF_HOME tells huggingface_hub where to cache model files. It MUST live
# OUTSIDE /app: the compose dev services bind-mount the source tree at
# `.:/app` for hot-reload, which would otherwise shadow a /app/.cache and
# hide the models baked in below. /opt is never bind-mounted nor volume-
# mounted, so the IMAGE is the single source of truth for model files.
# (A named volume over the cache previously drifted stale across a plugin
# upgrade — it held only the old turn-detector revision and shadowed the
# image's freshly-built one, breaking init with "Could not find file
# model_q8.onnx".) Mirrors the vision worker's UNIFACE_CACHE_DIR=/opt/... .
ENV HF_HOME=/opt/hf-cache

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
RUN mkdir -p /opt/hf-cache \
    && ENVIRONMENT=test python -m app.modules.interview_engine download-files \
    && chown -R nexus:nexus /opt/hf-cache \
    && rm -rf /tmp/dramatiq-prometheus /tmp/dramatiq-prometheus.lock
# ^ The download-files step above imports the broker chain, whose dramatiq
#   Prometheus middleware runs `os.makedirs("/tmp/dramatiq-prometheus")` AT
#   IMPORT TIME — as root, during the build — baking a root-owned dir into the
#   image that the non-root `nexus` runtime user then cannot write (the worker
#   logged repeated PermissionError on its metrics .db files). Removing the
#   build-time artifact lets the runtime process recreate it nexus-owned.

USER nexus

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
