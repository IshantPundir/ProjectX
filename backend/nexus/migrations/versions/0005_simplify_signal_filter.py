"""simplify signal_filter to types-only

Revises: 0004_pipeline_builder
"""
from alembic import op

revision = "0005_simplify_signal_filter"
down_revision = "0004_pipeline_builder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rewrite signal_filter JSONB to drop deleted fields; preserve include_types only.
    # If a row somehow has no include_types, default to all 4.
    op.execute("""
        UPDATE pipeline_template_stages
        SET signal_filter = jsonb_build_object(
            'include_types',
            COALESCE(
                signal_filter->'include_types',
                '["competency","experience","credential","behavioral"]'::jsonb
            )
        )
    """)
    op.execute("""
        UPDATE job_pipeline_stages
        SET signal_filter = jsonb_build_object(
            'include_types',
            COALESCE(
                signal_filter->'include_types',
                '["competency","experience","credential","behavioral"]'::jsonb
            )
        )
    """)


def downgrade() -> None:
    # Best-effort restore with permissive defaults on the deleted fields
    op.execute("""
        UPDATE pipeline_template_stages
        SET signal_filter = signal_filter
            || jsonb_build_object(
                'include_stages', '["screen","interview"]'::jsonb,
                'include_weights', '[1,2,3]'::jsonb,
                'include_priority', '["required","preferred"]'::jsonb
            )
    """)
    op.execute("""
        UPDATE job_pipeline_stages
        SET signal_filter = signal_filter
            || jsonb_build_object(
                'include_stages', '["screen","interview"]'::jsonb,
                'include_weights', '[1,2,3]'::jsonb,
                'include_priority', '["required","preferred"]'::jsonb
            )
    """)
