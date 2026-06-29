import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.models.base import Base
from app.models.identity import User
from app.schemas.identity import UserCreate
from app.schemas.document import DocumentCreate
from app.repositories.user import user_repo
from app.services.document import document_service

# We use SQLite memory for unit testing the schemas
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

@pytest.fixture(scope="function")
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.mark.asyncio
async def test_user_repository_crud(db_session):
    # Test Create
    user_in = UserCreate(
        full_name="Test User",
        email="test@example.com",
    )
    user = await user_repo.create(db=db_session, obj_in=user_in)
    assert user.id is not None
    assert user.full_name == "Test User"
    assert user.email == "test@example.com"
    
    # Test Read
    user_db = await user_repo.get_by_email(db=db_session, email="test@example.com")
    assert user_db is not None
    assert user_db.id == user.id

@pytest.mark.asyncio
async def test_document_service(db_session):
    # Setup test user
    user = User(id=uuid4(), full_name="Author", email="author@test.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    
    # Test Document Service
    doc_in = DocumentCreate(
        title="Quarterly Report",
        slug="q3-report",
        description="Q3 Financials"
    )
    
    document = await document_service.create_document(db=db_session, doc_in=doc_in, user_id=user.id)
    
    assert document.id is not None
    assert document.title == "Quarterly Report"
    assert document.current_version_id is not None
    
    # Check that initial version was created successfully
    await db_session.refresh(document, ['versions'])
    assert len(document.versions) == 1
    assert document.versions[0].id == document.current_version_id
    assert document.versions[0].version_number == 1
    assert document.versions[0].summary == "Initial creation"
