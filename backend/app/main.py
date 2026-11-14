import os
import io
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
from openai import OpenAI

app = FastAPI(
    title="HITMO Voice Video Editor API",
    version="0.1.0",
    description="Transcrição de vídeos, legendas SRT e detecção inicial de comandos falados."
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
bearer_scheme = HTTPBearer(auto_error=False)


def require_api_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="HITMO_API_TOKEN não configurado no servidor.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Token Bearer obrigatório.")
    if not secrets.compare_digest(credentials.credentials, API_TOKEN):
        raise HTTPException(status_code=403, detail="Token inválido.")
    return True
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "whisper-1")

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

@app.get("/")
def root():
    return {"name": "HITMO Voice Video Editor API", "status": "online", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok", "transcription_model": TRANSCRIPTION_MODEL}

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
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.get(file_ref.download_link)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível baixar o vídeo anexado ao ChatGPT. Tente anexá-lo novamente.")
    if len(content) > MAX_VIDEO_BYTES:
        raise HTTPException(413, "Vídeo excede o limite configurado de 200 MB.")
    uploaded = UploadFile(filename=file_ref.name, file=io.BytesIO(content))
    return await transcribe_video(uploaded, _authorized=True)


@app.post("/v1/transcribe")
async def transcribe_video(
    video: UploadFile = File(...),
    _authorized: bool = Depends(require_api_token),
):
    if not video.filename or Path(video.filename).suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise HTTPException(400, "Envie um vídeo MP4, MOV, M4V ou WEBM.")
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "OPENAI_API_KEY não configurada no servidor.")

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / f"input{Path(video.filename).suffix.lower()}"
        audio = Path(tmp) / "audio.mp3"
        total = 0
        with source.open("wb") as out:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "Vídeo excede o limite configurado de 200 MB.")
                out.write(chunk)

        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "64k", str(audio)],
            capture_output=True, text=True
        )
        if proc.returncode != 0 or not audio.exists():
            raise HTTPException(422, "Não foi possível extrair o áudio do vídeo.")

        if audio.stat().st_size > 24 * 1024 * 1024:
            raise HTTPException(413, "Áudio extraído excede 24 MB. Reduza a duração/qualidade do vídeo e tente novamente.")

        client = OpenAI()
        try:
            with audio.open("rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model=TRANSCRIPTION_MODEL,
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    language="pt"
                )
        except Exception as exc:
            raise HTTPException(502, f"Falha na transcrição: {str(exc)[:300]}")

        segments = [
            {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
            for s in (getattr(result, "segments", None) or [])
        ]
        if not segments and getattr(result, "text", ""):
            segments = [{"start": 0.0, "end": 0.0, "text": result.text.strip()}]

        srt = "\n\n".join(
            f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text']}"
            for i, s in enumerate(segments, 1)
        )
        return {
            "status": "transcribed",
            "model": TRANSCRIPTION_MODEL,
            "language": "pt",
            "text": getattr(result, "text", ""),
            "segments": segments,
            "subtitles_srt": srt,
            "voice_commands": detect_commands(segments),
            "note": "Esta versão transcreve e identifica sugestões de efeitos. A renderização/inserção dos efeitos no MP4 ainda não está implementada."
        }
