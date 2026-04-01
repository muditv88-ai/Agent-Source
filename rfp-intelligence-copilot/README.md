---
title: SourceIQ Backend
emoji: 📄
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# SourceIQ — RFP Intelligence Backend

FastAPI backend for the SourceIQ procurement intelligence platform.

## Endpoints

| Group | Base path |
|---|---|
| Auth | `/api/auth` |
| Projects | `/api/projects` |
| RFP | `/api/rfp` |
| Technical Analysis | `/api/technical-analysis` |
| Pricing Analysis | `/api/pricing-analysis` |
| Scenarios | `/api/scenarios` |
| Communications | `/api/communications` |
| Suppliers | `/api/suppliers` |
| Drawings | `/api/drawings` |
| Chat / Copilot | `/api/chat` |

Interactive docs available at `/docs` (Swagger UI) and `/redoc`.

## Environment Variables

Set these as **Secrets** in the HF Space settings:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | OpenAI API key for all LLM agents |
| `SECRET_KEY` | ✅ | JWT signing secret (any random 32-char string) |
| `SMTP_HOST` | optional | SMTP server for email sending |
| `SMTP_PORT` | optional | Default: 587 |
| `SMTP_USER` | optional | SMTP username / email address |
| `SMTP_PASSWORD` | optional | SMTP app password |
| `SMTP_FROM` | optional | From address for outbound emails |
| `DATABASE_URL` | optional | PostgreSQL URL (defaults to SQLite if unset) |

## Local Development

```bash
cd rfp-intelligence-copilot
cp .env.example .env          # fill in your values
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
