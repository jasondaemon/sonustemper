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

COPY mastering-ui/app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mastering-ui/app/app.py /app/app.py
COPY mastering /app/mastering

ENV DATA_DIR=/data \
    IN_DIR=/data/in \
    OUT_DIR=/data/out \
    PRESET_DIR=/presets

# Prepare writable dirs owned by non-root
RUN mkdir -p /data/in /data/out /presets && \
    chown -R app:app /data /presets /app

USER app

EXPOSE 8383

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8383", "--access-log", "--log-level", "info"]
