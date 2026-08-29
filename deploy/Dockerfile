# raghealth-server — findings dashboard + alerts
# Build from repo root:  docker build -f deploy/Dockerfile -t raghealth-server .
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY raghealth ./raghealth
COPY raghealth_server ./raghealth_server
RUN pip install --no-cache-dir '.[server]'
ENV RAGHEALTH_DB=/data/raghealth_server.db
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "raghealth_server", "run", "--host", "0.0.0.0", "--port", "8080"]
