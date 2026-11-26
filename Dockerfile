FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies
# rich/textual only power the desktop TUI (tui_app.py, cli_monitor.py), not the server.
COPY requirements.txt .
RUN grep -v -E '^(rich|textual)\b' requirements.txt > /tmp/requirements-server.txt \
    && pip install --no-cache-dir -r /tmp/requirements-server.txt

# Copy application code
COPY src/ ./src/
COPY populate_all.py .
# Pre-gzip CSS/JS once; aiohttp serves the .gz copy to browsers that accept it.
RUN gzip -k -9 src/web/static/style.css src/web/static/app.js src/web/static/js/*.js

# Run as an ordinary user (uid 1000, group users), not root. Only the data
# folders are writable; the code stays root-owned and read-only to the app.
# (config.py also creates tests/fixtures at import.)
RUN useradd --uid 1000 --gid 100 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/archive/posters /app/archive/raw /app/archive/backdrops /app/db /app/tests/fixtures \
    && chown -R app:users /app/archive /app/db /app/tests
USER app

# Expose web interface port
EXPOSE 8088

# Persistent volume for SQLite database, posters, and raw snapshots
VOLUME ["/app/archive"]

# Healthcheck: a raw HTTP request from bash to /healthz (no database work, no
# Python start-up every 30 s). Start-up can take a minute after a cold boot.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/8088 && printf "GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n" >&3 && head -n 1 <&3 | grep -q " 200 "' || exit 1

# Start DVDRewind web server bound to all interfaces
CMD ["python", "-m", "src.cli", "serve", "--host", "0.0.0.0", "--port", "8088"]
