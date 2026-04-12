FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector.py .

ENV OLLAMA_URL="http://localhost:11434/api/embeddings"
ENV QDRANT_HOST="localhost"
ENV SLEEP_INTERVAL=3600

CMD ["python", "collector.py"]
