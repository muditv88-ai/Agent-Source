from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_db

router = APIRouter()

@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Health check with database connectivity test."""
    try:
        # Simple query to test DB connection
        from sqlmodel import select
        from app.models.project_file import ProjectFile
        db.exec(select(ProjectFile).limit(1)).all()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "message": "RFP Intelligence Copilot ready",
        "database": db_status,
    }