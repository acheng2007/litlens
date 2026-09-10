"""Initial schema, native vectors and full-text indexes."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from litlens.config import settings

revision = "0001"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("libraries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner_id", sa.String(200), nullable=False),
        sa.Column("embedding_space", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_libraries_owner_id", "libraries", ["owner_id"])
    op.create_table("documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_id", sa.String(36), sa.ForeignKey("libraries.id"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("file_path", sa.Text), sa.Column("arxiv_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("library_id", "source_key"))
    op.create_index("ix_documents_library_id", "documents", ["library_id"])
    op.create_table("chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False), sa.Column("page", sa.Integer, nullable=False),
        sa.Column("section", sa.String(300)),
        sa.Column("embedding", Vector(settings.embedding_dimensions), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index"))
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.execute("CREATE INDEX ix_chunks_fts ON chunks USING gin (to_tsvector('english', text))")
    op.execute("CREATE INDEX ix_chunks_vector ON chunks USING hnsw (embedding vector_cosine_ops)")
    op.create_table("jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_id", sa.String(36), sa.ForeignKey("libraries.id"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id")),
        sa.Column("kind", sa.String(50), nullable=False), sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("duration_ms", sa.Float),
        sa.Column("result", sa.JSON, nullable=False), sa.Column("trace_id", sa.String(36), nullable=False))
    for column in ["library_id", "document_id", "status", "available_at"]:
        op.create_index(f"ix_jobs_{column}", "jobs", [column])
    op.create_table("evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_id", sa.String(36), sa.ForeignKey("libraries.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON, nullable=False))
    op.create_index("ix_evaluations_library_id", "evaluations", ["library_id"])


def downgrade():
    for table in ["evaluations", "jobs", "chunks", "documents", "libraries"]:
        op.drop_table(table)
