FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WHISPER_MODEL=base \
    OUTPUT_DIR=/data/output

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements_web.txt .
RUN pip install -r requirements_web.txt
COPY . .
RUN mkdir -p /data/output

EXPOSE 10000
CMD ["python", "web_app.py"]
