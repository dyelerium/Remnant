FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download sentence-transformers model (cached in image layer)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application code
COPY . .

# Create required directories
RUN mkdir -p memory/projects logs workspace /tmp/remnant

# Non-root user — docker group GID 107 matches host socket ownership
RUN useradd -m -u 1000 remnant \
    && groupadd -g 107 docker \
    && usermod -aG docker remnant \
    && chown -R remnant:remnant /app
USER remnant

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
