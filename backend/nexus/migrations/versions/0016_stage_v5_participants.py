"""stage_type v5 + pipeline_stage_participants

1. Create `pipeline_stage_participants` (instance-level staffing only) with
   canonical RLS pair + grants to nexus_app.
2. Drop old `ck_*_stages_stage_type` CHECK constraints (migration 0015
   enforced a 9-value allowlist).
3. Rename legacy rows: recruiter/panel_interview -> human_interview;
   ai_interview -> ai_screening. Deletes offer rows.
4. Re-sequence position per instance / per template (0..N-1) to avoid
   breaking UpdateJobPipelineRequest.check_positions_sequential on the
   next auto-save PATCH after offer deletion.
5. Re-create CHECK constraints with the 6-value allowlist.

Downgrade is lossy: deleted offer rows are NOT restored; rename-reverted
rows keep their new UUID identities.

Revision ID: 0016_stage_v5_participants
Revises: 0015_pipeline_stage_v4
Create Date: 2026-04-22
"""

from alembic import op


revision = "0016_stage_v5_participants"
down_revision = "0015_pipeline_stage_v4"
branch_labels = None
depends_on = None


STAGE_TYPES_V5 = (
    "intake",
    "phone_screen",
    "ai_screening",
    "human_interview",
    "debrief",
    "take_home",
)

STAGE_TYPES_V4 = (
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
    "intake",
    "recruiter",
    "debrief",
    "offer",
)


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # 1. Create participants table.
    op.execute(
        """
        CREATE TABLE pipeline_stage_participants (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            stage_id    uuid NOT NULL REFERENCES job_pipeline_stages(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role        text NOT NULL
                          CONSTRAINT ck_stage_participants_role
                          CHECK (role IN ('interviewer', 'observer', 'reviewer')),
            assigned_by uuid REFERENCES users(id) ON DELETE SET NULL,
            assigned_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (stage_id, user_id, role)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_stage_participants_stage ON pipeline_stage_participants (stage_id)"
    )
    op.execute(
        "CREATE INDEX ix_stage_participants_user ON pipeline_stage_participants (user_id)"
    )
    op.execute(
        "CREATE INDEX ix_stage_participants_tenant ON pipeline_stage_participants (tenant_id)"
    )

    # 2. RLS + policies + grants.
    op.execute("ALTER TABLE pipeline_stage_participants ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY "tenant_isolation" ON pipeline_stage_participants
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY "service_bypass" ON pipeline_stage_participants
          USING (current_setting('app.bypass_rls', true) = 'true')
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_stage_participants TO nexus_app"
    )

    # 3. Drop old CHECK constraints BEFORE the UPDATE — otherwise renaming
    #    to ai_screening (not in the old allowlist) violates the constraint.
    op.drop_constraint(
        "ck_template_stages_stage_type", "pipeline_template_stages", type_="check"
    )
    op.drop_constraint(
        "ck_job_pipeline_stages_stage_type", "job_pipeline_stages", type_="check"
    )

    # 4. Rename legacy rows (both tables).
    for table in ("pipeline_template_stages", "job_pipeline_stages"):
        op.execute(
            f"""
            UPDATE {table} SET stage_type = CASE stage_type
                WHEN 'recruiter' THEN 'human_interview'
                WHEN 'panel_interview' THEN 'human_interview'
                WHEN 'ai_interview' THEN 'ai_screening'
                ELSE stage_type
            END
            """
        )

    # 5. Delete offer rows outright.
    op.execute("DELETE FROM pipeline_template_stages WHERE stage_type = 'offer'")
    op.execute("DELETE FROM job_pipeline_stages WHERE stage_type = 'offer'")

    # 6. Re-sequence positions per pipeline / per template. Required because
    #    UpdateJobPipelineRequest.check_positions_sequential rejects gaps on
    #    the next PATCH. Two-phase UPDATE: offset first so the UNIQUE
    #    (instance_id, position) / (template_id, position) constraints can't
    #    collide mid-statement when rows shift left. (Constraints are NOT
    #    DEFERRABLE; PG checks per-row during UPDATE.)
    op.execute("UPDATE job_pipeline_stages SET position = position + 1000000")
    op.execute(
        """
        WITH renumbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY instance_id ORDER BY position) - 1 AS new_pos
            FROM job_pipeline_stages
        )
        UPDATE job_pipeline_stages s
           SET position = r.new_pos
          FROM renumbered r
         WHERE s.id = r.id
        """
    )
    op.execute("UPDATE pipeline_template_stages SET position = position + 1000000")
    op.execute(
        """
        WITH renumbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY template_id ORDER BY position) - 1 AS new_pos
            FROM pipeline_template_stages
        )
        UPDATE pipeline_template_stages s
           SET position = r.new_pos
          FROM renumbered r
         WHERE s.id = r.id
        """
    )

    # 7. Re-create CHECK with the 6-value allowlist.
    op.create_check_constraint(
        "ck_template_stages_stage_type",
        "pipeline_template_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V5)}",
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_stage_type",
        "job_pipeline_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V5)}",
    )


def downgrade() -> None:
    # Restore v4 CHECK. Fails if any 'ai_screening' rows exist — by design,
    # since the rename is lossy. Operator must manually rename first.
    op.drop_constraint(
        "ck_template_stages_stage_type", "pipeline_template_stages", type_="check"
    )
    op.drop_constraint(
        "ck_job_pipeline_stages_stage_type", "job_pipeline_stages", type_="check"
    )
    op.create_check_constraint(
        "ck_template_stages_stage_type",
        "pipeline_template_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V4)}",
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_stage_type",
        "job_pipeline_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V4)}",
    )
    op.execute("DROP TABLE IF EXISTS pipeline_stage_participants")
