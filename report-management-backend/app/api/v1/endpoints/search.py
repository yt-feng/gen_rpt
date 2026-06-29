from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.deps import get_db, PageParams, FilterParams, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/", response_model=APIResponse[list])
async def global_search(
    q: str,
    page: PageParams = Depends(),
    filters: FilterParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Global search across documents, metadata, and status.
    """
    return success_response(
        data=[],
        message=f"Search results for '{q}'",
        metadata={"total": 0, "offset": page.offset, "limit": page.limit, "has_more": False}
    )
