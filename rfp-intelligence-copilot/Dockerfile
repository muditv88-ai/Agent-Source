# Hugging Face Spaces — FastAPI Docker deployment
# HF Spaces runs as a non-root user on port 7860.
# DATA_DIR is set to /data which is used by project_store and db_core
# for all persistent data. On HF Spaces free tier the filesystem is
# ephemeral — use STORAGE_BACKEND=hf + HF_REPO_ID to persist to a
# private HF Dataset repo, or set DATABASE_URL to an external Postgres.

FROM python:3.11-slim

# System deps for pdfplumber / psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Persistent data directory — project_store and db_core both resolve to DATA_DIR.
# On HF Spaces free tier this is still ephemeral unless STORAGE_BACKEND=hf is set.
# On a paid persistent Space, bind-mount a volume here.
ENV DATA_DIR=/data
RUN mkdir -p /data

# HF Spaces requires port 7860
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
