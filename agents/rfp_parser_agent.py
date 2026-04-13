"""
rfp_parser_agent.py
Parses raw RFP documents (PDF/DOCX text) and extracts structured fields:
  - title, buyer, deadline, categories, evaluation_criteria, attachments
Uses NVIDIA NIM (Nemotron-253B) for reasoning-heavy extraction.
"""

import os
import json
import logging
from typing import Any
from openai import AsyncOpenAI   # NIM is OpenAI-compatible

logger = logging.getLogger(__name__)

NIM_BASE_URL    = os.getenv("NIM_BASE_URL",    "https://integrate.api.nvidia.com/v1")
NIM_API_KEY     = os.getenv("NIM_API_KEY",     "")
REASONING_MODEL = os.getenv("REASONING_MODEL", "nvidia/llama-3.1-nemotron-ultra-253b-v1")

SYSTEM_PROMPT = """
You are an RFP analysis expert. Given raw RFP text, extract and return a
JSON object with these fields (use null if not found):
{
  "title":               string,
  "buyer":               string,
  "deadline":            string (ISO-8601 date or null),
  "budget":              string (value + currency, or null),
  "categories":          [string],
  "evaluation_criteria": [{"criterion": string, "weight": number|null}],
  "submission_format":   string,
  "contact_email":       string|null,
  "attachments_required":[string]
}
Return ONLY valid JSON — no markdown, no commentary.
""".strip()


class RFPParserAgent:
    def __init__(self, context: dict[str, Any] = {}):
        self.context = context
        self.client  = AsyncOpenAI(
            base_url=NIM_BASE_URL,
            api_key=NIM_API_KEY,
        )

    async def run(self, payload: dict) -> dict:
        """
        payload expects:
          {
            "text":     str,   # raw extracted text from the RFP document
            "filename": str    # original file name (for logging)
          }
        """
        raw_text = payload.get("text", "")
        filename = payload.get("filename", "unknown")

        if not raw_text:
            logger.warning(f"RFPParserAgent: empty text for {filename}")
            return {"error": "No text provided", "filename": filename}

        logger.info(f"RFPParserAgent: parsing {filename} ({len(raw_text)} chars)")

        try:
            response = await self.client.chat.completions.create(
                model=REASONING_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": raw_text[:12_000]},  # token guard
                ],
                temperature=0.1,
                max_tokens=1024,
            )
            raw_json = response.choices[0].message.content.strip()
            parsed   = json.loads(raw_json)
            parsed["filename"] = filename
            logger.info(f"RFPParserAgent: successfully parsed {filename}")
            return parsed

        except Exception as exc:
            logger.error(f"RFPParserAgent error for {filename}: {exc}")
            return {"error": str(exc), "filename": filename}
