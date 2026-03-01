FROM python:3.11-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync.py fit.py ./

VOLUME ["/data"]

CMD ["python", "-u", "sync.py"]
