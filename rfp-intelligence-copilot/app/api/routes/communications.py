from fastapi import APIRouter
from app.models.schemas import ClarificationRequest, ClarificationResponse
from app.services.communication_engine import draft_clarification_email

router = APIRouter()

@router.post("/clarification-email", response_model=ClarificationResponse)
def clarification_email(req: ClarificationRequest):
    draft = draft_clarification_email(req.supplier_id, req.questions)
    return ClarificationResponse(subject=draft["subject"], body=draft["body"])