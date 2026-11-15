"""Isolated Vosk transcription worker for the HITMO API."""
import json
import os
import sys
import wave
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("KALDI_NUM_THREADS", "1")

def main():
    audio_path = Path(sys.argv[1])
    model_path = Path(sys.argv[2])
    from vosk import KaldiRecognizer, Model

    words = []
    with wave.open(str(audio_path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
            raise RuntimeError("Audio must be mono 16 kHz 16-bit PCM WAV")
        model = Model(str(model_path))
        recognizer = KaldiRecognizer(model, wav.getframerate())
        recognizer.SetWords(True)
        while True:
            data = wav.readframes(4000)
            if not data:
                break
            if recognizer.AcceptWaveform(data):
                words.extend(json.loads(recognizer.Result()).get("result", []))
        words.extend(json.loads(recognizer.FinalResult()).get("result", []))

    segments = []
    for item in words:
        word = str(item.get("word", "")).strip()
        start = max(0.0, float(item.get("start", 0.0)))
        end = max(start, float(item.get("end", start)))
        if word and end > start:
            segments.append({"start": start, "end": end, "text": word})
    print(json.dumps({
        "text": " ".join(x["text"] for x in segments).strip(),
        "segments": segments,
    }, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Vosk worker failed: {exc}", file=sys.stderr)
        raise
