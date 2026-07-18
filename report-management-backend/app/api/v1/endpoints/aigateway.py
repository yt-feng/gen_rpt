from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from uuid import UUID

from app.api.deps import get_db
from app.services.rag_integration import ai_gateway_service

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = "deepseek-chat"
    temperature: float = 0.2
    response_format: Optional[dict] = None
    max_tokens: Optional[int] = None
    slug: Optional[str] = None

@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    db: AsyncSession = Depends(get_db),
    x_internal_token: str = Header(...)
):
    """
    OpenAI-compatible chat completion endpoint.
    Routes LLM calls from scripts to DeepSeek while tracking budgets and performance.
    """
    from app.core.config import settings
    expected = getattr(settings, "INTERNAL_TOKEN", None) or "trusted-worker-secret"
    if x_internal_token != expected:
        raise HTTPException(status_code=403, detail="Invalid internal token")

    try:
        messages_dict = [{"role": m.role, "content": m.content} for m in req.messages]
        res = await ai_gateway_service.chat_completion(
            db=db,
            messages=messages_dict,
            temperature=req.temperature,
            model=req.model,
            response_format=req.response_format,
            max_tokens=req.max_tokens,
            slug=req.slug
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
