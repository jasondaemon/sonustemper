FROM python:3.11-slim

ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    passwd \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user/group
RUN groupadd -g ${APP_GID} app && \
    useradd -m -u ${APP_UID} -g app app

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY sonustemper/ /app/sonustemper/
COPY sonustemper-ui/app/ /app/sonustemper-ui/app/
COPY assets/ /app/assets/

ENV DATA_DIR=/data \
    PRESET_DIR=/data/presets/user \
    GEN_PRESET_DIR=/data/presets/generated \
    ASSET_PRESET_DIR=/app/assets/presets

# Prepare writable dirs owned by non-root
RUN mkdir -p \
    /data/presets/user /data/presets/generated /data/presets/builtin \
    /data/library/songs \
    /data/previews && \
    chown -R app:app /data /app

USER app

EXPOSE 8383

CMD ["uvicorn", "sonustemper.server:app", "--host", "0.0.0.0", "--port", "8383", "--access-log", "--log-level", "info"]
