from typing import TypeVar, Generic, Optional, Any, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

T = TypeVar("T")

class PaginationMetadata(BaseModel):
    total: int
    offset: int
    limit: int
    has_more: bool

class APIResponse(BaseModel, Generic[T]):
    status: str = Field(description="Response status, usually 'success' or 'error'")
    message: str = Field(description="Human-readable message summarizing the response")
    data: Optional[T] = Field(default=None, description="The primary data payload")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional context or pagination metadata")
    errors: Optional[List[Dict[str, Any]]] = Field(default=None, description="List of structured error objects if applicable")
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)

def success_response(data: T, message: str = "Success", metadata: Optional[Dict[str, Any]] = None) -> APIResponse[T]:
    return APIResponse(
        status="success",
        message=message,
        data=data,
        metadata=metadata
    )

def error_response(message: str, errors: Optional[List[Dict[str, Any]]] = None) -> APIResponse[None]:
    return APIResponse(
        status="error",
        message=message,
        errors=errors
    )
