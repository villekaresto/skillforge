#!/usr/bin/env python3
"""Ingest a video or podcast from any URL (or local file) into evidence Claude can read.

Produces, in --out-dir (default: a fresh folder under ~/.skillforge/work):
  source.json     metadata + transcript segments (machine readable)
  transcript.md   timestamped transcript (human/Claude readable)
  frames/*.jpg    optional keyframes when --frames is given
  gemini.md       optional whole-video observations when --engine gemini is used

Engines for media without captions:
  local   faster-whisper on this machine via `uv run` (audio never leaves the computer)
  gemini  Google Gemini (needs GEMINI_API_KEY); also the only engine that can WATCH the whole video
  auto    captions, then local if uv is present, then gemini if a key is present, else metadata only

Only the standard library. External tools: yt-dlp (URLs), ffmpeg/ffprobe (frames, audio), uv (local engine).
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("SKILLFORGE_HOME", Path.home() / ".skillforge"))
WORK_ROOT = HOME / "work"
GEMINI_API = "https://generativelanguage.googleapis.com"
GEMINI_MODEL = os.environ.get("SKILLFORGE_GEMINI_MODEL", "gemini-2.5-flash")
WHISPER_MODEL = os.environ.get("SKILLFORGE_WHISPER_MODEL", "small")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus", ".oga", ".weba"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
RETRYABLE = ("403", "Sign in to confirm", "HTTP Error 429", "Requested format is not available", "nsig", "player")


# --------------------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"[ingest] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 2) -> None:
    log(msg)
    sys.exit(code)


def need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        pkg = {"ffprobe": "ffmpeg"}.get(binary, binary)
        die(f"{binary} is not installed.  macOS: brew install {pkg}   Debian: sudo apt install {pkg}   Windows: winget install {pkg}")
    return path


def run(cmd: list, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def tail(text: str, lines: int = 4, limit: int = 600) -> str:
    """The last few non-empty lines of a tool's stderr: the cause is rarely on the very last line."""
    kept = [l.strip() for l in text.splitlines() if l.strip()][-lines:]
    return " | ".join(kept)[-limit:]


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def is_url(s: str) -> bool:
    return re.match(r"^https?://", s, re.I) is not None


def slugify(text: str, limit: int = 60) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()   # cómo -> como, café -> cafe
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:limit].rstrip("-") or "untitled"


def read_env_file() -> dict:
    cfg = HOME / ".env"
    out: dict = {}
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def setting(name: str) -> str | None:
    return os.environ.get(name) or read_env_file().get(name) or None


def clear_stale(out: Path) -> None:
    """A reused --out-dir must not hand us files from an earlier, possibly failed, run."""
    for p in out.glob("media*"):
        p.unlink(missing_ok=True)
    for p in out.glob("audio.*"):
        p.unlink(missing_ok=True)
    for name in ("source.json", "transcript.md", "gemini.md", "playlist.json"):
        (out / name).unlink(missing_ok=True)
    shutil.rmtree(out / "frames", ignore_errors=True)


# --------------------------------------------------------------------------- captions

def to_sec(stamp: str) -> float:
    parts = [float(p) for p in stamp.replace(",", ".").split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_vtt(text: str) -> list[dict]:
    """Parse WebVTT/SRT into [{start, end, text}], merging YouTube's rolling duplicate cues.

    YouTube auto-captions repeat the previous line at the top of every cue and put cue settings
    (align:start position:0%) after the timing; both are dropped here."""
    segs: list[dict] = []
    recent: list[str] = []
    ts = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3}"
    ent = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        m = re.search(rf"({ts})\s*-->\s*({ts})", block)
        if not m:
            continue
        rest = block[m.end():]
        body = rest.split("\n", 1)[1] if "\n" in rest else ""   # skip cue settings on the timing line
        body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"&[a-z#0-9]+;", lambda x: ent.get(x.group(), x.group()), body)
        lines = [re.sub(r"\s+", " ", l.strip()) for l in body.splitlines() if l.strip() and not l.strip().isdigit()]
        lines = [l for l in lines if l not in recent[-3:]]   # rolling captions: line already shown in the last cue
        if not lines:
            continue
        recent += lines
        content = re.sub(r"\s+", " ", " ".join(lines))
        if segs and (content == segs[-1]["text"] or segs[-1]["text"].endswith(content)):
            continue
        if segs and content.startswith(segs[-1]["text"]) and len(segs[-1]["text"]) > 10:
            segs[-1]["text"] = content
            segs[-1]["end"] = to_sec(m.group(2))
            continue
        segs.append({"start": to_sec(m.group(1)), "end": to_sec(m.group(2)), "text": content})
    return segs


