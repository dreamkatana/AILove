FROM python:3.12-slim

WORKDIR /workspace

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

COPY backend/app ./backend/app
COPY frontend ./frontend
COPY PROTOCOL.md ./PROTOCOL.md

WORKDIR /workspace/backend

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
