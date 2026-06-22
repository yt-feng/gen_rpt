"""
storage/schemas/review_schema.py

Minimal review metadata structure for type consistency across the storage layer.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class ReviewMeta:
    """Lightweight review metadata record."""
    report_id: str
    review_status: str   # "pending" | "ai_reviewed" | "human_reviewed"
    ai_score: float = 0.0
    model: str = ""
    timestamp_utc: str = ""
    workflow_run_id: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp_utc:
            self.timestamp_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ReviewMeta":
        return cls(
            report_id=data.get("report_id", ""),
            review_status=data.get("review_status", "pending"),
            ai_score=float(data.get("ai_score", 0)),
            model=data.get("model", ""),
            timestamp_utc=data.get("timestamp_utc", ""),
            workflow_run_id=data.get("workflow_run_id", ""),
        )
