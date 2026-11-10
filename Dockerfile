FROM python:3.12-slim

WORKDIR /app

# Install only runtime dependencies (keeps image small for 512 MB RAM free tier)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend

ENV PYTHONUNBUFFERED=1 \
    PORT=7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
