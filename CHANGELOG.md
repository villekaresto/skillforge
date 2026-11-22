# Changelog

Generated from the git history. Dates are 2026-11-07 unless noted; all work happened in one day of QA-driven iteration.

## 0.5.1

Dated 2026-11-22. Checks from the 0.5.0 retro, so its mistakes cannot recur.

- `forge.py --lint-all` lints every forged skill and lens on disk as a re-forge would render it, and exits 1 if any fail. Skills written before the lint gate, or with `--allow-lint`, no longer go unnoticed.
- Two skill or lens files with the same slug in one run are refused before anything is written. Notes are exempt: notes on one topic share a slug, one dated section per source.
- Tests now enforce that the plugin version matches the newest CHANGELOG entry, that every flag of `forge.py`, `ingest.py` and `playlist.py` is in SKILL.md's Flags reference, and that no tracked file contains a home-directory path.
- SKILL.md gains a `playlist.py` flags table (`--sleep` was undocumented). `CLAUDE.md` records the test command, the rules the tests enforce and the PR workflow.

## 0.5.0

Dated 2026-11-22.

- **Lint blocks writes.** A skill or lens with any `# lint:` finding is no longer written: `forge.py` exits 2, lists each finding per file, and writes nothing. Before, lint only printed in `--dry-run`, so an over-budget skill (1,283 words, steps over 45 words, pitfalls without `symptom → cause → fix`) could still be written. Notes are not linted.
- `--allow-lint` writes anyway. Each finding goes to stderr and into `lint_overridden` in the JSON result, so a bypass stays visible. It is independent of `--allow-drop`.
- A run with several distilled files lints all of them before writing any, so a refusal never leaves half a run on disk.
- The dry-run ends its lint block with `# lint: N finding(s); a write will refuse without --allow-lint`.

## 0.4.0

- **Lens**, a third verdict between skill and note, for sources that teach principles rather than a procedure. A lens forges a skill folder whose SKILL.md says when to apply it and lists 5–15 timestamped principles, the mistakes the speakers warn about, and 2–3 worked examples. Lenses merge by topic with the same citation, drop-guard and attribution rules as skills, and `supersedes_notes` moves absorbed sources out of their note topics.
- **Shareable export.** `--export` leaves out `references/frames/` and every `apply_to_me` section, so zips are small and keep profile context on the machine.
- `forge.py --export`, a fix so ingest never reads stdin, and a refined duplicate-source guard (one skill plus one note per video per run).

## 0.3.0

- **User profile.** `~/.skillforge/profile.md` (or `SKILLFORGE_PROFILE`) holds your role, tools with versions, core work, context and "not relevant" topics. `/forge` reads it first (`forge.py --profile`). The judge's "central to your work" test and `apply_to_me` use it, and an empty `apply_to_me` is flagged when a profile exists. Only `references/profile.example.md`, with placeholders, ships; `profile.md` is git-ignored.
- **Version-mismatch pitfalls.** Sources record `versions_shown`. When a major version differs from the profile's, forge.py adds a `symptom → cause → fix` pitfall to the skill (or note), regenerated on every forge so re-runs stay idempotent.
- **Privacy.** Repository history rewritten. Examples, tests and docs are neutral; QA docs keep only aggregate numbers; author and committer are `skillforge`.

## 0.2.0

