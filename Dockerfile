FROM node:22-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_PATH=/usr/local/lib/node_modules

# --- системные зависимости -------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        ffmpeg \
        ca-certificates curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# --- python-зависимости -----------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt \
    && npm install -g hyperframes@0.8.181

COPY . .

VOLUME ["/app/data"]
ENV DATA_DIR=/app/data \
    RENDER_MODE=local

CMD ["python3", "-m", "app"]
