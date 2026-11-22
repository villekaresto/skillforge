#!/usr/bin/env python3
"""Turn a distilled JSON file (written by Claude after studying the evidence) into a Claude Code skill.

  forge.py distilled.json --dry-run      print the SKILL.md that would be written
  forge.py distilled.json                write/merge the skill, update the catalog (refused while lint findings remain)
  forge.py distilled.json --allow-lint   write anyway, echoing the lint findings
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
EXPORT_DIR = Path(os.environ.get("SKILLFORGE_EXPORT_DIR", Path.home() / "Projects" / "skillforge-export"))
PROFILE = Path(os.environ.get("SKILLFORGE_PROFILE", HOME / "profile.md"))
PROFILE_EXAMPLE = Path(__file__).resolve().parent.parent / "references" / "profile.example.md"


# --------------------------------------------------------------------------- user profile

def load_profile() -> dict | None:
    """The user's profile (~/.skillforge/profile.md): free text Claude reads before judging, plus a `## Tools` list
    of `- Name: version` lines that forge.py compares with the versions a source shows."""
    if not PROFILE.is_file():
        return None
    text = PROFILE.read_text(encoding="utf-8")
    tools: dict = {}
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        m = re.match(r"\s*[-*]\s*([^:<>]+?)\s*:\s*([^<>#]+?)\s*(?:#.*)?$", line)
        if section == "tools" and m:
            tools[m.group(1).strip().lower()] = m.group(2).strip()
    return {"path": str(PROFILE), "text": text, "tools": tools}


def major(version: str) -> str:
    m = re.search(r"\d+", version or "")
    return m.group(0) if m else (version or "").strip().lower()


def version_pitfalls(sources: list[dict], profile: dict | None) -> list[dict]:
    """One pitfall per tool whose version in a source differs from the user's profile (major versions compared)."""
    if not profile or not profile["tools"]:
        return []
    out, seen = [], set()
    for src in sources:
        for tool, shown in (src.get("versions_shown") or {}).items():
            mine = profile["tools"].get(tool.strip().lower())
            if not mine or major(str(shown)) == major(mine) or (tool.lower(), str(shown)) in seen:
                continue
            seen.add((tool.lower(), str(shown)))
            item = {"text": f"Menus, defaults or labels differ from what the source shows → it uses {tool} {shown}, your profile says "
                            f"{mine} → check each step against your version before relying on it", "auto": "version"}
            if src.get("id") or src.get("url"):
                item["source"] = src.get("id") or src["url"]
            out.append(item)
    return out


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


def render_lens_md(d: dict) -> str:
    """A lens: when to apply it, the principles as rules of thumb, the mistakes the speakers warn about, worked examples."""
    desc = d["description"].replace("\n", " ").strip()
    labels, multi = source_labels(d["sources"]), len(d["sources"]) > 1
    out = ["---", f"name: {d['slug']}", f"description: {json.dumps(desc, ensure_ascii=False)}", "---", "", f"# {d['name']}", ""]
    if d.get("summary"):
        out += [d["summary"].strip(), ""]
    out += ["## When to apply", *[f"- {w}" for w in d["when_to_apply"]], ""]
    out.append("## Principles")
    for i, pr in enumerate(d["principles"], 1):
        line = f"{i}. **{pr['text']}**"
        if pr.get("detail"):
            line += f" {pr['detail']}"
        out.append(line + cite(pr, labels, multi))
    out += ["", "## Common mistakes"]
    for m in d["mistakes"]:
        m = as_pitfall(m)
        out.append(f"- {m['text']}" + cite(m, labels, multi))
    out += ["", "## Worked examples"]
    for ex in d["examples"]:
        out += [f"**{ex.get('situation', '').strip()}**", "", ex.get("apply", "").strip() + cite(ex, labels, multi), ""]
    out += ["## Sources"]
    for i, s in enumerate(d["sources"], 1):
        out.append(f"- {f'S{i}: ' if multi else ''}[{s.get('title') or s['url']}]({s['url']}) by {s.get('uploader') or 'unknown'}, "
                   f"ingested {s['ingested_at'][:10]} (confidence {s.get('confidence', '?')})")
    out += ["", "Notes with timestamps for each source live in `references/`.", ""]
    return "\n".join(out)


