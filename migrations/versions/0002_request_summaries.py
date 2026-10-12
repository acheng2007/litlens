"""Owner/library-scoped request observability."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_id", sa.String(36), sa.ForeignKey("libraries.id"), nullable=False),
        sa.Column("owner_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
    )
    op.create_index("ix_request_summaries_library_id", "request_summaries", ["library_id"])
    op.create_index("ix_request_summaries_owner_id", "request_summaries", ["owner_id"])


def downgrade():
    op.drop_table("request_summaries")
