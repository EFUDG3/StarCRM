# Single-service build for Cloud Run: builds the React frontend, then serves it
# from the FastAPI backend so the API and the site share one URL.
#
# Build/deploy from the PROJECT ROOT (this directory), e.g.:
#   gcloud run deploy star-crm --source . --region us-west1
#
# (The backend-only Dockerfile in backend/ is kept for API-only deploys.)

# --- Stage 1: build the frontend ---
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build          # outputs /fe/dist

# --- Stage 2: backend runtime that also serves the built frontend ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
# Bring in the built site; main.py serves ./static when it exists.
COPY --from=frontend /fe/dist ./static

# Cloud Run injects $PORT (default 8080). Bind to it.
ENV PORT=8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
