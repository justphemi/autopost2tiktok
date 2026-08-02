#!/bin/bash

echo "🚀 Starting boltreels API locally..."

# Activate virtual environment
source venv/bin/activate

# Start FastAPI (direct-post only — no Redis/Celery worker needed)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
