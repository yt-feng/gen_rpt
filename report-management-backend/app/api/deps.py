from typing import Optional, Callable
from fastapi import Query, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db

class PageParams:
    def __init__(
        self,
        offset: int = Query(0, ge=0, description="Pagination offset"),
        limit: int = Query(50, ge=1, le=100, description="Pagination limit")
    ):
        self.offset = offset
        self.limit = limit

class FilterParams:
    def __init__(
        self,
        status: Optional[str] = Query(None, description="Filter by status"),
        reviewer_id: Optional[str] = Query(None, description="Filter by reviewer ID"),
        tag: Optional[str] = Query(None, description="Filter by tag"),
        sort_by: str = Query("created_at", description="Field to sort by"),
        sort_order: str = Query("desc", description="Sort order (asc/desc)")
    ):
        self.status = status
        self.reviewer_id = reviewer_id
        self.tag = tag
        self.sort_by = sort_by
        self.sort_order = sort_order

def get_current_user_placeholder(request: Request) -> dict:
    """
    Placeholder for actual JWT validation.
    In Phase 4, we just mock a user object.
    """
    token = request.headers.get("Authorization")
    if not token:
        # For development purposes, allow anonymous access as a default user
        # In a real scenario, this would raise 401
        return {"id": "00000000-0000-0000-0000-000000000000", "role": "admin"}
    
    # Placeholder token decoding
    return {"id": "00000000-0000-0000-0000-000000000000", "role": "admin"}

class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: dict = Depends(get_current_user_placeholder)):
        if user.get("role") not in self.allowed_roles:
            raise HTTPException(status_code=403, detail="Operation not permitted")
        return user
