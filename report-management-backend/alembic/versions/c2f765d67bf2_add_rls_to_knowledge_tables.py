"""
Mako template for Alembic revision scripts.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2f765d67bf2'
down_revision = '6e0bca9ddb0e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Enable RLS and create isolation policies on knowledge_collections
        op.execute("ALTER TABLE knowledge_collections ENABLE ROW LEVEL SECURITY;")
        op.execute("""
            CREATE POLICY knowledge_collections_isolation ON knowledge_collections
            USING (owner_id = current_setting('app.current_user_id', true)::uuid
                   OR id IN (SELECT collection_id FROM collection_permissions
                             WHERE user_id = current_setting('app.current_user_id', true)::uuid));
        """)
        
        # Enable RLS and create isolation policies on knowledge_documents
        op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;")
        op.execute("""
            CREATE POLICY knowledge_documents_isolation ON knowledge_documents
            USING (collection_id IN (SELECT id FROM knowledge_collections));
        """)
        
        # Enable RLS and create isolation policies on knowledge_chunks
        op.execute("ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;")
        op.execute("""
            CREATE POLICY knowledge_chunks_isolation ON knowledge_chunks
            USING (document_id IN (SELECT id FROM knowledge_documents));
        """)
        
        # Grant BYPASSRLS to the postgres service role if it exists
        # Removed because Supabase restricts this and it aborts the transaction
        pass


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Disable RLS and drop policies on knowledge_chunks
        op.execute("DROP POLICY IF EXISTS knowledge_chunks_isolation ON knowledge_chunks;")
        op.execute("ALTER TABLE knowledge_chunks DISABLE ROW LEVEL SECURITY;")
        
        # Disable RLS and drop policies on knowledge_documents
        op.execute("DROP POLICY IF EXISTS knowledge_documents_isolation ON knowledge_documents;")
        op.execute("ALTER TABLE knowledge_documents DISABLE ROW LEVEL SECURITY;")
        
        # Disable RLS and drop policies on knowledge_collections
        op.execute("DROP POLICY IF EXISTS knowledge_collections_isolation ON knowledge_collections;")
        op.execute("ALTER TABLE knowledge_collections DISABLE ROW LEVEL SECURITY;")
