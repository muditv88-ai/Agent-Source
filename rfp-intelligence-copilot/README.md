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

Set these as **Secrets** in the HF Space settings (`Settings → Repository secrets`):

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | OpenAI API key for all LLM agents |
| `SECRET_KEY` | ✅ | JWT signing secret (any random 32-char string) |
| `STORAGE_BACKEND` | ✅ for persistence | Set to `hf` to persist data to a HF Dataset repo |
| `HF_TOKEN` | ✅ if `STORAGE_BACKEND=hf` | HF token with **write** access — generate at https://huggingface.co/settings/tokens |
| `HF_REPO_ID` | ✅ if `STORAGE_BACKEND=hf` | Private Dataset repo ID, e.g. `myorg/sourceiq-data`. Create it first at https://huggingface.co/new-dataset |
| `DATABASE_URL` | optional | PostgreSQL URL (e.g. Neon/Supabase). Defaults to SQLite at `/data/sourceiq.db` |
| `SMTP_HOST` | optional | SMTP server for email sending |
| `SMTP_PORT` | optional | Default: 587 |
| `SMTP_USER` | optional | SMTP username / email address |
| `SMTP_PASSWORD` | optional | SMTP app password |
| `SMTP_FROM` | optional | From address for outbound emails |

## Persistence on HF Spaces

HF Spaces containers are **ephemeral** — the filesystem is wiped on every restart or redeploy. To persist projects, uploaded files, and pricing data:

1. Create a **private** Dataset repo on HF (e.g. `myorg/sourceiq-data`)
2. Generate an HF token with **write** scope
3. In the Space settings → **Repository secrets**, add:
   - `STORAGE_BACKEND` = `hf`
   - `HF_TOKEN` = your token
   - `HF_REPO_ID` = `myorg/sourceiq-data`

All project files, supplier responses, pricing data, templates, and metadata will be automatically synced to the Dataset repo on every write and restored from it on first read after a cold start.

For the SQLite database (supplier records, scenarios, auth), either:
- Leave `DATABASE_URL` unset — SQLite is used but will reset on restart (acceptable for demo)
- Set `DATABASE_URL` to a free [Neon](https://neon.tech) or [Supabase](https://supabase.com) Postgres URL for full persistence

## Local Development

```bash
cd rfp-intelligence-copilot
cp .env.example .env          # fill in your values
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
