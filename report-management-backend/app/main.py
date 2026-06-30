from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.logging.logger import logger
from app.api.v1.router import api_router, internal_router
from app.middleware.request_logging import RequestLoggingMiddleware
from app.core.exceptions import register_exception_handlers

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle handler."""
    # --- STARTUP ---
    logger.info(f"Starting {settings.APP_NAME} [{settings.APP_ENV}]")
    logger.info(f"DATABASE_URL configured: {'YES' if settings.DATABASE_URL else 'NO'}")
    
    # Validate DB connection
    try:
        import time
        from sqlalchemy import text
        from app.core.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection: OK")

        # Seed placeholder user if not present
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy import select
        from uuid import UUID
        from app.models.identity import User
        
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as session:
            placeholder_id = UUID("00000000-0000-0000-0000-000000000000")
            result = await session.execute(select(User).where(User.id == placeholder_id))
            user = result.scalar_one_or_none()
            if not user:
                logger.info("Seeding default placeholder user...")
                db_user = User(
                    id=placeholder_id,
                    full_name="Placeholder Admin",
                    email="placeholder@admin.com",
                    status="active"
                )
                session.add(db_user)
                await session.commit()
                logger.info("Default placeholder user seeded successfully.")
    except Exception as e:
        logger.error(f"Database connection or seeding FAILED at startup: {e}")
    
    # Validate R2 connection (non-fatal)
    try:
        from app.storage.provider import storage_provider
        r2_health = await storage_provider.health_check()
        if r2_health.get("status") == "healthy":
            logger.info("Cloudflare R2 connection: OK")
        else:
            logger.warning(f"Cloudflare R2 degraded: {r2_health.get('error')}")
    except Exception as e:
        logger.warning(f"Cloudflare R2 check skipped: {e}")
    
    logger.info(f"API available at: {settings.API_V1_STR}")
    logger.info("Startup complete. Ready to serve requests.")
    
    yield
    
    # --- SHUTDOWN ---
    logger.info("Shutting down application.")
    from app.core.database import engine
    await engine.dispose()

app = FastAPI(
    title="Report Management Backend",
    description="Enterprise Report Management Platform — Phases 1-10",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan
)

# Register central error handlers
register_exception_handlers(app)

# Middleware
app.add_middleware(RequestLoggingMiddleware)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if "https://gen-rpt-review-frontend.pages.dev" not in origins:
    origins.append("https://gen-rpt-review-frontend.pages.dev")

if "*" in origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
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
    
    # not_configured is a warning, not a full degradation — DB must be healthy
    storage_status = storage_health.get("status")
    overall_status = "healthy"
    if db_status != "healthy":
        overall_status = "degraded"
    elif storage_status not in ("healthy", "not_configured"):
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
