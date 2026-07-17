import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
import pytest_asyncio

from app.models.base import Base
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeTag,
    KnowledgeCategory,
    CollectionPermission
)
from app.main import app
from app.core.config import settings

# Test database
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function", autouse=True)
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        from app.database.session import get_db
        async def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_collection_cloning(db_session, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    user_id = uuid.uuid4()
    
    # Create source collection
    src = KnowledgeCollection(
        id=uuid.uuid4(),
        name="Original Collection",
        slug="original-slug",
        owner_id=user_id
    )
    db_session.add(src)
    await db_session.commit()
    
    # Create document in source collection
    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=src.id,
        file_name="doc.txt",
        original_file_name="doc.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="checksum",
        storage_path="path/doc.txt",
        size=100,
        processing_status="completed"
    )
    db_session.add(doc)
    await db_session.commit()
    
    # Clone collection
    from app.services.knowledge_collection import knowledge_collection_service
    clone = await knowledge_collection_service.clone_collection(
        db_session, src.id, "Cloned Collection", "cloned-slug", user_id
    )
    
    assert clone.name == "Cloned Collection"
    assert clone.slug == "cloned-slug"
    
    # Verify documents cloned
    docs_res = await db_session.execute(
        select(KnowledgeDocument).filter(KnowledgeDocument.collection_id == clone.id)
    )
    cloned_docs = docs_res.scalars().all()
    assert len(cloned_docs) == 1
    assert cloned_docs[0].file_name == "doc.txt"

@pytest.mark.asyncio
async def test_tag_management(db_session):
    from app.services.knowledge_tag import knowledge_tag_service
    from app.schemas.knowledge import TagCreate
    
    # 1. Create Tags
    tag1 = await knowledge_tag_service.create_tag(db_session, TagCreate(name="Tag One", slug="tag-one"))
    tag2 = await knowledge_tag_service.create_tag(db_session, TagCreate(name="Tag Two", slug="tag-two"))
    
    assert tag1.name == "Tag One"
    
    # 2. List Tags
    tags = await knowledge_tag_service.list_tags(db_session)
    assert len(tags) == 2
    
    # 3. Merge Tags
    user_id = uuid.uuid4()
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=user_id)
    db_session.add(col)
    await db_session.commit()
    
    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="doc.txt",
        original_file_name="doc.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="checksum",
        storage_path="path/doc.txt",
        size=100
    )
    db_session.add(doc)
    await db_session.commit()
    
    # Assign Tag One to Document
    await knowledge_tag_service.assign_tag_to_document(db_session, doc.id, tag1.id)
    
    # Merge Tag One into Tag Two
    await knowledge_tag_service.merge_tags(db_session, tag1.id, tag2.id)
    
    # Tag One should be deleted, document now has Tag Two
    tags_after = await knowledge_tag_service.list_tags(db_session)
    assert len(tags_after) == 1
    assert tags_after[0].id == tag2.id

@pytest.mark.asyncio
async def test_category_management(db_session):
    from app.services.knowledge_category import knowledge_category_service
    from app.schemas.knowledge import CategoryCreate
    
    # 1. Create Parent Category
    parent = await knowledge_category_service.create_category(
        db_session, CategoryCreate(name="Finance", slug="finance", description="Finance docs")
    )
    # 2. Create Child Category
    child = await knowledge_category_service.create_category(
        db_session, CategoryCreate(name="Taxes", slug="taxes", parent_id=parent.id, description="Tax docs")
    )
    
    # 3. List Tree
    tree = await knowledge_category_service.get_category_tree(db_session)
    assert len(tree) == 1
    assert tree[0].name == "Finance"
    assert len(tree[0].children) == 1
    assert tree[0].children[0].name == "Taxes"

@pytest.mark.asyncio
async def test_permission_and_isolation(db_session):
    from app.services.knowledge_permission import knowledge_permission_service
    
    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    
    col = KnowledgeCollection(
        id=uuid.uuid4(),
        name="Secure Workspace",
        slug="secure",
        owner_id=owner_id,
        visibility="private"
    )
    db_session.add(col)
    await db_session.commit()
    
    # Assign Editor permissions to editor_id
    await knowledge_permission_service.assign_permission(
        db_session, col.id, editor_id, "editor", owner_id
    )
    
    # Check permissions
    assert await knowledge_permission_service.check_permission(db_session, col.id, owner_id, "owner") is True
    assert await knowledge_permission_service.check_permission(db_session, col.id, editor_id, "editor") is True
    assert await knowledge_permission_service.check_permission(db_session, col.id, stranger_id, "viewer") is False

@pytest.mark.asyncio
async def test_similar_document_detection(db_session):
    from app.services.knowledge_relationship import knowledge_relationship_service
    
    user_id = uuid.uuid4()
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=user_id)
    db_session.add(col)
    await db_session.commit()
    
    doc1 = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="annual_report_2025.txt",
        original_file_name="annual_report_2025.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="checksum1",
        storage_path="path/1.txt",
        size=1000
    )
    doc2 = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="annual_report_2026.txt",
        original_file_name="annual_report_2026.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="checksum2",
        storage_path="path/2.txt",
        size=1010
    )
    db_session.add(doc1)
    db_session.add(doc2)
    await db_session.commit()
    
    similar = await knowledge_relationship_service.find_similar_documents(db_session, doc1.id)
    assert len(similar) == 1
    assert similar[0].document_id == doc2.id
    assert similar[0].similarity_score > 0.5
