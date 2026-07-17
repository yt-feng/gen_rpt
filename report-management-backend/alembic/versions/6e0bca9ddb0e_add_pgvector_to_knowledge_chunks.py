"""
Mako template for Alembic revision scripts.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '6e0bca9ddb0e'
down_revision = 'c3eb66f2f55e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Add vector column
    op.add_column("knowledge_chunks", sa.Column("embedding", Vector(1536), nullable=True))
    # Create IVFFlat ANN index
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding")
    op.drop_column("knowledge_chunks", "embedding")
