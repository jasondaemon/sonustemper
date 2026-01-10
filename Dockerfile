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

ENV DATA_DIR=/data \
    MASTER_IN_DIR=/data/mastering/in \
    MASTER_OUT_DIR=/data/mastering/out \
    MASTER_TMP_DIR=/data/mastering/tmp \
    TAG_IN_DIR=/data/tagging/in \
    TAG_TMP_DIR=/data/tagging/tmp \
    PRESET_DIR=/data/presets/user \
    GEN_PRESET_DIR=/data/presets/generated \
    ANALYSIS_IN_DIR=/data/analysis/in \
    ANALYSIS_OUT_DIR=/data/analysis/out \
    ANALYSIS_TMP_DIR=/data/analysis/tmp

# Prepare writable dirs owned by non-root
RUN mkdir -p \
    /data/mastering/in /data/mastering/out /data/mastering/tmp \
    /data/tagging/in /data/tagging/tmp \
    /data/presets/user /data/presets/generated \
    /data/analysis/in /data/analysis/out /data/analysis/tmp \
    /data/previews && \
    chown -R app:app /data /app

USER app

EXPOSE 8383

CMD ["uvicorn", "sonustemper.server:app", "--host", "0.0.0.0", "--port", "8383", "--access-log", "--log-level", "info"]
