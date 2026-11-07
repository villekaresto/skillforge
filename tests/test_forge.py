import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORGE = ROOT / "skills" / "forge" / "scripts" / "forge.py"
INGEST = ROOT / "skills" / "forge" / "scripts" / "ingest.py"
sys.path.insert(0, str(INGEST.parent))

import ingest  # noqa: E402


def run_forge(args, env):
    return subprocess.run([sys.executable, str(FORGE), *args], capture_output=True, text=True, env=env)


def distilled(url="https://example.com/v/abc", worthy=True):
    return {
        "verdict": {"skill_worthy": worthy, "confidence": 0.8, "reason": "has steps"},
        "source": {"url": url, "id": "abc", "title": "Demo video", "uploader": "Someone", "duration": 120,
                   "ingested_at": "2026-11-07T10:00:00+00:00"},
        "slug": "demo-task",
        "name": "Do the demo task",
        "description": "Do the demo task. Use when asked to demo.",
        "summary": "A summary.",
        "procedure": [{"step": "One", "timestamp": "0:10"}, {"step": "Two", "detail": "more", "timestamp": "0:20"},
                      {"step": "Three", "timestamp": "0:30"}],
        "pitfalls": ["Watch out"],
        "commands": [{"description": "list", "language": "bash", "command": "ls -la", "timestamp": "0:25"}],
        "key_claims": [{"claim": "Demos are good", "timestamp": "0:05"}],
        "apply_to_me": ["Try it"],
    }


def env_for(tmp_path, monkeypatch):
    env = dict(**__import__("os").environ)
    env["SKILLFORGE_HOME"] = str(tmp_path / "home")
    env["SKILLFORGE_SKILLS_DIR"] = str(tmp_path / "skills")
    return env


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()))
    r = run_forge([str(f), "--dry-run"], env)
    assert r.returncode == 0, r.stderr
    assert "name: demo-task" in r.stdout
    assert not (tmp_path / "skills").exists()


def test_create_then_merge_accumulates_sources(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()))
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    skill = tmp_path / "skills" / "demo-task"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "references" / "commands.md").read_text().count("ls -la") == 1
    assert not list(skill.glob("scripts/*")), "commands must never become executable scripts"

    f.write_text(json.dumps(distilled(url="https://example.com/v/def")))
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    stored = json.loads((skill / ".forge" / "skill.json").read_text())
    assert stored["version"] == 2
    assert len(stored["sources"]) == 2
    cat = json.loads((tmp_path / "home" / "catalog.json").read_text())
    assert cat["skills"]["demo-task"]["version"] == 2


def test_not_worthy_writes_note(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled(worthy=False)))
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    notes = list((tmp_path / "home" / "notes").glob("*.md"))
    assert len(notes) == 1
    assert "Skill-worthy: no" in notes[0].read_text()
    assert not (tmp_path / "skills").exists()


def test_rejects_thin_skill(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    del d["procedure"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    r = run_forge([str(f)], env)
    assert r.returncode == 2
    assert "procedure" in r.stderr


def test_vtt_parsing_merges_rolling_captions():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
hello there

00:00:03.000 --> 00:00:05.000
hello there

00:00:05.000 --> 00:00:07.000
hello there friends

00:00:07.000 --> 00:00:09.000
<c>next</c> line &amp; more
"""
    segs = ingest.parse_vtt(vtt)
    assert [s["text"] for s in segs] == ["hello there friends", "next line & more"]
    assert segs[0]["start"] == 1.0 and segs[0]["end"] == 7.0


def test_vtt_youtube_auto_captions_drop_cue_settings_and_repeated_lines():
    vtt = """WEBVTT
Kind: captions

00:00:00.000 --> 00:00:02.000 align:start position:0%
hello dear oders

00:00:02.000 --> 00:00:02.010 align:start position:0%
hello dear oders


00:00:02.010 --> 00:00:04.000 align:start position:0%
hello dear oders
in<00:00:02.500><c> this</c><00:00:03.000><c> video</c>

00:00:04.000 --> 00:00:06.000 align:start position:0%
in this video
we talk about rules
"""
    text = " ".join(s["text"] for s in ingest.parse_vtt(vtt))
    assert "align:" not in text and "position:" not in text
    assert text == "hello dear oders in this video we talk about rules"


def test_gemini_transcript_parsing():
    segs = ingest.parse_gemini_transcript("[0:03] Speaker 1: hi\n[1:02:10] late line\nnoise")
    assert segs[0]["start"] == 3 and segs[1]["start"] == 3730


def test_hms():
    assert ingest.hms(65) == "1:05" and ingest.hms(3725) == "1:02:05"


def test_cookie_flags_are_exclusive(tmp_path):
    r = subprocess.run([sys.executable, str(INGEST), "x.mp4", "--cookies", "a", "--cookies-from-browser", "firefox"],
                       capture_output=True, text=True, env={**__import__("os").environ, "SKILLFORGE_HOME": str(tmp_path)})
    assert r.returncode == 2 and "not both" in r.stderr


def test_vtt_entities_and_tags():
    segs = ingest.parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<00:00:00.500><c>a &amp; b</c> &quot;q&quot;\n")
    assert segs[0]["text"] == 'a & b "q"'


def test_env_file_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "HOME", tmp_path)
    (tmp_path / ".env").write_text('GEMINI_API_KEY="abc"\n# comment\nSKILLFORGE_WHISPER_MODEL=medium\n', encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ingest.setting("GEMINI_API_KEY") == "abc"
    assert ingest.setting("SKILLFORGE_WHISPER_MODEL") == "medium"
    assert ingest.setting("NOPE") is None
