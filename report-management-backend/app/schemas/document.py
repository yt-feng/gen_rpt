from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
from app.models.enums import DocStatus, DocChangeType, SectionContentType, BlockContentType

class DocumentBase(BaseModel):
    title: str
    slug: str
    document_type: Optional[str] = None
    industry: Optional[str] = None
    language: str = "en"
    description: Optional[str] = None

class DocumentCreate(DocumentBase):
    pass

class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    document_type: Optional[str] = None
    industry: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None
    status: Optional[DocStatus] = None

class DocumentResponse(DocumentBase):
    id: UUID
    status: DocStatus
    current_version_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class DocumentVersionBase(BaseModel):
    version_number: int
    change_type: DocChangeType
    summary: Optional[str] = None
    status: DocStatus = DocStatus.draft

class DocumentVersionCreate(DocumentVersionBase):
    document_id: UUID
    parent_version: Optional[UUID] = None

class DocumentVersionResponse(DocumentVersionBase):
    id: UUID
    document_id: UUID
    parent_version: Optional[UUID] = None
    created_by: Optional[UUID] = None
    
    model_config = ConfigDict(from_attributes=True)

class DocumentSectionBase(BaseModel):
    section_order: int
    title: str
    section_type: SectionContentType = SectionContentType.Custom

class DocumentSectionCreate(DocumentSectionBase):
    version_id: UUID

class DocumentSectionResponse(DocumentSectionBase):
    id: UUID
    version_id: UUID
    
    model_config = ConfigDict(from_attributes=True)

class DocumentBlockBase(BaseModel):
    block_order: int
    block_type: BlockContentType
    content_json: Optional[Dict[str, Any]] = None
    markdown: Optional[str] = None
    html: Optional[str] = None
    metadata_: Optional[Dict[str, Any]] = Field(default_factory=dict, alias="metadata")

class DocumentBlockCreate(DocumentBlockBase):
    section_id: UUID

class DocumentBlockResponse(DocumentBlockBase):
    id: UUID
    section_id: UUID
    
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class DocumentFileBase(BaseModel):
    file_type: str
    storage_path: str
    checksum: Optional[str] = None
    size: Optional[int] = None

class DocumentFileCreate(DocumentFileBase):
    version_id: UUID

class DocumentFileResponse(DocumentFileBase):
    id: UUID
    version_id: UUID
    
    model_config = ConfigDict(from_attributes=True)
