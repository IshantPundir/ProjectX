"""report share tokens — public recordings link columns

Adds the capability-token columns to report_shares so an emailed report PDF can
link to a public /recordings/<token> playback page. The token itself is never
stored — only its keyed HMAC-SHA256 hash (share_token_hash), looked up via a
partial unique index.

Revision ID: 0062_report_share_tokens
Revises: 0061_report_shares
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_shares", sa.Column("share_token_hash", sa.Text(), nullable=True))
    op.add_column("report_shares", sa.Column("share_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("report_shares", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("report_shares", sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "report_shares",
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # Partial unique index for O(1) token lookup; only hashed rows participate.
    op.create_index(
        "ix_report_shares_token_hash",
        "report_shares",
        ["share_token_hash"],
        unique=True,
        postgresql_where=sa.text("share_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_report_shares_token_hash", table_name="report_shares")
    op.drop_column("report_shares", "view_count")
    op.drop_column("report_shares", "last_viewed_at")
    op.drop_column("report_shares", "revoked_at")
    op.drop_column("report_shares", "share_expires_at")
    op.drop_column("report_shares", "share_token_hash")
