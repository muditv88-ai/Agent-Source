#!/usr/bin/env python
"""
Run the FastAPI app with proper CORS handling for all responses.
This wrapper ensures CORS headers are always sent, even on error responses.
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

# Now import FastAPI and other dependencies
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
import uvicorn

# Import the original app
from app.main import app as original_app

# Get CORS origins from environment
_raw_origins = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000,http://localhost:8082"
)
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

print(f"[CORS] Allowed origins: {ALLOWED_ORIGINS}", file=sys.stderr)

# Create a new app with explicit error handling
app = FastAPI()

# Add custom exception handler for all exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all exceptions with proper CORS headers."""
    import traceback
    print(f"[ERROR] {exc}", file=sys.stderr)
    traceback.print_exc()

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Credentials": "true",
        }
    )

# Mount the original app's routes
# Copy all routes from original_app
for route in original_app.routes:
    app.routes.append(route)

# Re-add CORS middleware with explicit origin handling
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    print("[START] Running FastAPI with CORS wrapper", file=sys.stderr)
    print(f"[START] Listening on http://0.0.0.0:8000", file=sys.stderr)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="debug"
    )
