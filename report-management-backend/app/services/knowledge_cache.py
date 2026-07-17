import time
from typing import Dict, Any, Optional

class KnowledgeCacheService:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        # Check expiration
        if time.time() > entry["expires_at"]:
            del self._cache[key]
            return None
        return entry["value"]

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + ttl
        }

    def delete(self, key: str) -> None:
        if key in self._cache:
            del self._cache[key]

    def clear(self) -> None:
        self._cache.clear()

    def invalidate_collection(self, collection_id: Any) -> None:
        col_str = str(collection_id)
        keys_to_delete = [
            key for key in self._cache 
            if col_str in key or "stats" in key or "permission" in key
        ]
        for key in keys_to_delete:
            self.delete(key)

    def invalidate_tags(self) -> None:
        keys_to_delete = [key for key in self._cache if "tag" in key]
        for key in keys_to_delete:
            self.delete(key)

    def invalidate_categories(self) -> None:
        keys_to_delete = [key for key in self._cache if "category" in key]
        for key in keys_to_delete:
            self.delete(key)

knowledge_cache_service = KnowledgeCacheService()
