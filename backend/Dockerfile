FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libass9 fonts-dejavu-core ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /opt/vosk \
    && curl -L --fail --retry 5 --retry-all-errors \
       "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip" \
       -o /tmp/vosk-model.zip \
    && unzip -q /tmp/vosk-model.zip -d /opt/vosk \
    && test -d /opt/vosk/vosk-model-small-pt-0.3 \
    && rm /tmp/vosk-model.zip

COPY app ./app

ENV PORT=8000
ENV VOSK_MODEL_PATH=/opt/vosk/vosk-model-small-pt-0.3
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
