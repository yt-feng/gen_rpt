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
