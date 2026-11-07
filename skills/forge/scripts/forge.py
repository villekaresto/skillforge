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
import shutil
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

def claim_line(c: dict) -> str:
    return f"- [{c['timestamp']}] {c['claim']}" if c.get("timestamp") else f"- {c['claim']}"


def render_skill_md(d: dict) -> str:
    name = d["name"]
    desc = d["description"].replace("\n", " ").strip()
    # JSON strings are valid YAML scalars: quoting keeps ': ', '#', quotes and leading '-' or '[' from breaking frontmatter.
    out = ["---", f"name: {d['slug']}", f"description: {json.dumps(desc, ensure_ascii=False)}", "---", "", f"# {name}", ""]
    if d.get("summary"):
        out += [d["summary"].strip(), ""]
    if d.get("when_to_use"):
        out += ["## When to use", *[f"- {t}" for t in d["when_to_use"]], ""]
    if d.get("prerequisites"):
        out += ["## Prerequisites", *[f"- {p}" for p in d["prerequisites"]], ""]
    labels = source_labels(d["sources"])
    if d.get("procedure"):
        out.append("## Procedure")
        for i, step in enumerate(d["procedure"], 1):
            line = f"{i}. **{step['step']}**"
            if step.get("detail"):
                line += f" {step['detail']}"
            line += cite(step, labels, len(d["sources"]) > 1)
            out.append(line)
        out.append("")
    if d.get("decision_points"):
        out.append("## Decision points")
        for dp in d["decision_points"]:
            out.append(f"- **{dp['when']}** → {dp['then']}{cite(dp, labels, len(d['sources']) > 1)}")
        out.append("")
    if d.get("pitfalls"):
        out += ["## Pitfalls", *[f"- {item_text(p)}{cite(p, labels, len(d['sources']) > 1) if isinstance(p, dict) else ''}"
                                 for p in d["pitfalls"]], ""]
    if d.get("verification"):
        out += ["## How to verify it worked", *[f"- {v}" for v in d["verification"]], ""]
    if d.get("commands"):
        out += ["## Commands", "See `references/commands.md` for every command shown in the source, with timestamps. "
                "Review before running; they were transcribed from a video, not tested here.", ""]
    out += ["## Sources"]
    multi = len(d["sources"]) > 1
    for i, s in enumerate(d["sources"], 1):
        out.append(f"- {f'S{i}: ' if multi else ''}[{s.get('title') or s['url']}]({s['url']}) by {s.get('uploader') or 'unknown'}, "
                   f"ingested {s['ingested_at'][:10]} (confidence {s.get('confidence', '?')})")
    out += ["", "Notes with timestamps for each source live in `references/`.", ""]
    return "\n".join(out)


def source_labels(sources: list[dict]) -> dict:
    """Map each source's id and url to S1, S2 ... in Sources-list order."""
    labels = {}
    for i, s in enumerate(sources, 1):
        for key in (s.get("id"), s.get("url")):
            if key:
                labels[key] = f"S{i}"
    return labels


def cite(step: dict, labels: dict, multi: bool) -> str:
    """' _(S1 0:43; S2 0:38)_' for a step backed by several sources, ' _(source 0:43)_' for a single-source skill."""
    refs = step.get("refs") or ([{"source": step.get("source"), "timestamp": step.get("timestamp")}]
                                if step.get("timestamp") or step.get("source") else [])
    parts = []
    for r in refs:
        where = labels.get(r.get("source") or "") if multi else None
        parts.append(" ".join(x for x in (where or ("" if multi else "source"), r.get("timestamp")) if x))
    parts = [p for p in parts if p]
    return f" _({'; '.join(parts)})_" if parts else ""


TRAILING_STAMP = re.compile(r"\s*\((?:S\d+\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\)\.?\s*$")


def as_pitfall(p) -> dict:
    """Pitfalls may be plain strings; a trailing '(1:30)' becomes the timestamp so it can carry a source label."""
    if isinstance(p, dict):
        return p
    m = TRAILING_STAMP.search(p)
    return {"text": p[: m.start()].rstrip() + ("." if p[: m.start()].rstrip()[-1:] not in ".!?" else ""), "timestamp": m.group(1)} \
        if m else {"text": p}


def item_text(p) -> str:
    return p if isinstance(p, str) else p.get("text", "")


