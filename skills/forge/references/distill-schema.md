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

  "slug": "ERP-studio-automated-action",
  "name": "Create an automated action in ERP Studio",
  "description": "Build a Studio automated action in ERP 17 that reacts to a record change. Use when the user wants to automate a field update, send an email on state change, or asks how Studio automations work.",
  "summary": "One paragraph: what this skill does and when it pays off.",
  "when_to_use": ["phrasing a user might say", "another trigger"],
  "prerequisites": ["ERP 17 Enterprise with Studio", "admin rights"],
  "procedure": [
    {"step": "Open Studio on the target model", "detail": "Settings > Studio, pick the model from the view.", "timestamp": "1:42"},
    {"step": "Add an automation", "detail": "Automations tab, New, set trigger = On update.", "timestamp": "2:10"}
  ],
  "decision_points": [
    {"when": "Trigger must fire only once per record", "then": "use 'On creation' instead of 'On update' and guard with a boolean field"}
  ],
  "pitfalls": ["Automations run as the triggering user; permission errors look like silent failures (6:30)."],
  "verification": ["Create a test record and confirm the follow-up field changes within seconds."],
  "commands": [
    {"description": "Server action python", "language": "python", "command": "for rec in records:\n    rec.write({'x_flag': True})", "timestamp": "4:05"}
  ],

  "key_claims": [{"claim": "Studio automations replace most custom module code for simple rules", "timestamp": "0:55"}],
  "assumed_knowledge": ["what a model and a field are in ERP"],
  "contestable": ["Speaker says automations never affect performance; untrue for high-volume models (8:12)."],
  "apply_to_me": ["Replace the manual container-status email with an On-update automation."]
}
```

## Field rules

- `verdict`, `source` always required. `source.url` required.
- When `skill_worthy` is true: `slug`, `name`, `description`, `procedure` (3+ steps) required.
- When false: `summary`, `key_claims`, `apply_to_me` are what make the learning note useful. `slug` optional (used for the note filename).
- Timestamps as `M:SS` or `H:MM:SS`, matching a transcript cue time (not a chapter time).
- `procedure[].source` (optional): the `id` (or url) of the source a step comes from. Leave it out for a single-source skill.
  On a merge, forge.py fills it in for you: steps carried over unchanged keep the earlier source, new or reworded steps get the
  incoming source. Set it yourself when you reword an old step but its evidence is still the old video. Steps render as `(S2 1:14)`.
- `procedure[].refs` (optional, merges): a step shown in more than one source lists each one,
  `"refs": [{"source": "<id>", "timestamp": "0:43"}, {"source": "<other id>", "timestamp": "0:38"}]`, rendered `(S1 0:43; S2 0:38)`.
  Use it instead of writing source names into `timestamp`.
- `verdict.confidence` on a merge is your confidence in the merged skill, and is also recorded as the new source's confidence.
- `commands[].command` verbatim from the source. No invented flags.
- On a merge, `sources` is managed by forge.py; still pass the single new `source`.
- Keep `description` under 300 characters; it is what makes the skill trigger later.
