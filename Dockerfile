FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps (CPU-only torch to keep image small)
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Install package
COPY pyproject.toml .
COPY zolai/ zolai/
RUN pip install --no-cache-dir -e .

# Copy project (data/ is gitignored — mount at runtime)
COPY scripts/ scripts/
COPY wiki/ wiki/
COPY config/ config/
COPY .env.example .env.example

# Copy UI assets for the review web interface
COPY zolai/ui/templates ./zolai/ui/templates
COPY zolai/ui/static ./zolai/ui/static

# data/ is NOT copied — mount as volume
VOLUME ["/app/data"]

EXPOSE 8000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "zolai.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
