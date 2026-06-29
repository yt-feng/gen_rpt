import pytest
import pytest_asyncio
import uuid

from app.api.deps import get_db
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select
from app.models.base import Base
from app.models.document import Document
from app.models.identity import User
from app.services.workflow import workflow_service
from app.models.workflow import WorkflowInstance

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(
    TEST_DATABASE_URL, 
    echo=False,
    poolclass=StaticPool,
    connect_args={'check_same_thread': False}
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

@pytest_asyncio.fixture(scope="function")
async def test_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with TestingSessionLocal() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.mark.asyncio
async def test_workflow_event_idempotency(test_db: AsyncSession):
    # Setup test document
    doc_id = uuid.uuid4()
    user = User(id=uuid.uuid4(), full_name="Test", email="test@test.com")
    doc = Document(id=doc_id, title="Test", slug="test", language="en")
    test_db.add(user)
    test_db.add(doc)
    await test_db.commit()

    idempotency_key = f"test-key-{uuid.uuid4()}"
    
    # 1. Process valid event
    result = await workflow_service.process_workflow_event(
        db=test_db,
        document_id=doc_id,
        event_type="report_generated",
        idempotency_key=idempotency_key,
        new_state="GENERATED"
    )
    assert result["status"] == "success"
    assert result["new_state"] == "GENERATED"
    
    # 2. Test Idempotency (Sending the EXACT same payload with same idempotency_key)
    result2 = await workflow_service.process_workflow_event(
        db=test_db,
        document_id=doc_id,
        event_type="report_generated",
        idempotency_key=idempotency_key,
        new_state="GENERATED"
    )
    assert result2["status"] == "skipped"
    assert result2["message"] == "Event already processed"

@pytest.mark.asyncio
async def test_workflow_event_not_found_rollback(test_db: AsyncSession):
    idempotency_key = f"test-key-{uuid.uuid4()}"
    
    # Process event for missing document
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await workflow_service.process_workflow_event(
            db=test_db,
            document_id=uuid.uuid4(),
            event_type="report_generated",
            idempotency_key=idempotency_key,
            new_state="GENERATED"
        )
        
    assert exc_info.value.status_code == 404
    assert "Document not found" in str(exc_info.value.detail)
