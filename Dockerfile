# 1. Use the official, lightweight Python 3.12 image
FROM python:3.12-slim

# 2. Set environment variables to optimize Python for the Cloud
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

# 3. Create a working directory inside the container
WORKDIR /app

# 4. Install system-level audio dependencies
# ffmpeg is essential for handling streaming audio bytes in Linux!
RUN apt-get update && apt-get install -y \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 5. Copy your strictly pruned requirements file
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy all your beautiful Python code into the container
COPY . .

# 7. Expose the port (Cloud Run uses this for documentation purposes)
EXPOSE 8000

# 8. Start the server (Dynamically passing the Cloud Run PORT, fallback to 8000)
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-graceful-shutdown 10"]