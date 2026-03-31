import asyncio
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from app.models.schemas import AnalysisRequest
from app.services.job_store import job_store, JobStatus
from app.services.supplier_parser import extract_supplier_answers
from app.services.aggregator import aggregate_scores, compute_overall_score
from app.services.ai_scorer import score_question, generate_supplier_summary
from app.services.document_parser import parse_document

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=10)

UPLOAD_DIR = Path("uploads")
META_DIR   = Path("metadata")


def _resolve_rfp_files(rfp_id: str, project_id: str = None):
    if project_id:
        from app.services.project_store import (
            get_rfp_path, get_supplier_paths, get_questions_path, get_suppliers_meta_path
        )
        rfp_path       = get_rfp_path(project_id)
        supplier_paths = get_supplier_paths(project_id)
        questions_path = get_questions_path(project_id)
        suppliers_meta = get_suppliers_meta_path(project_id)
        return rfp_path, supplier_paths, questions_path, suppliers_meta
    else:
        rfp_files      = list(UPLOAD_DIR.glob(f"{rfp_id}_rfp*"))
        rfp_path       = rfp_files[0] if rfp_files else None
        supplier_paths = list(UPLOAD_DIR.glob(f"{rfp_id}_supplier_*"))
        questions_path = META_DIR / f"{rfp_id}_questions.json"
        suppliers_meta = META_DIR / f"{rfp_id}_suppliers.json"
        return rfp_path, supplier_paths, questions_path, suppliers_meta


def _do_analysis(rfp_id: str, project_id: str = None) -> dict:
    rfp_path, supplier_paths, questions_path, suppliers_meta_path = _resolve_rfp_files(rfp_id, project_id)

    if not rfp_path or not rfp_path.exists():
        raise FileNotFoundError(f"RFP file not found for rfp_id={rfp_id}")
    if not supplier_paths:
        raise FileNotFoundError("No supplier files found")
    if not questions_path.exists():
        raise FileNotFoundError("Parsed questions not found — please parse the RFP first")

    questions = json.loads(questions_path.read_text())

    # Load supplier name overrides from metadata
    supplier_names: dict = {}
    if suppliers_meta_path and suppliers_meta_path.exists():
        supplier_names = json.loads(suppliers_meta_path.read_text())

    # Step 1: Extract answers from each supplier document
    parsed_suppliers = []
    for sp in supplier_paths:
        parsed = parse_document(str(sp))
        full_text = parsed.get("full_text", "")
        result = extract_supplier_answers(full_text, questions)
        # Prefer metadata name over LLM-detected name
        meta_name = supplier_names.get(str(sp)) or supplier_names.get(sp.name)
        if meta_name:
            result["supplier_name"] = meta_name
        parsed_suppliers.append(result)

    # Step 2: Build cross-supplier answer map for quantitative context
    # {question_id: {supplier_name: answer}}
    cross_answers: dict = {}
    for sup in parsed_suppliers:
        for qid, ans in sup.get("answers", {}).items():
            cross_answers.setdefault(qid, {})[sup["supplier_name"]] = ans

    # Step 3: Score each supplier
    suppliers_output = []
    for sup in parsed_suppliers:
        supplier_name = sup["supplier_name"]
        answers = sup.get("answers", {})

        # Score every question
        question_scores: dict = {}
        for q in questions:
            qid = q["question_id"]
            answer = answers.get(qid, "No response provided")
            # Pass other suppliers' answers for quantitative context
            other_answers = {k: v for k, v in cross_answers.get(qid, {}).items() if k != supplier_name}
            try:
                score_data = score_question(q, answer, other_answers)
            except Exception:
                score_data = {"score": 0, "rationale": "Scoring failed"}
            question_scores[qid] = score_data

        # Aggregate to category level
        category_results = aggregate_scores(questions, question_scores, answers, supplier_name)
        overall = compute_overall_score(category_results, questions)

        # Generate AI summary
        try:
            summary = generate_supplier_summary(supplier_name, category_results, overall)
        except Exception:
            summary = {"strengths": [], "weaknesses": [], "recommendation": ""}

        suppliers_output.append({
            "supplier_name": supplier_name,
            "overall_score": overall,
            "category_scores": category_results,
            "strengths": summary.get("strengths", []),
            "weaknesses": summary.get("weaknesses", []),
            "recommendation": summary.get("recommendation", ""),
        })

    # Sort by overall score descending
    suppliers_output.sort(key=lambda x: x["overall_score"], reverse=True)
    for i, s in enumerate(suppliers_output):
        s["rank"] = i + 1

    return {
        "rfp_id": rfp_id,
        "suppliers": suppliers_output,
        "total_questions": len(questions),
        "categories": list({q["category"] for q in questions}),
    }


async def _run_analysis_job(rfp_id: str, job_id: str, project_id: str = None):
    job_store.set_running(job_id)
    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor, lambda: _do_analysis(rfp_id, project_id)
        )
        job_store.set_completed(job_id, result)
    except Exception as e:
        job_store.set_failed(job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_analysis(req: AnalysisRequest, background_tasks: BackgroundTasks):
    """Legacy flat-file analysis (rfp_id based)."""
    job_id = job_store.create()
    background_tasks.add_task(_run_analysis_job, req.rfp_id, job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}


@router.get("/status/{job_id}")
async def get_analysis_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job.get("result"),
        "error":  job.get("error"),
    }


@router.get("/export/{rfp_id}")
async def export_analysis(rfp_id: str, format: str = "json"):
    export_dir = Path("exports")
    export_dir.mkdir(exist_ok=True)

    if format == "json":
        job = next(
            (j for j in job_store._store.values()
             if j.get("status") == "completed" and
             j.get("result", {}).get("rfp_id") == rfp_id),
            None,
        )
        if not job:
            raise HTTPException(status_code=404, detail="Analysis result not found")
        path = export_dir / f"{rfp_id}_analysis.json"
        path.write_text(json.dumps(job["result"], indent=2))
        return FileResponse(str(path), filename=f"{rfp_id}_analysis.json")

    raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")
