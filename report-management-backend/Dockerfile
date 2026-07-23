FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies from backend subdirectory
COPY report-management-backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and chromium dependencies
RUN playwright install --with-deps chromium

# Copy full repository (gen_rpt modules + backend code)
COPY . .

# Set working directory to the backend package
WORKDIR /app/report-management-backend

# Expose port
EXPOSE 8000

# Start command (runs migrations then starts API)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
