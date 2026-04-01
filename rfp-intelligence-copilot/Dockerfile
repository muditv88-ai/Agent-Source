# Hugging Face Spaces — FastAPI Docker deployment
# HF Spaces runs as a non-root user on port 7860 by default.
# We map that to uvicorn so HF can health-check it.

FROM python:3.11-slim

# Install system deps needed by pdfplumber / psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create uploads directory (HF ephemeral storage)
RUN mkdir -p uploads/rfps uploads/suppliers uploads/drawings

# HF Spaces requires the app to listen on port 7860
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
