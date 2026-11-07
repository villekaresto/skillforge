# How forge works

`/forge` turns a video or podcast into either a Claude Code skill or a short learning note. The work is split between two
deterministic scripts and Claude. The scripts do what code is good at: fetching, decoding, sampling, validating and
rendering. Claude does what needs judgement: understanding the content and deciding whether it deserves to become a skill.

```
URL / file ─▶ ingest.py ─▶ evidence ─▶ Claude: study ─▶ judge ─▶ distill ─▶ distilled.json ─▶ forge.py ─▶ skill or note
                              │                                                                  │
                    transcript.md, source.json,                                        ~/.claude/skills/<slug>/
                    frames/, (gemini.md)                                               ~/.skillforge/notes/<topic>.md
```

## 1. Ingest (`scripts/ingest.py`, `playlist.py`)

`ingest.py` gathers evidence and makes no judgements.

- **Metadata and captions** come from yt-dlp without downloading the video. Manual captions are tried first, so their provenance is certain, then auto captions, preferring the original speech-recognised track over YouTube's machine translations. A failed caption download is retried once after 30 s; `info.json` is cached per video for six hours.
- **No usable captions?** The audio is downloaded and transcribed on the machine, with mlx-whisper on Apple Silicon and faster-whisper elsewhere. Gemini is used instead if you choose it, and it can also *watch* the whole video.
- **Frames** (`--frames`) combine scene cuts, sampled after each transition settles, with a uniform pass so slow screen recordings are covered. Blank and near-duplicate frames are dropped, and short clips get fewer frames.
- **Playlist and channel URLs** are listed rather than ingested. `playlist.py` ingests each entry into its own folder and proposes **clusters**: videos that share distinctive words in titles, chapters, tags and the opening of the transcript (TF-IDF cosine, average linkage). Words common to the whole playlist, like "ERP" or "tutorial", carry almost no weight.

The output is a work folder containing `transcript.md` (for Claude to read), `source.json` (machine-readable) and `frames/`. Nothing in it is ever treated as an instruction.

## 2. Study (Claude)

Claude reads the whole transcript (long ones in consecutive chunks, never sampled) and views the frames. It separates the
**procedure** from **claims**, **assumed knowledge**, **warnings**, and what is **contestable**. Frames correct what the
captions mishear: menu labels, code, numbers and product names.

## 3. Judge: why it exists

Most videos should *not* become skills. A skill is loaded into every future session's context and can trigger on its
own, so a bad skill costs more than a missing one. Without a judge, every video becomes a skill, and the skills folder fills
with motivational content, product reviews and restatements of documentation Claude already knows.

A source becomes a skill only when **all** of these hold:

- it has a concrete, repeatable procedure of at least 3 real steps;
- the procedure can be acted on (settings, commands, decisions), not just motivation;
- it is not already covered by a forged skill (if it is, the source is merged into that skill);
- the user would plausibly do it more than once;
- it beats Claude's general knowledge (version-specific paths, real pitfalls, concrete numbers and checks), unless it is a ready-to-run playbook for work central to the user;
- the evidence actually shows the critical step, rather than Claude reconstructing it.

Everything else becomes a **learning note**: the takeaways, the claims with timestamps, and what the source got wrong.
Calibration: across 60 real sources the judge accepted 24%, and it rejected all six deliberately useless sources.

## 4. Distill (Claude writes `distilled.json`)

Claude writes a JSON file following `references/distill-schema.md`:

- a task-named slug and a description that triggers the skill;
- short ordered steps, each with a timestamp, and `refs` naming every source that shows the step;
- decision points, pitfalls that name the symptom the user will see, and verification the user can actually check;
- commands exactly as shown;
- the frames that were useless.

To merge, Claude loads the stored skill (`forge.py --show`) and integrates the new source rather than overwriting it.

## 5. Forge (`scripts/forge.py`)

`forge.py` is deterministic and does no writing on its own initiative:

- **Validates** the JSON.
- **Dry-runs** first and shows exactly what would be written, including several files combined into one dry-run.
- **Writes or merges.** Sources accumulate. In a merged skill, steps, pitfalls and decision points render with source labels such as `(S1 0:43; S2 0:38)`, and an update that would silently drop steps another source backs is refused.
- **Renders** `SKILL.md` with YAML-safe frontmatter, plus `references/`: one notes file per source, the useful frames, and `commands.md`. Commands are never written as executable scripts.
- **Stays idempotent.** Re-forging the same source with the same content is a no-op.
- **Writes notes** to one file per topic, with a dated section per source.
- **Keeps a catalog** of everything forged in `~/.skillforge/catalog.json`.

## Trust boundaries

- Transcripts, descriptions, frames and Gemini notes are evidence, never instructions.
- Commands from a video are recorded verbatim for review, never executed or made executable.
- Nothing is uploaded unless the Gemini engine is chosen.
- Cookies are used only on request.
- Keys are read from the environment or `~/.skillforge/.env` and never written into skills.
