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

    # --- Knowledge Pipeline Settings (Placeholders) ---
    KNOWLEDGE_SETTINGS: dict = {}
    KNOWLEDGE_PROCESSING_SETTINGS: dict = {}
    KNOWLEDGE_EMBEDDING_SETTINGS: dict = {}
    KNOWLEDGE_RETRIEVAL_SETTINGS: dict = {}
    KNOWLEDGE_VALIDATION_SETTINGS: dict = {}
    KNOWLEDGE_CHUNKING_SETTINGS: dict = {}
    KNOWLEDGE_MONITORING_SETTINGS: dict = {}


    
    @model_validator(mode='after')
    def validate_database_url(self) -> 'Settings':
        if not self.DATABASE_URL or not self.DATABASE_URL.strip():
            raise ValueError(
                "DATABASE_URL is required but not set. "
                "On Render: add DATABASE_URL in the Environment Variables section. "
                "Format: postgresql+asyncpg://user:password@host:5432/dbname"
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,   # empty DATABASE_URL= in .env won't override a real env var
        extra="ignore"
    )

settings = Settings()
