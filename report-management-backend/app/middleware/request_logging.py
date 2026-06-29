import time
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.logging.logger import logger

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        # Attach request_id to request state
        request.state.request_id = request_id
        
        start_time = time.time()
        
        logger.info(f"Incoming request: {request.method} {request.url.path} (ID: {request_id})")
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = str(process_time)
            
            logger.info(
                f"Completed request: {request.method} {request.url.path} "
                f"- Status: {response.status_code} "
                f"- Time: {process_time:.4f}s (ID: {request_id})"
            )
            return response
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"Failed request: {request.method} {request.url.path} "
                f"- Error: {str(e)} "
                f"- Time: {process_time:.4f}s (ID: {request_id})"
            )
            raise
