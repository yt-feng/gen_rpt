from typing import Optional
from jose import jwt
from datetime import datetime, timedelta
from app.core.config import settings

ALGORITHM = "HS256"

def create_access_token(subject: str, expires_delta: timedelta) -> str:
    """
    Foundation for JWT generation.
    """
    expire = datetime.utcnow() + expires_delta
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> Optional[dict]:
    """
    Foundation for JWT verification.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None
