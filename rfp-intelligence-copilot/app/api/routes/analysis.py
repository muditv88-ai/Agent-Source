"""
analysis.py — analysis routes with:
  - parallel per-supplier scoring (eliminates timeout)
  - dual-LLM cross-check (primary + checker model)
  - tech/commercial split scores
  - price comparison table across suppliers (extracted from FULL document text)
  - GCS-aware file resolution
  - supplier company name resolved from document header (not filename)
"""
import asyncio
import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from app.models.schemas import AnalysisRequest, ScoringConfig, PriceComparison
from app.services.job_store import job_store, JobStatus
from app.services.supplier_parser import extract_supplier_answers
from app.services.aggregator import aggregate_scores, compute_split_scores
from app.services.ai_scorer import (
    score_questions_parallel,
    extract_prices_from_text,
    extract_supplier_name_from_text,
    generate_supplier_summary,
)
from app.services.document_parser import parse_document

router   = APIRouter()
_executor = ThreadPoolExecutor(max_workers=4)

UPLOAD_DIR = Path("uploads")
META_DIR   = Path("metadata")


# ── File resolution ────────────────────────────────────────────────────────────

def _resolve_rfp_files(rfp_id: str, project_id: str = None):
    if project_id:
        from app.services.project_store import (
            ensure_rfp_local, ensure_suppliers_local,
            get_questions_path, load_metadata,
        )
        rfp_path       = ensure_rfp_local(project_id)
        supplier_paths = ensure_suppliers_local(project_id)
        questions_path = get_questions_path(project_id)
        supplier_names = load_metadata(project_id, "suppliers.json") or {}
        return rfp_path, supplier_paths, questions_path, supplier_names
    else:
        rfp_files      = list(UPLOAD_DIR.glob(f"{rfp_id}_rfp*"))
        rfp_path       = rfp_files[0] if rfp_files else None
        supplier_paths = list(UPLOAD_DIR.glob(f"{rfp_id}_supplier_*"))
        questions_path = META_DIR / f"{rfp_id}_questions.json"
        sup_meta_path  = META_DIR / f"{rfp_id}_suppliers.json"
        supplier_names = json.loads(sup_meta_path.read_text()) if sup_meta_path.exists() else {}
        return rfp_path, supplier_paths, questions_path, supplier_names


# ── Per-supplier analysis (runs in thread) ──────────────────────────────────────────

def _analyse_one_supplier(
    sup: dict,
    questions: list,
    cross_answers: dict,
    tech_weight: float,
    commercial_weight: float,
    dual_llm: bool,
    rfp_full_text: str,
) -> dict:
    supplier_name = sup["supplier_name"]
    answers       = sup.get("answers", {})
    full_text     = sup.get("full_text", "")   # ← full document text carried through

    question_scores = score_questions_parallel(
        questions=questions,
        supplier_answer="",
        answers=answers,
        cross_answers=cross_answers,
        supplier_name=supplier_name,
        dual_llm=dual_llm,
        max_workers=8,
    )

    category_results = aggregate_scores(questions, question_scores, answers, supplier_name)
    split            = compute_split_scores(category_results, tech_weight, commercial_weight)
    flagged_count    = sum(1 for s in question_scores.values() if s.get("flagged"))

    # ── Price extraction ───────────────────────────────────────────────────────────
    # Use the full document text (not just answer snippets) so the pricing
    # sheet / commercial table is always included in the extraction pass.
    pricing_text = full_text or " ".join(answers.values())
    prices = extract_prices_from_text(pricing_text, rfp_full_text, supplier_name)

    summary = generate_supplier_summary(
        supplier_name      = supplier_name,
        category_scores    = category_results,
        overall_score      = split["overall_score"],
        technical_score    = split["technical_score"],
        commercial_score   = split["commercial_score"],
        flagged_count      = flagged_count,
    )

    return {
        "supplier_name":     supplier_name,
        "overall_score":     split["overall_score"],
        "technical_score":   split["technical_score"],
        "commercial_score":  split["commercial_score"],
        "category_scores":   category_results,
        "flagged_questions": flagged_count,
        "prices":            prices,
        "strengths":         summary.get("strengths", []),
        "weaknesses":        summary.get("weaknesses", []),
        "recommendation":    summary.get("recommendation", ""),
    }


# ── Main analysis orchestrator ───────────────────────────────────────────────────────────

