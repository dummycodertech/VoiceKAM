FROM python:3.12-slim

LABEL org.opencontainers.image.title="voicekam" \
      org.opencontainers.image.description="VoiceKAM (metis) standalone voice-first AI Key Account Manager"

WORKDIR /app

# Install only runtime dependencies (keeps image small for 512 MB RAM free tier)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend

ENV PYTHONUNBUFFERED=1 \
    PORT=7860 \
    ALLOW_MOCK_LLM=false

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 7860)}/api/health', timeout=3)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
