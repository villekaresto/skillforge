#!/usr/bin/env python3
"""Local transcription helper. Run by ingest.py through `uv run --with faster-whisper`.

Prints JSON segments [{start, end, text}] to stdout. Audio never leaves this machine.
Models are cached by faster-whisper under ~/.cache/huggingface on first use
(small ≈ 480 MB, medium ≈ 1.5 GB, large-v3 ≈ 3 GB).
"""
import argparse
import json
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


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
    segments, info = model.transcribe(a.audio, language=a.language, vad_filter=True, beam_size=5)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments if s.text.strip()]
    print(f"[whisper] language={info.language} p={info.language_probability:.2f} segments={len(out)}", file=sys.stderr)
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
