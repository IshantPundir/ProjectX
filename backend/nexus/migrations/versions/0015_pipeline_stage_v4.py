"""phase_4_pipeline_stage_extensions

Extends pipeline_stages to support the v4 design:

1. Broaden `stage_type` CHECK to include the design's bookend + recruiter
   stages: ``intake``, ``recruiter``, ``debrief``, ``offer``. Existing values
   (``phone_screen``, ``ai_interview``, ``human_interview``,
   ``panel_interview``, ``take_home``) are preserved so no rows need rewriting.

2. Add nullable ``sla_days`` column — the per-stage candidate dwell limit
   (different concept from ``duration_minutes``, which stays as interview
   length). ``NULL`` means no SLA configured.

Applies to both ``pipeline_template_stages`` and ``job_pipeline_stages``.

Revision ID: 0015_pipeline_stage_v4
Revises: 0014_sessions_scheduler_core
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op


revision = "0015_pipeline_stage_v4"
down_revision = "0014_sessions_scheduler_core"
branch_labels = None
depends_on = None

STAGE_TYPES_V4 = (
    # v2c1 (pre-existing)
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
    # v4 additions (bookend + role-specific)
    "intake",
    "recruiter",
    "debrief",
    "offer",
)

STAGE_TYPES_PRE_V4 = (
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
)


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join("'" + v + "'" for v in values) + ")"


def upgrade() -> None:
    # Broaden stage_type CHECK on both tables (drop + recreate; Postgres
    # doesn't support ALTER CONSTRAINT CHECK in-place for named constraints).
    for table, cons in (
        ("pipeline_template_stages", "ck_template_stages_stage_type"),
        ("job_pipeline_stages", "ck_job_pipeline_stages_stage_type"),
    ):
        op.drop_constraint(cons, table, type_="check")
        op.create_check_constraint(
            cons, table, f"stage_type IN {_sql_in(STAGE_TYPES_V4)}"
        )

    # Add sla_days — nullable int, no default. Row-level writers set it
    # explicitly; reads treat NULL as "no SLA configured".
    for table in ("pipeline_template_stages", "job_pipeline_stages"):
        op.add_column(table, sa.Column("sla_days", sa.Integer(), nullable=True))
        # sla_days must be a positive integer if set; NULL is fine.
        op.create_check_constraint(
            f"ck_{table}_sla_days",
            table,
            "sla_days IS NULL OR sla_days > 0",
        )


def downgrade() -> None:
    for table in ("pipeline_template_stages", "job_pipeline_stages"):
        op.drop_constraint(f"ck_{table}_sla_days", table, type_="check")
        op.drop_column(table, "sla_days")

    # Restore the pre-v4 CHECK. This will FAIL if any rows carry a v4-only
    # stage_type value (intake/recruiter/debrief/offer) — by design; running
    # the downgrade is a destructive intent and the caller must clean up
    # those rows first.
    for table, cons in (
        ("pipeline_template_stages", "ck_template_stages_stage_type"),
        ("job_pipeline_stages", "ck_job_pipeline_stages_stage_type"),
    ):
        op.drop_constraint(cons, table, type_="check")
        op.create_check_constraint(
            cons, table, f"stage_type IN {_sql_in(STAGE_TYPES_PRE_V4)}"
        )
