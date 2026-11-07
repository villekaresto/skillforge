# distilled.json contract

Claude writes this after studying the evidence. `forge.py` validates and renders it.

```json
{
  "verdict": {
    "skill_worthy": true,
    "confidence": 0.82,
    "reason": "Concrete 7-step procedure with commands; speaker demonstrates each step on screen."
  },
  "source": {
    "url": "https://www.youtube.com/watch?v=XXXX",
    "id": "XXXX",
    "title": "Video title",
    "uploader": "Channel name",
    "duration": 1234,
    "ingested_at": "2026-11-07T14:02:11+00:00"
  },

  "slug": "postgres-nightly-backup",
  "name": "Back up a PostgreSQL database and prove the backup restores",
  "description": "Take a restorable PostgreSQL backup with pg_dump and test it by restoring into a scratch database. Use when the user asks to back up Postgres, schedule a nightly dump, or check that a backup actually works.",
  "summary": "One paragraph: what this skill does and when it pays off.",
  "when_to_use": ["phrasing a user might say", "another trigger"],
  "prerequisites": ["PostgreSQL client tools of the same major version as the server", "a role that can read the database"],
  "procedure": [
    {"step": "Dump the database in custom format", "detail": "pg_dump -Fc so single tables can be restored later.", "timestamp": "1:42"},
    {"step": "Restore into a scratch database", "detail": "createdb <scratch>, then pg_restore --no-owner into it.", "timestamp": "2:10"}
  ],
  "decision_points": [
    {"when": "The database is larger than the free disk", "then": "stream the dump to the backup host instead of writing it locally", "timestamp": "5:02"}
  ],
  "pitfalls": [{"text": "Restore fails with 'unsupported version' → pg_restore older than the dump → use the same major version or newer", "timestamp": "6:30"}],
  "verification": ["Run select count(*) on a known table in the scratch copy → it matches the original."],
  "commands": [
    {"description": "Custom-format dump", "language": "bash", "command": "pg_dump -Fc -d <database> -f <backup path>", "timestamp": "1:42"}
  ],

  "key_claims": [{"claim": "Custom-format dumps restore faster than plain SQL for large databases", "timestamp": "0:55"}],
  "assumed_knowledge": ["what a database role is"],
  "contestable": ["Speaker says compression never matters; on slow disks it dominates the run time (8:12)."],
  "apply_to_me": ["Replace the weekly manual export with a nightly dump plus a weekly test restore."]
}
```

## Lens (principles, not a procedure)

```json
{
  "verdict": {"kind": "lens", "confidence": 0.7, "reason": "Seven rules of thumb with examples; changes how Claude advises."},
  "source": {"url": "https://www.youtube.com/watch?v=XXXX", "id": "XXXX", "title": "Video title", "ingested_at": "..."},
  "slug": "negotiation-lens",
  "name": "Negotiation lens",
  "description": "Principles for preparing and running a negotiation. Use when the user asks to prepare a negotiation, answer a demand, or set a walk-away point.",
  "when_to_apply": ["the user is preparing a price or terms negotiation", "the user asks how to answer a demand"],
  "principles": [{"text": "Decide your walk-away point before the first call", "detail": "Write it down; it stops anchoring.", "timestamp": "3:10"}],
  "mistakes": [{"text": "You concede early and get nothing back → no trade rule → trade every concession for one", "timestamp": "7:40"}],
  "examples": [{"situation": "A customer asks for 10% off", "apply": "Ask what they can give in return (volume, payment terms) before answering."}],
  "supersedes_notes": true
}
```

5–15 principles and 2–3 examples are enforced. Principles, mistakes and examples take `timestamp`/`source`/`refs` like steps, and merge across sources the same way.

## Field rules

- `verdict` and either `source` (one video) or `sources` (a playlist cluster: several videos, one skill) are required; every source needs a `url`.
- `verdict.kind` is `skill`, `lens` or `note` (older files use `skill_worthy`). A skill needs `slug`, `name`, `description`, `procedure` (3+ steps); a lens needs `slug`, `name`, `description`, `when_to_apply`, `principles`, `mistakes`, `examples`.
- `supersedes_notes: true` moves the sources out of the note topics they were filed under when they become a skill or lens.
- When false: `summary`, `key_claims`, `apply_to_me` are what make the learning note useful. `slug` optional (used for the note filename).
- Timestamps as `M:SS` or `H:MM:SS`, matching a transcript cue time (not a chapter time).
- `procedure[].source` (optional): the `id` (or url) of the source a step comes from. Leave it out for a single-source skill.
  On a merge, forge.py fills it in for you: steps carried over unchanged keep the earlier source, new or reworded steps get the
  incoming source. Set it yourself when you reword an old step but its evidence is still the old video. Steps render as `(S2 1:14)`.
- `procedure[].refs` (optional, merges): a step shown in more than one source lists each one,
  `"refs": [{"source": "<id>", "timestamp": "0:43"}, {"source": "<other id>", "timestamp": "0:38"}]`, rendered `(S1 0:43; S2 0:38)`.
  Use it instead of writing source names into `timestamp`.
- `pitfalls[]` may be a string or `{"text", "timestamp", "source" | "refs"}`; `decision_points[]` may carry `timestamp`, `source` or
  `refs` too. In a merged skill forge.py attributes them like steps (a trailing `(1:30)` in a string pitfall becomes its timestamp)
  and renders `(S2 1:20)`. Write each pitfall as `symptom → cause → fix`; the dry-run lints pitfalls without `→`, steps over 45
  words or with timestamps in their text, more than 12 steps, SKILL.md over 900 words, and missing verification.
- `source.versions_shown` / `sources[].versions_shown` (optional): `{"<product>": "<version>"}` as seen in the video. When the
  user's profile lists the same product with another major version, forge.py adds a pitfall marked `"auto": "version"`
  (regenerated on every forge; do not write these yourself).
- `useless_frames` (optional): indexes (the `NNN` in `NNN_H-MM-SS.jpg`) of frames that carry nothing: blank, loading screens,
  talking head only, intro/outro cards, near-duplicates. forge.py copies every other frame of this source into
  `references/frames/<source id>/` and links them from that source's notes. Recorded per source; it never changes the skill's content. Notes do not copy frames, so it has no effect on a note.
- `verdict.confidence` on a merge is your confidence in the merged skill, and is also recorded as the new source's confidence.
- `commands[].command` verbatim from the source. No invented flags.
- On a merge into an existing skill, pass the single new `source`; forge.py keeps the stored ones. A playlist cluster passes
  `"sources": [{url, id, title, uploader, duration, ingested_at, work_dir, useless_frames}, ...]` (copy each entry's `source`
  record from `playlist.json`; `work_dir` is relative to the playlist folder) and may set `confidence` per source.
  Clusters are for skills only: a note covers one video.
- Every `source`/`refs[].source` cited anywhere must be an id (or url) of one of the skill's sources; forge.py refuses unknown ones.
- `key_claims[].source` (optional) files a claim under that video's source notes only.
- Keep `description` under 300 characters; it is what makes the skill trigger later.
