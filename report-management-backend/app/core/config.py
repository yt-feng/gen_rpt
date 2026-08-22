from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator

class Settings(BaseSettings):
    APP_NAME: str = "Report Management Backend"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    
    API_V1_STR: str = "/api/v1"
    
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Single source of truth for DB — must be set as env var on Render
    DATABASE_URL: str = ""
    
    # Supabase Integrations
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    
    # R2 Storage
    R2_ACCOUNT_ID: Optional[str] = None
    R2_ACCESS_KEY_ID: Optional[str] = None
    R2_SECRET_ACCESS_KEY: Optional[str] = None
    R2_BUCKET: Optional[str] = None
    
    # Redis Cache
    REDIS_URL: Optional[str] = None
    
    # Gateway APIs
    OPENAI_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    HF_API_TOKEN: Optional[str] = None  # Hugging Face Inference API token (free)
    
    # Auth
    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # GateX / MENA Compass Publishing Integration
    GATEX_BASE_URL: str = ""                    # e.g. https://<api-host>/api
    GATEX_API_KEY: str = ""                     # X-API-Key provided by MENA Compass team
    GATEX_TIMEOUT: int = 30                     # HTTP timeout in seconds
    GATEX_MAX_RETRIES: int = 3                  # Max retries for transient 5xx errors
    GATEX_VERIFY_UPLOAD: bool = True            # Verify presigned upload succeeded before submitting metadata
    GATEX_ENABLE_PUBLISHING: bool = False       # Master switch — keep False until credentials are configured
    GATEX_DEFAULT_COVER_PATH: str = ""          # Optional R2 path to fallback cover image
    
    # GitHub Workflow Dispatch
    GITHUB_TOKEN: Optional[str] = None
    GITHUB_REPO: str = "yt-feng/gen_rpt"

    # Internal webhook token — must match INTERNAL_TOKEN secret set in GitHub Actions
    INTERNAL_TOKEN: Optional[str] = None

    # --- Knowledge Intelligence (RAG) Feature Flags ---
    KNOWLEDGE_ENABLED: bool = False
    RAG_ENABLED: bool = False
    UPLOAD_ENABLED: bool = False
    PROCESSING_ENABLED: bool = False
    RETRIEVAL_ENABLED: bool = False
    VALIDATION_ENABLED: bool = False
    SEARCH_ENABLED: bool = False

    # --- Knowledge Providers & Logging ---
    KNOWLEDGE_LOG_LEVEL: str = "INFO"
    KNOWLEDGE_STORAGE_PROVIDER: str = "r2"
    KNOWLEDGE_VECTOR_PROVIDER: str = "pgvector"

    # --- Knowledge Object Storage Settings ---
    KNOWLEDGE_R2_BUCKET: Optional[str] = None
    KNOWLEDGE_STORAGE_PREFIX: str = "knowledge/"
    KNOWLEDGE_ARCHIVE_PREFIX: str = "archive/"
    KNOWLEDGE_EXPORT_PREFIX: str = "exports/"
    KNOWLEDGE_LOG_PREFIX: str = "logs/"
    KNOWLEDGE_PROCESSING_PREFIX: str = "processing/"
    KNOWLEDGE_RETENTION_POLICY_DAYS: int = 30
    KNOWLEDGE_STORAGE_VERSIONING: bool = True
    KNOWLEDGE_STORAGE_CHECKSUM_ALGO: str = "sha256"
    KNOWLEDGE_STORAGE_COMPRESSION: bool = False

    # --- Knowledge Pipeline Settings ---
    KNOWLEDGE_CHUNK_SIZE: int = 1000
    KNOWLEDGE_CHUNK_OVERLAP: int = 200
    KNOWLEDGE_EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"  # HF model; set to text-embedding-3-small for OpenAI
    KNOWLEDGE_EMBEDDING_DIMENSION: int = 384  # 384 for bge-small; 1536 for OpenAI text-embedding-3-small
    KNOWLEDGE_RETRY_COUNT: int = 3
    KNOWLEDGE_PROCESSING_TIMEOUT: int = 300  # seconds
    KNOWLEDGE_PARALLEL_WORKERS: int = 2
    KNOWLEDGE_LANGUAGE_DETECTION_CONFIDENCE: float = 0.8
    KNOWLEDGE_VALIDATION_STRICT: bool = False
    KNOWLEDGE_MAX_FILE_SIZE_MB: int = 50
    KNOWLEDGE_SETTINGS: dict = {}
    KNOWLEDGE_PROCESSING_SETTINGS: dict = {}
    KNOWLEDGE_EMBEDDING_SETTINGS: dict = {}
    KNOWLEDGE_RETRIEVAL_SETTINGS: dict = {}
    KNOWLEDGE_VALIDATION_SETTINGS: dict = {}
    KNOWLEDGE_CHUNKING_SETTINGS: dict = {}
    KNOWLEDGE_MONITORING_SETTINGS: dict = {}

    # --- Embedding Fallback Settings ---
    EMBEDDING_FALLBACK_PROVIDER: str = "ollama"  # "ollama" or "openai"
    OLLAMA_EMBEDDING_URL: str = "http://localhost:11434/api/embeddings"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"
    OPENAI_EMBEDDING_URL: str = "https://api.openai.com/v1/embeddings"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_API_KEY: str = ""

    # --- RAG Runtime Guardrails ---
    # Keep context deliberately smaller than the model window: retrieved evidence
    # should support the report, not consume the entire prompt budget.
    RAG_CONTEXT_TOKEN_BUDGET: int = 6000
    RAG_CONTEXT_CACHE_TTL_SECONDS: int = 14400
    RAG_MIN_RELEVANCE_SCORE: float = 0.35


    
    @model_validator(mode='after')
    def validate_database_url(self) -> 'Settings':
        if not self.DATABASE_URL or not self.DATABASE_URL.strip():
            raise ValueError(
                "DATABASE_URL is required but not set. "
                "On Render: add DATABASE_URL in the Environment Variables section. "
                "Format: postgresql+asyncpg://user:password@host:5432/dbname"
            )
        return self

    @model_validator(mode='after')
    def validate_jwt_secret(self) -> 'Settings':
        if self.APP_ENV == "production" and self.JWT_SECRET == "change-me":
            raise ValueError("JWT_SECRET must be set in production")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,   # empty DATABASE_URL= in .env won't override a real env var
        extra="ignore"
    )

settings = Settings()
