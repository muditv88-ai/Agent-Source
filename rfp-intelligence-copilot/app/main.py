from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.health import router as health_router
from app.api.routes.rfp import router as rfp_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.scenarios import router as scenarios_router
from app.api.routes.communications import router as communications_router

app = FastAPI(title="RFP Intelligence Copilot", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sourceiq.vercel.app",
        "https://sourceiq-muditv88-ais-projects.vercel.app",
        "https://sourceiq-git-main-muditv88-ais-projects.vercel.app",
        "https://*.vercel.app",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "ngrok-skip-browser-warning"],
)

app.include_router(health_router)
app.include_router(rfp_router, prefix="/rfp", tags=["RFP"])
app.include_router(analysis_router, prefix="/analysis", tags=["Analysis"])
app.include_router(scenarios_router, prefix="/scenarios", tags=["Scenarios"])
app.include_router(communications_router, prefix="/communications", tags=["Communications"])

@app.get("/")
def root():
    return {"message": "RFP Intelligence Copilot API is running", "endpoints": ["/health", "/rfp/upload", "/docs"]}
