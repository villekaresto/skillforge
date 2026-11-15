#!/usr/bin/env python3
"""Apple Silicon transcription helper. Run by ingest.py through `uv run --with mlx-whisper` on arm64 macOS.

Same contract as whisper_local.py: prints JSON segments [{start, end, text}] to stdout, audio never leaves the machine.
Models come from the mlx-community Hugging Face org and are cached under ~/.cache/huggingface on first use.

mlx-whisper has no voice-activity filter, and Whisper hallucinates on music or silence ("Thank you for watching").
Segments Whisper itself marks as probably not speech, or as repetitive, are dropped.
"""
import argparse
import json
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
MODELS = {"tiny": "mlx-community/whisper-tiny-mlx", "base": "mlx-community/whisper-base-mlx",
          "small": "mlx-community/whisper-small-mlx", "medium": "mlx-community/whisper-medium-mlx",
          "large-v3": "mlx-community/whisper-large-v3-mlx", "turbo": "mlx-community/whisper-large-v3-turbo"}


def keep(seg: dict) -> bool:
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0:
        return False                                        # Whisper's own "this is not speech" signal
    return seg.get("compression_ratio", 0) <= 2.4           # higher means a repeated phrase loop


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default=None)
    a = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from whisper_local import load_audio                    # ffmpeg decode, shared with the faster-whisper path
    import mlx_whisper                                      # resolved by uv

    result = mlx_whisper.transcribe(load_audio(a.audio), path_or_hf_repo=MODELS.get(a.model, a.model), language=a.language,
                                    condition_on_previous_text=False, verbose=None)
    segs = result.get("segments", [])
    out = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
           for s in segs if s["text"].strip() and keep(s)]
    print(f"[whisper-mlx] language={result.get('language')} segments={len(out)} dropped={len(segs) - len(out)}", file=sys.stderr)
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to cp1252; ingest.py reads this as UTF-8
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
