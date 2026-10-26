FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY audio/voice01_clone_reference.wav ./audio/voice01_clone_reference.wav

ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
