from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.auth import router as auth_router
from app.api.routes.rfp import router as rfp_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.scenarios import router as scenarios_router
from app.api.routes.communications import router as communications_router
from app.api.routes.chat import router as chat_router
from app.api.routes.pricing import router as pricing_router
from app.api.routes.projects import router as projects_router

app = FastAPI(title="RFP Intelligence Copilot", version="1.2.0")

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health_router)
app.include_router(auth_router,           prefix="/auth",           tags=["Auth"])
app.include_router(projects_router,       prefix="/projects",       tags=["Projects"])
app.include_router(rfp_router,            prefix="/rfp",            tags=["RFP"])
app.include_router(analysis_router,       prefix="/analysis",       tags=["Analysis"])
app.include_router(scenarios_router,      prefix="/scenarios",      tags=["Scenarios"])
app.include_router(communications_router, prefix="/communications", tags=["Communications"])
app.include_router(chat_router,           prefix="/chat",           tags=["Chat"])
app.include_router(pricing_router,        prefix="/pricing",        tags=["Pricing"])


@app.get("/")
def root():
    return {
        "message": "RFP Intelligence Copilot API is running",
        "version": "1.2.0",
    }
