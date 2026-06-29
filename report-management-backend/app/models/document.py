import uuid
from typing import Optional, List
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, UniqueConstraint, BigInteger, Enum
from app.models.base import Base, UUIDMixin, TimestampMixin, JSONVariant
from app.models.enums import DocStatus, DocChangeType, SectionContentType, BlockContentType

class Document(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "documents"
    title: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str] = mapped_column(String, default="en")
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    
    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus, name="doc_status", create_type=False), 
        default=DocStatus.draft
    )
    
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True, name="fk_current_version"), 
        nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    versions: Mapped[List["DocumentVersion"]] = relationship("DocumentVersion", back_populates="document", foreign_keys="[DocumentVersion.document_id]")

class DocumentVersion(Base, UUIDMixin):
    __tablename__ = "document_versions"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True)
    
    change_type: Mapped[DocChangeType] = mapped_column(
        Enum(DocChangeType, name="doc_change_type", create_type=False), 
        nullable=False
    )
    
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    
    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus, name="doc_status", create_type=False), 
        default=DocStatus.draft
    )

    actor_type: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    
    from app.models.enums import ReleaseStatus
    release_status: Mapped[ReleaseStatus] = mapped_column(
        Enum(ReleaseStatus, name="release_status", create_type=False),
        default=ReleaseStatus.Draft
    )
    
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_canonical_url: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_html_url: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_markdown_url: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_pdf_url: Mapped[str | None] = mapped_column(String, nullable=True)
    
    __table_args__ = (
        UniqueConstraint('document_id', 'version_number', name='uq_doc_version'),
    )

    document: Mapped["Document"] = relationship("Document", back_populates="versions", foreign_keys=[document_id])
    sections: Mapped[List["DocumentSection"]] = relationship("DocumentSection", back_populates="version")

class DocumentSection(Base, UUIDMixin):
    __tablename__ = "document_sections"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    stable_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    section_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    
    section_type: Mapped[SectionContentType] = mapped_column(
        Enum(SectionContentType, name="section_content_type", create_type=False),
        default=SectionContentType.Custom
    )

    version: Mapped["DocumentVersion"] = relationship("DocumentVersion", back_populates="sections")
    blocks: Mapped[List["DocumentBlock"]] = relationship("DocumentBlock", back_populates="section")

class DocumentBlock(Base, UUIDMixin):
    __tablename__ = "document_blocks"
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=False)
    stable_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    
    block_type: Mapped[BlockContentType] = mapped_column(
        Enum(BlockContentType, name="block_content_type", create_type=False),
        nullable=False
    )
    
    content_json: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    markdown: Mapped[str | None] = mapped_column(String, nullable=True)
    html: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONVariant, default=lambda: {})

    section: Mapped["DocumentSection"] = relationship("DocumentSection", back_populates="blocks")

class DocumentFile(Base, UUIDMixin):
    __tablename__ = "document_files"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
