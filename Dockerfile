# Receipts API with Tesseract OCR for bet-slip screenshots. Used by render.yaml (free tier).
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receipts receipts
COPY contracts contracts
ENV PORT=8000 RECEIPTS_DB=/tmp/receipts.db
# The free disk is wiped on restart, so reseed the labelled demo analysts from live Gemini data on boot.
CMD python -m receipts.seed_demo; uvicorn receipts.web:app --host 0.0.0.0 --port $PORT
