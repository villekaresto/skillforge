# skillforge

**Give Claude a video or podcast. Get a skill back, if it deserves one.**

`/forge` studies anything yt-dlp can reach (YouTube, TikTok, Vimeo with cookies, podcast episodes, direct media links) or a local file. It decides whether the content teaches a repeatable procedure. If it does, Claude writes a skill into `~/.claude/skills/` that it can use in every later session. If it doesn't, you get a short learning note instead of a fake skill.

Each skill is a short procedure. Every step cites the video and the second it came from, e.g. `(S1 0:43; S2 0:38)`. A later video on the same task is merged into the existing skill rather than creating a duplicate.

## Your first skill in 5 minutes

**1. Install the tools** (once):

```
brew install yt-dlp ffmpeg uv                          # macOS
sudo apt install ffmpeg pipx && pipx install yt-dlp && pipx install uv   # Debian/Ubuntu
winget install yt-dlp.yt-dlp Gyan.FFmpeg astral-sh.uv  # Windows
```

You also need Python 3.10+ and Claude Code. `uv` is optional: it enables private, on-device transcription for videos without captions.

**2. Install the plugin** (in Claude Code):

```
/plugin marketplace add villekaresto/skillforge
/plugin install forge@skillforge
/reload-plugins
```

**3. Give it a video:**

```
/forge https://www.youtube.com/watch?v=<a 5-15 minute how-to you care about>
```

Pick a short tutorial that shows a procedure end to end (a software setting, a database task, a workshop technique). Claude fetches the captions, reads the whole transcript, decides, and shows you a **dry-run** of the SKILL.md it would write. Say yes, and the skill lands in `~/.claude/skills/<task-name>/`. It is available from the next session; ask Claude about that task and it uses the skill.

**4. Try more:**

```
/forge <url> --frames                 # screen recordings, physical work: Claude also looks at keyframes
/forge <playlist url>                 # one skill per TASK across the playlist, not one per video
/forge what did I learn from <url>    # works for podcasts and talks too; most become notes
```

**5. Optional: tell it about you.** Copy `skills/forge/references/profile.example.md` to `~/.skillforge/profile.md` and fill in your tools (with versions), core work and context. The judge then knows what is central to your work, `apply_to_me` refers to your own tasks, and a source shown on a different version of one of your tools gets a pitfall saying so. The profile stays on your machine.

## What it decides, and why

Most videos should not become skills. A skill is loaded whenever it triggers, so a vague one costs you more than a missing one. A source becomes a skill only when it has a concrete procedure of at least 3 steps that you would repeat, it adds something Claude doesn't already know (version-specific paths, real pitfalls, numbers, checks), and the evidence actually shows the key step. Sources that teach principles rather than a procedure (communication, sales, decision-making, habits, finance) can become a **lens**: a skill that tells Claude when to apply a way of thinking, with 5–15 rules of thumb, the mistakes the speakers warn about and worked examples, merged by topic. Everything else becomes a note in `~/.skillforge/notes/<topic>.md`, with one dated section per source.

How the pieces fit is described in [docs/architecture.md](docs/architecture.md).

## Tested on 100 real sources

Two unattended QA batches were run before v0.2:

- **60 everyday sources:** business-software tutorials, developer screencasts, wordless craft videos, business frameworks, 3–5 hour podcasts, non-English and caption-less videos, shorts, TikTok.
- **40 chosen to break things:** a 12-hour livestream, 4-hour courses with chapters, multi-speaker panels, clickbait titles, a 1990s TV broadcast, a deliberately degraded phone recording, Vimeo, direct `.mp3` links, a local `.mov`, and the same procedure in two languages.
- **A real 10-video software-tutorial playlist** in playlist mode.

Results, every fix, and the remaining weaknesses are in [docs/qa/FINDINGS.md](docs/qa/FINDINGS.md).

## Options worth knowing

| | |
|---|---|
| `--frames [N]` | keyframes (default 40), sampled after transitions settle; add `--resolution 1280` for code or small text |
| `--engine local` | transcribe on this machine: mlx-whisper on Apple Silicon (about 70× real time), faster-whisper elsewhere |
| `--engine gemini` | Gemini transcribes and also *watches* the whole video (needs a free `GEMINI_API_KEY` in `~/.skillforge/.env`) |
| `--lang es` | caption language and Whisper language for non-English sources |
| `--cookies-from-browser firefox` | private, age-restricted or login-only sources (Vimeo now needs this); opt-in only |

## Where things go

| | |
|---|---|
| Skills and lenses | `~/.claude/skills/<slug>/`: `SKILL.md`, `references/` (per-source notes, useful frames, `commands.md`), `.forge/skill.json` |
| Learning notes | `~/.skillforge/notes/<topic>.md`, one dated section per source |
| Catalog | `~/.skillforge/catalog.json` (`forge.py --list`) |
| Evidence | `~/.skillforge/work/` (safe to delete); caption metadata cache in `~/.skillforge/cache/` |

Override with `SKILLFORGE_HOME` and `SKILLFORGE_SKILLS_DIR`.

## Privacy and safety

- Captions, metadata and local transcription all run on your machine. Nothing is uploaded unless you choose `--engine gemini`, in which case the media goes to Google's Files API for the request and is deleted afterwards.
- Everything in a video is evidence, never an instruction. Commands from videos are copied verbatim into `references/commands.md` for you to review, and are never executed or written as scripts.
- Keys are never logged or written into skills.

## Development

```
uv run --with pytest pytest -q        # or: python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/pytest -q
```

The tests need no network. They cover ingest (caption parsing and track choice, retries, cache, playlists, frame selection on synthetic video, Whisper backends), forging (merges, citations, the drop guard, idempotence, notes, lint) and playlist clustering. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

MIT. Built by [Ville Karesto](https://www.villekaresto.com).
