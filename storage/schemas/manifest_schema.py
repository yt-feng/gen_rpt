"""
storage/schemas/manifest_schema.py

Data contract for a per-report manifest file.
Lives at: reports/{REPORT_ID}/manifest.json
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class ManifestFiles:
    """Paths (R2 keys) for all files associated with a report version."""
    report_md: str = ""
    report_pdf: str = ""
    report_html: str = ""
    review_md: str = ""
    review_json: str = ""
    review_html: str = ""
    scores_json: str = ""
    findings_json: str = ""
    claims_json: str = ""
    sources_json: str = ""
    research_plan_json: str = ""
    report_payload_json: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

    @classmethod
    def from_dict(cls, data: dict) -> "ManifestFiles":
        return cls(**{k: data.get(k, "") for k in cls.__dataclass_fields__})


@dataclass
class Manifest:
    """Per-report manifest stored at reports/{report_id}/manifest.json."""
    report_id: str
    title: str
    slug: str
    latest_version: str = "v1"
    current_status: str = "generated"
    ai_score: float = 0.0
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    files: ManifestFiles = field(default_factory=ManifestFiles)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        if isinstance(self.files, dict):
            self.files = ManifestFiles.from_dict(self.files)

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["files"] = self.files.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        files_data = data.get("files", {})
        return cls(
            report_id=data["report_id"],
            title=data.get("title", ""),
            slug=data.get("slug", data["report_id"]),
            latest_version=data.get("latest_version", "v1"),
            current_status=data.get("current_status", "generated"),
            ai_score=float(data.get("ai_score", 0)),
            tags=data.get("tags", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            files=ManifestFiles.from_dict(files_data),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
