import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from app.models.schemas import AnalysisRequest, AnalysisResponse, SupplierResult, CategoryScore, QuestionScore
from app.services.document_parser import parse_document
from app.services.supplier_parser import extract_supplier_answers
from app.services.ai_scorer import score_question, generate_supplier_summary
from app.services.aggregator import aggregate_scores, compute_overall_score

router = APIRouter()

UPLOAD_DIR = Path("uploads")
META_DIR = Path("metadata")


@router.post("/run", response_model=AnalysisResponse)
def run_analysis(req: AnalysisRequest):
    """Run full agentic analysis across all suppliers for a given RFP"""

    # 1. Load extracted RFP questions
    meta_path = META_DIR / f"{req.rfp_id}_questions.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="RFP not parsed yet. Call /rfp/{rfp_id}/parse first.")

    questions = json.loads(meta_path.read_text())

    # 2. Find all supplier files for this RFP
    supplier_files = list(UPLOAD_DIR.glob(f"{req.rfp_id}_supplier_*"))
    if not supplier_files:
        raise HTTPException(status_code=404, detail="No supplier responses uploaded for this RFP.")

    # 3. Parse each supplier document and extract answers
    all_suppliers_raw: dict = {}  # supplier_name -> {question_id -> answer}
    supplier_file_map: dict = {}  # supplier_name -> file path

    for sf in supplier_files:
        parsed = parse_document(str(sf))
        supplier_data = extract_supplier_answers(parsed["full_text"], questions)
        name = supplier_data.get("supplier_name", sf.stem)
        all_suppliers_raw[name] = supplier_data.get("answers", {})
        supplier_file_map[name] = str(sf)

    # 4. For quantitative questions, gather all answers for relative scoring context
    quant_context: dict = {}  # question_id -> {supplier_name -> answer}
    for qid_obj in questions:
        if qid_obj["question_type"] == "quantitative":
            qid = qid_obj["question_id"]
            quant_context[qid] = {
                s_name: answers.get(qid, "No response")
                for s_name, answers in all_suppliers_raw.items()
            }

    # 5. Score each supplier at question level
    supplier_results = []
    for supplier_name, answers in all_suppliers_raw.items():
        question_scores = {}
        for q in questions:
            qid = q["question_id"]
            answer = answers.get(qid, "No response provided")
            context = quant_context.get(qid) if q["question_type"] == "quantitative" else None
            scored = score_question(q, answer, context)
            question_scores[qid] = scored

        # 6. Aggregate to category level
        category_results = aggregate_scores(questions, question_scores, answers, supplier_name)
        overall = compute_overall_score(category_results, questions)

        # 7. Generate AI summary
        summary = generate_supplier_summary(supplier_name, category_results, overall)

        supplier_results.append({
            "supplier_name": supplier_name,
            "overall_score": overall,
            "category_results": category_results,
            "strengths": summary.get("strengths", []),
            "weaknesses": summary.get("weaknesses", []),
            "recommendation": summary.get("recommendation", ""),
        })

    # 8. Rank suppliers
    supplier_results.sort(key=lambda x: x["overall_score"], reverse=True)

    formatted_suppliers = []
    for rank, s in enumerate(supplier_results, 1):
        cat_scores = [
            CategoryScore(
                category=c["category"],
                weighted_score=c["weighted_score"],
                question_count=c["question_count"],
                questions=[
                    QuestionScore(**q) for q in c["questions"]
                ],
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
    top_rec = f"{top.supplier_name} is the top-ranked supplier with a score of {top.overall_score}/10." if top else "No suppliers evaluated."

    return AnalysisResponse(
        rfp_id=req.rfp_id,
        status="completed",
        suppliers=formatted_suppliers,
        top_recommendation=top_rec,
        analysis_summary=f"Evaluated {len(formatted_suppliers)} supplier(s) across {len(set(q['category'] for q in questions))} categories and {len(questions)} questions.",
    )