def ytdlp_base(auth: list[str]) -> list[str]:
    return [need("yt-dlp"), "--no-playlist", "--no-warnings", *auth]


def ytdlp_with_retry(cmd: list[str], label: str, timeout: int = 3600) -> subprocess.CompletedProcess:
    """One retry with alternate YouTube clients, which clears most 403 / nsig / format failures without updating yt-dlp."""
    try:
        r = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        die(f"{label}: yt-dlp did not finish within {timeout // 60} min (very long video or a throttled connection).")
    if r.returncode and any(k in r.stderr for k in RETRYABLE):
        log(f"{label}: retrying with alternate player clients")
        try:
            r = run([*cmd, "--extractor-args", "youtube:player_client=android,ios,web"], timeout=timeout)
        except subprocess.TimeoutExpired:
            die(f"{label}: yt-dlp did not finish within {timeout // 60} min on the retry.")
    return r


def is_playlist_url(url: str) -> bool:
    """Playlist or channel pages, not a single video that merely carries &list=."""
    from urllib.parse import parse_qs, urlparse
    u = urlparse(url)
    q = parse_qs(u.query)
    if "youtube.com" in u.netloc or "youtu.be" in u.netloc:
        if "v" in q or u.netloc.endswith("youtu.be") or u.path.startswith(("/shorts/", "/live/")):
            return False
        return "list" in q or u.path.startswith(("/playlist", "/@", "/channel/", "/c/", "/user/"))
    return False


