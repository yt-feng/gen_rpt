from pydantic import BaseModel, ConfigDict
from typing import Optional
from uuid import UUID
from datetime import datetime
from app.models.enums import JobStatusType

class WorkflowInstanceBase(BaseModel):
    current_state: str

class WorkflowInstanceResponse(WorkflowInstanceBase):
    id: UUID
    document_id: UUID
    assigned_to: Optional[UUID] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class GenerationJobBase(BaseModel):
    github_run: Optional[str] = None
    workflow: Optional[str] = None
    status: JobStatusType = JobStatusType.pending
    logs: Optional[str] = None

class GenerationJobResponse(GenerationJobBase):
    id: UUID
    document_id: UUID
    started: datetime
    completed: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class PublishJobBase(BaseModel):
    platform: str
    status: JobStatusType = JobStatusType.pending

class PublishJobResponse(PublishJobBase):
    id: UUID
    version_id: UUID
    published_at: Optional[datetime] = None
    published_by: Optional[UUID] = None
    
    model_config = ConfigDict(from_attributes=True)
