import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from app.models.schemas import AnalysisRequest, AnalysisResponse, SupplierResult, CategoryScore, QuestionScore
from app.services.document_parser import parse_document
from app.services.supplier_parser import extract_supplier_answers
from app.services.ai_scorer import score_question, generate_supplier_summary
from app.services.aggregator import aggregate_scores, compute_overall_score
from app.services.job_store import job_store, JobStatus

router = APIRouter()

UPLOAD_DIR = Path("uploads")
META_DIR = Path("metadata")

_executor = ThreadPoolExecutor(max_workers=10)
_api_semaphore = asyncio.Semaphore(5)


class JobStartResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


def _run_in_thread(fn, *args):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(_executor, fn, *args)


async def _parse_supplier(sf: Path, questions: list) -> tuple:
    async with _api_semaphore:
        parsed = await _run_in_thread(parse_document, str(sf))
        supplier_data = await _run_in_thread(extract_supplier_answers, parsed["full_text"], questions)
    name = supplier_data.get("supplier_name", sf.stem)
    answers = supplier_data.get("answers", {})
    return name, answers


async def _score_one(q: dict, answer: str, context) -> tuple:
    async with _api_semaphore:
        result = await _run_in_thread(score_question, q, answer, context)
    return q["question_id"], result


async def _process_supplier(supplier_name: str, answers: dict, questions: list, quant_context: dict) -> dict:
    score_tasks = [
        _score_one(
            q,
            answers.get(q["question_id"], "No response provided"),
            quant_context.get(q["question_id"]) if q["question_type"] == "quantitative" else None,
        )
        for q in questions
    ]
    scored_pairs = await asyncio.gather(*score_tasks)
    question_scores = dict(scored_pairs)

    category_results = aggregate_scores(questions, question_scores, answers, supplier_name)
    overall = compute_overall_score(category_results, questions)

    async with _api_semaphore:
        summary = await _run_in_thread(generate_supplier_summary, supplier_name, category_results, overall)

    return {
        "supplier_name": supplier_name,
        "overall_score": overall,
        "category_results": category_results,
        "strengths": summary.get("strengths", []),
        "weaknesses": summary.get("weaknesses", []),
        "recommendation": summary.get("recommendation", ""),
    }


async def _run_full_analysis(rfp_id: str, job_id: str):
    """The actual analysis logic — runs in background, writes result to job_store."""
    job_store.set_running(job_id)
    try:
        meta_path = META_DIR / f"{rfp_id}_questions.json"
        if not meta_path.exists():
            job_store.set_failed(job_id, "RFP not parsed yet. Call /rfp/{rfp_id}/parse first.")
            return

        questions = json.loads(meta_path.read_text())

        supplier_files = list(UPLOAD_DIR.glob(f"{rfp_id}_supplier_*"))
        if not supplier_files:
            job_store.set_failed(job_id, "No supplier responses uploaded for this RFP.")
            return

        parse_tasks = [_parse_supplier(sf, questions) for sf in supplier_files]
        parsed_suppliers = await asyncio.gather(*parse_tasks)
        all_suppliers_raw = {name: answers for name, answers in parsed_suppliers}

        quant_context = {}
        for q in questions:
            if q["question_type"] == "quantitative":
                qid = q["question_id"]
                quant_context[qid] = {
                    name: answers.get(qid, "No response")
                    for name, answers in all_suppliers_raw.items()
                }

        supplier_tasks = [
            _process_supplier(name, answers, questions, quant_context)
            for name, answers in all_suppliers_raw.items()
        ]
        supplier_results = list(await asyncio.gather(*supplier_tasks))
        supplier_results.sort(key=lambda x: x["overall_score"], reverse=True)

        formatted_suppliers = []
        for rank, s in enumerate(supplier_results, 1):
            cat_scores = [
                CategoryScore(
                    category=c["category"],
                    weighted_score=c["weighted_score"],
                    question_count=c["question_count"],
                    questions=[QuestionScore(**q) for q in c["questions"]],
                )
                for c in s["category_results"]
            ]
            formatted_suppliers.append(
                SupplierResult(
                    supplier_id=f"supplier_{rank}",
                    supplier_name=s["supplier_name"],
                    overall_score=s["overall_score"],
                    rank=rank,
                    category_scores=cat_scores,
                    strengths=s["strengths"],
                    weaknesses=s["weaknesses"],
                    recommendation=s["recommendation"],
                )
            )

        top = formatted_suppliers[0] if formatted_suppliers else None
        top_rec = (
            f"{top.supplier_name} is the top-ranked supplier with a score of {top.overall_score:.1f}/10."
            if top else "No suppliers evaluated."
        )

        result = AnalysisResponse(
            rfp_id=rfp_id,
            status="completed",
            suppliers=formatted_suppliers,
            top_recommendation=top_rec,
            analysis_summary=(
                f"Evaluated {len(formatted_suppliers)} supplier(s) across "
                f"{len(set(q['category'] for q in questions))} categories "
                f"and {len(questions)} questions."
            ),
        )
        # Serialise via pydantic so job_store holds plain dict
        job_store.set_completed(job_id, result.model_dump())

    except Exception as e:
        job_store.set_failed(job_id, str(e))


@router.post("/run", response_model=JobStartResponse)
async def run_analysis(req: AnalysisRequest, background_tasks: BackgroundTasks):
    """Start analysis in the background. Returns job_id immediately."""
    job_id = job_store.create()
    background_tasks.add_task(_run_full_analysis, req.rfp_id, job_id)
    return JobStartResponse(job_id=job_id, status=JobStatus.PENDING)


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_analysis_status(job_id: str):
    """Poll this endpoint. Returns status + result when completed."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
    )
