FROM debian:bookworm-slim AS whisper-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake build-essential git curl coreutils \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git /opt/whisper.cpp \
    && cmake -S /opt/whisper.cpp -B /opt/whisper.cpp/build \
       -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
    && cmake --build /opt/whisper.cpp/build --config Release -j2 \
    && cmake --install /opt/whisper.cpp/build --prefix /opt/whisper-install \
    && mkdir -p /opt/whisper/models \
    && curl -L --fail --retry 5 --retry-all-errors \
       "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-q5_1.bin?download=true" \
       -o /opt/whisper/models/ggml-tiny-q5_1.bin \
    && echo "2827a03e495b1ed3048ef28a6a4620537db4ee51  /opt/whisper/models/ggml-tiny-q5_1.bin" | sha1sum -c -

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libstdc++6 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=whisper-builder /opt/whisper-install /opt/whisper-install
COPY --from=whisper-builder /opt/whisper/models /opt/whisper/models
RUN find /opt/whisper-install -type f -name 'lib*.so*' -exec cp -f {} /usr/local/lib/ + && ldconfig && test -x /opt/whisper-install/bin/whisper-cli && test -s /opt/whisper/models/ggml-tiny-q5_1.bin && ldd /opt/whisper-install/bin/whisper-cli | tee /tmp/whisper-ldd && ! grep -q 'not found' /tmp/whisper-ldd

COPY app ./app

ENV PORT=8000
ENV WHISPER_BINARY=/opt/whisper-install/bin/whisper-cli
ENV WHISPER_MODEL=/opt/whisper/models/ggml-tiny-q5_1.bin
ENV LD_LIBRARY_PATH=/usr/local/lib
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
