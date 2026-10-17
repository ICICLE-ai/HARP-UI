FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HARP_BEHIND_PROXY=1 \
    HARP_HOST=0.0.0.0 \
    HARP_PORT=8000 \
    HARP_PROXY_IPS=* \
    HARP_SERVER_DATA=/var/lib/harp

WORKDIR /opt/harp

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY harp_server/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# The server serves the existing page and can optionally run the build pipeline
# locally. Production deployments should normally use the separate TAPIS build
# app so model training does not consume the web pod's resources.
COPY profiling.html /opt/harp/profiling.html
COPY harp_server /opt/harp/harp_server
COPY job_runner /opt/harp/job_runner
COPY pipeline /opt/harp/pipeline

RUN mkdir -p /var/lib/harp

WORKDIR /opt/harp/harp_server
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["python", "-m", "harp_server.serve"]
