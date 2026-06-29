import pytest
import pytest_asyncio
from uuid import uuid4
import hashlib
from typing import Union, BinaryIO, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.models.base import Base
from app.services.storage import StorageService
from app.storage.provider import StorageProvider

# 1. Setup Mock Provider
class MockStorageProvider(StorageProvider):
    def __init__(self):
        self.storage = {}

    async def upload(self, file_data: Union[bytes, BinaryIO], path: str, content_type: str = "application/octet-stream") -> bool:
        if isinstance(file_data, bytes):
            self.storage[path] = file_data
        else:
            file_data.seek(0)
            self.storage[path] = file_data.read()
        return True

    async def download(self, path: str) -> Optional[bytes]:
        return self.storage.get(path)

    async def delete(self, path: str) -> bool:
        if path in self.storage:
            del self.storage[path]
            return True
        return False

    async def exists(self, path: str) -> bool:
        return path in self.storage

    async def get_signed_url(self, path: str, expiration_sec: int = 3600, method: str = 'get_object') -> str:
        if await self.exists(path):
            return f"https://mock-signed-url/{path}?expires={expiration_sec}"
        return ""

    async def health_check(self) -> dict:
        return {"status": "healthy", "latency_ms": 1.0}

mock_provider = MockStorageProvider()

# We need to monkeypatch the storage_service's provider
@pytest.fixture(autouse=True)
def patch_storage_provider(monkeypatch):
    import app.services.storage
    monkeypatch.setattr(app.services.storage, "storage_provider", mock_provider)

# 2. Setup Database
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

# 3. Tests
@pytest.mark.asyncio
async def test_path_generation():
    service = StorageService()
    doc_id = uuid4()
    ver_id = uuid4()
    path = service.generate_path(doc_id, ver_id, "report.pdf", "pdf")
    assert path == f"reports/{doc_id}/versions/{ver_id}/pdf/report.pdf"

@pytest.mark.asyncio
async def test_upload_and_db_sync(db_session):
    from app.services.storage import storage_service
    
    doc_id = uuid4()
    ver_id = uuid4()
    file_content = b"Mock PDF Content"
    
    # Needs a real document version in DB due to foreign keys. 
    # Actually, SQLite does not enforce FKs by default unless PRAGMA foreign_keys=ON is run. 
    # Let's assume it doesn't fail, or we create the parent records.
    # Since we didn't run the PRAGMA, it will allow the insert.
    
    db_file = await storage_service.upload_document_file(
        db=db_session,
        document_id=doc_id,
        version_id=ver_id,
        filename="report.pdf",
        file_type="pdf",
        content_type="application/pdf",
        file_data=file_content
    )
    
    assert db_file is not None
    assert db_file.id is not None
    assert db_file.storage_path == f"reports/{doc_id}/versions/{ver_id}/pdf/report.pdf"
    assert db_file.size == len(file_content)
    
    # Checksum verification
    hasher = hashlib.sha256()
    hasher.update(file_content)
    expected_checksum = hasher.hexdigest()
    assert db_file.checksum == expected_checksum
    
    # Check R2 Mock
    assert await mock_provider.exists(db_file.storage_path)

@pytest.mark.asyncio
async def test_get_signed_url(db_session):
    from app.services.storage import storage_service
    
    doc_id = uuid4()
    ver_id = uuid4()
    db_file = await storage_service.upload_document_file(
        db=db_session, document_id=doc_id, version_id=ver_id,
        filename="test.json", file_type="json", content_type="application/json",
        file_data=b"{}"
    )
    
    url = await storage_service.get_signed_url(db_session, db_file.id)
    assert url is not None
    assert url.startswith("https://mock-signed-url/")

@pytest.mark.asyncio
async def test_delete_document_file(db_session):
    from app.services.storage import storage_service
    
    db_file = await storage_service.upload_document_file(
        db=db_session, document_id=uuid4(), version_id=uuid4(),
        filename="test.json", file_type="json", content_type="application/json",
        file_data=b"{}"
    )
    
    path = db_file.storage_path
    assert await mock_provider.exists(path)
    
    success = await storage_service.delete_document_file(db_session, db_file.id)
    assert success is True
    
    # Assert deleted from storage
    assert not await mock_provider.exists(path)