def attribute(items: list, old_items: list, key, old: dict | None, new_src: dict) -> list:
    """Give every item a `source` once a skill has several sources. An item carried over unchanged from the stored
    version keeps the source it had (or the only old source); anything new comes from the source being merged in."""
    old_sources = (old or {}).get("sources", [])
    if not old_sources:
        return items
    sole_old = (old_sources[0].get("id") or old_sources[0]["url"]) if len(old_sources) == 1 else None
    known = {key(i): (i.get("source") if isinstance(i, dict) else None) or sole_old for i in old_items}
    new_id = new_src.get("id") or new_src["url"]
    out = []
    for item in items:
        if not item.get("source") and not item.get("refs"):
            k = key(item)
            src = known[k] if k in known else new_id     # a stored item with no known source stays unattributed
            item = {**item, "source": src} if src else item
        out.append(item)
    return out


def attribute_steps(procedure: list[dict], old: dict | None, new_src: dict) -> list[dict]:
    return attribute(procedure, (old or {}).get("procedure", []), lambda s: s["step"], old, new_src)


def attribute_pitfalls(pitfalls: list, old: dict | None, new_src: dict) -> list[dict]:
    old_items = [as_pitfall(p) for p in (old or {}).get("pitfalls", [])]
    return attribute([as_pitfall(p) for p in pitfalls], old_items, lambda p: p["text"], old, new_src)


def attribute_decisions(decisions: list[dict], old: dict | None, new_src: dict) -> list[dict]:
    return attribute(decisions, (old or {}).get("decision_points", []), lambda dp: dp["when"], old, new_src)


def source_frames(work_dir: Path | None, useless: set) -> tuple[list[dict], int]:
    """Frames ingest kept for this source (from work_dir/source.json), minus the ones Claude marked useless."""
    meta = (work_dir / "source.json") if work_dir else None
    if not meta or not meta.is_file():
        return [], 0
    kept, omitted = [], 0
    for f in json.loads(meta.read_text(encoding="utf-8")).get("frames") or []:
        if f.get("index") in useless:
            omitted += 1
            continue
        path = work_dir / "frames" / Path(f["path"]).name
        if path.is_file():
            kept.append({**f, "file": path})
    return kept, omitted


def source_key(src: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (src.get("id") or src["url"]).lower()).strip("-")[:40]


def render_source_notes(d: dict, src: dict, frames: list[str] | None = None) -> str:
    out = [f"# Source notes: {src.get('title') or src['url']}", "", f"- URL: {src['url']}", f"- By: {src.get('uploader') or 'unknown'}",
           f"- Ingested: {src['ingested_at']}", f"- Verdict: {d['verdict']['reason']}", ""]
    if d.get("key_claims"):
        out += ["## Key claims", *[claim_line(c) for c in d["key_claims"]], ""]
    if d.get("assumed_knowledge"):
        out += ["## What the speaker assumes you know", *[f"- {a}" for a in d["assumed_knowledge"]], ""]
    if d.get("contestable"):
        out += ["## Contestable or unverified", *[f"- {c}" for c in d["contestable"]], ""]
    if d.get("apply_to_me"):
        out += ["## Worth trying in my context", *[f"- {a}" for a in d["apply_to_me"]], ""]
    if frames:
        out += ["## Frames", "Keyframes from the source, by timestamp. Frames marked useless (blank, talking head, intro) are left out.", "",
                *frames, ""]
    return "\n".join(out) + "\n"


def render_commands(d: dict) -> str:
    out = ["# Commands shown in sources", "", "Transcribed from video/audio. Review before running.", ""]
    for c in d.get("commands", []):
        out += [f"## {c.get('description', 'command')} _({c.get('timestamp', '')})_", "", "```" + (c.get("language") or ""),
                c["command"].rstrip(), "```", ""]
    return "\n".join(out)


