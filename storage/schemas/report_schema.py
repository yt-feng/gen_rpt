"""
storage/schemas/report_schema.py

Minimal report metadata structure for type consistency across the storage layer.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List


@dataclass
class ReportMeta:
    """Lightweight report metadata record — derived from a report directory."""
    report_id: str
    title: str
    slug: str
    tags: List[str]
    status: str = "generated"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ReportMeta":
        return cls(
            report_id=data["report_id"],
            title=data.get("title", ""),
            slug=data.get("slug", data["report_id"]),
            tags=data.get("tags", []),
            status=data.get("status", "generated"),
            created_at=data.get("created_at", ""),
        )
