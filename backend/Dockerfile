# ---------- Backend Dockerfile ----------
# Multi-stage build for Deployable FastAPI backend
# Entrypoint: alembic upgrade → seed → uvicorn

FROM python:3.12-slim AS base

# WeasyPrint system dependencies (cairo, pango, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    libglib2.0-0 \
    shared-mime-info \
    curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Env defaults (overridden by docker-compose / deploy env)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=postgresql://deployable:deployable@postgres:5432/deployable \
    REDIS_URL=redis://redis:6379/0

EXPOSE 8000

# Entrypoint script: migrate → seed → serve
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
