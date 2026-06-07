"""session_evidence_json — gen-3 engine→report wire contract column.

Adds `sessions.session_evidence_json JSONB NULL`. Holds the full
SessionEvidence object (append-only notes + derived provenance + raw
transcript timing) written by the gen-3 interview engine via
`record_session_evidence`. The gen-2 result columns (`raw_result_json`,
`transcript`, `questions_asked`, `probes_fired`) are NOT removed — they
are still read by reporting/, reel/, and vision/ (separate later rewrite).

PG11+ metadata-only add (no table rewrite). Down-migration drops the column.

Revision ID: 0054_session_evidence
Revises: 0053
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_session_evidence"
down_revision: str | None = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "session_evidence_json",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "session_evidence_json")
