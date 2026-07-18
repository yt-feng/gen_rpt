import os
import socket
old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(*args, **kwargs):
    responses = old_getaddrinfo(*args, **kwargs)
    return [r for r in responses if r[0] == socket.AF_INET]
socket.getaddrinfo = new_getaddrinfo

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.core.rate_limit import limiter

from app.core.config import settings
from app.logging.logger import logger
from app.api.v1.router import api_router, internal_router
from app.middleware.request_logging import RequestLoggingMiddleware
from app.core.exceptions import register_exception_handlers
from prometheus_fastapi_instrumentator import Instrumentator
from app.core import metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle handler."""
    # --- STARTUP ---
    logger.info(f"Starting {settings.APP_NAME} [{settings.APP_ENV}]")
    logger.info(f"DATABASE_URL configured: {'YES' if settings.DATABASE_URL else 'NO'}")
    
    # Validate DB connection and run migrations
    try:
        import time
        from sqlalchemy import text
        from app.core.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection: OK")

        # Run Alembic migrations programmatically
        try:
            import alembic.config
            import alembic.command
            logger.info("Running database migrations...")
            alembic_cfg = alembic.config.Config("alembic.ini")
            
            # Run the synchronous alembic command in a thread so it doesn't block asyncio
            from anyio import to_thread
            await to_thread.run_sync(alembic.command.upgrade, alembic_cfg, "head")
            logger.info("Database migrations applied successfully.")
        except Exception as e:
            logger.error(f"Failed to run database migrations: {e}")

        # Seed all mock users if not present
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy import select
        from uuid import UUID
        from app.models.identity import User
        from app.api.v1.endpoints.auth import MOCK_USERS
        
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as session:
            for mock_user in MOCK_USERS:
                u_id = UUID(mock_user["id"])
                result = await session.execute(select(User).where(User.id == u_id))
                user = result.scalar_one_or_none()
                if not user:
                    db_user = User(
                        id=u_id,
                        full_name=mock_user["full_name"],
                        email=mock_user["email"],
                        status="active"
                    )
                    session.add(db_user)
            await session.commit()
            logger.info("All mock users seeded successfully.")
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

    # Hydrate MOCK_REPORTS from R2 so reports survive backend restarts.
    # This is critical for bulk-generated reports which have no DB rows.
    try:
        import asyncio
        from app.services.startup_hydration import hydrate_mock_reports_from_r2
        # Run in background so startup isn't blocked by a slow R2 scan
        asyncio.create_task(hydrate_mock_reports_from_r2())
        logger.info("R2 hydration task scheduled.")
    except Exception as e:
        logger.warning(f"R2 hydration scheduling failed: {e}")
    
    # Start knowledge processing pipeline
    if settings.KNOWLEDGE_ENABLED and settings.PROCESSING_ENABLED:
        try:
            from app.services.knowledge_processing.pipeline import knowledge_pipeline
            knowledge_pipeline.start()
        except Exception as e:
            logger.error(f"Failed to start Knowledge Processing Pipeline: {e}")

    # Initialize Redis Cache
    try:
        from app.services.knowledge_cache import knowledge_cache_service
        await knowledge_cache_service.init_redis()
    except Exception as e:
        logger.warning(f"Failed to initialize Redis cache: {e}")

    logger.info(f"API available at: {settings.API_V1_STR}")
    logger.info("Startup complete. Ready to serve requests.")
    
    yield
    
    # --- SHUTDOWN ---
    logger.info("Shutting down application.")
    try:
        from app.services.knowledge_processing.pipeline import knowledge_pipeline
        knowledge_pipeline.stop()
    except Exception as e:
        logger.error(f"Failed to stop Knowledge Processing Pipeline: {e}")
    try:
        from app.services.knowledge_cache import knowledge_cache_service
        await knowledge_cache_service.close_redis()
    except Exception as e:
        logger.error(f"Failed to close Redis cache: {e}")

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

Instrumentator().instrument(app).expose(app, endpoint="/metrics")


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Register central error handlers
register_exception_handlers(app)

# Middleware
app.add_middleware(SlowAPIMiddleware)
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
    Health check endpoint to validate application, database, cache, and embeddings.
    """
    start_time = time.time()
    db_status = "unhealthy"
    error_msg = None
    
    # 1. Database connection check
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
    storage_status = storage_health.get("status")

    # 2. Embedding health check (HF first, then OpenAI)
    embedding_status = "idle"
    if settings.KNOWLEDGE_ENABLED:
        use_hf = bool(settings.HF_API_TOKEN and settings.HF_API_TOKEN.strip())
        use_openai = bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY not in ("", "REPLACE_WITH_REAL_VALUE"))
        if use_hf:
            try:
                import httpx
                import anyio
                # Use the same router URL as the embedding worker (embedding.py)
                # IMPORTANT: api-inference.huggingface.co returns 404 for free tokens;
                # router.huggingface.co/hf-inference/models/ is the correct endpoint.
                hf_model = settings.KNOWLEDGE_EMBEDDING_MODEL if "/" in settings.KNOWLEDGE_EMBEDDING_MODEL else "BAAI/bge-small-en-v1.5"
                hf_health_url = f"https://router.huggingface.co/hf-inference/models/{hf_model}"
                async def check_hf_emb():
                    async with httpx.AsyncClient(timeout=10.0) as c:
                        r = await c.post(
                            hf_health_url,
                            headers={"Authorization": f"Bearer {settings.HF_API_TOKEN}"},
                            json={"inputs": ["health check"], "options": {"wait_for_model": False}}
                        )
                        # 503 = model loading (not a real failure, token is valid)
                        if r.status_code not in (200, 503):
                            r.raise_for_status()
                with anyio.fail_after(12.0):
                    await check_hf_emb()
                embedding_status = "healthy"
            except Exception as e:
                logger.error(f"Health check HF embedding connection failed: {e}")
                embedding_status = "degraded"
        elif use_openai:
            try:
                from openai import AsyncOpenAI
                import anyio
                client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                async def check_emb():
                    await client.embeddings.create(input=["test"], model="text-embedding-3-small")
                with anyio.fail_after(3.0):
                    await check_emb()
                embedding_status = "healthy"
            except Exception as e:
                logger.error(f"Health check OpenAI embedding connection failed: {e}")
                embedding_status = "degraded"
        else:
            embedding_status = "not_configured"

    # 3. Vector extension health check
    vector_status = "idle"
    if settings.KNOWLEDGE_ENABLED:
        try:
            if engine.dialect.name == "postgresql":
                async with engine.connect() as conn:
                    res = await conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')"))
                    vector_exists = res.scalar()
                vector_status = "healthy" if vector_exists else "not_configured"
            else:
                vector_status = "healthy"  # SQLite mockup
        except Exception as e:
            logger.error(f"Health check vector extension check failed: {e}")
            vector_status = "degraded"

    # 4. AI Gateway health check
    ai_gateway_status = "idle"
    if settings.RAG_ENABLED:
        if settings.DEEPSEEK_API_KEY and settings.DEEPSEEK_API_KEY != "REPLACE_WITH_REAL_VALUE":
            ai_gateway_status = "healthy"
        else:
            ai_gateway_status = "not_configured"

    # 5. Processing pipeline status
    try:
        from app.services.knowledge_processing.pipeline import knowledge_pipeline
        pipeline_status = "healthy" if knowledge_pipeline.active else "idle"
    except Exception as e:
        pipeline_status = "idle"

    # 6. Redis health check
    redis_status = "not_configured"
    if settings.REDIS_URL and settings.REDIS_URL != "REPLACE_WITH_REAL_VALUE":
        try:
            from app.services.knowledge_cache import knowledge_cache_service
            if knowledge_cache_service.redis:
                await knowledge_cache_service.redis.ping()
                redis_status = "healthy"
            else:
                redis_status = "degraded"
        except Exception as e:
            logger.error(f"Health check Redis connection failed: {e}")
            redis_status = "degraded"

    # 7. Stats query
    collections_count = 0
    documents_count = 0
    queue_jobs_count = 0
    if db_status == "healthy" and settings.KNOWLEDGE_ENABLED:
        try:
            async with engine.connect() as conn:
                from sqlalchemy import select, func
                from app.models.knowledge import KnowledgeCollection, KnowledgeDocument, KnowledgeProcessingQueue
                col_res = await conn.execute(select(func.count(KnowledgeCollection.id)).filter(KnowledgeCollection.deleted_at.is_(None)))
                collections_count = col_res.scalar() or 0
                doc_res = await conn.execute(select(func.count(KnowledgeDocument.id)).filter(KnowledgeDocument.deleted_at.is_(None)))
                documents_count = doc_res.scalar() or 0
                q_res = await conn.execute(select(func.count(KnowledgeProcessingQueue.id)))
                queue_jobs_count = q_res.scalar() or 0
        except Exception as e:
            logger.error(f"Failed to fetch health check database stats: {e}")

    # Determine overall status
    overall_status = "healthy"
    if db_status != "healthy":
        overall_status = "degraded"
    elif storage_status not in ("healthy", "not_configured"):
        overall_status = "degraded"
    elif embedding_status == "degraded":
        overall_status = "degraded"
    elif vector_status == "degraded":
        overall_status = "degraded"
    elif redis_status == "degraded":
        overall_status = "degraded"

    from app.services.knowledge_storage import knowledge_storage_service
    knowledge_storage_health = await knowledge_storage_service.check_connectivity()

    knowledge_health = {
        "status": "healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        "module_loaded": True,
        "workers_status": pipeline_status,
        "queue_status": pipeline_status,
        "processing_status": pipeline_status,
        "embedding_status": embedding_status,
        "validation_status": "healthy" if settings.KNOWLEDGE_ENABLED and settings.VALIDATION_ENABLED else "idle",
        "knowledge_index": vector_status,
        "storage_provider": settings.KNOWLEDGE_STORAGE_PROVIDER,
        "vector_provider": settings.KNOWLEDGE_VECTOR_PROVIDER,
        "knowledge_storage": knowledge_storage_health,
        "statistics": {
            "active_collections_count": collections_count,
            "active_documents_count": documents_count,
            "queue_jobs_count": queue_jobs_count
        },
        "feature_flags": {
            "KNOWLEDGE_ENABLED": settings.KNOWLEDGE_ENABLED,
            "RAG_ENABLED": settings.RAG_ENABLED,
            "UPLOAD_ENABLED": settings.UPLOAD_ENABLED,
            "PROCESSING_ENABLED": settings.PROCESSING_ENABLED,
            "RETRIEVAL_ENABLED": settings.RETRIEVAL_ENABLED,
            "VALIDATION_ENABLED": settings.VALIDATION_ENABLED,
            "SEARCH_ENABLED": settings.SEARCH_ENABLED,
        }
    }
    
    validation_health = {
        "status": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "validation_engine": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "authority_service": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "conflict_service": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "duplicate_service": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "confidence_service": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "policy_engine": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "history": "healthy" if settings.VALIDATION_ENABLED else "idle",
        "audit": "healthy" if settings.VALIDATION_ENABLED else "idle",
    }

    rag_integration_health = {
        "status": "healthy" if settings.RAG_ENABLED else "idle",
        "generation_context_service": "healthy" if settings.RAG_ENABLED else "idle",
        "prompt_builder": "healthy" if settings.RAG_ENABLED else "idle",
        "ai_gateway": ai_gateway_status,
        "knowledge_snapshot_service": "healthy" if settings.RAG_ENABLED else "idle",
        "context_cache": "healthy" if settings.RAG_ENABLED else "idle",
        "evidence_attribution_service": "healthy" if settings.RAG_ENABLED else "idle",
        "analytics": "healthy" if settings.RAG_ENABLED else "idle",
    }

    review_integration_health = {
        "status": "healthy" if settings.RAG_ENABLED else "idle",
        "evidence_viewer": "healthy" if settings.RAG_ENABLED else "idle",
        "knowledge_browser": "healthy" if settings.RAG_ENABLED else "idle",
        "traceability_service": "healthy" if settings.RAG_ENABLED else "idle",
        "validation_dashboard": "healthy" if settings.RAG_ENABLED else "idle",
        "review_snapshot_service": "healthy" if settings.RAG_ENABLED else "idle",
        "evidence_analytics": "healthy" if settings.RAG_ENABLED else "idle",
    }

    response_time_ms = round((time.time() - start_time) * 1000, 2)
    
    return {
        "status": overall_status,
        "environment": settings.APP_ENV,
        "revision": os.getenv("RENDER_GIT_COMMIT", "unknown")[:7],
        "database": {
            "status": db_status,
            "error": error_msg
        },
        "storage": storage_health,
        "redis": {
            "status": redis_status
        },
        "knowledge": knowledge_health,
        "validation": validation_health,
        "rag_integration": rag_integration_health,
        "review_integration": review_integration_health,
        "response_time_ms": response_time_ms
    }





# Include API routers
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(internal_router, prefix="/api/internal")

logger.info("FastAPI application initialized.")
