from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, List, Any

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

MOCK_COMMENTS: Dict[str, List[Dict[str, Any]]] = {}

@router.get("/{document_id}/comments", response_model=APIResponse[list])
async def list_comments(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List threaded comments for a document.
    """
    comments = MOCK_COMMENTS.get(document_id, [])
    return success_response(data=comments, message="Fetched comments")

@router.post("/{document_id}/comments", response_model=APIResponse[list])
async def create_comment(
    document_id: str,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Create a new comment or resolve an existing one.
    """
    if document_id not in MOCK_COMMENTS:
        MOCK_COMMENTS[document_id] = []
        
    comments = MOCK_COMMENTS[document_id]
    
    if payload.get("_action") == "resolve":
        comment_id = payload.get("commentId")
        for comment in comments:
            if comment.get("id") == comment_id:
                comment["status"] = "resolved"
    else:
        comments.append(payload)
        
    return success_response(data=comments, message="Updated comments")