def playlist_entries(url: str, auth: list[str]) -> dict:
    r = ytdlp_with_retry([*ytdlp_base(auth)[:1], "--flat-playlist", "--no-warnings", "-J", *auth, url], "playlist")
    if r.returncode:
        die(f"yt-dlp could not read this playlist.\n{r.stderr.strip()[-1200:]}")
    info = json.loads(r.stdout)
    entries = [{"id": e.get("id"), "title": e.get("title"), "duration": hms(e["duration"]) if e.get("duration") else None,
                "url": e.get("url") if str(e.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={e.get('id')}"}
               for e in info.get("entries") or [] if e]
    return {"playlist": info.get("title"), "url": url, "uploader": info.get("uploader") or info.get("channel"),
            "count": len(entries), "entries": entries,
            "next": "This is a playlist. Ingest each entry URL separately (one work dir each), then judge and merge per task."}


def pick_subtitle(subs: list[Path], lang: str) -> Path:
    """Prefer YouTube's '<lang>-orig' track: it is the speech-recognised original. When YouTube mislabels the audio
    language, the plain '<lang>' auto track is a machine translation of a recognition in the wrong language."""
    orig = [p for p in subs if f".{lang}-orig." in p.name]
    exact = [p for p in subs if f".{lang}." in p.name]
    return (orig or exact or sorted(subs, key=lambda p: p.stat().st_size, reverse=True))[0]


def caption_failure(r: subprocess.CompletedProcess) -> str | None:
    """yt-dlp exits non-zero but still writes info.json when only the subtitle download fails (often HTTP 429)."""
    if r.returncode and re.search(r"Unable to download (?:video )?subtitles|HTTP Error \d+", r.stderr):
        return tail(r.stderr, lines=2, limit=300)
    return None


CAPTION_RETRY_WAIT = float(os.environ.get("SKILLFORGE_CAPTION_RETRY_WAIT", "30"))
INFO_CACHE_TTL = 6 * 3600          # YouTube's signed subtitle URLs inside info.json expire after roughly six hours


def video_key(url: str) -> str:
    """A stable cache key: the YouTube video id when there is one, otherwise a hash of the URL."""
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([\w-]{11})", url)
    return f"youtube-{m.group(1)}" if m else "url-" + hashlib.sha1(url.encode()).hexdigest()[:16]


def info_cache_path(url: str) -> Path:
    return HOME / "cache" / f"{video_key(url)}.info.json"


def caption_passes(base: list[str], out: Path) -> tuple[list[Path], str | None, list[str], subprocess.CompletedProcess]:
    """Manual captions first (provenance certain), then auto captions."""
    out_glob = lambda: sorted(out.glob("media*.vtt")) + sorted(out.glob("media*.srt"))
    errors: list[str] = []
    r = ytdlp_with_retry([*base, "--write-subs"], "captions")
    subs = out_glob()
    if subs:
        return subs, "manual", [], r
    if fail := caption_failure(r):
        errors.append(f"captions: {fail}")
    r = ytdlp_with_retry([*base, "--write-auto-subs"], "auto captions")
    subs = out_glob()
    if subs:
        return subs, "auto", [], r
    if fail := caption_failure(r):
        errors.append(f"auto captions: {fail}")
    return [], None, errors, r



MUSIC_ONLY = "captions are mostly [Music]/[Applause] or noise: the video probably has no narration; the frames are the evidence (use --frames)."


def non_speech_share(segs: list[dict]) -> float:
    """Share of caption segments that are only a sound tag ([Music], [Música], ♪) or a one-word filler loop."""
    tag = re.compile(r"^\W*(\[[^\]]*\]|♪+|\(\w+\))\W*$")
    words = [s["text"].strip().lower() for s in segs]
    filler = Counter(w for w in words if len(w.split()) <= 2)
    loops = {w for w, n in filler.items() if n >= max(5, len(words) // 10)}
    return sum(1 for w in words if tag.match(w) or w in loops) / max(len(words), 1)


def fetch_captions(url: str, out: Path, lang: str, auth: list[str]) -> tuple[dict, list[dict], str | None, list[str]]:
    tail_args = ["--skip-download", "--write-info-json", "--sub-format", "vtt/srt/best",
                 "--sub-langs", f"{lang},{lang}-*,{lang}.*", "-o", str(out / "media.%(ext)s")]
    cache = info_cache_path(url)
    fresh = cache.is_file() and time.time() - cache.stat().st_mtime < INFO_CACHE_TTL
    # A fresh cached info.json skips YouTube's player request (the call that trips bot checks); captions still download.
    base = ytdlp_base(auth) + tail_args + (["--load-info-json", str(cache)] if fresh else [url])
    if fresh:
        log(f"using cached metadata {cache.name}")
    subs, provenance, errors, r = caption_passes(base, out)
    if not list(out.glob("media*.info.json")) and fresh:
        log("cached metadata was rejected; fetching it again")
        base = ytdlp_base(auth) + tail_args + [url]
        subs, provenance, errors, r = caption_passes(base, out)
    info_files = list(out.glob("media*.info.json"))
    if not info_files:
        needs_login = any(k in r.stderr for k in ("Sign in", "Private", "logged-in", "login", "--cookies", "HTTP Error 401"))
        hint = "\n   This source needs an account: if you have access, add --cookies-from-browser firefox (or chrome, safari)." \
            if needs_login else ""
        die(f"yt-dlp could not read this URL.\n{r.stderr.strip()[-1200:]}{hint}")
    if not subs and errors:
        # Caption downloads fail transiently (HTTP 429 above all). One patient retry before falling back to transcription.
        log(f"captions failed ({errors[-1][:120]}); waiting {CAPTION_RETRY_WAIT:.0f} s and trying once more")
        time.sleep(CAPTION_RETRY_WAIT)
        subs, provenance, errors, r = caption_passes(base, out)
    for e in errors if not subs else []:
        log(e)
    info = json.loads(info_files[0].read_text(encoding="utf-8"))
    if not fresh:
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(info_files[0], cache)
    meta = {
        "url": info.get("webpage_url") or url, "id": info.get("id"), "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"), "duration": info.get("duration"),
        "upload_date": info.get("upload_date"), "extractor": info.get("extractor_key"),
        "description": (info.get("description") or "")[:4000],
        "chapters": [{"start": c.get("start_time"), "title": c.get("title")} for c in (info.get("chapters") or [])],
        "tags": (info.get("tags") or [])[:30], "language": info.get("language"),
    }
    segs = parse_vtt(pick_subtitle(subs, lang).read_text(encoding="utf-8", errors="replace")) if subs else []
    if segs and non_speech_share(segs) > 0.6:
        errors.append(MUSIC_ONLY)
        log(MUSIC_ONLY)
    return meta, segs, provenance, (errors if not subs else [])


def local_metadata(path: Path) -> dict:
    duration = None
    if shutil.which("ffprobe"):
        r = run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path])
        try:
            duration = float(json.loads(r.stdout)["format"]["duration"])
        except Exception:
            pass
    return {"url": str(path.resolve()), "id": hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:11],
            "title": path.stem, "uploader": None, "duration": duration, "upload_date": None,
            "extractor": "local", "description": "", "chapters": [], "tags": [], "language": None}


# --------------------------------------------------------------------------- media

def video_format(frame_width: int) -> str:
    """Download no more video than the frames need: frames are scaled to `frame_width`, and 60 fps adds nothing.
    A 5-hour 720p60 stream was 3+ GB for 40 frames at 768 px."""
    h = 480 if frame_width <= 854 else 720
    return f"bv*[height<={h}][fps<=30]+ba/bv*[height<={h}]+ba/b[height<={h}]/b"


