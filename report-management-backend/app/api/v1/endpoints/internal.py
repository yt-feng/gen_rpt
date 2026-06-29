from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.responses import APIResponse, success_response
from app.core.config import settings

router = APIRouter()

def verify_internal_token(x_internal_token: str = Header(...)):
    """
    Verifies that requests to internal endpoints come from trusted internal services.
    """
    # For now, just a placeholder check
    if x_internal_token != "trusted-worker-secret":
        raise HTTPException(status_code=403, detail="Invalid internal token")

@router.post("/sync/catalog", response_model=APIResponse[dict])
async def sync_catalog():
    """
    Internal endpoint to sync data from external background workers or Github actions.
    No public access.
    """
    return success_response(data={}, message="Sync placeholder triggered")
