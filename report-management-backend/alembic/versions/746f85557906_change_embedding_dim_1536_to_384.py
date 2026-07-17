"""
Change knowledge_chunks.embedding from Vector(1536) to Vector(384)
to support Hugging Face BAAI/bge-small-en-v1.5 embeddings (free).

IMPORTANT: This migration drops and recreates the embedding column.
Any existing 1536-dim embeddings will be lost. Re-process documents
after applying this migration.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '746f85557906'
down_revision = 'c2f765d67bf2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Drop old index first (if it exists)
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding")

        # Drop old 1536-dim column and recreate as 384-dim
        op.drop_column("knowledge_chunks", "embedding")
        op.add_column("knowledge_chunks", sa.Column("embedding", Vector(384), nullable=True))

        # Recreate IVFFlat index for cosine similarity with 384-dim vectors
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding "
            "ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )
    else:
        # SQLite (test env) — no vector type support; no-op
        pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding")
        op.drop_column("knowledge_chunks", "embedding")
        op.add_column("knowledge_chunks", sa.Column("embedding", Vector(1536), nullable=True))
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding "
            "ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )
