FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY physarum ./physarum
COPY static ./static
COPY data ./data
COPY scripts ./scripts
COPY app.py eval.py mcp_server.py ./
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