def render_skill_md(d: dict) -> str:
    if d.get("kind") == "lens":
        return render_lens_md(d)
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


def attribute_principles(items: list[dict], old: dict | None, new_src: dict) -> list[dict]:
    return attribute(items, (old or {}).get("principles", []), lambda p: p["text"], old, new_src)


def attribute_mistakes(items: list, old: dict | None, new_src: dict) -> list[dict]:
    old_items = [as_pitfall(p) for p in (old or {}).get("mistakes", [])]
    return attribute([as_pitfall(p) for p in items], old_items, lambda p: p["text"], old, new_src)


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
    mine = {src.get("id"), src["url"]} - {None}
    claims = [c for c in d.get("key_claims") or [] if not c.get("source") or c["source"] in mine]   # a claim may name its video
    if claims:
        out += ["## Key claims", *[claim_line(c) for c in claims], ""]
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
        out += ["### Commands (as shown or spoken in the source; review before running)"]
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
    if NOTE_MARK not in text:
        path.unlink()            # a pre-0.1.3 note: one file per video, superseded by this source's new section
        return
    for u, sec in note_sections(text):
        if u == url:
            text = text.replace(sec, "")
    if note_sections(text):
        path.write_text(text, encoding="utf-8")
    elif NOTE_MARK in path.read_text(encoding="utf-8"):
        path.unlink()                                                      # that topic file held only this source


def adopt_note(legacy: Path, topic: str) -> dict:
    """Move a pre-0.1.3 per-video note (one file per video, no section markers) into a topic note as a dated section."""
    text = legacy.read_text(encoding="utf-8")
    if NOTE_MARK in text:
        die(f"{legacy} is already a topic note")
    lines = text.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("# ") else legacy.stem
    url = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("- URL:")), None)
    if not url:
        die(f"{legacy} has no '- URL:' line; cannot tell which source it belongs to")
    date = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("- Date:")), today())
    body = [("#" + l if l.startswith("## ") else l) for l in lines[1:] if not l.startswith("- Date:")]
    section = "\n".join([f"{NOTE_MARK}{url} -->", f"## {date} · {title}", *body, NOTE_END]).strip() + "\n"
    slug = slugify(topic)
    path = NOTES_DIR / f"{slug}.md"
    new_text, action = upsert_note(path.read_text(encoding="utf-8") if path.is_file() else None, slug, url, section)
    path.write_text(new_text, encoding="utf-8")
    if legacy.resolve() != path.resolve():
        legacy.unlink()
    cat = load_catalog()
    entry = next((n for n in cat["notes"] if n.get("url") == url), {"date": date, "url": url, "title": title, "reason": ""})
    cat["notes"] = [n for n in cat["notes"] if n.get("url") != url] + [{**entry, "slug": slug, "file": str(path)}]
    save_catalog(cat)
    return {"adopted": str(legacy), "into": str(path), "action": action, "url": url}


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

def verdict_kind(d: dict) -> str:
    """skill (a procedure), lens (principles to apply), or note. Older files only say skill_worthy."""
    v = d.get("verdict") or {}
    if v.get("kind") in ("skill", "lens", "note"):
        return v["kind"]
    return "skill" if v.get("skill_worthy") else "note"


def validate(d: dict) -> None:
    if "verdict" not in d or not ("source" in d or "sources" in d):
        die("distilled JSON needs 'verdict' and 'source' (or 'sources' for a playlist cluster)")
    v = d["verdict"]
    if "reason" not in v or ("kind" not in v and "skill_worthy" not in v):
        die("verdict needs kind (skill|lens|note) or skill_worthy, and reason")
    kind = verdict_kind(d)
    if kind == "skill":
        for k in ("slug", "name", "description", "procedure"):
            if not d.get(k):
                die(f"skill-worthy output needs {k!r}")
    if kind == "lens":
        for k in ("slug", "name", "description", "when_to_apply", "principles", "mistakes", "examples"):
            if not d.get(k):
                die(f"a lens needs {k!r}")
        if not 5 <= len(d["principles"]) <= 15:
            die(f"a lens has 5-15 principles, got {len(d['principles'])}; fewer means a note, more means split the topic")
        if not 2 <= len(d["examples"]) <= 3:
            die(f"a lens has 2-3 worked examples, got {len(d['examples'])}")
        if not all(isinstance(x, dict) and x.get("text") for x in d["principles"]):
            die("each principle is an object with 'text' (plus timestamp/source or refs)")
    incoming = d["sources"] if "sources" in d else [d["source"]]
    if not isinstance(incoming, list) or not incoming or not all(isinstance(s, dict) and s.get("url") for s in incoming):
        die("every source needs a url")
    if "sources" in d and kind == "note":
        die("a learning note covers one source: write one distilled.json per video, reusing the topic slug")
    for u in [d.get("useless_frames")] + [s.get("useless_frames") for s in incoming]:
        if u is not None and (not isinstance(u, list) or not all(isinstance(i, int) for i in u)):
            die("useless_frames must be a list of frame indexes (integers)")


