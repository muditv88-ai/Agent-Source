"""
rfp_generation_agent.py

Handles FM-2.1 / FM-2.2 / FM-2.3 / FM-2.4:
  - AI generation of RFP from category + scope
  - Upload & parse of existing RFP document
  - Attachment of technical drawings (individual or ZIP) to line items
    Drawings are stored with metadata; a download bundle can be served to suppliers.

All heavy LLM calls use NVIDIA Nemotron (same model as rfp_extractor.py).
Existing rfp_extractor.parse_rfp_questions() is reused unchanged.
"""
import os
import io
import re
import json
import zipfile
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.agents.base_agent import BaseAgent, Tool
from app.services.rfp_extractor import extract_rfp_questions

logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

GENERATION_PROMPT = """
You are a senior procurement specialist. Generate a professional, complete RFP document.

Inputs:
- Category: {category}
- Scope: {scope}
- Special Requirements: {requirements}

Output a structured RFP JSON with these top-level keys:
  overview        : string  (project overview paragraph)
  scope_of_work   : string  (detailed scope)
  technical_reqs  : list of {{id, requirement, weight_pct}}
  commercial_reqs : list of {{field, description, required}}
  compliance_reqs : list of string
  submission_instructions: string
  evaluation_summary: string

Return ONLY valid JSON, no markdown fences.
"""


