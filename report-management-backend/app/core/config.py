from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, model_validator
import logging

class Settings(BaseSettings):
    APP_NAME: str = "Report Management Backend"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Single source of truth for DB
    DATABASE_URL: str
    
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
        if not self.DATABASE_URL:
            raise ValueError("DATABASE_URL must be provided in environment variables")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
