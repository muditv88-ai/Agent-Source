"""
rfp.py
FastAPI router — POST /api/v1/rfp/parse

Accepts a PDF or DOCX upload, extracts raw text, passes it through
AgentLoop → RFPParserAgent, and returns structured JSON.
"""

import uuid
import logging
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from agent_loop import AgentLoop
from api.deps import get_agent_loop

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rfp", tags=["RFP"])

# ── Accepted MIME types ───────────────────────────────────────────────────────
ALLOWED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB


# ── Text extraction helpers ───────────────────────────────────────────────────

def _extract_pdf(data: bytes) -> str:
    """Extract plain text from a PDF using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF extraction failed: {exc}")


def _extract_docx(data: bytes) -> str:
    """Extract plain text from a DOCX using python-docx."""
    try:
        import docx
        doc = docx.Document(BytesIO(data))
        return "\n".join(para.text for para in doc.paragraphs)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"DOCX extraction failed: {exc}")


def _extract_text(filename: str, data: bytes, mime: str) -> str:
    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        return _extract_pdf(data)
    return _extract_docx(data)


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/parse", summary="Parse an RFP document")
async def parse_rfp(
    file: UploadFile = File(..., description="PDF or DOCX RFP document"),
    loop: AgentLoop = Depends(get_agent_loop),
):
    """
    Upload a PDF or DOCX RFP.  Returns a structured JSON object with:
      title, buyer, deadline, budget, categories,
      evaluation_criteria, submission_format, contact_email,
      attachments_required.
    """
    # ── Validate MIME / size ──────────────────────────────────────────────────
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME and not file.filename.lower().endswith(
        (".pdf", ".docx")
    ):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{content_type}'. Upload a PDF or DOCX.",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds 20 MB limit ({len(raw_bytes) // 1024} KB received).",
        )

    logger.info(f"parse_rfp: received '{file.filename}' ({len(raw_bytes)} bytes)")

    # ── Extract text ──────────────────────────────────────────────────────────
    text = _extract_text(file.filename, raw_bytes, content_type)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract any text from the uploaded file.",
        )

    # ── Run agent loop ────────────────────────────────────────────────────────
    result = await loop.run(
        task="parse rfp",
        payload={"text": text, "filename": file.filename},
    )

    return JSONResponse(
        status_code=200,
        content={
            "session_id": loop.session_id,
            "filename":   file.filename,
            "parsed":     result.get("output", {}).get("rfp_parser", {}),
        },
    )
