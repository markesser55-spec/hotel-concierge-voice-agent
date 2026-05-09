# ==========================================
# STAGE 1: The Builder Room
# ==========================================
FROM python:3.12-slim AS builder

# 1. Install the heavy compilers needed for pipecat-ai
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 2. Create a virtual environment
RUN python -m venv /opt/venv

# 3. Make sure we use the virtual environment for pip installs
ENV PATH="/opt/venv/bin:$PATH"

# 4. Install requirements into the virtual environment
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ==========================================
# STAGE 2: The Clean Room (Final Image)
# ==========================================
FROM python:3.12-slim

# 1. Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    PATH="/opt/venv/bin:$PATH" 
    # ^ We add the venv to the path here too!

WORKDIR /app

# 2. Install ONLY runtime dependencies (ffmpeg)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 3. Copy the compiled packages from Stage 1
COPY --from=builder /opt/venv /opt/venv

# 4. Copy your application code
COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-graceful-shutdown 10"]