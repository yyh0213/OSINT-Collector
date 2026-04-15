FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV OLLAMA_URL="http://localhost:11434/api/embeddings"
ENV QDRANT_HOST="localhost"
ENV SLEEP_INTERVAL=3600

<<<<<<< HEAD
RUN chmod +x start.sh && sed -i 's/\r$//' start.sh

# reliability_viewer.py HTTP API 포트
EXPOSE 5050

CMD ["sh", "start.sh"]
=======
# FastAPI 통폐합으로 start.sh 제거, uvicorn 구동
EXPOSE 5050

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5050"]
>>>>>>> 6c55772018cf6d125ee77a01498d46614a967708