def render_note(d: dict, src: dict) -> str:
    """One dated section of a topic note; sub-headings are ### so sections stack in one file."""
    v = d["verdict"]
    out = [f"{NOTE_MARK}{src['url']} -->", f"## {today()} · {src.get('title') or src['url']}", "", f"- URL: {src['url']}", f"- By: {src.get('uploader') or 'unknown'}",
           f"- Skill-worthy: no (confidence {v.get('confidence')})", f"- Why: {v['reason']}", ""]
    if d.get("summary"):
        out += ["### Takeaway", d["summary"].strip(), ""]
    if d.get("key_claims"):
        out += ["### Key points", *[claim_line(c) for c in d["key_claims"]], ""]
    if d.get("procedure"):
        out += ["### Steps shown (not forged as a skill)",
                *[f"{i}. {st['step']}" + (f" ({st['timestamp']})" if st.get("timestamp") else "") for i, st in enumerate(d["procedure"], 1)], ""]
    if d.get("pitfalls"):
        out += ["### Pitfalls", *[f"- {item_text(p)}" + (f" ({p['timestamp']})" if isinstance(p, dict) and p.get("timestamp") else "")
                                 for p in d["pitfalls"]], ""]
    if d.get("verification"):
        out += ["### How to check", *[f"- {v}" for v in d["verification"]], ""]
    if d.get("commands"):
        out += ["### Commands shown (transcribed, review before running)"]
        for c in d["commands"]:
            out += [f"{c.get('description', 'command')} _({c.get('timestamp', '')})_", "", "```" + (c.get("language") or ""),
                    c["command"].rstrip(), "```", ""]
    if d.get("assumed_knowledge"):
        out += ["### What the speaker assumes you know", *[f"- {a}" for a in d["assumed_knowledge"]], ""]
    if d.get("contestable"):
        out += ["### Contestable or unverified", *[f"- {c}" for c in d["contestable"]], ""]
    if d.get("apply_to_me"):
        out += ["### Worth trying", *[f"- {a}" for a in d["apply_to_me"]], ""]
    out.append(NOTE_END)
    return "\n".join(out) + "\n"


NOTE_MARK = "<!-- forge-note: "
NOTE_END = "<!-- /forge-note -->"


def note_sections(text: str) -> list[tuple[str, str]]:
    """(url, section text) for every section in a topic note file."""
    return [(m.group(1), m.group(0)) for m in re.finditer(r"<!-- forge-note: (\S+) -->.*?<!-- /forge-note -->\n?", text, re.S)]


def remove_note_section(path: Path, url: str) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    for u, sec in note_sections(text):
        if u == url:
            text = text.replace(sec, "")
    if note_sections(text):
        path.write_text(text, encoding="utf-8")
    elif NOTE_MARK in path.read_text(encoding="utf-8"):
        path.unlink()                                                      # that topic file held only this source


def upsert_note(text: str | None, slug: str, url: str, section: str) -> tuple[str, str]:
    """Insert or replace this source's section in a topic note. Returns (new text, action)."""
    if not text:
        title = slug.replace("-", " ").capitalize()
        return f"# {title}\n\nLearning notes on one topic, one dated section per source. Written by /forge.\n\n{section}", "create"
    for u, sec in note_sections(text):
        if u == url:
            return text.replace(sec, section), "update"
    return text.rstrip("\n") + "\n\n" + section, "append"


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


def dropped_steps(existing: dict | None, procedure: list[dict], incoming: dict) -> list[str]:
    """Stored steps that cite a source other than the incoming one and are missing from the new procedure.
    That is what re-forging an old single-source distilled.json over a merged skill looks like."""
    if not existing or len(existing.get("sources", [])) < 2:
        return []
    mine = {incoming.get("id"), incoming["url"]} - {None}
    kept = {st["step"] for st in procedure}
    lost = []
    for st in existing.get("procedure", []):
        cited = {r.get("source") for r in st.get("refs") or []} | {st.get("source")}
        if st["step"] not in kept and cited - {None} - mine:
            lost.append(st["step"])
    return lost


