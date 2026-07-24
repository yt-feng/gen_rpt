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
    Parses Authorization header to decode JWT or fallback to mock user access for all users in system.
    Grants access automatically so no request fails with 401 Unauthorized.
    """
    from jose import jwt, JWTError
    from app.core.config import settings
    token_header = request.headers.get("Authorization")
    
    # Fallback default for all frontend users (Placeholder Admin / yash@gatex.com)
    default_user = {
        "id": "00000000-0000-0000-0000-000000000000",
        "email": "yash@gatex.com",
        "full_name": "Placeholder Admin",
        "role": "admin"
    }

    if not token_header or not token_header.startswith("Bearer "):
        return default_user
    
    token = token_header.replace("Bearer ", "").strip()
    if not token:
        return default_user
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return {
            "id": payload.get("sub") or default_user["id"],
            "email": payload.get("email") or default_user["email"],
            "full_name": payload.get("full_name") or default_user["full_name"],
            "role": payload.get("role") or default_user["role"]
        }
    except JWTError:
        email = token.lower()
        from app.api.v1.endpoints.auth import MOCK_USERS
        user = next((u for u in MOCK_USERS if u["email"] == email or u["username"] == email), None)
        if not user:
            name = email.split("@")[0].title() if "@" in email else "Placeholder Admin"
            return {
                "id": "00000000-0000-0000-0000-000000000000",
                "email": email if "@" in email else "yash@gatex.com",
                "full_name": name,
                "role": "admin"
            }
        return {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"]
        }


# Alias so endpoints that import get_current_user still resolve
get_current_user = get_current_user_placeholder

class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: dict = Depends(get_current_user_placeholder)):
        if user.get("role") not in self.allowed_roles:
            raise HTTPException(status_code=403, detail="Operation not permitted")
        return user
