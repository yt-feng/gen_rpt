from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.logging.logger import logger
from app.api.v1.router import api_router, internal_router
from app.middleware.request_logging import RequestLoggingMiddleware
from app.core.exceptions import register_exception_handlers

app = FastAPI(
    title="Report Management Backend",
    description="Phase 1 Backend Foundation for Report Management",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Register central error handlers
register_exception_handlers(app)

# Middleware
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import time
from sqlalchemy import text
from app.core.database import engine

@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint to validate application and database.
    """
    start_time = time.time()
    db_status = "unhealthy"
    error_msg = None
    
    try:
        if not settings.DATABASE_URL:
            raise ValueError("DATABASE_URL is missing")
            
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Health check database connection failed: {error_msg}")
        
    from app.storage.provider import storage_provider
    storage_health = await storage_provider.health_check()
        
    response_time_ms = round((time.time() - start_time) * 1000, 2)
    
    overall_status = "healthy"
    if db_status != "healthy" or storage_health.get("status") != "healthy":
        overall_status = "degraded"
    
    return {
        "status": overall_status,
        "environment": settings.APP_ENV,
        "database": {
            "status": db_status,
            "error": error_msg
        },
        "storage": storage_health,
        "response_time_ms": response_time_ms
    }

# Include API routers
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(internal_router, prefix="/api/internal")

logger.info("FastAPI application initialized.")
