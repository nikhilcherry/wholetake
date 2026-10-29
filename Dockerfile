FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system wholetake && useradd --system --gid wholetake --create-home wholetake

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY roomstamp/ /app/roomstamp/
COPY wholetake/ /app/wholetake/
COPY verdict/ /app/verdict/
COPY simx/ /app/simx/
COPY ingest/ /app/ingest/
COPY anchor/ /app/anchor/
COPY bot/ /app/bot/
COPY CONTRACTS.md /app/CONTRACTS.md
COPY py /app/py

RUN mkdir -p /app/data /app/logs /app/keys && chown -R wholetake:wholetake /app

USER wholetake

VOLUME ["/app/data", "/app/logs", "/app/keys"]
EXPOSE 8790 8791

ENV PYTHONUNBUFFERED=1 \
    WT_VERDICT_PORT=8790 \
    WHOLETAKE_ASR=none

CMD ["python", "-m", "verdict", "serve", "--host", "0.0.0.0", "--port", "8790"]
