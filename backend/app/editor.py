"""Renderização local inicial do HITMO VIDEO AI: ASS + FFmpeg, sem API paga."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _ass_time(seconds: float) -> str:
    value = max(0, int(round(seconds * 100)))
    hours, value = divmod(value, 360000)
    minutes, value = divmod(value, 6000)
    whole, centis = divmod(value, 100)
    return f"{hours}:{minutes:02}:{whole:02}.{centis:02}"


def _escape_ass(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def build_ass(segments: list[dict[str, Any]], destination: Path) -> None:
    header = """[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,58,&H00FFFFFF,&H0000FFFF,&H00101010,&H64000000,-1,0,0,0,100,100,0,0,1,4,1,2,80,80,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        start = float(segment.get("start", 0))
        end = float(segment.get("end", start))
        if not text or end <= start:
            continue
        words = text.split()
        if not words:
            continue
        total_cs = max(len(words) * 2, int((end - start) * 100))
        base, extra = divmod(total_cs, len(words))
        karaoke = []
        for index, word in enumerate(words):
            duration = base + (1 if index < extra else 0)
            karaoke.append(f"{{\\kf{duration}}}{_escape_ass(word)}")
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,"
            + " ".join(karaoke)
        )
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def render_video(
    source: Path,
    destination: Path,
    segments: list[dict[str, Any]],
    instructions: str,
) -> dict[str, Any]:
    """Burn animated karaoke captions and apply requested conservative finishing."""
    ass_path = destination.with_suffix(".ass")
    build_ass(segments, ass_path)

    normalized = " ".join(instructions.casefold().split())
    video_filters = [f"subtitles={ass_path.as_posix()}"]
    zoom_requested = any(term in normalized for term in (
        "zoom: sim", "zoom = sim", "aplique zoom", "quero zoom",
        "adicione zoom", "aproximacao de camera", "aproximação de câmera",
    ))
    if zoom_requested:
        video_filters.insert(0, "scale=iw*1.08:ih*1.08,crop=iw/1.08:ih/1.08")
    audio_filters = []
    noise_requested = any(term in normalized for term in (
        "reduza o ruido", "reduza o ruído", "remova o ruido", "remova o ruído",
        "limpe o audio", "limpe o áudio", "limpar audio: sim", "limpar áudio: sim",
    ))
    if noise_requested:
        audio_filters.append("afftdn=nf=-25")

    command = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(source),
        "-vf", ",".join(video_filters),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
    ]
    if audio_filters:
        command.extend(["-af", ",".join(audio_filters)])
    command.append(str(destination))
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "Falha desconhecida no FFmpeg")[-1200:]
        raise RuntimeError(detail)
    return {
        "output_bytes": destination.stat().st_size,
        "captions": len(segments),
        "zoom_applied": zoom_requested,
        "noise_reduction_applied": noise_requested,
        "note": "Primeira versão: legendas ASS animadas e efeitos básicos por instrução. Cortes inteligentes de silêncio, análise visual semântica e overlays gerados serão adicionados nas próximas etapas."
    }
