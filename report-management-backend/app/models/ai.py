import uuid
from typing import Optional, List, Dict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, DateTime, Enum, Boolean
from app.models.base import Base, UUIDMixin, JSONVariant
from app.models.enums import ProposalStatus, AIProviderType

class AIPromptTemplate(Base, UUIDMixin):
    __tablename__ = "ai_prompt_templates"
    name: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    template_text: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class AIProposal(Base, UUIDMixin):
    __tablename__ = "ai_proposals"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    
    # Store array of stable IDs
    target_node_stable_ids: Mapped[list] = mapped_column(JSONVariant, nullable=False)
    
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_prompt_templates.id", ondelete="SET NULL"), nullable=True)
    context_bundle: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    
    model_provider: Mapped[AIProviderType] = mapped_column(
        Enum(AIProviderType, name="ai_provider_type", create_type=False),
        nullable=False
    )
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    
    response_content: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, name="proposal_status", create_type=False),
        default=ProposalStatus.pending
    )
    
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timestamp: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Metrics
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
