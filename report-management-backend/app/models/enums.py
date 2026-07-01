import enum

class DocStatus(str, enum.Enum):
    draft = "draft"
    ai_reviewed = "ai_reviewed"
    assigned = "assigned"
    in_review = "in_review"
    needs_revision = "needs_revision"
    waiting_for_ai = "waiting_for_ai"
    waiting_for_human = "waiting_for_human"
    approved = "approved"
    ready_for_publish = "ready_for_publish"
    published = "published"
    rejected = "rejected"
    archived = "archived"

class ReviewAssignmentStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    reassigned = "reassigned"

class ReviewerRole(str, enum.Enum):
    primary = "primary"
    secondary = "secondary"
    technical = "technical"
    editorial = "editorial"
    manager = "manager"

class CommentActionType(str, enum.Enum):
    comment = "comment"
    ai_request = "ai_request"

class DocChangeType(str, enum.Enum):
    AI_GENERATION = "AI_GENERATION"
    AI_REGENERATION = "AI_REGENERATION"
    HUMAN_EDIT = "HUMAN_EDIT"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ROLLBACK = "ROLLBACK"
    MERGE = "MERGE"
    PUBLISH_RELEASE = "PUBLISH_RELEASE"
    IMPORTED_VERSION = "IMPORTED_VERSION"
    RESTORE = "RESTORE"
    SYSTEM_UPDATE = "SYSTEM_UPDATE"

class ReleaseStatus(str, enum.Enum):
    Draft = "Draft"
    Internal_Review = "Internal Review"
    Approved = "Approved"
    Release_Candidate = "Release Candidate"
    Published = "Published"
    Archived = "Archived"

class BlockActor(str, enum.Enum):
    AI = "AI"
    Human = "Human"
    System = "System"

class ReviewDecisionType(str, enum.Enum):
    approved = "approved"
    needs_revision = "needs_revision"
    rejected = "rejected"

class JobStatusType(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"

class PublishStatusType(str, enum.Enum):
    """States for GateX external publishing lifecycle."""
    publishing = "publishing"               # In progress — presign/upload/submit underway
    published = "published"                 # Successfully published to GateX
    publish_failed = "publish_failed"       # Failed; may be retried
    external_sync_pending = "external_sync_pending"  # Submitted; awaiting GateX processing
    external_sync_failed = "external_sync_failed"    # GateX processing failed
    unpublishing = "unpublishing"           # Unpublish in progress
    unpublished = "unpublished"             # Successfully removed from GateX
    rejected = "rejected"                   # Rejected after publish attempt

class BlockContentType(str, enum.Enum):
    paragraph = "paragraph"
    table = "table"
    chart = "chart"
    image = "image"
    citation = "citation"
    quote = "quote"
    list = "list"
    code = "code"
    equation = "equation"

class SectionContentType(str, enum.Enum):
    Executive_Summary = "Executive Summary"
    Introduction = "Introduction"
    Market = "Market"
    Conclusion = "Conclusion"
    Appendix = "Appendix"
    References = "References"
    Custom = "Custom"

class EditorActionType(str, enum.Enum):
    AI = "AI"
    Human = "Human"
    System = "System"

class ProposalStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    modified_accepted = "modified_accepted"

class AIProviderType(str, enum.Enum):
    groq = "groq"
    openai = "openai"
    anthropic = "anthropic"
    gemini = "gemini"
    local = "local"
