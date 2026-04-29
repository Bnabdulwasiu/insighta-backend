import os
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse


# API versioning
class APIVersionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Always pass through CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            if os.getenv("ENV") == "production":
                version = request.headers.get("X-API-Version")
                if not version:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "status": "error",
                            "message": "API version header required"
                        }
                    )
        return await call_next(request)


