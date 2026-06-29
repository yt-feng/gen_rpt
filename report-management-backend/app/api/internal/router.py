from fastapi import APIRouter

internal_router = APIRouter(tags=["Internal"])

@internal_router.post("/sync")
def internal_sync():
    """
    Placeholder endpoint for future GitHub Actions communication.
    """
    return {"message": "Not Implemented"}