STAMP_IN_TEXT = re.compile(r"\(\s*(?:[A-Z][\w-]*\s+)?\d{1,2}:\d{2}(?::\d{2})?")


def lint(d: dict, skill_md: str) -> list[str]:
    """Tightness checks for a skill or lens. The dry-run prints them; a real write refuses while any remain, unless
    --allow-lint is passed (then they are echoed to stderr and recorded as lint_overridden). Notes are not linted."""
    out = []
    body = skill_md.split("\n---\n", 1)[-1].split("\n## Sources", 1)[0]     # instructions only: no frontmatter, no Sources
    words = len(body.split())
    if words > 900:
        out.append(f"SKILL.md is {words} words; aim for under 900 (move demo values and per-source detail into key_claims)")
    steps = d.get("procedure") or []
    if len(steps) > 12:
        out.append(f"{len(steps)} steps; more than 12 usually means sub-steps or alternatives belong in decision_points")
    for i, st in enumerate(steps, 1):
        n = len((st.get("step", "") + " " + st.get("detail", "")).split())
        if n > 45:
            out.append(f"step {i} is {n} words; keep step + detail under 45")
        if STAMP_IN_TEXT.search(st.get("step", "") + " " + st.get("detail", "")):
            out.append(f"step {i} has a timestamp in its text; use timestamp/refs, which render as citations")
    for p in d.get("pitfalls") or []:
        text = item_text(p)
        if "→" not in text and "->" not in text:
            out.append(f"pitfall does not lead with the symptom ('symptom → cause → fix'): {text[:60]!r}")
    demo = re.compile(r"\b(demo|the presenter|in the video)\b", re.I)
    leaks = [f"step {i}" for i, st in enumerate(steps, 1) if demo.search(st.get("step", "") + " " + st.get("detail", ""))]
    leaks += [f"prerequisite {i}" for i, p in enumerate(d.get("prerequisites") or [], 1) if demo.search(p)]
    leaks += [f"verification {i}" for i, v in enumerate(d.get("verification") or [], 1) if demo.search(v)]
    if leaks:
        out.append(f"demo values in {', '.join(leaks[:5])}: use placeholders, put the presenter's values in key_claims")
    for i, pr in enumerate(d.get("principles") or [], 1):
        n = len((pr.get("text", "") + " " + pr.get("detail", "")).split())
        if n > 40:
            out.append(f"principle {i} is {n} words; a rule of thumb fits in under 40")
        if STAMP_IN_TEXT.search(pr.get("text", "") + " " + pr.get("detail", "")):
            out.append(f"principle {i} has a timestamp in its text; use timestamp/refs")
    if d.get("kind") == "lens" and any(not (pr.get("timestamp") or pr.get("refs")) for pr in d.get("principles") or []):
        out.append("every principle needs a timestamp (or refs) so it can be checked against the source")
    if d.get("procedure") and not d.get("verification"):
        out.append("no verification: add at least one check the user can see (a field value, a chatter line, a count)")
    return out


def unknown_citations(d: dict, sources: list[dict]) -> list[str]:
    """Source ids cited by steps, pitfalls, decision points or claims that match none of the skill's sources:
    a typo would otherwise silently lose its S-label."""
    known = {x for s in sources for x in (s.get("id"), s.get("url")) if x}
    items = [*(d.get("procedure") or []), *[p for p in d.get("pitfalls") or [] if isinstance(p, dict)],
             *(d.get("decision_points") or []), *(d.get("key_claims") or []), *(d.get("principles") or []),
             *[m for m in d.get("mistakes") or [] if isinstance(m, dict)], *(d.get("examples") or [])]
    cited = {r.get("source") for it in items for r in (it.get("refs") or [])} | {it.get("source") for it in items}
    return sorted(c for c in cited - {None} if c not in known)


