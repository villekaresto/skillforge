#!/usr/bin/env python3
"""Local transcription helper. Run by ingest.py through `uv run --with faster-whisper`.

Prints JSON segments [{start, end, text}] to stdout. Audio never leaves this machine.
Models are cached by faster-whisper under ~/.cache/huggingface on first use
(small ≈ 480 MB, medium ≈ 1.5 GB, large-v3 ≈ 3 GB).

Audio is decoded with the ffmpeg binary, not PyAV: faster-whisper's PyAV decoding breaks whenever PyAV
changes its API, and pinning an old PyAV forces a source build on platforms without a wheel.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
SAMPLE_RATE = 16000


def load_audio(path: str, sr: int = SAMPLE_RATE):
    """Decode any media file to mono float32 PCM at `sr` Hz using ffmpeg."""
    import numpy as np  # faster-whisper dependency

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg is not installed")
    r = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                       capture_output=True)
    if r.returncode:
        sys.exit(f"ffmpeg could not decode {path}: {r.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default=None)
    ap.add_argument("--device", default=os.environ.get("SKILLFORGE_WHISPER_DEVICE", "auto"))
    a = ap.parse_args()

    from faster_whisper import WhisperModel  # resolved by uv

    compute = "int8" if a.device in ("cpu", "auto") else "float16"
    model = WhisperModel(a.model, device=a.device, compute_type=compute)
    segments, info = model.transcribe(load_audio(a.audio), language=a.language, vad_filter=True, beam_size=5)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments if s.text.strip()]
    print(f"[whisper] language={info.language} p={info.language_probability:.2f} segments={len(out)}", file=sys.stderr)
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to cp1252; ingest.py reads this as UTF-8
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
