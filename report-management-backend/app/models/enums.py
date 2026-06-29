import enum

class DocStatus(str, enum.Enum):
    draft = "draft"
    ai_reviewed = "ai_reviewed"
    in_review = "in_review"
    approved = "approved"
    published = "published"
    rejected = "rejected"

class DocChangeType(str, enum.Enum):
    AI_GENERATION = "AI_GENERATION"
    AI_REGENERATION = "AI_REGENERATION"
    HUMAN_EDIT = "HUMAN_EDIT"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ROLLBACK = "ROLLBACK"

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