def step_citations(st: dict) -> set:
    """(source, timestamp) pairs a step cites; rewording a step keeps them, deleting it loses them."""
    pairs = {(r.get("source"), r.get("timestamp")) for r in st.get("refs") or []}
    if st.get("source"):
        pairs.add((st.get("source"), st.get("timestamp")))
    return {p for p in pairs if p[0]}


def dropped_steps(existing: dict | None, procedure: list[dict], incoming_ids: set, field: str = "procedure",
                  key: str = "step") -> list[str]:
    """Stored steps backed by a source other than the incoming ones whose evidence is gone from the new procedure.
    A step counts as kept when its text survives OR any of its (source, timestamp) citations still appears, so a
    tightening rewrite that rewords steps passes while re-forging an old single-source distilled.json does not."""
    if not existing or len(existing.get("sources", [])) < 2:
        return []
    kept_text = {st[key] for st in procedure}
    kept_cites = set().union(*(step_citations(st) for st in procedure)) if procedure else set()
    lost = []
    for st in existing.get(field, []):
        cites = step_citations(st)
        others = {c for c in cites if c[0] not in incoming_ids}
        if st[key] in kept_text or not others or cites & kept_cites:
            continue
        lost.append(st[key])
    return lost


def incoming_sources(d: dict, work_dir: Path | None) -> list[dict]:
    """The source(s) this distilled.json brings, each with its own work dir (for frames) and useless_frames."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = []
    for raw in (d["sources"] if "sources" in d else [d["source"]]):
        src = dict(raw)
        wd = src.pop("work_dir", None)
        src["_work_dir"] = (work_dir / wd if work_dir and not Path(wd).is_absolute() else Path(wd)) if wd else work_dir
        useless = src.get("useless_frames") if "sources" in d else (d.get("useless_frames") or src.get("useless_frames"))
        if useless:
            src["useless_frames"] = sorted(set(useless))
        src.setdefault("ingested_at", now)
        if "sources" in d:
            src.setdefault("confidence", d["verdict"].get("confidence"))   # a cluster may rate each video separately
        else:
            src["confidence"] = d["verdict"].get("confidence")
        out.append(src)
    return out


def public(src: dict) -> dict:
    return {k: v for k, v in src.items() if not k.startswith("_")}


def lint_refusal(findings_by_file: list[tuple[str, list[str]]]) -> str:
    lines = ["lint failed; nothing was written. Fix these (see --dry-run) or pass --allow-lint to write anyway:"]
    for name, findings in findings_by_file:
        lines.append(f"  {name}:")
        lines += [f"    - {w}" for w in findings]
    return "\n".join(lines)


def forge(d: dict, dry_run: bool, allow_drop: bool = False, work_dir: Path | None = None, pending: dict | None = None,
          allow_lint: bool = False, check_only: bool = False, label: str = "distilled.json") -> list[str]:
    """`pending` carries note texts produced earlier in the same dry-run, so a combined dry-run shows a second note on
    a topic as an append, exactly as the real write will. `check_only` runs every guard and returns the lint findings
    without printing or writing, so a multi-file run can refuse before writing anything. Returns the lint findings."""
    validate(d)
    incoming = incoming_sources(d, work_dir)
    cat = load_catalog()

    kind = verdict_kind(d)
    if kind == "note" and check_only:
        return []
    if kind == "note":
        src = public(incoming[0])
        slug = slugify(d.get("slug") or re.sub(r"[^a-z0-9]+", "-", (src.get("title") or "note").lower())[:50])
        # One file per topic slug; each source is a dated section, replaced when the same source is re-studied.
        path = NOTES_DIR / f"{slug}.md"
        profile = load_profile()
        mismatch = version_pitfalls([src], profile)
        if mismatch:
            d = {**d, "pitfalls": [p for p in d.get("pitfalls") or [] if not (isinstance(p, dict) and p.get("auto"))] + mismatch}
        section = render_note(d, src)
        current = (pending or {}).get(path, path.read_text(encoding="utf-8") if path.is_file() else None)
        text, action = upsert_note(current, slug, src["url"], section)
        if pending is not None:
            pending[path] = text
        if dry_run:
            verb = {"create": "would create", "update": "would update this source's section in", "append": "would append to"}[action]
            print(f"# {verb} learning note {path}\n\n{section}")
            return []
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        for prev in [x for x in cat["notes"] if x.get("url") == src["url"] and Path(x["file"]) != path]:
            remove_note_section(Path(prev["file"]), src["url"])           # the source moved to another topic
        cat["notes"] = [x for x in cat["notes"] if x.get("url") != src["url"]]
        cat["notes"].append({"date": today(), "url": src["url"], "title": src.get("title"), "slug": slug, "file": str(path),
                             "reason": d["verdict"]["reason"]})
        save_catalog(cat)
        print(json.dumps({"written": "note", "action": action, "path": str(path)}, indent=2))
        return []

    slug = slugify(d["slug"])
    skill_dir = SKILLS_DIR / slug
    store = skill_dir / ".forge" / "skill.json"
    existing = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else None

    # Sources accumulate; everything else is the latest distilled (Claude merges content before calling us).
    sources = list((existing or {}).get("sources", []))   # a copy: `existing` must keep describing the stored version
    new_source = False
    for src in incoming:
        if not any(s["url"] == src["url"] for s in sources):
            sources.append(public(src))
            new_source = True
        else:                                    # same source re-studied: its confidence and frame verdicts may have changed
            refreshed = {k: src[k] for k in ("confidence", "useless_frames", "versions_shown") if k in src}
            sources = [{**s, **refreshed} if s["url"] == src["url"] else s for s in sources]
    unknown = unknown_citations(d, sources)
    if unknown:
        die(f"citations name sources this skill does not have: {unknown}. Use each source's id (or url) exactly.")
    # Re-forging the same source with the same content re-renders the files but is not a new version.
    content = {k: v for k, v in d.items() if k not in ("source", "sources", "useless_frames", "supersedes_notes")}
    content["kind"] = kind
    # Version-mismatch pitfalls are generated from the sources and the profile on every forge, never hand-written.
    profile = load_profile()
    generated = version_pitfalls(sources, profile)
    kept = [p for p in content.get("pitfalls") or [] if not (isinstance(p, dict) and p.get("auto") == "version")]
    if kept or generated:
        content["pitfalls"] = kept + generated
    ids = {x for s in incoming for x in (s.get("id"), s["url"]) if x}
    lost = dropped_steps(existing, content.get("procedure", []), ids) + \
        dropped_steps(existing, content.get("principles", []), ids, field="principles", key="text")
    if lost and not allow_drop:
        die(f"this would drop {len(lost)} step(s) that other sources of '{slug}' back: {lost[:5]}. "
            f"Load the stored version with --show {slug} and merge into it, or pass --allow-drop if removing them is intended.")
    if len(sources) > 1 and existing and len(incoming) == 1:
        for field, fn in (("procedure", attribute_steps), ("pitfalls", attribute_pitfalls), ("decision_points", attribute_decisions),
                          ("principles", attribute_principles), ("mistakes", attribute_mistakes)):
            if content.get(field):
                content[field] = fn(content[field], existing, incoming[0])
    old_content = {k: v for k, v in (existing or {}).items() if k not in ("sources", "forged_at", "version")}
    old_content.setdefault("kind", "skill")        # skills forged before lenses existed
    changed = existing is None or new_source or content != old_content
    merged = {**content, "sources": sources,
              "forged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds") if changed else existing.get("forged_at"),
              "version": (existing or {}).get("version", 0) + (1 if changed else 0)}
    action = "create" if not existing else "update" if changed else "unchanged"

    skill_md = render_skill_md(merged)
    per_source = [(src, *source_frames(src["_work_dir"], set(src.get("useless_frames") or []))) for src in incoming]
    uncited = [st.get("step") or st.get("text") for st in [*merged.get("procedure", []), *merged.get("principles", [])]
               if not st.get("source") and not st.get("refs")] if len(sources) > 1 else []
    # A source promoted from a note to this skill or lens leaves its note topic, but only when the file says so:
    # a long course can deliberately feed both a skill and a note.
    promoted = [x for x in cat["notes"] if d.get("supersedes_notes") and any(x.get("url") == s["url"] for s in incoming)]
    findings = lint(merged, skill_md)
    if check_only:
        return findings
    if dry_run:
        verb = {"create": "would create", "update": "would update", "unchanged": "unchanged, would re-render"}[action]
        kept, omitted = sum(len(f) for _, f, _ in per_source), sum(o for _, _, o in per_source)
        print(f"# {verb} {kind} {skill_dir}  (version {merged['version']}, {len(sources)} source{'s' * (len(sources) != 1)}; "
              f"{kept} frames to references, {omitted} marked useless)")
        if uncited:
            print(f"# warning: {len(uncited)} step(s)/principle(s) cite no source in a multi-source {kind}: {uncited[:3]}")
        for x in promoted:
            print(f"# would move {x['url']} out of note topic {Path(x['file']).stem}")
        for w in findings:
            print(f"# lint: {w}")
        if findings:
            print(f"# lint: {len(findings)} finding(s); a write will refuse without --allow-lint")
        for p in generated:
            print(f"# profile: version mismatch added as a pitfall: {p['text']}")
        if profile and not merged.get("apply_to_me"):
            print("# profile: apply_to_me is empty although a profile exists; tie the skill to the user's tools and work")
        print()
        print(skill_md)
        return findings

    if findings and not allow_lint:
        die(lint_refusal([(label, findings)]))
    for w in findings:
        print(f"[forge] lint overridden (--allow-lint): {w}", file=sys.stderr)
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / ".forge").mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for src, frames, _ in per_source:
        key = source_key(src)
        frame_dir = skill_dir / "references" / "frames" / key
        links = []
        evidence = src["_work_dir"] and (src["_work_dir"] / "source.json").is_file()
        if not evidence:                 # rewriting from stored sources: keep the frames already in references
            links = [f"- [{p.stem.split('_', 1)[-1].replace('-', ':')}](frames/{key}/{p.name})" for p in sorted(frame_dir.glob("*.jpg"))]
        else:
            shutil.rmtree(frame_dir, ignore_errors=True)
        if evidence and frames:
            frame_dir.mkdir(parents=True)
            for f in frames:
                shutil.copyfile(f["file"], frame_dir / f["file"].name)
                links.append(f"- [{f.get('stamp', '')}](frames/{key}/{f['file'].name})")
        (skill_dir / "references" / f"source-{key}.md").write_text(render_source_notes(merged, public(src), links), encoding="utf-8")
    if merged.get("commands"):
        (skill_dir / "references" / "commands.md").write_text(render_commands(merged), encoding="utf-8")
    store.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    for x in promoted:
        remove_note_section(Path(x["file"]), x["url"])
    cat["notes"] = [x for x in cat["notes"] if x not in promoted]
    cat["skills"][slug] = {"name": merged["name"], "kind": kind, "path": str(skill_dir), "version": merged["version"], "updated": today(),
                           "sources": [s["url"] for s in sources], "confidence": d["verdict"].get("confidence")}
    save_catalog(cat)
    result = {"written": action, "kind": kind, "skill": slug, "path": str(skill_dir), "notes_moved": len(promoted),
              "version": merged["version"], "sources": len(sources)}
    if findings:
        result["lint_overridden"] = findings
    print(json.dumps(result, indent=2))
    return findings


PRIVATE_SECTIONS = ("Worth trying in my context", "Worth trying", "Frames")


def shareable(md: str) -> str:
    """Strip what must stay local before a skill leaves the machine: apply_to_me (built from the user's profile) and
    links to frames that are not exported."""
    for title in PRIVATE_SECTIONS:
        md = re.sub(rf"\n#+ {re.escape(title)}\n.*?(?=\n#+ |\Z)", "\n", md, flags=re.S)
    return md


def export_skills(which: str) -> list[dict]:
    """Zip forged skills for upload elsewhere: SKILL.md at the zip root, references/ beside it, .forge/ left out."""
    import zipfile
    forged = sorted(d for d in SKILLS_DIR.glob("*") if (d / ".forge" / "skill.json").is_file() and (d / "SKILL.md").is_file())
    chosen = forged if which == "all" else [d for d in forged if d.name == slugify(which)]
    if not chosen:
        die(f"no forged skill {which!r} in {SKILLS_DIR}" if which != "all" else f"no forged skills in {SKILLS_DIR}")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in chosen:
        target = EXPORT_DIR / f"{d.name}.zip"
        rel = lambda f: f.relative_to(d).parts
        files = sorted(f for f in d.rglob("*") if f.is_file() and ".forge" not in rel(f) and "frames" not in rel(f))
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                if f.suffix == ".md":
                    z.writestr(f.relative_to(d).as_posix(), shareable(f.read_text(encoding="utf-8")))
                else:
                    z.write(f, f.relative_to(d).as_posix())
        out.append({"skill": d.name, "zip": str(target), "files": len(files)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("distilled", nargs="*", help="distilled JSON file(s) written by Claude; several give one combined dry-run or write")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-drop", action="store_true", help="allow an update to remove steps that other sources back")
    ap.add_argument("--allow-lint", action="store_true", help="write a skill or lens even though lint findings remain")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", metavar="SLUG")
    ap.add_argument("--adopt-note", metavar="FILE", nargs="+", help="move pre-0.1.3 per-video notes into --topic")
    ap.add_argument("--topic", metavar="SLUG", help="topic note slug for --adopt-note")
    ap.add_argument("--export", metavar="SLUG|all", help="zip forged skills (SKILL.md at the zip root, no .forge/) into SKILLFORGE_EXPORT_DIR")
    ap.add_argument("--profile", action="store_true", help="print the user profile read before judging (or how to create one)")
    a = ap.parse_args()
    if a.export:
        print(json.dumps(export_skills(a.export), indent=2))
        return
    if a.profile:
        prof = load_profile()
        if prof:
            print(f"# profile: {prof['path']}  (tools parsed: {json.dumps(prof['tools'])})\n")
            print(prof["text"])
        else:
            print(f"# no profile at {PROFILE}. Copy {PROFILE_EXAMPLE} there and fill it in, or judge without one.")
        return
    if a.adopt_note:
        if not a.topic:
            ap.error("--adopt-note needs --topic SLUG")
        for name in a.adopt_note:
            print(json.dumps(adopt_note(Path(name).expanduser(), a.topic), indent=2))
        return
    if a.list:
        cat = load_catalog()
        # Note topics are the slugs to reuse when a new video is on the same subject; legacy per-video notes included.
        cat["note_topics"] = sorted(p.stem for p in NOTES_DIR.glob("*.md")) if NOTES_DIR.is_dir() else []
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
    docs = [(Path(n), json.loads(Path(n).read_text(encoding="utf-8"))) for n in a.distilled]
    # A video may feed one skill and one note in a run (a long course: a procedure plus reference material), but not two
    # skills or two notes: that is a stale solo file left next to a cluster file, and it would be forged twice.
    seen: dict = {}
    for path, d in docs:
        kind = verdict_kind(d)
        for src in (d.get("sources") or [d.get("source") or {}]):
            key = (src.get("url"), kind)
            if key in seen:
                die(f"{src['url']} appears as a {kind} in both {seen[key]} and {path}; a video feeds at most one skill and one "
                    f"note per run (rename the stale file, e.g. distilled.superseded.json)")
            seen[key] = path
    for path, d in docs:                  # promoting a note while re-filing the same video as a note contradicts itself
        if d.get("supersedes_notes"):
            for src in (d.get("sources") or [d.get("source") or {}]):
                if (src.get("url"), "note") in seen:
                    die(f"{path} supersedes the note for {src['url']}, but {seen[(src.get('url'), 'note')]} files it as a "
                        f"note in the same run; leave the old note file out (e.g. rename it distilled.superseded.json)")
    if not a.dry_run and not a.allow_lint:      # check every file before writing any, so a refusal never leaves half a run
        failed = [(str(path), w) for path, d in docs
                  if (w := forge(d, False, a.allow_drop, work_dir=path.resolve().parent, check_only=True, label=str(path)))]
        if failed:
            die(lint_refusal(failed))
    pending: dict = {}
    for i, (path, d) in enumerate(docs):
        if len(a.distilled) > 1:
            print(f"{chr(10) if i else ''}======== {path} ========")
        forge(d, a.dry_run, a.allow_drop, work_dir=path.resolve().parent, pending=pending, allow_lint=a.allow_lint,
              label=str(path))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to cp1252, which has no "→"
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
