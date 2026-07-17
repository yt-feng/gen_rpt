import time
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.logging.logger import logger
from app.core.config import settings

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        # Attach request_id to request state
        request.state.request_id = request_id
        
        # 1. Extract user_id from JWT token
        user_id = "anonymous"
        token_header = request.headers.get("Authorization")
        if token_header and token_header.startswith("Bearer "):
            token = token_header.replace("Bearer ", "").strip()
            from jose import jwt, JWTError
            try:
                payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
                user_id = payload.get("sub") or "anonymous"
            except JWTError:
                # Fallback strictly for local development
                if settings.APP_ENV == "development":
                    email = token.lower()
                    try:
                        from app.api.v1.endpoints.auth import MOCK_USERS
                        user = next((u for u in MOCK_USERS if u["email"] == email), None)
                        if user:
                            user_id = user["id"]
                    except Exception:
                        pass

        # 2. Classify knowledge operation
        knowledge_op = "non-knowledge"
        if "/knowledge" in request.url.path:
            # e.g., /api/v1/knowledge/collections -> collections
            parts = [p for p in request.url.path.split("/") if p]
            if len(parts) > 3:
                knowledge_op = parts[3]
            else:
                knowledge_op = "general"
        
        start_time = time.time()
        
        logger.info(
            f"Incoming request: {request.method} {request.url.path} (ID: {request_id}, User: {user_id}, Op: {knowledge_op})",
            extra={
                "request_id": request_id,
                "user_id": user_id,
                "knowledge_operation": knowledge_op
            }
        )
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = str(process_time)
            
            logger.info(
                f"Completed request: {request.method} {request.url.path} "
                f"- Status: {response.status_code} "
                f"- Time: {process_time:.4f}s (ID: {request_id}, User: {user_id}, Op: {knowledge_op})",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "knowledge_operation": knowledge_op,
                    "status_code": response.status_code,
                    "process_time": process_time
                }
            )
            return response
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"Failed request: {request.method} {request.url.path} "
                f"- Error: {str(e)} "
                f"- Time: {process_time:.4f}s (ID: {request_id}, User: {user_id}, Op: {knowledge_op})",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "knowledge_operation": knowledge_op,
                    "error": str(e),
                    "process_time": process_time
                }
            )
            raise
