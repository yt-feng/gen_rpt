"""
storage/schemas/catalog_schema.py

Data contract for a single catalog entry.
The catalog (catalog/catalog.json) is the primary entry point for the frontend.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

# ── Allowed status values ────────────────────────────────────────────────────
VALID_STATUSES = {
    "generated",
    "ai_reviewed",
    "in_review",
    "needs_revision",
    "approved",
    "published",
    "rejected",
}

# ── Allowed tag categories (non-exhaustive; used for documentation) ──────────
VALID_TAG_CATEGORIES = {
    "country",
    "industry",
    "topic",
    "technology",
    "policy",
    "investment",
    "market",
    "custom",
}


@dataclass
class CatalogEntry:
    """A single entry in catalog/catalog.json."""

    report_id: str
    title: str
    slug: str
    status: str                      # must be in VALID_STATUSES
    review_status: str               # e.g. "pending", "ai_reviewed", "human_reviewed"
    ai_score: float                  # 0–100
    tags: List[str] = field(default_factory=list)
    created_at: str = ""             # ISO-8601 UTC
    updated_at: str = ""             # ISO-8601 UTC
    latest_version: str = "v1"
    manifest_path: str = ""          # R2 key, e.g. reports/{id}/manifest.json

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{self.status}'. Must be one of: {sorted(VALID_STATUSES)}"
            )
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.manifest_path:
            self.manifest_path = f"reports/{self.report_id}/manifest.json"

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = _now_iso()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CatalogEntry":
        return cls(
            report_id=data["report_id"],
            title=data.get("title", ""),
            slug=data.get("slug", data["report_id"]),
            status=data.get("status", "generated"),
            review_status=data.get("review_status", "pending"),
            ai_score=float(data.get("ai_score", 0)),
            tags=data.get("tags", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            latest_version=data.get("latest_version", "v1"),
            manifest_path=data.get("manifest_path", ""),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
