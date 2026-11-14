import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
import uvicorn

API_BASE_URL = os.getenv("HITMO_API_BASE_URL", "https://hitmo-video-ai.onrender.com").rstrip("/")
API_TOKEN = os.getenv("HITMO_API_TOKEN", "")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
REDIS_URL = os.getenv("REDIS_URL", "")
JWT_SIGNING_KEY = os.getenv("JWT_SIGNING_KEY", "")
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(200 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
ALLOWED_MIME_TYPES = {
    "video/mp4", "video/quicktime", "video/x-m4v", "video/webm",
    "application/octet-stream",
}
ALLOWED_FILE_HOST = "files.oaiusercontent.com"

def _build_auth_provider() -> GitHubProvider:
    required = {"GITHUB_CLIENT_ID": GITHUB_CLIENT_ID, "GITHUB_CLIENT_SECRET": GITHUB_CLIENT_SECRET, "REDIS_URL": REDIS_URL, "JWT_SIGNING_KEY": JWT_SIGNING_KEY}
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Configure no Render as variáveis: " + ", ".join(missing))
    encryption_key = base64.urlsafe_b64encode(hashlib.sha256(("HITMO_VIDEO_AI_STORAGE:" + JWT_SIGNING_KEY).encode("utf-8")).digest())
    storage = FernetEncryptionWrapper(key_value=RedisStore(url=REDIS_URL), fernet=Fernet(encryption_key))
    return GitHubProvider(client_id=GITHUB_CLIENT_ID, client_secret=GITHUB_CLIENT_SECRET, base_url="https://hitmo-video-ai-1.onrender.com", jwt_signing_key=JWT_SIGNING_KEY, client_storage=storage)


mcp = FastMCP("HITMO VIDEO AI — Editor Inteligente", auth=_build_auth_provider())


class ChatGPTFile(BaseModel):
    """File object automatically supplied by ChatGPT through openai/fileParams."""
    model_config = ConfigDict(extra="forbid")
    download_url: str = Field(description="Temporary HTTPS download URL supplied by ChatGPT.")
    file_id: str = Field(description="Opaque ChatGPT file identifier.")
    mime_type: str | None = Field(default=None, description="MIME type supplied by ChatGPT.")
    file_name: str | None = Field(default=None, description="Original filename supplied by ChatGPT.")


FILE_META = {"openai/fileParams": ["file"]}


def _validate_file(file: ChatGPTFile) -> tuple[str, str]:
    name = file.file_name or "video.mp4"
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Formato não suportado. Envie MP4, MOV, M4V ou WEBM.")
    parsed = urlparse(file.download_url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_FILE_HOST or parsed.username or parsed.password:
        raise ValueError("O link temporário do arquivo não pertence ao domínio seguro de arquivos do ChatGPT.")
    mime = (file.mime_type or "").lower().split(";")[0].strip()
    if mime and mime not in ALLOWED_MIME_TYPES:
        raise ValueError("O tipo MIME informado não corresponde a um vídeo aceito.")
    if mime == "application/octet-stream" or not mime:
        mime = {
            ".mp4": "video/mp4", ".mov": "video/quicktime",
            ".m4v": "video/x-m4v", ".webm": "video/webm",
        }[suffix]
    return name, mime


def _require_config() -> None:
    if not API_TOKEN:
        raise RuntimeError("O segredo HITMO_API_TOKEN não está configurado no serviço MCP.")


def _safe_api_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, str):
            detail = re.sub(r"(?i)bearer\s+\S+", "Bearer [redigido]", detail)
            return detail[:500]
    except (ValueError, json.JSONDecodeError):
        pass
    return f"A API HITMO retornou HTTP {response.status_code}."


def _validate_transcription(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("A API retornou JSON em formato inesperado.")
    if not isinstance(data.get("status"), str) or data["status"] != "transcribed":
        raise ValueError("A API não confirmou status=transcribed; resultado não será tratado como concluído.")
    if not isinstance(data.get("text"), str):
        raise ValueError("A resposta da API não contém o campo text válido.")
    segments = data.get("segments")
    if not isinstance(segments, list):
        raise ValueError("A resposta da API não contém segments em formato de lista.")
    for item in segments:
        if not isinstance(item, dict) or not isinstance(item.get("start"), (int, float)) or not isinstance(item.get("end"), (int, float)) or not isinstance(item.get("text"), str):
            raise ValueError("A API retornou um segmento sem timestamps/texto válidos.")
        if item["start"] < 0 or item["end"] < item["start"]:
            raise ValueError("A API retornou timestamps inválidos.")
    if not isinstance(data.get("subtitles_srt"), str):
        raise ValueError("A resposta da API não contém subtitles_srt válido.")
    commands = data.get("voice_commands")
    if not isinstance(commands, list):
        raise ValueError("A resposta da API não contém voice_commands em formato de lista.")
    for item in commands:
        if not isinstance(item, dict) or not isinstance(item.get("phrase"), str) or not isinstance(item.get("effect"), str):
            raise ValueError("A API retornou um comando de voz em formato inválido.")
    return {
        "status": data["status"],
        "model": data.get("model"),
        "language": data.get("language"),
        "text": data["text"],
        "segments": segments,
        "subtitles_srt": data["subtitles_srt"],
        "voice_commands": commands,
        "note": data.get("note"),
    }


async def _post_transcription_response(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        raise RuntimeError(f"Falha na API HITMO (HTTP {response.status_code}): {_safe_api_error(response)}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("A API HITMO não retornou JSON válido.") from exc
    return _validate_transcription(payload)


@mcp.tool(
    name="check_health",
    description="Verifica a disponibilidade real da API HITMO VIDEO AI pelo endpoint GET /health. Não inicia transcrição.",
)
async def check_health() -> dict[str, Any]:
    """Check the live REST API health endpoint."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(f"{API_BASE_URL}/health")
        if response.is_error:
            return {"connected": False, "http_status": response.status_code, "message": _safe_api_error(response)}
        try:
            payload = response.json()
        except ValueError:
            return {"connected": False, "http_status": response.status_code, "message": "A API respondeu sem JSON válido."}
        return {
            "connected": response.status_code == 200 and payload.get("status") == "ok",
            "http_status": response.status_code,
            "api_status": payload.get("status"),
            "transcription_model": payload.get("transcription_model"),
        }
    except httpx.HTTPError:
        return {"connected": False, "message": "Não foi possível alcançar a API HITMO VIDEO AI."}


@mcp.tool(
    name="transcribe_video",
    description="Recebe um vídeo anexado nesta conversa e o envia como multipart/form-data no campo video para POST /v1/transcribe. Aceita MP4, MOV, M4V ou WEBM até 200 MB.",
    meta=FILE_META,
)
async def transcribe_video(file: ChatGPTFile) -> dict[str, Any]:
    """Download a ChatGPT file through its temporary URL, then upload it to the existing REST endpoint."""
    _require_config()
    name, mime = _validate_file(file)
    timeout = httpx.Timeout(connect=20.0, read=600.0, write=600.0, pool=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", file.download_url) as download:
                if download.is_error:
                    raise RuntimeError(f"Não foi possível baixar o arquivo do ChatGPT (HTTP {download.status_code}). Anexe o vídeo novamente.")
                length = download.headers.get("content-length")
                if length and int(length) > MAX_VIDEO_BYTES:
                    raise RuntimeError("O vídeo excede o limite de 200 MB.")
                with tempfile.TemporaryFile() as temp:
                    total = 0
                    async for chunk in download.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_VIDEO_BYTES:
                            raise RuntimeError("O vídeo excede o limite de 200 MB.")
                        temp.write(chunk)
                    temp.seek(0)
                    response = await client.post(
                        f"{API_BASE_URL}/v1/transcribe",
                        headers={"Authorization": f"Bearer {API_TOKEN}"},
                        files={"video": (name, temp, mime)},
                    )
        return await _post_transcription_response(response)
    except httpx.TimeoutException:
        raise RuntimeError("A operação excedeu o tempo limite. Nenhuma transcrição foi confirmada; tente novamente.")
    except httpx.HTTPError:
        raise RuntimeError("Falha de comunicação ao baixar ou enviar o vídeo. Nenhuma transcrição foi confirmada.")


@mcp.tool(
    name="transcribe_chatgpt_file",
    description="Encaminha a referência de um vídeo anexado pelo ChatGPT ao endpoint existente POST /v1/transcribe-from-gpt. Aceita um único vídeo MP4, MOV, M4V ou WEBM.",
    meta=FILE_META,
)
async def transcribe_chatgpt_file(file: ChatGPTFile) -> dict[str, Any]:
    """Translate ChatGPT's file-param shape to the REST endpoint's verified OpenAPI implementation shape."""
    _require_config()
    name, mime = _validate_file(file)
    payload = {
        "openaiFileIdRefs": [{
            "name": name,
            "id": file.file_id,
            "mime_type": mime,
            "download_link": file.download_url,
        }]
    }
    timeout = httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/v1/transcribe-from-gpt",
                headers={"Authorization": f"Bearer {API_TOKEN}"},
                json=payload,
            )
        return await _post_transcription_response(response)
    except httpx.TimeoutException:
        raise RuntimeError("A operação excedeu o tempo limite. Nenhuma transcrição foi confirmada; tente novamente.")
    except httpx.HTTPError:
        raise RuntimeError("Falha de comunicação com a API HITMO. Nenhuma transcrição foi confirmada.")


app = mcp.http_app(path="/mcp", stateless_http=True)


async def health_route(request: Request):
    return JSONResponse({"status": "ok", "service": "hitmo-mcp-bridge"})


app.add_route("/health", health_route, methods=["GET"])


if __name__ == "__main__":
    if not API_TOKEN:
        raise SystemExit("Configure HITMO_API_TOKEN no ambiente antes de iniciar.")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
