import time
import json
import logging
from typing import Dict, Any, Optional
import redis.asyncio as redis
from app.core.config import settings

logger = logging.getLogger("report_management")

class KnowledgeCacheService:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.redis: Optional[redis.Redis] = None
        self.use_redis = False

    async def init_redis(self) -> None:
        if settings.REDIS_URL and settings.REDIS_URL != "REPLACE_WITH_REAL_VALUE":
            try:
                self.redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=2.0)
                await self.redis.ping()
                self.use_redis = True
                logger.info("Successfully connected to Redis cache.")
            except Exception as e:
                self.redis = None
                self.use_redis = False
                logger.warning(f"Failed to connect to Redis at {settings.REDIS_URL}: {e}. Falling back to in-memory dict.")
        else:
            logger.warning("REDIS_URL is not set. Falling back to in-memory dict.")

    async def close_redis(self) -> None:
        if self.redis:
            await self.redis.close()
            logger.info("Closed Redis cache connection pool.")

    async def get(self, key: str) -> Optional[Any]:
        cache_type = key.split(":")[0] if ":" in key else "unknown"
        if self.use_redis and self.redis:
            try:
                val = await self.redis.get(key)
                if val is not None:
                    from app.core.metrics import knowledge_cache_hits_total
                    knowledge_cache_hits_total.labels(cache_type=cache_type).inc()
                    return json.loads(val)
                return None
            except Exception as e:
                logger.error(f"Redis get failed: {e}")
        
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() > entry["expires_at"]:
            del self._cache[key]
            return None
            
        from app.core.metrics import knowledge_cache_hits_total
        knowledge_cache_hits_total.labels(cache_type=cache_type).inc()
        return entry["value"]

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        if self.use_redis and self.redis:
            try:
                await self.redis.setex(key, ttl, json.dumps(value, default=str))
                return
            except Exception as e:
                logger.error(f"Redis set failed: {e}")
        
        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + ttl
        }

    async def delete(self, key: str) -> None:
        if self.use_redis and self.redis:
            try:
                await self.redis.delete(key)
                return
            except Exception as e:
                logger.error(f"Redis delete failed: {e}")

        if key in self._cache:
            del self._cache[key]

    async def clear(self) -> None:
        if self.use_redis and self.redis:
            try:
                await self.redis.flushdb()
                return
            except Exception as e:
                logger.error(f"Redis flushdb failed: {e}")

        self._cache.clear()

    async def invalidate_collection(self, collection_id: Any) -> None:
        col_str = str(collection_id)
        if self.use_redis and self.redis:
            try:
                async for key in self.redis.scan_iter(match=f"*{col_str}*"):
                    await self.redis.delete(key)
                async for key in self.redis.scan_iter(match="*stats*"):
                    await self.redis.delete(key)
                async for key in self.redis.scan_iter(match="*permission*"):
                    await self.redis.delete(key)
                return
            except Exception as e:
                logger.error(f"Redis invalidate_collection failed: {e}")

        keys_to_delete = [
            key for key in self._cache 
            if col_str in key or "stats" in key or "permission" in key
        ]
        for key in keys_to_delete:
            await self.delete(key)

    async def invalidate_tags(self) -> None:
        if self.use_redis and self.redis:
            try:
                async for key in self.redis.scan_iter(match="*tag*"):
                    await self.redis.delete(key)
                return
            except Exception as e:
                logger.error(f"Redis invalidate_tags failed: {e}")

        keys_to_delete = [key for key in self._cache if "tag" in key]
        for key in keys_to_delete:
            await self.delete(key)

    async def invalidate_categories(self) -> None:
        if self.use_redis and self.redis:
            try:
                async for key in self.redis.scan_iter(match="*category*"):
                    await self.redis.delete(key)
                return
            except Exception as e:
                logger.error(f"Redis invalidate_categories failed: {e}")

        keys_to_delete = [key for key in self._cache if "category" in key]
        for key in keys_to_delete:
            await self.delete(key)

knowledge_cache_service = KnowledgeCacheService()

