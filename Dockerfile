FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY populate_all.py .
COPY tests/ ./tests/

# Prepare persistent archive directories
RUN mkdir -p /app/archive/posters /app/archive/raw /app/archive/backdrops

# Expose web interface port
EXPOSE 8088

# Persistent volume for SQLite database, posters, and raw snapshots
VOLUME ["/app/archive"]

# Healthcheck to ensure web server is responsive
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/archive/status')" || exit 1

# Start DVDRewind web server bound to all interfaces
CMD ["python", "-m", "src.cli", "serve", "--host", "0.0.0.0", "--port", "8088"]
