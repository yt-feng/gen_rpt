from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from app.models.enums import JobStatusType, BlockActor

class AIEditRequestBase(BaseModel):
    prompt: str
    instruction: str
    status: JobStatusType = JobStatusType.pending
    model: Optional[str] = None

class AIEditRequestResponse(AIEditRequestBase):
    id: UUID
    block_id: UUID
    created_by: Optional[UUID] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class BlockEditBase(BaseModel):
    old_value: Optional[Dict[str, Any]] = None
    new_value: Dict[str, Any]
    reason: Optional[str] = None

class BlockEditResponse(BlockEditBase):
    id: UUID
    block_id: UUID
    edited_by: Optional[UUID] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ChangeHistoryBase(BaseModel):
    entity: str
    entity_id: UUID
    change_type: str
    actor: BlockActor
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None

class ChangeHistoryResponse(ChangeHistoryBase):
    id: UUID
    document_id: UUID
    version_id: UUID
    timestamp: datetime
    
    model_config = ConfigDict(from_attributes=True)
