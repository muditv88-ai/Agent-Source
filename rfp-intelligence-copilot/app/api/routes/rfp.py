from fastapi import APIRouter, UploadFile, File, HTTPException
from app.models.schemas import UploadResponse, ParseResponse
from app.services.workbook_parser import parse_workbook
import uuid
import shutil
from pathlib import Path

router = APIRouter()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

@router.post("/upload", response_model=UploadResponse)
async def upload_rfp(file: UploadFile = File(...)):
    """Upload RFP Excel template"""
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Only Excel files allowed")
    
    rfp_id = str(uuid.uuid4())
    dest_path = UPLOAD_DIR / f"{rfp_id}_{file.filename}"
    
    with dest_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return UploadResponse(
        rfp_id=rfp_id, 
        filename=file.filename, 
        status="uploaded"
    )

@router.post("/{rfp_id}/parse", response_model=ParseResponse)
async def parse_rfp(rfp_id: str):
    """Parse uploaded RFP into canonical structure"""
    # Find the uploaded file
    upload_files = list(UPLOAD_DIR.glob(f"{rfp_id}*"))
    if not upload_files:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_path = upload_files[0]
    workbook_data = parse_workbook(str(file_path))
    
    canonical_model = {
        "rfp_id": rfp_id,
        "workbook_structure": workbook_data
    }
    
    return ParseResponse(
        rfp_id=rfp_id,
        status="parsed successfully", 
        canonical_model=canonical_model
    )