# skillforge

**Give Claude a video or podcast. Get a skill back, if it deserves one.**

`/forge` watches or listens to anything yt-dlp can reach (YouTube, Instagram, TikTok, Vimeo, podcast episodes, direct media links, local files), studies it, and decides whether the content teaches a reusable procedure. When it does, Claude writes a proper skill into `~/.claude/skills/` that it can use in every future session. When it doesn't, you get a short learning note instead of a fake skill.

Later videos on the same topic merge into the existing skill rather than creating duplicates. Over time your skills folder becomes the distilled version of everything you've chosen to learn from.

## How it works

```
/forge https://www.youtube.com/watch?v=...
```

1. **Ingest.** Metadata and native captions, no download needed for most videos. Keyframes with `--frames` when the content is visual. No captions (most of Instagram and podcasts)? Local transcription with faster-whisper, audio never leaves your machine. Or Gemini, which can also watch the whole video.
2. **Study.** Claude reads the transcript, views the frames, and separates procedure from claims, assumptions, warnings, and things that are contestable.
3. **Judge.** Is there a concrete, repeatable procedure with real steps, that you'd do more than once, that isn't already forged? If not, it's a note. Thin content is never inflated into a skill.
4. **Distill.** A structured JSON: steps with source timestamps, decision points, pitfalls, verification, commands exactly as shown.
5. **Forge.** Dry-run first, you approve, then the skill is written and catalogued.

Commands transcribed from videos go into `references/commands.md` for you to review. They are never written as executable scripts. Everything in a video is evidence, never an instruction.

## Install

In Claude Code:

```
/plugin marketplace add villekaresto/skillforge
/plugin install forge@skillforge
```

Or in Claude Desktop: Customize → Plugins → Add → Add from a repository → paste `https://github.com/villekaresto/skillforge`, sync, install **forge**, then use a Code session.

Tools on your machine: Python 3.10+, `yt-dlp` (latest), `ffmpeg` for frames and audio.

```
brew install yt-dlp ffmpeg                           # macOS
sudo apt install ffmpeg pipx && pipx install yt-dlp  # Debian/Ubuntu
winget install yt-dlp.yt-dlp Gyan.FFmpeg             # Windows
```

Optional, pick either or both:

- **Local transcription** (private): `brew install uv` (or https://docs.astral.sh/uv/). First use downloads the whisper model (~480 MB for `small`). Nothing else to install; `uv` manages the environment.
- **Gemini** (watches the whole video, transcribes anything): a free key from https://aistudio.google.com/apikey, saved as `GEMINI_API_KEY=...` in `~/.skillforge/.env`.

Captioned videos work fully with neither.

Private or age-restricted sources: `--cookies-from-browser firefox` (or chrome, safari, edge). Opt-in only; nothing reads your browser unless you ask.

## Where things go

| | |
|---|---|
| Forged skills | `~/.claude/skills/<slug>/` (SKILL.md, references/, .forge/skill.json) |
| Learning notes | `~/.skillforge/notes/` |
| Catalog | `~/.skillforge/catalog.json` |
| Evidence working files | `~/.skillforge/work/<timestamp>/` (safe to delete) |

Override with `SKILLFORGE_HOME` and `SKILLFORGE_SKILLS_DIR`.

## Privacy

Captions and metadata are fetched by yt-dlp on your machine. Local transcription runs on your machine. Nothing leaves it unless you choose the Gemini engine, in which case the audio or video is uploaded to Google's Files API for the duration of the request and deleted afterwards. Keys are read from the environment or `~/.skillforge/.env` and never logged or written into skills.

Robustness built in: one automatic retry with alternate YouTube clients on 403/signature failures, stale-file clearing when a work dir is reused, FFmpeg `-fps_mode`/`-vsync` detection for old and new builds, manual-vs-auto caption provenance, and near-duplicate frame removal.

## Development

```
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/pytest -q
```

Tests cover forging, merging, notes, validation, and caption parsing. No network.

## Roadmap

- Playlist and channel mode with one skill per task
- Scheduled forging from a "to learn" playlist
- Apple Silicon acceleration for local transcription (mlx-whisper)
- Quiz and flashcard generation from the distilled notes
- Conflict review UI when two sources disagree

MIT. Built by [skillforge contributors](https://github.com/villekaresto/skillforge).