def _do_analysis(
    rfp_id: str,
    project_id: str = None,
    tech_weight: float = 70.0,
    commercial_weight: float = 30.0,
    dual_llm: bool = True,
) -> dict:
    rfp_path, supplier_paths, questions_path, supplier_names = \
        _resolve_rfp_files(rfp_id, project_id)

    if not rfp_path or not rfp_path.exists():
        raise FileNotFoundError("RFP file not found")
    if not supplier_paths:
        raise FileNotFoundError("No supplier files found")
    if not questions_path.exists():
        raise FileNotFoundError("Parsed questions not found — please parse the RFP first")

    questions = json.loads(questions_path.read_text())

    rfp_doc       = parse_document(str(rfp_path))
    rfp_full_text = rfp_doc.get("full_text", "")

    # Step 1: Parse all supplier documents and resolve their display names.
    # Name resolution priority:
    #   1. Explicit name stored in suppliers.json at upload time
    #   2. Company name extracted by LLM from document header
    #   3. Filename stem as last resort
    parsed_suppliers = []
    for sp in supplier_paths:
        parsed    = parse_document(str(sp))
        full_text = parsed.get("full_text", "")
        result    = extract_supplier_answers(full_text, questions)

        meta_name = (
            supplier_names.get(str(sp))
            or supplier_names.get(sp.name)
        )
        if meta_name and meta_name != sp.stem:
            # Explicit name was set and is not just the filename stem — use it
            display_name = meta_name
        else:
            # Try to read company name from the document itself
            llm_name = extract_supplier_name_from_text(full_text, sp.stem)
            display_name = llm_name or meta_name or sp.stem

        result["supplier_name"] = display_name
        result["full_text"]     = full_text   # carry full text for pricing extraction
        parsed_suppliers.append(result)

    # Step 2: Cross-supplier answer map for quantitative context
    cross_answers: Dict = {}
    for sup in parsed_suppliers:
        for qid, ans in sup.get("answers", {}).items():
            cross_answers.setdefault(qid, {})[sup["supplier_name"]] = ans

    # Step 3: Score all suppliers IN PARALLEL
    suppliers_output = []
    with ThreadPoolExecutor(max_workers=min(4, len(parsed_suppliers))) as ex:
        futures = {
            ex.submit(
                _analyse_one_supplier,
                sup, questions, cross_answers,
                tech_weight, commercial_weight, dual_llm, rfp_full_text
            ): sup["supplier_name"]
            for sup in parsed_suppliers
        }
        for fut in as_completed(futures):
            try:
                suppliers_output.append(fut.result())
            except Exception as e:
                sname = futures[fut]
                suppliers_output.append({
                    "supplier_name": sname,
                    "overall_score": 0, "technical_score": 0, "commercial_score": 0,
                    "category_scores": [], "flagged_questions": 0, "prices": [],
                    "strengths": [], "weaknesses": [],
                    "recommendation": f"Analysis failed: {e}",
                })

    # Step 4: Build price comparison table from full-document pricing extraction
    price_map:   Dict[str, Dict[str, str]] = {}
    price_units: Dict[str, str] = {}
    for sup_result in suppliers_output:
        sname = sup_result["supplier_name"]
        for item in sup_result.pop("prices", []):
            li = item.get("line_item", "").strip()
            if not li:
                continue
            price_map.setdefault(li, {})[sname] = item.get("value", "")
            price_units[li] = item.get("unit", "")

    price_comparison = [
        PriceComparison(
            line_item = li,
            suppliers = price_map[li],
            unit      = price_units.get(li, ""),
        ).dict()
        for li in sorted(price_map)
    ]

    # Step 5: Rank
    suppliers_output.sort(key=lambda x: x["overall_score"], reverse=True)
    for i, s in enumerate(suppliers_output):
        s["rank"]        = i + 1
        s["supplier_id"] = s["supplier_name"]

    return {
        "rfp_id":           rfp_id,
        "suppliers":        suppliers_output,
        "total_questions":  len(questions),
        "categories":       list({q["category"] for q in questions}),
        "price_comparison": price_comparison,
        "scoring_config": {
            "tech_weight":       tech_weight,
            "commercial_weight": commercial_weight,
            "dual_llm":          dual_llm,
        },
    }


async def _run_analysis_job(
    rfp_id: str,
    job_id: str,
    project_id: str = None,
    tech_weight: float = 70.0,
    commercial_weight: float = 30.0,
    dual_llm: bool = True,
):
    job_store.set_running(job_id)
    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: _do_analysis(rfp_id, project_id, tech_weight, commercial_weight, dual_llm)
        )
        job_store.set_completed(job_id, result)
    except Exception as e:
        job_store.set_failed(job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ── Routes ──────────────────────────────────────────────────────────────

@router.post("/run")
async def run_analysis(req: AnalysisRequest, background_tasks: BackgroundTasks):
    job_id = job_store.create()
    background_tasks.add_task(
        _run_analysis_job,
        req.rfp_id, job_id, None,
        req.tech_weight, req.commercial_weight, req.dual_llm,
    )
    return {"job_id": job_id, "status": JobStatus.PENDING}


@router.get("/status/{job_id}")
async def get_analysis_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id":  job_id,
        "status":  job["status"],
        "result":  job.get("result"),
        "error":   job.get("error"),
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
