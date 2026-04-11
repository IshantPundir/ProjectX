"""signal schema v2 — flat signal list + job metadata columns

Revision ID: 0003_signal_schema_v2
Revises: 0002_add_updated_by
Create Date: 2026-04-11

Replaces 5 rigid signal columns (required_skills, preferred_skills,
must_haves, good_to_haves, min_experience_years) with a single JSONB
`signals` column on job_posting_signal_snapshots.

Adds 8 optional metadata columns to job_postings: employment_type,
work_arrangement, location, salary_range_min, salary_range_max,
salary_currency, travel_required, start_date_pref.

Clean-slate migration — no data to preserve.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_signal_schema_v2"
down_revision = "0002_add_updated_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- job_posting_signal_snapshots: drop 5 old columns, add 1 new ---
    op.drop_column("job_posting_signal_snapshots", "required_skills")
    op.drop_column("job_posting_signal_snapshots", "preferred_skills")
    op.drop_column("job_posting_signal_snapshots", "must_haves")
    op.drop_column("job_posting_signal_snapshots", "good_to_haves")
    op.drop_column("job_posting_signal_snapshots", "min_experience_years")

    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("signals", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    # --- job_postings: add 8 metadata columns ---
    op.add_column("job_postings", sa.Column("employment_type", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("work_arrangement", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("location", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("salary_range_min", sa.Integer(), nullable=True))
    op.add_column("job_postings", sa.Column("salary_range_max", sa.Integer(), nullable=True))
    op.add_column("job_postings", sa.Column("salary_currency", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("travel_required", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("start_date_pref", sa.Text(), nullable=True))


def downgrade() -> None:
    # --- job_postings: drop 8 metadata columns ---
    op.drop_column("job_postings", "start_date_pref")
    op.drop_column("job_postings", "travel_required")
    op.drop_column("job_postings", "salary_currency")
    op.drop_column("job_postings", "salary_range_max")
    op.drop_column("job_postings", "salary_range_min")
    op.drop_column("job_postings", "location")
    op.drop_column("job_postings", "work_arrangement")
    op.drop_column("job_postings", "employment_type")

    # --- job_posting_signal_snapshots: drop signals, restore 5 old columns ---
    op.drop_column("job_posting_signal_snapshots", "signals")

    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("required_skills", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("preferred_skills", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("must_haves", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("good_to_haves", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("min_experience_years", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
