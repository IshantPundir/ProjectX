#!/usr/bin/env bash
# Resets the local Supabase Postgres and re-applies all Alembic migrations.
# Two steps because `supabase db reset` only re-runs backend/supabase/migrations/
# — it knows nothing about the Alembic revisions under backend/nexus/migrations/.
# Skipping step 2 leaves the DB without the RLS hardening + Phase 2/3 tables, and
# nexus startup will fail in _assert_rls_completeness.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="$(cd "${NEXUS_DIR}/.." && pwd)"

echo "==> [1/2] supabase db reset  (cwd: ${BACKEND_DIR})"
cd "${BACKEND_DIR}"
supabase db reset

echo
echo "==> [2/2] alembic upgrade head  (cwd: ${NEXUS_DIR})"
cd "${NEXUS_DIR}"
docker compose run --rm nexus alembic upgrade head

echo
echo "==> Done. DB is at Alembic head; nexus will boot cleanly."
