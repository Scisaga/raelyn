FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY bin /app/bin
COPY scripts /app/scripts

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.txt

# Dev builds are expected to run on Linux/WSL and provide project-local external binaries under ./bin.
# Fail fast (and ensure the binaries are actually runnable in this image).
RUN set -euo pipefail; \
    for f in ffmpeg ffprobe node; do \
      if [ ! -x "/app/bin/$f" ]; then \
        echo "[docker] missing /app/bin/$f" >&2; \
        echo "[docker] fix (host): run scripts/dev/download-ffmpeg.sh, download-node.sh" >&2; \
        exit 1; \
      fi; \
    done; \
    /app/bin/ffmpeg -version | head -n 1; \
    /app/bin/node --version; \
    python -c "import yt_dlp; print(yt_dlp.version.__version__)"

COPY backend /app/backend
COPY static /app/static
COPY ui /app/ui

ENV PYTHONPATH=/app/backend

EXPOSE 8000
