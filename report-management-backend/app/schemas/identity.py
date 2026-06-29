from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Optional, List
from uuid import UUID
from datetime import datetime

class UserBase(BaseModel):
    full_name: str
    email: EmailStr
    avatar: Optional[str] = None
    status: str = "active"

class UserCreate(UserBase):
    pass

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    avatar: Optional[str] = None
    status: Optional[str] = None

class UserResponse(UserBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None

class RoleResponse(RoleBase):
    id: UUID
    
    model_config = ConfigDict(from_attributes=True)

class OrganizationBase(BaseModel):
    name: str
    slug: str

class OrganizationResponse(OrganizationBase):
    id: UUID
    
    model_config = ConfigDict(from_attributes=True)