def forge(d: dict, dry_run: bool, allow_drop: bool = False, work_dir: Path | None = None) -> None:
    validate(d)
    useless = d.get("useless_frames") or []
    if not isinstance(useless, list) or not all(isinstance(i, int) for i in useless):
        die("useless_frames must be a list of frame indexes (integers)")
    src = dict(d["source"])
    if useless:
        src["useless_frames"] = sorted(set(useless))
    src.setdefault("ingested_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    src["confidence"] = d["verdict"].get("confidence")
    cat = load_catalog()

    if not d["verdict"]["skill_worthy"]:
        slug = slugify(d.get("slug") or re.sub(r"[^a-z0-9]+", "-", (src.get("title") or "note").lower())[:50])
        # One file per topic slug; each source is a dated section, replaced when the same source is re-studied.
        path = NOTES_DIR / f"{slug}.md"
        section = render_note(d, src)
        text, action = upsert_note(path.read_text(encoding="utf-8") if path.is_file() else None, slug, src["url"], section)
        if dry_run:
            verb = {"create": "would create", "update": "would update this source's section in", "append": "would append to"}[action]
            print(f"# {verb} learning note {path}\n\n{section}")
            return
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        for prev in [x for x in cat["notes"] if x.get("url") == src["url"] and Path(x["file"]) != path]:
            remove_note_section(Path(prev["file"]), src["url"])           # the source moved to another topic
        cat["notes"] = [x for x in cat["notes"] if x.get("url") != src["url"]]
        cat["notes"].append({"date": today(), "url": src["url"], "title": src.get("title"), "slug": slug, "file": str(path),
                             "reason": d["verdict"]["reason"]})
        save_catalog(cat)
        print(json.dumps({"written": "note", "action": action, "path": str(path)}, indent=2))
        return

    slug = slugify(d["slug"])
    skill_dir = SKILLS_DIR / slug
    store = skill_dir / ".forge" / "skill.json"
    existing = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else None

    # Sources accumulate; everything else is the latest distilled (Claude merges content before calling us).
    sources = list((existing or {}).get("sources", []))   # a copy: `existing` must keep describing the stored version
    new_source = not any(s["url"] == src["url"] for s in sources)
    if new_source:
        sources.append(src)
    else:                                        # same source re-studied: its frame verdicts may have changed
        sources = [{**s, "useless_frames": src["useless_frames"]} if s["url"] == src["url"] and "useless_frames" in src
                   else s for s in sources]
    # Re-forging the same source with the same content re-renders the files but is not a new version.
    content = {k: v for k, v in d.items() if k not in ("source", "useless_frames")}   # frame verdicts belong to the source
    lost = dropped_steps(existing, content.get("procedure", []), src)
    if lost and not allow_drop:
        die(f"this would drop {len(lost)} step(s) that other sources of '{slug}' back: {lost[:5]}. "
            f"Load the stored version with --show {slug} and merge into it, or pass --allow-drop if removing them is intended.")
    if len(sources) > 1:
        for field, fn in (("procedure", attribute_steps), ("pitfalls", attribute_pitfalls), ("decision_points", attribute_decisions)):
            if content.get(field):
                content[field] = fn(content[field], existing, src)
    old_content = {k: v for k, v in (existing or {}).items() if k not in ("sources", "forged_at", "version")}
    changed = existing is None or new_source or content != old_content
    merged = {**content, "sources": sources,
              "forged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds") if changed else existing.get("forged_at"),
              "version": (existing or {}).get("version", 0) + (1 if changed else 0)}
    action = "create" if not existing else "update" if changed else "unchanged"

    skill_md = render_skill_md(merged)
    frames, omitted = source_frames(work_dir, set(useless))
    if dry_run:
        verb = {"create": "would create", "update": "would update", "unchanged": "unchanged, would re-render"}[action]
        print(f"# {verb} {skill_dir}  (version {merged['version']}, {len(sources)} source{'s' * (len(sources) != 1)}; "
              f"{len(frames)} frames to references, {omitted} marked useless)\n")
        print(skill_md)
        return

    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / ".forge").mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    key = source_key(src)
    frame_dir = skill_dir / "references" / "frames" / key
    shutil.rmtree(frame_dir, ignore_errors=True)
    links = []
    if frames:
        frame_dir.mkdir(parents=True)
        for f in frames:
            shutil.copyfile(f["file"], frame_dir / f["file"].name)
            links.append(f"- [{f.get('stamp', '')}](frames/{key}/{f['file'].name})")
    (skill_dir / "references" / f"source-{key}.md").write_text(render_source_notes(merged, src, links), encoding="utf-8")
    if merged.get("commands"):
        (skill_dir / "references" / "commands.md").write_text(render_commands(merged), encoding="utf-8")
    store.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    cat["skills"][slug] = {"name": merged["name"], "path": str(skill_dir), "version": merged["version"], "updated": today(),
                           "sources": [s["url"] for s in sources], "confidence": src["confidence"]}
    save_catalog(cat)
    print(json.dumps({"written": action, "skill": slug, "path": str(skill_dir),
                      "version": merged["version"], "sources": len(sources)}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("distilled", nargs="?", help="Path to distilled JSON written by Claude")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-drop", action="store_true", help="allow an update to remove steps that other sources back")
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
    path = Path(a.distilled)
    forge(json.loads(path.read_text(encoding="utf-8")), a.dry_run, a.allow_drop, work_dir=path.resolve().parent)


if __name__ == "__main__":
    main()
