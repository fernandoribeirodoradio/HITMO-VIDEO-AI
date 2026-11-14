import io
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="HITMO Voice Video Editor API",
    version="0.2.0",
    description="Transcrição gratuita de vídeos com Whisper.cpp, legendas SRT e detecção inicial de comandos falados."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(200 * 1024 * 1024)))
API_TOKEN = os.getenv("HITMO_API_TOKEN", "")
WHISPER_BINARY = os.getenv("WHISPER_BINARY", "/usr/local/bin/whisper-cli")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "/opt/whisper/models/ggml-tiny-q5_1.bin")
WHISPER_THREADS = os.getenv("WHISPER_THREADS", "2")
bearer_scheme = HTTPBearer(auto_error=False)


def require_api_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="HITMO_API_TOKEN não configurado no servidor.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Token Bearer obrigatório.")
    if not secrets.compare_digest(credentials.credentials, API_TOKEN):
        raise HTTPException(status_code=403, detail="Token inválido.")
    return True


TRANSCRIPTION_MODEL = "whisper.cpp/tiny-q5_1"

COMMANDS = [
    {
        "phrases": ["a bruxa está à solta", "a bruxa ta solta", "bruxa está à solta"],
        "effect": "witch_flying",
        "description": "Inserir uma bruxa voando rapidamente pela tela.",
    },
    {
        "phrases": ["meu jesus", "meu senhor", "jesus amado"],
        "effect": "jesus_reaction",
        "description": "Sugerir uma aparição visual temática de Jesus com luz suave.",
    },
]


def srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def normalize(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def detect_commands(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for segment in segments:
        spoken = normalize(segment.get("text", ""))
        for command in COMMANDS:
            if any(normalize(p) in spoken for p in command["phrases"]):
                found.append({
                    "start": segment.get("start", 0),
                    "end": segment.get("end", 0),
                    "phrase": segment.get("text", ""),
                    "effect": command["effect"],
                    "suggestion": command["description"],
                    "status": "suggested_not_rendered",
                })
    return found


def parse_whisper_json(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    raw_segments = payload.get("transcription")
    if not isinstance(raw_segments, list):
        raise ValueError("O Whisper.cpp não retornou a lista de segmentos esperada.")

    segments = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        offsets = item.get("offsets") or {}
        start_ms = offsets.get("from")
        end_ms = offsets.get("to")
        if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)):
            start, end = float(start_ms) / 1000, float(end_ms) / 1000
        else:
            timestamps = item.get("timestamps") or {}
            def parse_timestamp(value: str) -> float:
                match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", value or "")
                if not match:
                    return 0.0
                h, m, s, ms = map(int, match.groups())
                return h * 3600 + m * 60 + s + ms / 1000
            start = parse_timestamp(timestamps.get("from", ""))
            end = parse_timestamp(timestamps.get("to", ""))
        text = str(item.get("text", "")).strip()
        if text:
            segments.append({"start": max(0.0, start), "end": max(start, end), "text": text})

    full_text = " ".join(segment["text"] for segment in segments).strip()
    return full_text, segments


@app.get("/")
def root():
    return {"name": "HITMO Voice Video Editor API", "status": "online", "engine": TRANSCRIPTION_MODEL, "docs": "/docs"}


@app.get("/health")
def health():
    binary_ok = Path(WHISPER_BINARY).is_file()
    model_ok = Path(WHISPER_MODEL).is_file()
    return {
        "status": "ok" if binary_ok and model_ok else "degraded",
        "transcription_model": TRANSCRIPTION_MODEL,
        "engine_available": binary_ok,
        "model_available": model_ok,
    }


class OpenAIFileRef(BaseModel):
    name: str
    id: str
    mime_type: str
    download_link: str


class GPTFileRequest(BaseModel):
    openaiFileIdRefs: list[OpenAIFileRef]


@app.post("/v1/transcribe-from-gpt")
async def transcribe_from_gpt(
    payload: GPTFileRequest,
    _authorized: bool = Depends(require_api_token),
):
    if not payload.openaiFileIdRefs:
        raise HTTPException(400, "Nenhum vídeo foi anexado à solicitação.")
    if len(payload.openaiFileIdRefs) > 1:
        raise HTTPException(400, "Envie apenas um vídeo por edição.")
    file_ref = payload.openaiFileIdRefs[0]
    parsed = urlparse(file_ref.download_link)
    if parsed.scheme != "https" or parsed.hostname != "files.oaiusercontent.com":
        raise HTTPException(400, "Link de arquivo não permitido.")
    suffix = Path(file_ref.name).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"} or not file_ref.mime_type.startswith("video/"):
        raise HTTPException(400, "O arquivo anexado precisa ser um vídeo MP4, MOV, M4V ou WEBM.")
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
            async with client.stream("GET", file_ref.download_link) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_VIDEO_BYTES:
                        raise HTTPException(413, "Vídeo excede o limite configurado de 200 MB.")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível baixar o vídeo anexado ao ChatGPT. Tente anexá-lo novamente.")
    uploaded = UploadFile(filename=file_ref.name, file=io.BytesIO(content))
    return await transcribe_video(uploaded, _authorized=True)


@app.post("/v1/transcribe")
async def transcribe_video(
    video: UploadFile = File(...),
    _authorized: bool = Depends(require_api_token),
):
    if not video.filename or Path(video.filename).suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise HTTPException(400, "Envie um vídeo MP4, MOV, M4V ou WEBM.")
    if not Path(WHISPER_BINARY).is_file() or not Path(WHISPER_MODEL).is_file():
        raise HTTPException(503, "Motor gratuito de transcrição indisponível. Verifique o deploy do Whisper.cpp.")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        source = workdir / f"input{Path(video.filename).suffix.lower()}"
        audio = workdir / "audio.wav"
        output_prefix = workdir / "transcript"
        total = 0
        with source.open("wb") as out:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "Vídeo excede o limite configurado de 200 MB.")
                out.write(chunk)

        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(audio)],
            capture_output=True, text=True
        )
        if proc.returncode != 0 or not audio.exists():
            raise HTTPException(422, "Não foi possível extrair o áudio do vídeo.")

        command = [
            WHISPER_BINARY, "-m", WHISPER_MODEL, "-f", str(audio),
            "-l", "pt", "-t", WHISPER_THREADS, "-oj", "-of", str(output_prefix)
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "A transcrição demorou mais que 30 minutos e foi interrompida.")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Erro desconhecido")[-700:]
            raise HTTPException(502, f"Falha no Whisper.cpp: {detail}")

        json_path = output_prefix.with_suffix(".json")
        if not json_path.is_file():
            raise HTTPException(502, "O Whisper.cpp terminou sem gerar o arquivo JSON.")
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            text, segments = parse_whisper_json(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(502, f"Não foi possível interpretar a transcrição: {str(exc)[:250]}")

        if not segments:
            raise HTTPException(422, "Nenhuma fala foi identificada no vídeo.")

        srt = "\n\n".join(
            f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text']}"
            for i, s in enumerate(segments, 1)
        )
        return {
            "status": "transcribed",
            "model": TRANSCRIPTION_MODEL,
            "language": "pt",
            "text": text,
            "segments": segments,
            "subtitles_srt": srt,
            "voice_commands": detect_commands(segments),
            "note": "Transcrição gratuita com Whisper.cpp. A renderização/inserção dos efeitos no MP4 ainda não está implementada."
        }
