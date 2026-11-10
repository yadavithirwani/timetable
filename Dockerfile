# Timetable Studio - one container: FastAPI API + static web app.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TIMETABLE_DATA_DIR=/data \
    PORT=8000

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend
COPY config/period_quotas.json config/rules.json config/

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Uploaded schools and generated timetables live here - mount a persistent volume.
# The entrypoint fixes the volume's ownership and then runs the server as the "app" user.
RUN mkdir -p /data && useradd --create-home app && chown -R app /data /app \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/v2/health')"

# One worker process: solve jobs are tracked in memory and CP-SAT already uses every core.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
