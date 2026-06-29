import pytest
import pytest_asyncio
import uuid
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.enums import DocChangeType, ReleaseStatus
from app.services.versioning import versioning_service
from app.services.canonical import VersionManager
from app.services.snapshot import snapshot_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(
    TEST_DATABASE_URL, 
    echo=False,
    poolclass=StaticPool,
    connect_args={'check_same_thread': False}
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function")
async def test_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with TestingSessionLocal() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.mark.asyncio
async def test_version_restore(test_db: AsyncSession):
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test Doc", slug=f"doc-{doc_id.hex[:6]}", status="draft")
    
    # Version 1
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type=DocChangeType.AI_GENERATION)
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec1 = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="V1")
    test_db.add_all([sec1, block1])
    await test_db.commit()
    
    # Version 2
    ver2 = await VersionManager.create_new_version(test_db, doc_id, ver1.id, DocChangeType.HUMAN_EDIT, uuid.uuid4())
    # Wait, create_new_version doesn't flush blocks, we need to flush them
    await test_db.flush()
    # Update V2 block to "V2"
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == ver2.id, DocumentBlock.stable_id == "block_1"
    )
    b2 = (await test_db.execute(stmt)).scalars().first()
    b2.markdown = "V2"
    doc.current_version_id = ver2.id
    await test_db.commit()
    
    # Restore to Version 1
    actor_id = uuid.uuid4()
    ver3 = await versioning_service.restore_version(test_db, doc_id, ver2.id, ver1.id, actor_id)
    doc.current_version_id = ver3.id
    await test_db.commit()
    
    assert ver3.version_number == 3
    assert ver3.change_type == DocChangeType.RESTORE
    assert ver3.summary == f"Restored from version {ver1.id}"
    
    # Verify content in ver3 is back to V1
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == ver3.id, DocumentBlock.stable_id == "block_1"
    )
    b3 = (await test_db.execute(stmt)).scalars().first()
    assert b3.markdown == "V1"

@pytest.mark.asyncio
async def test_compare_versions(test_db: AsyncSession):
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test Doc", slug=f"doc2-{doc_id.hex[:6]}", status="draft")
    
    # Version 1
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type=DocChangeType.AI_GENERATION)
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec1 = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Hello")
    block2 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_2", block_order=2, block_type="paragraph", markdown="World")
    test_db.add_all([sec1, block1, block2])
    await test_db.commit()
    
    # Version 2
    ver2 = await VersionManager.create_new_version(test_db, doc_id, ver1.id, DocChangeType.HUMAN_EDIT, uuid.uuid4())
    await test_db.flush()
    
    # Modify block_1, leave block_2 alone, add block_3
    stmt = select(DocumentBlock).join(DocumentSection).where(
        DocumentSection.version_id == ver2.id
    )
    blocks_v2 = (await test_db.execute(stmt)).scalars().all()
    for b in blocks_v2:
        if b.stable_id == "block_1":
            b.markdown = "Hello Edited"
            
    sec_v2 = (await test_db.execute(select(DocumentSection).where(DocumentSection.version_id == ver2.id))).scalars().first()
    block3 = DocumentBlock(id=uuid.uuid4(), section_id=sec_v2.id, stable_id="block_3", block_order=3, block_type="paragraph", markdown="New")
    test_db.add(block3)
    await test_db.commit()
    
    # Compare ver1 to ver2
    diff = await versioning_service.compare_versions(test_db, ver1.id, ver2.id)
    
    assert "block_3" in diff["added"]
    assert len(diff["removed"]) == 0
    assert len(diff["modified"]) == 1
    assert diff["modified"][0]["stable_id"] == "block_1"
    assert diff["modified"][0]["old_markdown"] == "Hello"
    assert diff["modified"][0]["new_markdown"] == "Hello Edited"
    assert "block_2" in diff["unchanged"]

@pytest.mark.asyncio
async def test_snapshot_engine(test_db: AsyncSession):
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, title="Test Doc", slug=f"doc3-{doc_id.hex[:6]}", status="draft")
    
    ver1 = DocumentVersion(id=uuid.uuid4(), document_id=doc_id, version_number=1, change_type=DocChangeType.AI_GENERATION)
    test_db.add_all([doc, ver1])
    await test_db.flush()
    
    sec1 = DocumentSection(id=uuid.uuid4(), version_id=ver1.id, stable_id="sec_1", section_order=1, title="Test")
    block1 = DocumentBlock(id=uuid.uuid4(), section_id=sec1.id, stable_id="block_1", block_order=1, block_type="paragraph", markdown="Snapshot Test")
    test_db.add_all([sec1, block1])
    await test_db.commit()
    
    # Run snapshot
    result = await snapshot_engine.generate_snapshot(test_db, doc_id, ver1.id)
    
    assert "checksum" in result
    assert result["json_url"].startswith(f"reports/{doc_id}/versions/{ver1.id}/json")
    
    # Verify DB updated
    await test_db.refresh(ver1)
    assert ver1.checksum == result["checksum"]
    assert ver1.snapshot_html_url == result["html_url"]
