from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime

class NotificationBase(BaseModel):
    message: str
    read: bool = False

class NotificationResponse(NotificationBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ActivityLogBase(BaseModel):
    action: str
    details: Optional[Dict[str, Any]] = None

class ActivityLogResponse(ActivityLogBase):
    id: UUID
    user_id: Optional[UUID] = None
    timestamp: datetime
    
    model_config = ConfigDict(from_attributes=True)

class AuditLogBase(BaseModel):
    table_name: str
    record_id: UUID
    action: str
    old_data: Optional[Dict[str, Any]] = None
    new_data: Optional[Dict[str, Any]] = None

class AuditLogResponse(AuditLogBase):
    id: UUID
    changed_by: Optional[UUID] = None
    timestamp: datetime
    
    model_config = ConfigDict(from_attributes=True)
