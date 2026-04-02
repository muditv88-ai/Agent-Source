# Hugging Face Spaces — FastAPI Docker deployment
# The actual application lives in rfp-intelligence-copilot/ subdirectory.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy deps first for layer caching
COPY rfp-intelligence-copilot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full application
COPY rfp-intelligence-copilot/ .

ENV DATA_DIR=/data
RUN mkdir -p /data

# HF Spaces requires port 7860
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
