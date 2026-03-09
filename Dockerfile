FROM python:3.11-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync.py fit.py ./

RUN adduser -D -u 1000 appuser && chown appuser /data 2>/dev/null || true

VOLUME ["/data"]

USER appuser

CMD ["python", "-u", "sync.py"]
