"""Phase 4 — add stage_questions.question_kind.

Adds `stage_questions.question_kind` (TEXT NOT NULL DEFAULT
'technical_depth') with a CHECK constraint allowing all 4 engine-side
Literal values. Metadata-only column add (PG11+); no table rewrite.

The bank-generator LLM (Phase 4) emits this field per question as one of
3 values: `technical_depth | behavioral_star | compliance_binary`. The
4th value (`open_culture`) is allowed by the CHECK as a forward-compat
slot for an eventual `OpenCultureTask`; the generator does not emit it
in Phase 4. See `app/modules/interview_engine/tasks/factory.py` and the
spec at
`docs/superpowers/specs/2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md`.

POST-MIGRATION STATE:
  Every existing row reads `'technical_depth'`. Existing banks remain in
  their current `confirmed`/`reviewing` status. To get real per-question
  kinds, recruiters regenerate via
  `POST /api/jobs/{id}/banks/{bank_id}/regenerate` (existing endpoint) —
  the new bank-gen prompt picks the right kind per question. NO automatic
  backfill is performed, by design (see Phase-4 design spec
  §"Backfill"). Engine routes default-kind questions through
  `TechnicalDepthTask` — the same behavior as `main` today, so no
  regression.

Revision ID: 0026_question_kind_column
Revises: 0025_drop_engine_dispatch_tables
Create Date: 2026-05-03
"""

from alembic import op


revision = "0026_question_kind_column"
down_revision = "0025_drop_engine_dispatch_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE stage_questions "
        "ADD COLUMN question_kind TEXT NOT NULL DEFAULT 'technical_depth'"
    )
    op.execute(
        "ALTER TABLE stage_questions "
        "ADD CONSTRAINT stage_questions_question_kind_check "
        "CHECK (question_kind IN "
        "('technical_depth', 'behavioral_star', 'compliance_binary', 'open_culture'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE stage_questions "
        "DROP CONSTRAINT IF EXISTS stage_questions_question_kind_check"
    )
    op.execute(
        "ALTER TABLE stage_questions DROP COLUMN IF EXISTS question_kind"
    )