def download_media(url: str, out: Path, audio_only: bool, auth: list[str], frame_width: int = 768) -> Path:
    fmt = ["-f", "bestaudio[ext=m4a]/bestaudio/best", "-x", "--audio-format", "m4a"] if audio_only else \
          ["-f", video_format(frame_width), "--merge-output-format", "mp4"]
    r = ytdlp_with_retry([*ytdlp_base(auth), *fmt, "-o", str(out / "media.%(ext)s"), url], "download")
    files = [p for p in out.glob("media.*") if p.suffix.lower() in AUDIO_EXT | VIDEO_EXT and not p.name.endswith(".part")]
    if r.returncode or not files:
        die(f"download failed.\n{r.stderr.strip()[-1200:]}")
    return files[0]


def extract_audio(media: Path, out: Path) -> Path:
    audio = out / "audio.m4a"
    r = run([need("ffmpeg"), "-y", "-i", media, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", audio])
    if r.returncode:
        die(f"ffmpeg audio extraction failed: {r.stderr[-800:]}")
    return audio


# --------------------------------------------------------------------------- frames

def vfr_flag(ffmpeg: str) -> list[str]:
    """-fps_mode on FFmpeg 5.1+, -vsync on older builds."""
    r = run([ffmpeg, "-hide_banner", "-h", "full"])
    return ["-fps_mode", "vfr"] if "-fps_mode" in r.stdout else ["-vsync", "vfr"]


def thumb(ffmpeg: str, img: Path) -> bytes:
    r = subprocess.run([ffmpeg, "-v", "quiet", "-i", str(img), "-vf", "scale=16:16", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    return r.stdout


def dedup(ffmpeg: str, pairs: list[tuple[Path, float]], threshold: float = 3.0, max_gap: float | None = None) -> list[tuple[Path, float]]:
    """Return the frames that differ from the previously kept one; does not delete anything.
    A frame `max_gap` seconds after the last kept one is kept anyway: code being typed in an editor barely changes
    a 16x16 thumbnail, and those minutes are usually the ones that matter."""
    kept: list[tuple[Path, float]] = []
    last: bytes | None = None
    for f, t in pairs:
        cur = thumb(ffmpeg, f)
        if last is not None and len(cur) == len(last) == 256 and not (max_gap and t - kept[-1][1] >= max_gap):
            if sum(abs(a - b) for a, b in zip(cur, last)) / 256 < threshold:
                continue
        kept.append((f, t))
        last = cur
    return kept


def is_blank(ffmpeg: str, img: Path) -> bool:
    """A fade-to-black or a single-colour card. Kept deliberately narrow: a dark editor with one line of code
    (mean ~30, sd ~3) must survive, so only near-uniform or near-black frames go."""
    r = subprocess.run([ffmpeg, "-v", "quiet", "-i", str(img), "-vf", "scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    px = r.stdout
    if len(px) != 64 * 36:
        return False
    mean = sum(px) / len(px)
    sd = (sum((v - mean) ** 2 for v in px) / len(px)) ** 0.5
    return sd < 1.0 or (mean < 8 and sd < 3)


def thin_by_time(pairs: list[tuple[Path, float]], max_frames: int, duration: float | None) -> list[tuple[Path, float]]:
    """Pick at most `max_frames`, one per equal time bucket first, so a burst of cuts (memes, slides) cannot crowd out
    a long static stretch; leftover slots go to the remaining frames, evenly by index."""
    if len(pairs) <= max_frames:
        return pairs
    span = duration or (pairs[-1][1] + 1)
    chosen: dict[int, int] = {}
    for i, (_, t) in enumerate(pairs):
        chosen.setdefault(min(int(t / span * max_frames), max_frames - 1), i)
    keep = set(chosen.values())
    rest = [i for i in range(len(pairs)) if i not in keep]
    need = max_frames - len(keep)
    if need > 0 and rest:
        keep |= {rest[round(j * (len(rest) - 1) / max(need - 1, 1))] for j in range(min(need, len(rest)))}
    return [p for i, p in enumerate(pairs) if i in keep]


def settle_times(stamps: list[float], duration: float | None, gap: float = 1.0, settle: float = 0.7) -> list[float]:
    """Scene detection fires during a transition (wipes, fades), often several times a second. Collapse cuts that are
    less than `gap` apart and sample `settle` seconds after the last one, when the new screen is fully drawn."""
    out: list[float] = []
    group_end = None
    for t in sorted(stamps):
        if group_end is not None and t - group_end >= gap:
            out.append(group_end + settle)
        group_end = t
    if group_end is not None:
        out.append(group_end + settle)
    limit = (duration - 0.1) if duration else None
    return [min(t, limit) if limit else t for t in out]


def parse_section(text: str) -> tuple[float, float]:
    """'1:17:00-1:37:30' -> (4620.0, 5850.0). Each side is H:MM:SS, M:SS or seconds."""
    m = re.fullmatch(r"\s*([\d:.]+)\s*-\s*([\d:.]+)\s*", text or "")
    if not m:
        die(f"--section wants START-END like 1:17:00-1:37:00, got {text!r}")
    start, end = to_sec(m.group(1)), to_sec(m.group(2))
    if end <= start:
        die(f"--section end must be after start: {text!r}")
    return start, end


def extract_frames(video: Path, out: Path, max_frames: int, duration: float | None, width: int, no_dedup: bool,
                   offset: float = 0.0) -> list[dict]:
    """`offset` is where `video` starts inside the source (a --section download), so stamps match the transcript."""
    """Scene changes (threshold 0.2) plus a uniform pass, merged by time, near-duplicates removed, thinned to the cap.
    The uniform pass guarantees coverage of slow screen recordings where typing never trips the scene detector."""
    ffmpeg = need("ffmpeg")
    if duration and duration < 120:                       # a 30 s short needs ~12 frames, not 40 near-identical ones
        max_frames = min(max_frames, max(4, int(duration / 2.5)))
    frames_dir = out / "frames"
    frames_dir.mkdir(exist_ok=True)
    scale = f"scale='min({width},iw)':-2"
    r = run([ffmpeg, "-hide_banner", "-i", video, "-vf", "select='gt(scene,0.2)',showinfo", *vfr_flag(ffmpeg), "-f", "null", "-"],
            timeout=1800)
    stamps = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    pairs = []
    for i, t in enumerate(settle_times(stamps, duration)[: max_frames * 3]):
        f = frames_dir / f"scene_{i:04d}.jpg"
        run([ffmpeg, "-hide_banner", "-ss", f"{t:.2f}", "-i", video, "-frames:v", 1, "-vf", scale, "-q:v", 3, f], timeout=120)
        if f.is_file():
            pairs.append((f, t))
    if duration:
        step = max(duration / max(max_frames, 1), 2.0)
        run([ffmpeg, "-hide_banner", "-i", video, "-vf", f"fps=1/{step:.3f},{scale}", "-q:v", 3, frames_dir / "uniform_%04d.jpg"], timeout=1800)
        pairs += [(f, i * step) for i, f in enumerate(sorted(frames_dir.glob("uniform_*.jpg")))]
    pairs.sort(key=lambda p: p[1])
    blank = [(f, t) for f, t in pairs if is_blank(ffmpeg, f)]
    if blank and len(blank) < len(pairs):                 # an all-blank result means the video itself is plain
        log(f"frames: dropped {len(blank)} blank/black frames")
        for f, _ in blank:
            f.unlink()
        pairs = [p for p in pairs if p not in blank]
    if not no_dedup and pairs:
        kept = dedup(ffmpeg, pairs, max_gap=max((duration or 0) / max(max_frames, 1) * 2, 20.0))
        if len(kept) >= max(3, min(max_frames, len(pairs)) // 4):
            if len(kept) != len(pairs):
                log(f"frames: dropped {len(pairs) - len(kept)} near-duplicates")
            pairs = kept
        else:
            log("frames: dedup would leave too few frames (static content); keeping all")
    if len(pairs) > max_frames:
        kept = thin_by_time(pairs, max_frames, duration)
        for f, _ in set(pairs) - set(kept):
            f.unlink()
        pairs = kept
    result = []
    for i, (f, t) in enumerate(pairs, 1):
        t += offset
        new = frames_dir / f"{i:03d}_{hms(t).replace(':', '-')}.jpg"
        f.rename(new)
        result.append({"index": i, "time": round(t, 2), "stamp": hms(t), "path": str(new)})
    for leftover in list(frames_dir.glob("scene_*.jpg")) + list(frames_dir.glob("uniform_*.jpg")):
        leftover.unlink()
    return result


# --------------------------------------------------------------------------- local whisper

def local_available() -> bool:
    return bool(shutil.which("uv"))


NO_SPEECH = ("local transcription found no speech (music, silence, or text shown on screen only). "
             "The content may be visual: re-run with --frames.")
NO_SPEECH_FRAMES = "local transcription found no speech (music or silence); the frames are the evidence."


def whisper_language(code: str | None) -> str | None:
    """Whisper wants a bare ISO 639-1 code: YouTube metadata says 'en-US', 'pt_BR', 'zh-Hans'."""
    if not code:
        return None
    base = re.split(r"[-_]", code.strip().lower())[0]
    return base if re.fullmatch(r"[a-z]{2,3}", base) else None


WHISPER_BACKENDS = {
    "mlx": ("whisper_mlx.py", "mlx-whisper>=0.4"),          # Apple Silicon GPU through MLX
    "faster": ("whisper_local.py", "faster-whisper>=1.1"),  # CPU (or CUDA) through CTranslate2, works everywhere
}


def whisper_backends() -> list[str]:
    """Order to try: SKILLFORGE_WHISPER_BACKEND=mlx|faster forces one; auto prefers MLX on arm64 macOS."""
    import platform
    forced = (setting("SKILLFORGE_WHISPER_BACKEND") or "auto").lower()
    if forced in WHISPER_BACKENDS:
        return [forced]
    apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    return ["mlx", "faster"] if apple_silicon else ["faster"]


def transcribe_local(audio: Path, language: str | None) -> tuple[list[dict], str]:
    """Returns (segments, backend). Falls back to faster-whisper when the MLX path fails (no wheel, old macOS)."""
    failures = []
    for backend in whisper_backends():
        script, package = WHISPER_BACKENDS[backend]
        cmd = [need("uv"), "run", "--quiet", "--with", package, "--python", "3.12", str(Path(__file__).with_name(script)),
               str(audio), "--model", WHISPER_MODEL]
        if language:
            cmd += ["--language", language]
        log(f"local transcription with {package.split('>')[0]} {WHISPER_MODEL} (first run downloads the model)")
        r = run(cmd, timeout=int(setting("SKILLFORGE_WHISPER_TIMEOUT") or 3600))
        if not r.returncode:
            return json.loads(r.stdout), backend
        failures.append(f"{backend}: {tail(r.stderr)}")
        log(f"{backend} transcription failed; " + ("trying the next backend" if backend != whisper_backends()[-1] else "giving up"))
    raise RuntimeError("local transcription failed:\n" + "\n".join(failures))


# --------------------------------------------------------------------------- gemini

def _gemini(url: str, key: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None, timeout: int = 120):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("x-goog-api-key", key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.headers, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        die(f"Gemini HTTP {e.code}: {e.read()[:600].decode(errors='replace')}")


def gemini_upload(path: Path, key: str):
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    hdr, _ = _gemini(f"{GEMINI_API}/upload/v1beta/files", key, data=json.dumps({"file": {"display_name": path.name}}).encode(),
                     headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                              "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size),
                              "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"})
    upload_url = hdr.get("X-Goog-Upload-URL")
    if not upload_url:
        die("Gemini did not return an upload URL")
    _, info = _gemini(upload_url, key, data=path.read_bytes(), timeout=900,
                      headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize", "Content-Type": mime})
    f = info.get("file", {})
    name, uri, state = f.get("name"), f.get("uri"), f.get("state")
    deadline = time.time() + 600
    while state == "PROCESSING" and time.time() < deadline:
        time.sleep(5)
        _, f = _gemini(f"{GEMINI_API}/v1beta/{name}", key)
        state = f.get("state")
    if state != "ACTIVE":
        die(f"Gemini file state: {state}")
    return uri, name, mime


def gemini_generate(parts: list[dict], key: str) -> str:
    body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2}}
    _, out = _gemini(f"{GEMINI_API}/v1beta/models/{GEMINI_MODEL}:generateContent", key, data=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json"}, timeout=900)
    try:
        return "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        die(f"Gemini returned no text: {json.dumps(out)[:500]}")


def gemini_delete(name: str, key: str) -> None:
    try:
        _gemini(f"{GEMINI_API}/v1beta/{name}", key, method="DELETE")
    except SystemExit:
        pass


TRANSCRIBE_PROMPT = ("Transcribe this recording verbatim. One line per utterance in the form [M:SS] text (or [H:MM:SS] when over an hour). "
                     "Mark speaker changes as Speaker 1:, Speaker 2: when audible. No commentary, transcript only.")
WATCH_PROMPT = ("Watch this entire video as a careful analyst preparing to teach its content. Produce, with timestamps:\n"
                "1. A chronological log of what is shown and said (every meaningful step, screen, command, diagram, or claim).\n"
                "2. Any on-screen text, code, commands, URLs, or numbers, reproduced exactly.\n"
                "3. Moments where the speaker warns, corrects themselves, or says something is important.\n"
                "Be exhaustive about procedure. Do not summarize; log.")


def parse_gemini_transcript(text: str) -> list[dict]:
    segs = []
    for line in text.splitlines():
        m = re.match(r"\s*\[?((?:\d+:)?\d{1,2}:\d{2})\]?\s*(.+)", line)
        if m:
            segs.append({"start": to_sec(m.group(1)), "end": None, "text": m.group(2).strip()})
    return segs


# --------------------------------------------------------------------------- output

def write_transcript_md(meta: dict, segs: list[dict], provenance: str | None, out: Path, has_frames: bool = False) -> Path:
    lines = [f"# {meta.get('title') or 'Untitled'}", "", f"- Source: {meta['url']}", f"- By: {meta.get('uploader') or 'unknown'}",
             f"- Duration: {hms(meta['duration']) if meta.get('duration') else 'unknown'}", f"- Transcript: {provenance or 'none'}", ""]
    if meta.get("chapters"):
        lines += ["## Chapters", *[f"- [{hms(c['start'] or 0)}] {c['title']}" for c in meta["chapters"]], ""]
    if meta.get("description"):
        lines += ["## Description", meta["description"].strip(), ""]
    lines.append("## Transcript")
    if not segs:
        lines.append("_Local transcription ran and found no speech. The frames in frames/ are the evidence._"
                     if provenance == "local-whisper" and has_frames else
                     "_Local transcription ran and found no speech (music, silence, or on-screen text only). Re-run with --frames._"
                     if provenance == "local-whisper" else
                     "_No transcript available. Use --engine local (needs uv) or --engine gemini (needs GEMINI_API_KEY), or --frames._")
    lines += [f"[{hms(s['start'])}] {s['text']}" for s in segs]
    p = out / "transcript.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="URL (YouTube, Instagram, TikTok, Vimeo, podcast page, direct media link) or local file")
    ap.add_argument("--out-dir", help="Evidence folder (default: new folder under ~/.skillforge/work)")
    ap.add_argument("--engine", choices=["auto", "captions", "local", "gemini"], default="auto",
                    help="Transcription when there are no captions. gemini also WATCHES the whole video.")
    ap.add_argument("--frames", type=int, nargs="?", const=40, default=0, metavar="N", help="Extract up to N keyframes (default 40)")
    ap.add_argument("--resolution", type=int, default=768, help="Max frame width in px (default 768; 1280 for on-screen text)")
    ap.add_argument("--section", metavar="START-END", help="take frames from this part only, e.g. 1:17:00-1:37:00 (long webinars, streams)")
    ap.add_argument("--no-dedup", action="store_true", help="Keep near-duplicate frames")
    ap.add_argument("--lang", default="en", help="Preferred caption language (default en); also forces Whisper's language")
    ap.add_argument("--cookies", metavar="FILE", help="Netscape cookie file for private sources")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER", help="firefox, chrome, safari, edge, brave ...")
    ap.add_argument("--keep-media", action="store_true", help="Keep downloaded media in the work dir")
    args = ap.parse_args()
    args.lang_given = any(a == "--lang" or a.startswith("--lang=") for a in sys.argv[1:])

    if args.cookies and args.cookies_from_browser:
        die("use --cookies or --cookies-from-browser, not both")
    cookies_file = args.cookies or setting("SKILLFORGE_COOKIES_FILE")
    cookies_browser = args.cookies_from_browser or (None if args.cookies else setting("SKILLFORGE_COOKIES_FROM_BROWSER"))
    auth = ["--cookies", str(Path(cookies_file).expanduser())] if cookies_file else \
           ["--cookies-from-browser", cookies_browser] if cookies_browser else []

    src = args.source
    local = not is_url(src)
    if local and not Path(src).is_file():
        die(f"not a URL and not a file: {src}")

    out = Path(args.out_dir).expanduser() if args.out_dir else WORK_ROOT / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    clear_stale(out)

    segs: list[dict] = []
    provenance: str | None = None
    media: Path | None = Path(src) if local else None
    gemini_notes: str | None = None

    if local:
        meta = local_metadata(media)
    elif is_playlist_url(src):
        # yt-dlp would write every entry's captions to the same file names and the last video would win silently.
        listing = playlist_entries(src, auth)
        (out / "playlist.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(listing, indent=2, ensure_ascii=False))
        return
    else:
        meta, segs, provenance, caption_errors = fetch_captions(src, out, args.lang, auth)
    meta["ingested_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    meta["slug_hint"] = slugify(meta.get("title"))

    key = setting("GEMINI_API_KEY")
    engine = args.engine
    watch = engine == "gemini"
    transcribe = None
    if not segs and engine != "captions":
        if engine == "local" or (engine == "auto" and local_available()):
            transcribe = "local"
            if not local_available():
                die("--engine local needs uv.  macOS: brew install uv   other: https://docs.astral.sh/uv/getting-started/installation/")
        elif engine == "gemini" or (engine == "auto" and key):
            transcribe = "gemini"
        else:
            log("no captions; no uv for local transcription and no GEMINI_API_KEY. Continuing with metadata only.")
    if watch and not key:
        die("--engine gemini needs GEMINI_API_KEY (env or ~/.skillforge/.env). Free key: https://aistudio.google.com/apikey")

    section = parse_section(args.section) if args.section else None      # frames only; transcript stays complete
    if section and not args.frames:
        log("--section only applies to --frames; ignoring it")
    errors: list[str] = [] if local else list(caption_errors)
    whisper_backend: str | None = None
    frames: list[dict] = []
    if transcribe or watch or args.frames:
        if media is None:
            media = download_media(src, out, not (watch or args.frames), auth, args.resolution if not watch else 1280)
        is_video = media.suffix.lower() in VIDEO_EXT

        if args.frames:                                    # frames first: they must survive a transcription failure
            if is_video and section:
                # Cut the section locally: yt-dlp's ranged downloads stream through ffmpeg and crawl. A fast re-encode
                # keeps the cut frame-accurate, so frame stamps line up with the transcript.
                clip = out / "section.mp4"
                run([need("ffmpeg"), "-v", "error", "-y", "-ss", f"{section[0]:.2f}", "-i", media, "-t", f"{section[1] - section[0]:.2f}",
                     "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", clip], timeout=1800)
                frames = extract_frames(clip, out, args.frames, section[1] - section[0], args.resolution, args.no_dedup,
                                        offset=section[0])
                clip.unlink(missing_ok=True)
            elif is_video:
                frames = extract_frames(media, out, args.frames, meta.get("duration"), args.resolution, args.no_dedup)
            else:
                log("--frames ignored: source is audio only")

        audio_path = None
        def audio() -> Path:
            nonlocal audio_path
            if audio_path is None:
                audio_path = extract_audio(media, out) if is_video else media
            return audio_path

        if transcribe == "local":
            try:
                # Only a language the user asked for: YouTube's metadata language is often wrong ('ja' on an English
                # news clip, 'ar' on an English unboxing), and a forced wrong language makes Whisper translate or hallucinate.
                segs, whisper_backend = transcribe_local(audio(), whisper_language(args.lang if args.lang != "en" or args.lang_given else None))
                provenance = "local-whisper"
                if not segs:
                    errors.append(NO_SPEECH_FRAMES if frames else NO_SPEECH)
                    log(errors[-1])
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                errors.append(f"local transcription: {tail(str(exc))}")
                log(errors[-1])
                if key:
                    log("falling back to Gemini transcription because a key is available")
                    transcribe = "gemini"

        if transcribe == "gemini" or (watch and is_video):
            upload = media if (watch and is_video) else audio()
            log(f"uploading {upload.name} ({upload.stat().st_size // 1_000_000} MB) to Gemini")
            uri, name, mime = gemini_upload(upload, key)
            try:
                if transcribe == "gemini":
                    segs = parse_gemini_transcript(gemini_generate([{"file_data": {"mime_type": mime, "file_uri": uri}}, {"text": TRANSCRIBE_PROMPT}], key))
                    provenance = "gemini"
                if watch and is_video:
                    gemini_notes = gemini_generate([{"file_data": {"mime_type": mime, "file_uri": uri}}, {"text": WATCH_PROMPT}], key)
                    (out / "gemini.md").write_text(f"# Gemini observations ({GEMINI_MODEL})\n\n{gemini_notes}\n", encoding="utf-8")
            finally:
                gemini_delete(name, key)

    if media and not local and not args.keep_media:
        media.unlink(missing_ok=True)
        (out / "audio.m4a").unlink(missing_ok=True)

    (out / "source.json").write_text(json.dumps({"meta": meta, "transcript": segs, "transcript_provenance": provenance, "frames": frames,
                                                 "gemini_notes_file": str(out / "gemini.md") if gemini_notes else None, "errors": errors},
                                                indent=2, ensure_ascii=False), encoding="utf-8")
    md = write_transcript_md(meta, segs, provenance, out, has_frames=bool(frames))
    print(json.dumps({"work_dir": str(out), "transcript_md": str(md), "title": meta.get("title"), "uploader": meta.get("uploader"),
                      "duration": hms(meta["duration"]) if meta.get("duration") else None, "segments": len(segs),
                      "transcript_provenance": provenance, "whisper_backend": whisper_backend,
                      "frames": len(frames), "gemini_notes": bool(gemini_notes),
                      "errors": errors, "slug_hint": meta["slug_hint"]}, indent=2))


if __name__ == "__main__":
    main()