def _parse_json(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_text": raw}


# In-memory drawing store (replace with DB / S3 metadata in production)
_drawing_store: Dict[str, List[dict]] = {}


class RFPGenerationAgent(BaseAgent):
    """
    Two modes:
      - 'generate' : create an RFP from category + scope via LLM
      - 'upload'   : parse an already-uploaded RFP document

    Both modes produce the same output schema, making downstream
    processing (scoring, pricing) mode-agnostic.

    Drawing management (FM-2.3):
      - attach_drawing()  : register a single drawing file against a line item
      - attach_zip()      : bulk-register drawings from a ZIP archive
      - get_drawings()    : return all drawings for a project (supplier download bundle)
    """

    def __init__(self):
        tools = [
            Tool(
                name="generate_rfp_text",
                description="Generate full RFP JSON from category, scope and special requirements",
                fn=self._generate_rfp_text,
                schema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "scope": {"type": "string"},
                        "requirements": {"type": "string"},
                    },
                    "required": ["category", "scope"],
                },
            ),
            Tool(
                name="parse_uploaded_rfp",
                description="Parse an uploaded RFP document text into structured questions",
                fn=self._parse_uploaded,
                schema={
                    "type": "object",
                    "properties": {"file_text": {"type": "string"}},
                    "required": ["file_text"],
                },
            ),
            Tool(
                name="attach_drawing",
                description="Register a single technical drawing against a line item",
                fn=self._attach_drawing,
                schema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "line_item_id": {"type": "string"},
                        "filename": {"type": "string"},
                        "file_bytes": {"type": "string", "description": "base64-encoded file content"},
                        "part_number": {"type": "string"},
                        "revision": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["project_id", "line_item_id", "filename"],
                },
            ),
            Tool(
                name="attach_zip",
                description="Bulk-register drawings from a ZIP archive; each file becomes a drawing entry",
                fn=self._attach_zip,
                schema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "line_item_id": {"type": "string"},
                        "zip_bytes": {"type": "string", "description": "base64-encoded ZIP content"},
                    },
                    "required": ["project_id", "line_item_id", "zip_bytes"],
                },
            ),
            Tool(
                name="get_drawings",
                description="Return all drawings attached to a project for supplier download",
                fn=self._get_drawings,
                schema={
                    "type": "object",
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"],
                },
            ),
        ]
        super().__init__(tools)

    # ── Tool implementations ──────────────────────────────────────────────

    def _generate_rfp_text(self, category: str, scope: str,
                            requirements: str = "") -> dict:
        prompt = GENERATION_PROMPT.format(
            category=category, scope=scope, requirements=requirements
        )
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content or ""
        parsed = _parse_json(raw)
        return {"rfp_structure": parsed, "source": "generated", "category": category}

    def _parse_uploaded(self, file_text: str) -> dict:
        """Reuses existing rfp_extractor service — zero changes to that file."""
        return extract_rfp_questions(file_text)

    def _attach_drawing(
        self,
        project_id: str,
        line_item_id: str,
        filename: str,
        file_bytes: Optional[str] = None,
        part_number: str = "",
        revision: str = "A",
        description: str = "",
    ) -> dict:
        """
        Register a single drawing.
        In production replace the in-memory store with S3 upload + DB record.
        file_bytes is base64-encoded content when provided by the API route.
        """
        drawing_id = hashlib.md5(
            f"{project_id}{line_item_id}{filename}{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:12]

        entry = {
            "drawing_id": drawing_id,
            "project_id": project_id,
            "line_item_id": line_item_id,
            "filename": filename,
            "part_number": part_number,
            "revision": revision,
            "description": description,
            "uploaded_at": datetime.utcnow().isoformat(),
            # In production: s3_url would replace file_bytes here
            "download_ready": True,
        }

        key = f"{project_id}:{line_item_id}"
        _drawing_store.setdefault(key, []).append(entry)
        logger.info("Drawing attached: %s → %s", project_id, filename)
        return entry

    def _attach_zip(
        self, project_id: str, line_item_id: str, zip_bytes: str
    ) -> dict:
        """
        Extract a ZIP archive and register every contained file as a drawing.
        zip_bytes: base64-encoded ZIP binary.
        """
        import base64

        raw_zip = base64.b64decode(zip_bytes)
        attached = []
        errors = []

        try:
            with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
                for name in zf.namelist():
                    # Skip directories and hidden files
                    if name.endswith("/") or name.startswith("__"):
                        continue
                    try:
                        file_data = zf.read(name)
                        entry = self._attach_drawing(
                            project_id=project_id,
                            line_item_id=line_item_id,
                            filename=os.path.basename(name),
                            part_number=os.path.splitext(os.path.basename(name))[0],
                        )
                        attached.append(entry)
                    except Exception as e:
                        errors.append({"file": name, "error": str(e)})
        except zipfile.BadZipFile as e:
            return {"error": f"Invalid ZIP file: {e}", "attached": []}

        return {
            "attached_count": len(attached),
            "drawings": attached,
            "errors": errors,
        }

    def _get_drawings(self, project_id: str) -> dict:
        """Return all drawings for a project, grouped by line_item_id."""
        result: Dict[str, List[dict]] = {}
        for key, drawings in _drawing_store.items():
            pid, lid = key.split(":", 1)
            if pid == project_id:
                result.setdefault(lid, []).extend(drawings)
        total = sum(len(v) for v in result.values())
        return {
            "project_id": project_id,
            "total_drawings": total,
            "by_line_item": result,
        }

    # ── Agent entry point ─────────────────────────────────────────────────

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        mode = input.get("mode", "generate")

        if mode == "generate":
            generated = self._generate_rfp_text(
                category=input.get("category", ""),
                scope=input.get("scope", ""),
                requirements=input.get("requirements", ""),
            )
            # Auto-extract questions from the generated text overview
            rfp_text = json.dumps(generated.get("rfp_structure", {}))
            questions = self._parse_uploaded(rfp_text)
            return {**generated, "questions": questions}

        elif mode == "upload":
            return self._parse_uploaded(input.get("file_text", ""))

        elif mode == "attach_drawing":
            return self._attach_drawing(**{k: v for k, v in input.items() if k != "mode"})

        elif mode == "attach_zip":
            return self._attach_zip(**{k: v for k, v in input.items() if k != "mode"})

        elif mode == "get_drawings":
            return self._get_drawings(project_id=input["project_id"])

        return {"error": f"Unknown mode: {mode}"}
