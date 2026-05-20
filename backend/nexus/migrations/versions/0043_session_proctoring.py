"""session proctoring — violation log + terminated state + tenant config

Adds:
  * sessions.proctoring_violations  (JSONB NOT NULL DEFAULT '[]')
  * sessions.proctoring_outcome     (TEXT NULL — terminating reason)
  * sessions.proctoring_violation_count (INTEGER NOT NULL DEFAULT 0)
  * sessions_state_check            (+ 'terminated' value)
  * tenant_settings.proctoring_enabled              (BOOLEAN NOT NULL DEFAULT true)
  * tenant_settings.proctoring_soft_violation_limit (INTEGER NOT NULL DEFAULT 3)
  * tenant_settings.proctoring_fullscreen_grace_seconds (INTEGER NOT NULL DEFAULT 10)

No new tables → no new RLS policy pair (both sessions and tenant_settings
already carry tenant_isolation + service_bypass; new columns inherit).

Revision ID: 0043
Revises: 0042
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_STATES_NEW = (
    "'created','pre_check','consented','active','completed','cancelled','error','terminated'"
)
_STATES_OLD = (
    "'created','pre_check','consented','active','completed','cancelled','error'"
)


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "proctoring_violations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("sessions", sa.Column("proctoring_outcome", sa.Text(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column(
            "proctoring_violation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_state_check")
    op.execute(
        f"ALTER TABLE public.sessions ADD CONSTRAINT sessions_state_check "
        f"CHECK (state IN ({_STATES_NEW}))"
    )

    op.add_column(
        "tenant_settings",
        sa.Column("proctoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "proctoring_soft_violation_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "proctoring_fullscreen_grace_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("10"),
        ),
    )


def downgrade() -> None:
    # Re-tightening the CHECK requires no 'terminated' rows remain.
    op.execute("UPDATE public.sessions SET state='cancelled' WHERE state='terminated'")
    op.execute("ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_state_check")
    op.execute(
        f"ALTER TABLE public.sessions ADD CONSTRAINT sessions_state_check "
        f"CHECK (state IN ({_STATES_OLD}))"
    )
    op.drop_column("tenant_settings", "proctoring_fullscreen_grace_seconds")
    op.drop_column("tenant_settings", "proctoring_soft_violation_limit")
    op.drop_column("tenant_settings", "proctoring_enabled")
    op.drop_column("sessions", "proctoring_violation_count")
    op.drop_column("sessions", "proctoring_outcome")
    op.drop_column("sessions", "proctoring_violations")
