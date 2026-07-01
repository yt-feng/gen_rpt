"""
GateX Taxonomy Cache Service
============================
Fetches and caches public lookup data from the MENA Compass API:
  - Report categories  (GET /api/common/categories?type=report)
  - Tags               (GET /api/common/tags)
  - Regions            (GET /api/common/regions)
  - Industries         (GET /api/common/industries)

All endpoints are public — no authentication required.
Cache TTL is 1 hour (3600 seconds). Stale cache is refreshed on first use.
"""

import time
import asyncio
from typing import Optional, List, Dict, Any
import httpx

from app.core.config import settings
from app.logging.logger import logger

# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 3600  # 1 hour

# ---------------------------------------------------------------------------
# In-memory cache store
# ---------------------------------------------------------------------------
_cache: Dict[str, Dict[str, Any]] = {
    "categories": {"data": [], "fetched_at": 0.0},
    "tags": {"data": [], "fetched_at": 0.0},
    "regions": {"data": [], "fetched_at": 0.0},
    "industries": {"data": [], "fetched_at": 0.0},
}

_cache_lock = asyncio.Lock()


def _is_stale(key: str) -> bool:
    return (time.time() - _cache[key]["fetched_at"]) > _CACHE_TTL_SECONDS


def _base_url() -> str:
    return settings.GATEX_BASE_URL.rstrip("/")


# ---------------------------------------------------------------------------
# Internal fetcher
# ---------------------------------------------------------------------------
async def _fetch_all_pages(path: str, params: Optional[dict] = None) -> List[dict]:
    """
    Fetches all pages from a paginated GateX public endpoint.
    Uses limit=100 and steps through until all items are loaded.
    No auth header is sent — these are public endpoints.
    """
    base = _base_url()
    if not base:
        logger.warning("GATEX_BASE_URL is not configured — taxonomy fetch skipped.")
        return []

    all_items: List[dict] = []
    start = 0
    limit = 100
    total: Optional[int] = None

    async with httpx.AsyncClient(timeout=settings.GATEX_TIMEOUT) as client:
        while True:
            query = {"start": start, "limit": limit, **(params or {})}
            try:
                resp = await client.get(f"{base}{path}", params=query)
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("data", {}).get("items", [])
                if total is None:
                    total = payload.get("data", {}).get("total", 0)
                all_items.extend(items)
                start += len(items)
                if start >= (total or 0) or not items:
                    break
            except httpx.HTTPStatusError as e:
                logger.error(f"GateX taxonomy fetch failed [{path}]: {e.response.status_code} — {e.response.text[:200]}")
                break
            except Exception as e:
                logger.error(f"GateX taxonomy fetch error [{path}]: {e}")
                break

    return all_items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def get_categories(force_refresh: bool = False) -> List[dict]:
    """Returns all report-type categories. Refreshes cache if stale or forced."""
    async with _cache_lock:
        if force_refresh or _is_stale("categories"):
            logger.info("Refreshing GateX categories cache...")
            items = await _fetch_all_pages("/common/categories", {"type": "report"})
            _cache["categories"]["data"] = items
            _cache["categories"]["fetched_at"] = time.time()
            logger.info(f"Cached {len(items)} GateX categories.")
    return _cache["categories"]["data"]


async def get_tags(force_refresh: bool = False) -> List[dict]:
    """Returns all tags. Refreshes cache if stale or forced."""
    async with _cache_lock:
        if force_refresh or _is_stale("tags"):
            logger.info("Refreshing GateX tags cache...")
            items = await _fetch_all_pages("/common/tags")
            _cache["tags"]["data"] = items
            _cache["tags"]["fetched_at"] = time.time()
            logger.info(f"Cached {len(items)} GateX tags.")
    return _cache["tags"]["data"]


async def get_regions(force_refresh: bool = False) -> List[dict]:
    """Returns all regions. Refreshes cache if stale or forced."""
    async with _cache_lock:
        if force_refresh or _is_stale("regions"):
            logger.info("Refreshing GateX regions cache...")
            items = await _fetch_all_pages("/common/regions")
            _cache["regions"]["data"] = items
            _cache["regions"]["fetched_at"] = time.time()
            logger.info(f"Cached {len(items)} GateX regions.")
    return _cache["regions"]["data"]


async def get_industries(force_refresh: bool = False) -> List[dict]:
    """Returns all industries. Refreshes cache if stale or forced."""
    async with _cache_lock:
        if force_refresh or _is_stale("industries"):
            logger.info("Refreshing GateX industries cache...")
            items = await _fetch_all_pages("/common/industries")
            _cache["industries"]["data"] = items
            _cache["industries"]["fetched_at"] = time.time()
            logger.info(f"Cached {len(items)} GateX industries.")
    return _cache["industries"]["data"]


async def resolve_category_id(industry_hint: Optional[str]) -> Optional[int]:
    """
    Attempts to find a matching GateX category ID from the report's industry label.
    Falls back to the first available report category if no match found.
    Returns None if no categories are available.
    """
    categories = await get_categories()
    if not categories:
        return None

    if industry_hint:
        hint_lower = industry_hint.lower()
        for cat in categories:
            if hint_lower in cat.get("name", "").lower():
                return cat["id"]

    # Fallback to first category
    return categories[0]["id"] if categories else None


async def resolve_tag_ids(tags: Optional[List[str]] = None, max_tags: int = 5) -> List[int]:
    """
    Attempts to match provided tag names to GateX tag IDs.
    Returns up to max_tags (1–5) IDs. Falls back to first available tag if no match.
    The GateX API requires at least 1 tag ID.
    """
    all_tags = await get_tags()
    if not all_tags:
        return []

    matched: List[int] = []

    if tags:
        for t in tags:
            t_lower = t.lower()
            for gt in all_tags:
                if t_lower in gt.get("name", "").lower():
                    if gt["id"] not in matched:
                        matched.append(gt["id"])
                    break

    if not matched:
        # Fallback: use the first available tag
        matched = [all_tags[0]["id"]]

    return matched[:max_tags]


async def resolve_region_id(region_hint: Optional[str]) -> Optional[int]:
    """Attempts to match a region name to a GateX region ID. Optional field."""
    regions = await get_regions()
    if not regions or not region_hint:
        return None

    hint_lower = region_hint.lower()
    for r in regions:
        if hint_lower in r.get("name", "").lower():
            return r["id"]
    return None


def get_cache_status() -> dict:
    """Returns the current cache state for admin/debug endpoints."""
    now = time.time()
    return {
        k: {
            "count": len(v["data"]),
            "fetched_at": v["fetched_at"],
            "age_seconds": round(now - v["fetched_at"], 1),
            "is_stale": _is_stale(k),
        }
        for k, v in _cache.items()
    }