**New**
- **Playlist mode** (`scripts/playlist.py`, `scripts/clusters.py`). It ingests a playlist or channel and proposes topic clusters (TF-IDF over titles, chapters, tags and transcript, average linkage). Claude turns each cluster into one skill per *task*, with every step citing the videos that show it, and everything goes through one combined dry-run. Re-running the same folder extends it.
- **Multi-source skills in one go.** `forge.py` accepts `"sources": [...]` with per-source `work_dir`, `useless_frames` and `confidence`, plus several distilled files in one call. A combined dry-run simulates earlier files, so a second note on a topic shows as an append.
- **Apple Silicon transcription.** `whisper_mlx.py` runs mlx-whisper (about 70× real time, 7× faster than faster-whisper on the same 10-minute video) and falls back to faster-whisper automatically. `SKILLFORGE_WHISPER_BACKEND` forces one or the other.
- **Tighter skills.** SKILL.md's "Write it tight" rules: imperative steps under 45 words, at most 12 steps, alternatives as decision points, `symptom → cause → fix` pitfalls, checkable verification, about 900 words. `forge.py --dry-run` prints `# lint:` warnings for each.
- **`--section START-END`**: frames from one chapter of a long video. The video is downloaded at the capped quality and cut locally, frame-accurately, so the frames line up with the full transcript.
- **`forge.py --adopt-note FILE... --topic SLUG`** moves pre-0.1.3 per-video notes into topic notes.
- Distill rules from a skeptical review of the best skills: one method per path, placeholders instead of demo data, every step an action, optional actions labelled, product version in the trigger. The new lint also flags presenter demo values.
- `docs/architecture.md`, a README rewritten as a five-minute quickstart, and this changelog.

**Fixed**
- Citations naming unknown sources are refused instead of silently losing their label, and a claim can name the video it came from.
- The drop guard follows `(source, timestamp)` citations, so a merged skill can be reworded tighter without losing another source's steps.
- One video can no longer be forged twice in one run (cluster file plus solo note).
- Captions that are mostly `[Music]` are flagged.
- Frames-only downloads are capped at 480p/30 fps (720p for `--resolution` above 854). A 5-hour stream was 3+ GB.
- A login hint is shown for any site that needs an account (Vimeo).
- Spare frame slots fill the largest time gaps; a "starting soon" loop no longer eats the frame budget.
- Bare media links keep the URL you gave (not the redirect mirror) and get their duration from ffprobe.
- Rewriting a skill without its original evidence keeps the frames already in `references/`.
- yt-dlp timeouts end with a message instead of a traceback.
- `--list` shows `note_topics`.

## 0.1.3

- Captions are retried once after 30 s before falling back to local Whisper, and `info.json` is cached per video for 6 hours.
- Useful frames are copied into `references/frames/<source>/`; the new `useless_frames` field leaves the rest out.
- In merged skills, pitfalls and decision points cite their source the way steps do.
- Notes: one file per topic slug, with a dated section per source. Pre-0.1.3 per-video notes are migrated on re-forge.
- A re-studied source refreshes its confidence.
- QA artifacts moved out of the repo, docs moved under `docs/qa/`, and personal data removed from tracked files.

## 0.1.2

Twenty-two fixes from an unattended QA run over 60 real sources:

- **VTT parsing:** cue settings and YouTube's repeated rolling lines are dropped.
- **SKILL.md frontmatter** is quoted as YAML. A `:` in a description made skills unloadable.
- **Re-forging unchanged content** is a no-op.
- **Playlist URLs** are listed instead of silently ingesting the last video under the playlist's title.
- **Local Whisper** decodes with ffmpeg rather than PyAV; the earlier PyAV pin had no wheel, so local transcription never ran. Caption download failures (HTTP 429) are reported and retried.
- **Whisper language:** the language code is normalised, the original caption track wins over YouTube's machine translation, and Whisper is no longer forced to use wrong YouTube metadata languages.
- **Merges:** steps are attributed to sources (`S1`/`S2`, with `refs` for steps several videos show), and updates that would drop another source's steps are refused.
- **Frames:** sampled after transitions settle, kept spread over time, and blank or fade-to-black frames are dropped. Short clips get fewer frames.
- **Notes** keep steps, pitfalls, verification, commands and caveats, and never overwrite another source's note.
- **No speech found** by Whisper produces a warning, and slug hints transliterate accents.
- **SKILL.md judge** was tightened to reject generic basics, then loosened for ready-to-run playbooks of the user's own core work, with guidance on version mismatches, non-English UI labels, spoken-only commands and long transcripts.

## 0.1.1

- Local Whisper fix attempt (PyAV pin), hybrid scene-plus-uniform frames, and non-fatal transcription errors.

## 0.1.0

- First release: `ingest.py` (yt-dlp captions, frames, faster-whisper or Gemini), the Claude study/judge/distill workflow in SKILL.md, and `forge.py` (dry-run, write, merge, notes, catalog).
