"""drop verified-knockout DB columns

Revision ID: 0059_drop_knockout
Revises: 0058
Create Date: 2026-06-14

Final step of the interview-engine knockout deletion (spec
docs/superpowers/specs/2026-06-14-interview-engine-clock-knockout-deletion-design.md).
The verified-knockout early-close feature was removed across the engine, the durable
SessionEvidence contract, and the reporting layer; these three columns are now orphaned:

  - ``tenant_settings.engine_knockout_policy`` (+ its CHECK) — added 0027, default flipped 0030.
  - ``sessions.knockout_failures``                          — added 0027.
  - ``session_reports.knockout_results``                    — added 0047.

Migrations 0027 / 0030 / 0047 remain as historical record. The downgrade restores all three
columns with their pre-drop definitions (engine_knockout_policy at the post-0030 default
``'close_polite'``).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059_drop_knockout"
down_revision = "0058"
branch_labels = None
depends_on = None

_KNOCKOUT_POLICY_CK = "ck_tenant_settings_engine_knockout_policy"


def upgrade() -> None:
    op.drop_constraint(_KNOCKOUT_POLICY_CK, "tenant_settings", type_="check")
    op.drop_column("tenant_settings", "engine_knockout_policy")
    op.drop_column("sessions", "knockout_failures")
    op.drop_column("session_reports", "knockout_results")


def downgrade() -> None:
    # session_reports.knockout_results — nullable JSONB, no default (per 0047).
    op.add_column(
        "session_reports",
        sa.Column("knockout_results", postgresql.JSONB(), nullable=True),
    )
    # sessions.knockout_failures — NOT NULL JSONB default '[]' (per 0027).
    op.add_column(
        "sessions",
        sa.Column(
            "knockout_failures",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # tenant_settings.engine_knockout_policy — NOT NULL Text, default 'close_polite'
    # (the post-0030 default) + the IN-list CHECK (per 0027).
    op.add_column(
        "tenant_settings",
        sa.Column(
            "engine_knockout_policy",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'close_polite'"),
        ),
    )
    op.create_check_constraint(
        _KNOCKOUT_POLICY_CK,
        "tenant_settings",
        "engine_knockout_policy IN ('record_only', 'close_polite')",
    )
