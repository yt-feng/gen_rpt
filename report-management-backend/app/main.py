from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.logging.logger import logger
from app.api.v1.router import api_router
from app.api.internal.router import internal_router
from app.middleware.request_logging import RequestLoggingMiddleware

app = FastAPI(
    title="Report Management Backend",
    description="Phase 1 Backend Foundation for Report Management",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Middleware
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint to validate application is running.
    """
    return {"status": "healthy", "environment": settings.ENVIRONMENT}

# Include API routers
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(internal_router, prefix="/api/internal")

logger.info("FastAPI application initialized.")
