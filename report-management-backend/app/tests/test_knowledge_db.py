import pytest
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
import pytest_asyncio

from app.models.base import Base
from app.models.identity import User, Organization
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeChunk,
    EmbeddingMetadata,
    KnowledgeSource,
    RetrievalSession,
    RetrievalResult,
    ValidationResult,
    KnowledgeRelationship,
    KnowledgeProcessingQueue,
    KnowledgeActivityHistory,
    CollectionPermission,
    KnowledgeAnalytics,
    KnowledgeVersionHistory,
    KnowledgeSynchronizationLog,
    KnowledgeProcessingAuditLog,
    KnowledgeCategory,
    KnowledgeTag
)
from app.schemas.knowledge import (
    CollectionCreate,
    CollectionUpdate,
    DocumentCreate,
    DocumentUpdate,
    SourceCreate,
    ChunkCreate,
    PermissionCreate,
    ProcessingJobCreate,
    ValidationRequest
)
from app.repositories.knowledge import (
    collection_repo,
    document_repo,
    source_repo,
    chunk_repo,
    retrieval_repo,
    validation_repo,
    queue_repo,
    permission_repo,
    analytics_repo
)

# Use SQLite memory for repository tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def test_user_and_org(db_session: AsyncSession):
    user = User(
        id=uuid4(),
        full_name="Knowledge Tester",
        email="tester@knowledge.com",
        status="active"
    )
    org = Organization(
        id=uuid4(),
        name="Knowledge Corp",
        slug="knowledge-corp"
    )
    db_session.add(user)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(org)
    return user, org


@pytest.mark.asyncio
async def test_collection_repository_crud(db_session: AsyncSession, test_user_and_org):
    user, org = test_user_and_org

    # Create Collection
    coll_in = CollectionCreate(
        name="Test Collection",
        slug="test-collection",
        description="A collection for unit testing",
        status="active",
        owner_id=user.id,
        organization_id=org.id,
        visibility="private"
    )
    collection = await collection_repo.create(db=db_session, obj_in=coll_in)
    assert collection.id is not None
    assert collection.name == "Test Collection"
    assert collection.slug == "test-collection"
    assert collection.owner_id == user.id

    # Read Collection
    coll_db = await collection_repo.get_by_slug(db=db_session, slug="test-collection")
    assert coll_db is not None
    assert coll_db.id == collection.id

    # Update Collection
    coll_up = CollectionUpdate(name="Updated Collection Name")
    updated = await collection_repo.update(db=db_session, db_obj=collection, obj_in=coll_up)
    assert updated.name == "Updated Collection Name"

    # Soft Delete
    assert collection.deleted_at is None
    collection.deleted_at = datetime.now(timezone.utc)
    db_session.add(collection)
    await db_session.commit()
    
    # Assert get_by_slug excludes soft-deleted
    coll_db_post_delete = await collection_repo.get_by_slug(db=db_session, slug="test-collection")
    assert coll_db_post_delete is None


@pytest.mark.asyncio
async def test_collection_unique_constraints(db_session: AsyncSession, test_user_and_org):
    user, _ = test_user_and_org

    # Create first collection
    coll1 = CollectionCreate(
        name="Duplicate Coll",
        slug="dup-coll",
        owner_id=user.id
    )
    await collection_repo.create(db=db_session, obj_in=coll1)

    # Try creating second collection with duplicate name
    coll2 = CollectionCreate(
        name="Duplicate Coll",
        slug="different-slug",
        owner_id=user.id
    )
    with pytest.raises(IntegrityError):
        await collection_repo.create(db=db_session, obj_in=coll2)


@pytest.mark.asyncio
async def test_document_and_chunks_cascade(db_session: AsyncSession, test_user_and_org):
    user, org = test_user_and_org

    # Create Collection
    coll_in = CollectionCreate(
        name="Cascade Coll",
        slug="cascade-coll",
        owner_id=user.id
    )
    collection = await collection_repo.create(db=db_session, obj_in=coll_in)

    # Create Document
    doc_in = DocumentCreate(
        collection_id=collection.id,
        file_name="report.pdf",
        original_file_name="report_original.pdf",
        mime_type="application/pdf",
        extension="pdf",
        checksum="sha256checksum",
        storage_path="/storage/report.pdf",
        version=1,
        size=1024,
        processing_status="pending",
        upload_status="pending",
        validation_status="pending",
        created_by=user.id
    )
    document = await document_repo.create(db=db_session, obj_in=doc_in)
    assert document.id is not None

    # Create Source metadata
    src_in = SourceCreate(
        publisher="Test Publisher",
        author="Test Author",
        source_type="manual_upload"
    )
    # Manual relationship association
    source = KnowledgeSource(document_id=document.id, **src_in.model_dump())
    db_session.add(source)

    # Create Chunk
    chk_in = ChunkCreate(
        document_id=document.id,
        chunk_number=0,
        section="Abstract",
        heading="Introduction",
        page=1,
        token_count=100,
        character_count=500,
        hash="chunkhash",
        chunk_metadata={"author": "Tester"}
    )
    chunk = await chunk_repo.create(db=db_session, obj_in=chk_in)
    assert chunk.id is not None

    # Verify Chunk retrieved
    chunk_db = await chunk_repo.get_by_document_and_number(db=db_session, document_id=document.id, chunk_number=0)
    assert chunk_db is not None
    assert chunk_db.section == "Abstract"

    # Delete Document and verify cascade delete of chunks
    await document_repo.remove(db=db_session, id=document.id)

    # Verify chunk is gone
    chunk_db_post = await chunk_repo.get_by_document_and_number(db=db_session, document_id=document.id, chunk_number=0)
    assert chunk_db_post is None


@pytest.mark.asyncio
async def test_retrieval_and_validation_repos(db_session: AsyncSession, test_user_and_org):
    user, _ = test_user_and_org

    # Create retrieval session
    session = RetrievalSession(
        query="What is testing?",
        user_id=user.id,
        status="completed"
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    assert session.id is not None

    # Create validation result
    val_in = ValidationRequest(
        session_id=session.id,
        validation_type="source_validation",
        confidence=0.95,
        result="validated",
        evidence={"status": "verified"},
        validator="SystemValidator"
    )
    validation = await validation_repo.create(db=db_session, obj_in=val_in)
    assert validation.id is not None

    # Query validation list
    validations = await validation_repo.list_by_session(db=db_session, session_id=session.id)
    assert len(validations) == 1
    assert validations[0].validator == "SystemValidator"


@pytest.mark.asyncio
async def test_queue_and_permissions(db_session: AsyncSession, test_user_and_org):
    user, _ = test_user_and_org

    # Create Collection
    coll_in = CollectionCreate(
        name="Permission Coll",
        slug="perm-coll",
        owner_id=user.id
    )
    collection = await collection_repo.create(db=db_session, obj_in=coll_in)

    # Create Permission
    perm_in = PermissionCreate(
        user_id=user.id,
        permission_level="owner"
    )
    # Associate collection_id manually
    perm_db = CollectionPermission(collection_id=collection.id, **perm_in.model_dump())
    db_session.add(perm_db)
    await db_session.commit()

    # Query permission
    perm_res = await permission_repo.get_user_permission(db=db_session, collection_id=collection.id, user_id=user.id)
    assert perm_res is not None
    assert perm_res.permission_level == "owner"
