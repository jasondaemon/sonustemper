FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY mastering-ui/app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mastering-ui/app/app.py /app/app.py
COPY mastering /app/mastering

ENV DATA_DIR=/data \
    IN_DIR=/data/in \
    OUT_DIR=/data/out \
    PRESET_DIR=/presets

EXPOSE 8383

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8383", "--access-log", "--log-level", "info"]
