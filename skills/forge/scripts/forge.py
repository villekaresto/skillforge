#!/usr/bin/env python3
"""Turn a distilled JSON file (written by Claude after studying the evidence) into a Claude Code skill.

  forge.py distilled.json --dry-run      print the SKILL.md that would be written
  forge.py distilled.json                write/merge the skill, update the catalog
  forge.py --list                        show everything forged so far
  forge.py --show SLUG                   print a skill's stored source JSON

Not skill-worthy? A learning note is filed under ~/.skillforge/notes instead.
Commands found in videos are written to references/commands.md, never as executable scripts.
See references/distill-schema.md for the JSON contract.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("SKILLFORGE_HOME", Path.home() / ".skillforge"))
SKILLS_DIR = Path(os.environ.get("SKILLFORGE_SKILLS_DIR", Path.home() / ".claude" / "skills"))
NOTES_DIR = HOME / "notes"
CATALOG = HOME / "catalog.json"
REQUIRED = {"verdict", "source"}


def die(msg: str, code: int = 2) -> None:
    print(f"[forge] {msg}", file=sys.stderr)
    sys.exit(code)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not s or len(s) > 64:
        die(f"bad slug {text!r}: lowercase letters, digits, hyphens, max 64")
    return s


def load_catalog() -> dict:
    if CATALOG.is_file():
        return json.loads(CATALOG.read_text(encoding="utf-8"))
    return {"skills": {}, "notes": []}


def save_catalog(cat: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")


def today() -> str:
    return dt.date.today().isoformat()


# --------------------------------------------------------------------------- rendering

def render_skill_md(d: dict) -> str:
    name = d["name"]
    desc = d["description"].replace("\n", " ").strip()
    out = ["---", f"name: {d['slug']}", f"description: {desc}", "---", "", f"# {name}", ""]
    if d.get("summary"):
        out += [d["summary"].strip(), ""]
    if d.get("when_to_use"):
        out += ["## When to use", *[f"- {t}" for t in d["when_to_use"]], ""]
    if d.get("prerequisites"):
        out += ["## Prerequisites", *[f"- {p}" for p in d["prerequisites"]], ""]
    if d.get("procedure"):
        out.append("## Procedure")
        for i, step in enumerate(d["procedure"], 1):
            line = f"{i}. **{step['step']}**"
            if step.get("detail"):
                line += f" {step['detail']}"
            if step.get("timestamp"):
                line += f" _(source {step['timestamp']})_"
            out.append(line)
        out.append("")
    if d.get("decision_points"):
        out.append("## Decision points")
        for dp in d["decision_points"]:
            out.append(f"- **{dp['when']}** → {dp['then']}")
        out.append("")
    if d.get("pitfalls"):
        out += ["## Pitfalls", *[f"- {p}" for p in d["pitfalls"]], ""]
    if d.get("verification"):
        out += ["## How to verify it worked", *[f"- {v}" for v in d["verification"]], ""]
    if d.get("commands"):
        out += ["## Commands", "See `references/commands.md` for every command shown in the source, with timestamps. "
                "Review before running; they were transcribed from a video, not tested here.", ""]
    out += ["## Sources"]
    for s in d["sources"]:
        out.append(f"- [{s.get('title') or s['url']}]({s['url']}) by {s.get('uploader') or 'unknown'}, ingested {s['ingested_at'][:10]}"
                   f" (confidence {s.get('confidence', '?')})")
    out += ["", "Notes with timestamps for each source live in `references/`.", ""]
    return "\n".join(out)


def render_source_notes(d: dict, src: dict) -> str:
    out = [f"# Source notes: {src.get('title') or src['url']}", "", f"- URL: {src['url']}", f"- By: {src.get('uploader') or 'unknown'}",
           f"- Ingested: {src['ingested_at']}", f"- Verdict: {d['verdict']['reason']}", ""]
    if d.get("key_claims"):
        out += ["## Key claims", *[f"- [{c.get('timestamp', '')}] {c['claim']}" for c in d["key_claims"]], ""]
    if d.get("assumed_knowledge"):
        out += ["## What the speaker assumes you know", *[f"- {a}" for a in d["assumed_knowledge"]], ""]
    if d.get("contestable"):
        out += ["## Contestable or unverified", *[f"- {c}" for c in d["contestable"]], ""]
    if d.get("apply_to_me"):
        out += ["## Worth trying in my context", *[f"- {a}" for a in d["apply_to_me"]], ""]
    return "\n".join(out) + "\n"


def render_commands(d: dict) -> str:
    out = ["# Commands shown in sources", "", "Transcribed from video/audio. Review before running.", ""]
    for c in d.get("commands", []):
        out += [f"## {c.get('description', 'command')} _({c.get('timestamp', '')})_", "", "```" + (c.get("language") or ""),
                c["command"].rstrip(), "```", ""]
    return "\n".join(out)


def render_note(d: dict, src: dict) -> str:
    v = d["verdict"]
    out = [f"# {src.get('title') or src['url']}", "", f"- URL: {src['url']}", f"- By: {src.get('uploader') or 'unknown'}",
           f"- Date: {today()}", f"- Skill-worthy: no (confidence {v.get('confidence')})", f"- Why: {v['reason']}", ""]
    if d.get("summary"):
        out += ["## Takeaway", d["summary"].strip(), ""]
    if d.get("key_claims"):
        out += ["## Key points", *[f"- [{c.get('timestamp', '')}] {c['claim']}" for c in d["key_claims"]], ""]
    if d.get("apply_to_me"):
        out += ["## Worth trying", *[f"- {a}" for a in d["apply_to_me"]], ""]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- forging

def validate(d: dict) -> None:
    missing = REQUIRED - d.keys()
    if missing:
        die(f"distilled JSON missing keys: {sorted(missing)}")
    if "skill_worthy" not in d["verdict"] or "reason" not in d["verdict"]:
        die("verdict needs skill_worthy (bool) and reason (str)")
    if d["verdict"]["skill_worthy"]:
        for k in ("slug", "name", "description", "procedure"):
            if not d.get(k):
                die(f"skill-worthy output needs {k!r}")
    if "url" not in d["source"]:
        die("source needs url")


def forge(d: dict, dry_run: bool) -> None:
    validate(d)
    src = dict(d["source"])
    src.setdefault("ingested_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    src["confidence"] = d["verdict"].get("confidence")
    cat = load_catalog()

    if not d["verdict"]["skill_worthy"]:
        slug = slugify(d.get("slug") or re.sub(r"[^a-z0-9]+", "-", (src.get("title") or "note").lower())[:50])
        path = NOTES_DIR / f"{today()}-{slug}.md"
        text = render_note(d, src)
        if dry_run:
            print(f"# would write learning note {path}\n\n{text}")
            return
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        cat["notes"].append({"date": today(), "url": src["url"], "title": src.get("title"), "file": str(path), "reason": d["verdict"]["reason"]})
        save_catalog(cat)
        print(json.dumps({"written": "note", "path": str(path)}, indent=2))
        return

    slug = slugify(d["slug"])
    skill_dir = SKILLS_DIR / slug
    store = skill_dir / ".forge" / "skill.json"
    existing = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else None

    # Sources accumulate; everything else is the latest distilled (Claude merges content before calling us).
    sources = (existing or {}).get("sources", [])
    if not any(s["url"] == src["url"] for s in sources):
        sources.append(src)
    merged = {**d, "sources": sources, "forged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "version": ((existing or {}).get("version", 0) + 1)}
    merged.pop("source", None)

    skill_md = render_skill_md(merged)
    if dry_run:
        print(f"# would {'update' if existing else 'create'} {skill_dir}  (version {merged['version']})\n")
        print(skill_md)
        return

    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / ".forge").mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    note_name = f"source-{re.sub(r'[^a-z0-9]+', '-', (src.get('id') or src['url']).lower()).strip('-')[:40]}.md"
    (skill_dir / "references" / note_name).write_text(render_source_notes(merged, src), encoding="utf-8")
    if merged.get("commands"):
        (skill_dir / "references" / "commands.md").write_text(render_commands(merged), encoding="utf-8")
    store.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    cat["skills"][slug] = {"name": merged["name"], "path": str(skill_dir), "version": merged["version"], "updated": today(),
                           "sources": [s["url"] for s in sources], "confidence": src["confidence"]}
    save_catalog(cat)
    print(json.dumps({"written": "update" if existing else "create", "skill": slug, "path": str(skill_dir),
                      "version": merged["version"], "sources": len(sources)}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("distilled", nargs="?", help="Path to distilled JSON written by Claude")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", metavar="SLUG")
    a = ap.parse_args()
    if a.list:
        cat = load_catalog()
        print(json.dumps(cat, indent=2, ensure_ascii=False))
        return
    if a.show:
        p = SKILLS_DIR / slugify(a.show) / ".forge" / "skill.json"
        if not p.is_file():
            die(f"no forged skill {a.show!r}")
        print(p.read_text(encoding="utf-8"))
        return
    if not a.distilled:
        ap.error("distilled JSON path required")
    forge(json.loads(Path(a.distilled).read_text(encoding="utf-8")), a.dry_run)


if __name__ == "__main__":
    main()
