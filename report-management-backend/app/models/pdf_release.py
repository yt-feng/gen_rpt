"""
app/models/pdf_release.py

Tracks versioned, immutable PDF artifacts generated for report publication.
Every PDF is tied to a specific document version and HTML content checksum.
Never overwrite — append a new record for each regeneration.
"""

import uuid
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, BigInteger, Boolean, ForeignKey, DateTime, func

from app.models.base import Base, UUIDMixin


class PdfRelease(Base, UUIDMixin):
    """
    Immutable versioned PDF artifact for a document.

    Lifecycle:
      - Created when a user clicks Publish (PDF generated or reused).
      - is_active=True means this is the latest PDF for the document.
      - On regeneration, previous record is set is_active=False and a new record is created.
      - gatex_published_version=True means this exact PDF was sent to GateX.
    """
    __tablename__ = "pdf_releases"

    # Document this PDF belongs to
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The document version that was active when this PDF was generated (nullable — mock mode)
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )

    # Monotonically increasing PDF version number per document (v1, v2, v3, …)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # SHA-256 checksum of the HTML used to generate the PDF.
    # Used for change detection — same checksum means reuse the PDF.
    html_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Human-readable document version label (e.g. "1.2", "v5") from the report dict
    canonical_version_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # R2 storage path: reports/{document_id}/versions/pdf/v{n}/report.pdf
    storage_path: Mapped[str] = mapped_column(String, nullable=False)

    # File metadata
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    render_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Audit
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    # State flags
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    gatex_published_version: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
