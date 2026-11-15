import io
import gc
import logging
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

logger = logging.getLogger("hitmo")

app = FastAPI(
    title="HITMO Voice Video Editor API",
    version="0.2.0",
    description="Transcrição gratuita em português com Vosk, legendas SRT e renderização local com FFmpeg."
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
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "/opt/vosk-model-small-pt-0.3")
bearer_scheme = HTTPBearer(auto_error=False)


def require_api_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="HITMO_API_TOKEN não configurado no servidor.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Token Bearer obrigatório.")
    if not secrets.compare_digest(credentials.credentials, API_TOKEN):
        raise HTTPException(status_code=403, detail="Token inválido.")
    return True


TRANSCRIPTION_MODEL = "Vosk/vosk-model-small-pt-0.3"

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


def transcribe_audio_vosk(audio_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Transcreve WAV mono 16 kHz com Vosk e devolve palavras com tempos reais."""
    try:
        import wave
        from vosk import KaldiRecognizer, Model
    except ImportError as exc:
        raise RuntimeError("Biblioteca Vosk não instalada no servidor.") from exc

    model_path = Path(VOSK_MODEL_PATH)
    if not model_path.is_dir():
        raise RuntimeError(f"Modelo Vosk não encontrado em {model_path}")

    with wave.open(str(audio_path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
            raise RuntimeError("O áudio precisa estar em WAV mono, 16 kHz e 16 bits.")
        model = Model(str(model_path))
        recognizer = KaldiRecognizer(model, wav.getframerate())
        recognizer.SetWords(True)
        words: list[dict[str, Any]] = []
        while True:
            data = wav.readframes(4000)
            if not data:
                break
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                words.extend(result.get("result", []))
        final_result = json.loads(recognizer.FinalResult())
        words.extend(final_result.get("result", []))

    segments = []
    for item in words:
        word = str(item.get("word", "")).strip()
        if not word:
            continue
        start = max(0.0, float(item.get("start", 0.0)))
        end = max(start, float(item.get("end", start)))
        if end > start:
            segments.append({"start": start, "end": end, "text": word})

    full_text = " ".join(segment["text"] for segment in segments).strip()
    del recognizer
    del model
    gc.collect()
    return full_text, segments


@app.get("/")
def root():
    return {"name": "HITMO Voice Video Editor API", "status": "online", "engine": TRANSCRIPTION_MODEL, "docs": "/docs"}


@app.get("/health")
def health():
    model_ok = Path(VOSK_MODEL_PATH).is_dir()
    return {
        "status": "ok" if model_ok else "degraded",
        "transcription_model": TRANSCRIPTION_MODEL,
        "engine_available": True,
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
    if not Path(VOSK_MODEL_PATH).is_dir():
        raise HTTPException(503, "Modelo gratuito Vosk não encontrado no servidor.")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        source = workdir / f"input{Path(video.filename).suffix.lower()}"
        audio = workdir / "audio.wav"
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

        try:
            text, segments = transcribe_audio_vosk(audio)
        except Exception as exc:
            raise HTTPException(502, f"Falha na transcrição Vosk: {str(exc)[:700]}")


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
            "note": "Transcrição gratuita e local com Vosk; os tempos são estimados por palavra pelo reconhecedor."
        }


# Edição local: vídeo + arquivo de instruções, sem API paga de GPT.
import shutil
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse
from .editor import render_video


@app.post("/v1/edit")
async def edit_video(
    video: UploadFile = File(...),
    instructions: UploadFile | None = File(default=None),
    _authorized: bool = Depends(require_api_token),
):
    """Recebe vídeo e briefing .md/.txt; transcreve com Vosk e renderiza MP4 com FFmpeg."""
    if not video.filename or Path(video.filename).suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise HTTPException(400, "Envie um vídeo MP4, MOV, M4V ou WEBM.")
    brief = "Criar legendas dinâmicas em branco e amarelo, preservar o formato original e manter a fala natural."
    if instructions is not None:
        if instructions.filename and Path(instructions.filename).suffix.lower() not in {".md", ".txt"}:
            raise HTTPException(400, "O arquivo de instruções deve ser .md ou .txt.")
        raw_brief = await instructions.read(65537)
        if len(raw_brief) > 65536:
            raise HTTPException(413, "O arquivo de instruções deve ter no máximo 64 KB.")
        brief = raw_brief.decode("utf-8", errors="replace").strip() or brief

    workdir = Path(tempfile.mkdtemp(prefix="hitmo-edit-"))
    logger.info("edit_started filename=%s", video.filename)
    try:
        source = workdir / ("original" + Path(video.filename).suffix.lower())
        total = 0
        with source.open("wb") as out:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "Vídeo excede o limite configurado de 200 MB.")
                out.write(chunk)

        with source.open("rb") as handle:
            upload = UploadFile(filename=video.filename, file=handle)
            transcription = await transcribe_video(upload, _authorized=True)
        logger.info("edit_transcription_complete words=%d", len(transcription.get("segments", [])))
        gc.collect()

        output = workdir / "HITMO_VIDEO_AI_editado.mp4"
        try:
            logger.info("edit_render_started")
            edit_info = render_video(source, output, transcription["segments"], brief)
            logger.info("edit_render_complete output_bytes=%d", edit_info.get("output_bytes", 0))
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "A renderização excedeu 30 minutos.")
        except RuntimeError as exc:
            raise HTTPException(502, f"Falha ao renderizar o vídeo: {str(exc)[-900:]}")

        headers = {
            "X-HITMO-Captions": str(edit_info["captions"]),
            "X-HITMO-Output-Bytes": str(edit_info["output_bytes"]),
        }
        return FileResponse(
            path=output,
            media_type="video/mp4",
            filename="HITMO_VIDEO_AI_editado.mp4",
            headers=headers,
            background=BackgroundTask(shutil.rmtree, str(workdir), ignore_errors=True),
        )
    except Exception:
        logger.exception("edit_failed")
        shutil.rmtree(workdir, ignore_errors=True)
        raise
