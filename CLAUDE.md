# skillforge

A Claude Code plugin: `skills/forge/` ingests a video or podcast (`scripts/ingest.py`, `scripts/playlist.py`) and turns
Claude's distilled JSON into a skill, lens or note (`scripts/forge.py`). See `docs/architecture.md`.

## Tests

Run the suite the way CI does (Python 3.12, ffmpeg on PATH):

```
uv run --no-project --python 3.12 --with pytest --with numpy --with pyyaml pytest -q -rs
```

CI runs on ubuntu, macOS and Windows on every push and PR. Tests drive the scripts through the CLI (`run_forge`), with
`SKILLFORGE_HOME` and `SKILLFORGE_SKILLS_DIR` pointed at a temp dir. Test fixtures must be lint-clean, or writes are refused.

## Rules the tests enforce

- A skill or lens with lint findings is not written without `--allow-lint`.
- `.claude-plugin/plugin.json`'s version equals the newest `## x.y.z` heading in `CHANGELOG.md`.
- Every flag of `forge.py`, `ingest.py` and `playlist.py` appears in the Flags reference in `skills/forge/SKILL.md`.
- No tracked file contains a machine-specific home-directory path; use `~` or a placeholder.

## Workflow

- Grill before building: agree on scope before writing code.
- Work on a branch, never directly on `main`. Open a PR whose body has a short summary, test evidence (local run and CI
  link) and merge danger (one-way or two-way door, blast radius). Rebase-merge, then delete the branch.
- Behaviour changes bump the minor version; fixes and checks bump the patch. Add the CHANGELOG entry in the same PR.
- Do not commit or push until the maintainer says so.
- Before every commit, scan the diff for identifying details about the maintainer or their machine. This repo is public.
