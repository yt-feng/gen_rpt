from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from jose import jwt
from app.core.config import settings
from app.core.responses import APIResponse, success_response, error_response
from app.core.rate_limit import limiter

router = APIRouter()

MOCK_USERS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "jacob@gatex.com",
        "username": "jacob",
        "password": "jacob1@1",
        "full_name": "Jacob",
        "role": "reviewer"
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "email": "denis@gatex.com",
        "username": "denis",
        "password": "denis1@1",
        "full_name": "Denis",
        "role": "reviewer"
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "email": "frank@gatex.com",
        "username": "frank",
        "password": "frank1@1",
        "full_name": "Frank",
        "role": "reviewer"
    },
    {
        "id": "44444444-4444-4444-4444-444444444444",
        "email": "sam@gatex.com",
        "username": "sam",
        "password": "sam1@1",
        "full_name": "Sam",
        "role": "reviewer"
    },
    {
        "id": "55555555-5555-5555-5555-555555555555",
        "email": "yash@gatex.com",
        "username": "yash",
        "password": "yash1@1",
        "full_name": "Yash Yelave",
        "role": "manager"
    },
    {
        "id": "00000000-0000-0000-0000-000000000000",
        "email": "placeholder@admin.com",
        "username": "admin",
        "password": "admin",
        "full_name": "Placeholder Admin",
        "role": "admin"
    }
]

class LoginRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: str

@router.post("/login", response_model=APIResponse[dict])
@limiter.limit("5/minute")
async def login(request: Request, req: LoginRequest):
    identity = (req.email or req.username or "").strip().lower()
    if not identity:
        raise HTTPException(status_code=400, detail="Missing username or email")
    password = req.password
    
    user = next(
        (u for u in MOCK_USERS if u["email"].lower() == identity or u["username"].lower() == identity),
        None
    )
    if not user or user["password"] != password:
        raise HTTPException(status_code=401, detail="Invalid username/email or password")
        
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user["role"],
        "exp": expire
    }
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    token = f"Bearer {encoded_jwt}"
    return success_response(
        data={
            "token": token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "full_name": user["full_name"],
                "role": user["role"]
            }
        },
        message="Login successful"
    )

@router.get("/me", response_model=APIResponse[dict])
async def get_me(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    email = authorization.replace("Bearer ", "").strip()
    user = next((u for u in MOCK_USERS if u["email"] == email), None)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    return success_response(
        data={
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"]
        },
        message="Fetched active user profile"
    )
