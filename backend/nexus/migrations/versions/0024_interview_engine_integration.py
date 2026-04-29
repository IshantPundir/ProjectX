"""Phase 3C.2 — Interview engine integration.

Adds:
  1. `engine_dispatch_tokens` (tenant-scoped) — one row per minted engine
     dispatch JWT.
  2. `engine_token_uses` (service-bypass-only) — composite (jti, endpoint)
     primary key enforces single-use semantics per endpoint.
  3. Seven columns on `sessions` for engine result persistence:
     raw_result_json, transcript, questions_asked, probes_fired,
     agent_completed_at, result_status, error_code.

Down migration drops both tables and the seven columns. WARNING: down loses
raw_result_json + transcript for completed sessions; the rollback runbook
requires a backup export first.

Revision ID: 0024_interview_engine_integration
Revises: 0023_tenant_hard_delete_cascade
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_interview_engine_integration"
down_revision = "0023_tenant_hard_delete_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # populated in subsequent tasks


def downgrade() -> None:
    pass  # populated in subsequent tasks
