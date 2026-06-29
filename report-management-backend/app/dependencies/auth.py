from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from app.core.security import verify_token
from app.logging.logger import logger

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Dependency to get the current authenticated user.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = verify_token(token)
    if payload is None:
        raise credentials_exception
    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    
    # In a real app, you would fetch the user from the database here
    # user = await user_service.get_user(user_id)
    # if user is None:
    #     raise credentials_exception
    # return user
    return {"user_id": user_id, "role": "user"}

async def require_admin(current_user: dict = Depends(get_current_user)):
    """
    Dependency to enforce admin role.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user
