FROM python:3.14-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Package metadata first (better layer caching)
COPY pyproject.toml README.md ./
COPY zolai/ zolai/

# Base + ML extras with CPU-only torch (no NVIDIA in containers by default).
# For GPU images, build with:
#   RUN pip install --no-cache-dir -e ".[gpu]" --extra-index-url https://download.pytorch.org/whl/cu130
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    -e ".[ml]"

# Copy project (data/ is gitignored — mount at runtime)
COPY scripts/ scripts/
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
