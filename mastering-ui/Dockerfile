FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/app.py /app/app.py

# Copy host Docker CLI into the image
COPY docker-cli/docker /usr/bin/docker
RUN chmod +x /usr/bin/docker

EXPOSE 8383
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8383", "--access-log", "--log-level", "info"]
