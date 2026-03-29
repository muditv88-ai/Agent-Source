def draft_clarification_email(supplier_id: str, questions: list[str]) -> dict:
    subject = f"Clarification requested for supplier {supplier_id}"
    body = "Dear Supplier,\n\nPlease clarify the following items:\n" + "\n".join([f"- {q}" for q in questions]) + "\n\nRegards,\nProcurement Team"
    return {"subject": subject, "body": body}