"""
Session module — re-exports the canonical engine and session factory from app.core.database.
All code should import get_db from this module (or from app.api.deps which wraps it).
"""
from app.core.database import engine, AsyncSessionLocal as async_session_maker, get_db

__all__ = ["engine", "async_session_maker", "get_db"]
