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
    return subprocess.run([sys.executable, str(FORGE), *args], capture_output=True, text=True, env=env, encoding="utf-8")


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
        "pitfalls": ["Watch out → the demo step was skipped → run step one first"],
        "verification": ["The output lists the new file"],
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
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert r.returncode == 0, r.stderr
    assert "name: demo-task" in r.stdout
    assert not (tmp_path / "skills").exists()


def test_create_then_merge_accumulates_sources(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    skill = tmp_path / "skills" / "demo-task"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "references" / "commands.md").read_text(encoding="utf-8").count("ls -la") == 1
    assert not list(skill.glob("scripts/*")), "commands must never become executable scripts"

    f.write_text(json.dumps(distilled(url="https://example.com/v/def")), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    stored = json.loads((skill / ".forge" / "skill.json").read_text(encoding="utf-8"))
    assert stored["version"] == 2
    assert len(stored["sources"]) == 2
    cat = json.loads((tmp_path / "home" / "catalog.json").read_text(encoding="utf-8"))
    assert cat["skills"]["demo-task"]["version"] == 2


def test_not_worthy_writes_note(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled(worthy=False)), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    notes = list((tmp_path / "home" / "notes").glob("*.md"))
    assert len(notes) == 1
    assert "Skill-worthy: no" in notes[0].read_text(encoding="utf-8")
    assert not (tmp_path / "skills").exists()


def test_rejects_thin_skill(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    del d["procedure"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
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
                       capture_output=True, text=True, env={**__import__("os").environ, "SKILLFORGE_HOME": str(tmp_path)}, encoding="utf-8")
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


def test_frontmatter_description_is_quoted_yaml(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    d["description"] = 'Back up Postgres: dump, #tags, "quotes" - use for \'backup\''
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert r.returncode == 0, r.stderr
    line = next(l for l in r.stdout.splitlines() if l.startswith("description: "))
    assert json.loads(line[len("description: "):]) == d["description"]
    try:
        import yaml
    except ImportError:
        return
    front = r.stdout.split("---")[1]
    assert yaml.safe_load(front)["description"] == d["description"]


def test_same_source_twice_is_idempotent(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["written"] == "unchanged"
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text(encoding="utf-8"))
    assert stored["version"] == 1 and len(stored["sources"]) == 1

    d = distilled()
    d["pitfalls"].append("Re-study shows a crash → stale cache → clear it")
    f.write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert json.loads(r.stdout)["written"] == "update"
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text(encoding="utf-8"))
    assert stored["version"] == 2 and len(stored["sources"]) == 1


def test_playlist_urls_are_detected_but_single_videos_are_not():
    yes = ["https://www.youtube.com/playlist?list=PLaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "https://www.youtube.com/@examplechannel",
           "https://www.youtube.com/channel/UCkQPikELWZFLgQNHd73jkdg/videos"]
    no = ["https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PLaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "https://youtu.be/aaaaaaaaaaa",
          "https://www.youtube.com/shorts/bbbbbbbbbbb", "https://www.tiktok.com/@examplechannel/video/1234567890123456789", "https://vimeo.com/123"]
    assert all(ingest.is_playlist_url(u) for u in yes)
    assert not any(ingest.is_playlist_url(u) for u in no)


def test_reused_out_dir_drops_previous_evidence(tmp_path):
    for name in ("source.json", "transcript.md", "gemini.md", "playlist.json", "media.en.vtt", "audio.m4a"):
        (tmp_path / name).write_text("stale", encoding="utf-8")
    ingest.clear_stale(tmp_path)
    assert not list(tmp_path.iterdir())


def test_caption_download_failure_is_reported_not_swallowed():
    r = subprocess.CompletedProcess([], 1, "", "[info] Writing video subtitles to: x.en.vtt\n"
                                              "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests\n")
    assert "429" in ingest.caption_failure(r)
    assert ingest.caption_failure(subprocess.CompletedProcess([], 0, "", "")) is None


def test_whisper_helper_decodes_with_ffmpeg_not_pyav(tmp_path):
    import shutil
    numpy = __import__("pytest").importorskip("numpy")
    if not shutil.which("ffmpeg"):
        __import__("pytest").skip("ffmpeg missing")
    sys.path.insert(0, str(INGEST.parent))
    import whisper_local
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ar", "44100", str(wav)], check=True)
    audio = whisper_local.load_audio(str(wav))
    assert audio.dtype == numpy.float32 and 15000 < len(audio) < 17000
    assert "av<" not in INGEST.read_text(encoding="utf-8"), "pinning PyAV forces source builds on platforms without wheels"


def test_whisper_language_codes_are_normalised():
    assert ingest.whisper_language("en-US") == "en"
    assert ingest.whisper_language("pt_BR") == "pt"
    assert ingest.whisper_language("zh-Hans") == "zh"
    assert ingest.whisper_language("de") == "de"
    assert ingest.whisper_language(None) is None and ingest.whisper_language("und-x") == "und"


def test_merge_attributes_steps_to_their_source(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    first = distilled()
    f.write_text(json.dumps(first), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0

    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"].insert(1, {"step": "One and a half", "timestamp": "1:14"})
    f.write_text(json.dumps(second), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert json.loads(r.stdout)["written"] == "update"
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "**One** _(S1 0:10)_" in md and "**One and a half** _(S2 1:14)_" in md and "**Three** _(S1 0:30)_" in md
    assert "- S1: [Demo video]" in md and "- S2: [Demo video]" in md
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text(encoding="utf-8"))
    assert [s["step"] for s in stored["procedure"]].count("One") == 1, "no duplicated steps after a merge"

    r = run_forge([str(f)], env)          # merging the same second source again changes nothing
    assert json.loads(r.stdout)["written"] == "unchanged"


def test_note_keeps_caveats(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled(worthy=False)
    d["contestable"] = ["Title oversells this as a full course"]
    d["assumed_knowledge"] = ["what a database role is"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert "Title oversells this as a full course" in r.stdout and "what a database role is" in r.stdout
    assert "## Steps shown" in r.stdout and "2. Two (0:20)" in r.stdout


def test_scene_cuts_in_a_transition_collapse_to_one_settled_frame():
    assert ingest.settle_times([10.0, 10.2, 10.5, 30.0], duration=120) == [11.2, 30.7]
    assert ingest.settle_times([59.8], duration=60) == [59.9]
    assert ingest.settle_times([], duration=60) == []


def test_extract_frames_on_a_synthetic_video(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        __import__("pytest").skip("ffmpeg missing")
    video = tmp_path / "v.mp4"
    # test pattern 3 s, colour bars 3 s, test pattern 3 s: two hard cuts
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=3:r=25", "-f", "lavfi", "-i",
                    "smptebars=s=320x240:d=3:r=25", "-f", "lavfi", "-i", "testsrc=s=320x240:d=3:r=25", "-filter_complex",
                    "[0][1][2]concat=n=3:v=1:a=0", str(video)], check=True)
    frames = ingest.extract_frames(video, tmp_path, max_frames=10, duration=9.0, width=320, no_dedup=False)
    assert frames and all(Path(f["path"]).is_file() for f in frames)
    assert len({f["path"] for f in frames}) == len(frames)
    assert not list((tmp_path / "frames").glob("scene_*")) and not list((tmp_path / "frames").glob("uniform_*"))


def test_step_backed_by_two_sources_cites_both(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"][0] = {"step": "One", "refs": [{"source": "abc", "timestamp": "0:10"}, {"source": "def", "timestamp": "0:38"}]}
    f.write_text(json.dumps(second), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "**One** _(S1 0:10; S2 0:38)_" in md


def test_reforging_an_old_source_cannot_silently_drop_merged_steps(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    first = distilled()
    f.write_text(json.dumps(first), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"].append({"step": "Four, only in the second video", "timestamp": "2:00"})
    f.write_text(json.dumps(second), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0

    f.write_text(json.dumps(first), encoding="utf-8")                      # stale single-source distilled for source 1
    r = run_forge([str(f)], env)
    assert r.returncode == 2 and "Four, only in the second video" in r.stderr
    assert run_forge([str(f), "--allow-drop"], env).returncode == 0


def test_thinning_keeps_a_long_static_stretch_covered():
    pairs = [(Path(f"s{i}.jpg"), i * 0.5) for i in range(40)]            # a burst of cuts in the first 20 s
    pairs += [(Path(f"u{i}.jpg"), 20.0 + i * 24) for i in range(25)]     # uniform frames over the next 10 minutes
    kept = ingest.thin_by_time(pairs, max_frames=20, duration=620)
    assert len(kept) == 20
    times = sorted(t for _, t in kept)
    assert max(b - a for a, b in zip(times, times[1:])) <= 62, "no gap longer than ~2 buckets"


def test_note_renders_commands(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled(worthy=False)), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert "ls -la" in r.stdout and "Watch out" in r.stdout


def test_blank_frames_are_dropped_but_dark_code_frames_survive(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        __import__("pytest").skip("ffmpeg missing")
    ff = shutil.which("ffmpeg")
    black, white, code = tmp_path / "black.jpg", tmp_path / "white.jpg", tmp_path / "code.jpg"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=black:s=640x360", "-frames:v", "1", str(black)], check=True)
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=white:s=640x360", "-frames:v", "1", str(white)], check=True)
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=0x1e1e1e:s=640x360", "-vf",
                    "drawbox=x=40:y=30:w=220:h=14:color=0x9cdcfe:t=fill,drawbox=x=40:y=60:w=160:h=14:color=0xce9178:t=fill",
                    "-frames:v", "1", str(code)], check=True)
    assert ingest.is_blank(ff, black) and ingest.is_blank(ff, white)
    assert not ingest.is_blank(ff, code)


def test_no_speech_warning_points_to_frames():
    assert "--frames" in ingest.NO_SPEECH and "no speech" in ingest.NO_SPEECH
    assert "NO_SPEECH_FRAMES if frames else NO_SPEECH" in INGEST.read_text(encoding="utf-8")


def test_original_language_subtitle_track_wins_over_translation(tmp_path):
    big_translation = tmp_path / "media.en.vtt"
    big_translation.write_text("x" * 5000, encoding="utf-8")
    orig = tmp_path / "media.en-orig.vtt"
    orig.write_text("x" * 10, encoding="utf-8")
    assert ingest.pick_subtitle([big_translation, orig], "en") == orig
    assert ingest.pick_subtitle([big_translation], "en") == big_translation


def test_untimed_claims_render_without_empty_brackets(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled(worthy=False)
    d["key_claims"] = [{"claim": "From the description only"}]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert "- From the description only" in r.stdout and "[]" not in r.stdout


def test_whisper_language_comes_from_the_user_not_youtube_metadata():
    src = INGEST.read_text(encoding="utf-8")
    assert 'whisper_language(meta.get("language")' not in src


def test_one_note_file_per_topic_with_a_section_per_source(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    a = distilled(worthy=False)
    f.write_text(json.dumps(a), encoding="utf-8")
    assert json.loads(run_forge([str(f)], env).stdout)["action"] == "create"
    a["summary"] = "Re-studied summary."
    f.write_text(json.dumps(a), encoding="utf-8")
    assert json.loads(run_forge([str(f)], env).stdout)["action"] == "update"   # same source: its section is replaced
    b = distilled(url="https://example.com/v/other", worthy=False)
    b["source"]["title"] = "Second video"
    f.write_text(json.dumps(b), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert "would append to learning note" in r.stdout
    assert json.loads(run_forge([str(f)], env).stdout)["action"] == "append"
    notes = list((tmp_path / "home" / "notes").glob("*.md"))
    assert [n.name for n in notes] == ["demo-task.md"]
    text = notes[0].read_text(encoding="utf-8")
    assert text.count("<!-- forge-note: ") == 2 and "Re-studied summary." in text and "A summary." in text
    assert sum(l.startswith("## ") for l in text.splitlines()) == 2 and "· Demo video" in text and "· Second video" in text
    cat = json.loads((tmp_path / "home" / "catalog.json").read_text(encoding="utf-8"))
    assert len(cat["notes"]) == 2 and {n["file"] for n in cat["notes"]} == {str(notes[0])}

    b["slug"] = "another-topic"                                    # the second source is re-filed under another topic
    f.write_text(json.dumps(b), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    assert notes[0].read_text(encoding="utf-8").count("<!-- forge-note: ") == 1
    assert (tmp_path / "home" / "notes" / "another-topic.md").is_file()


def test_slug_hint_transliterates_accents():
    assert ingest.slugify("Cómo reparar un pinchazo") == "como-reparar-un-pinchazo"
    assert ingest.slugify("Crème brûlée") == "creme-brulee"


def test_short_clips_get_fewer_frames(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        __import__("pytest").skip("ffmpeg missing")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=20:r=25", str(video)], check=True)
    frames = ingest.extract_frames(video, tmp_path, max_frames=40, duration=20.0, width=320, no_dedup=True)
    assert 0 < len(frames) <= 8


class FakeYtdlp:
    """Stands in for yt-dlp: writes info.json always, subtitles only once `succeed_after` caption calls have failed."""
    def __init__(self, out, succeed_after):
        self.out, self.succeed_after, self.calls = out, succeed_after, []

    def __call__(self, cmd, label):
        self.calls.append(cmd)
        (self.out / "media.info.json").write_text(json.dumps({"id": "abcdefghijk", "title": "T", "duration": 60}), encoding="utf-8")
        if len(self.calls) > self.succeed_after and "--write-auto-subs" in cmd:
            (self.out / "media.en.vtt").write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")


def caption_env(tmp_path, monkeypatch, succeed_after):
    out = tmp_path / "work"
    out.mkdir()
    fake = FakeYtdlp(out, succeed_after)
    sleeps = []
    monkeypatch.setattr(ingest, "HOME", tmp_path / "home")
    monkeypatch.setattr(ingest, "ytdlp_base", lambda auth: ["yt-dlp"])
    monkeypatch.setattr(ingest, "ytdlp_with_retry", fake)
    monkeypatch.setattr(ingest.time, "sleep", sleeps.append)
    return out, fake, sleeps


def test_captions_are_retried_once_after_a_wait_before_whisper(tmp_path, monkeypatch):
    out, fake, sleeps = caption_env(tmp_path, monkeypatch, succeed_after=2)   # manual + auto fail, retry succeeds
    meta, segs, provenance, errors = ingest.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk", out, "en", [])
    assert sleeps == [ingest.CAPTION_RETRY_WAIT] and provenance == "auto" and segs and not errors


def test_caption_failure_after_the_retry_is_reported(tmp_path, monkeypatch):
    out, fake, sleeps = caption_env(tmp_path, monkeypatch, succeed_after=99)
    meta, segs, provenance, errors = ingest.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk", out, "en", [])
    assert len(sleeps) == 1 and not segs and any("429" in e for e in errors)


def test_info_json_is_cached_per_video_and_reused(tmp_path, monkeypatch):
    out, fake, sleeps = caption_env(tmp_path, monkeypatch, succeed_after=0)
    url = "https://www.youtube.com/watch?v=abcdefghijk&t=10"
    ingest.fetch_captions(url, out, "en", [])
    cache = tmp_path / "home" / "cache" / "youtube-abcdefghijk.info.json"
    assert cache.is_file() and url in fake.calls[-1]
    for f in out.iterdir():
        f.unlink()
    ingest.fetch_captions("https://youtu.be/abcdefghijk", out, "en", [])     # same video, other URL form
    assert "--load-info-json" in fake.calls[-1] and url not in fake.calls[-1]


def test_useless_frames_are_left_out_of_references(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    work = tmp_path / "work"
    (work / "frames").mkdir(parents=True)
    frames = []
    for i, stamp in [(1, "0-10"), (2, "0-20"), (3, "0-30")]:
        name = f"{i:03d}_{stamp}.jpg"
        (work / "frames" / name).write_bytes(b"jpg")
        frames.append({"index": i, "time": i * 10, "stamp": stamp.replace("-", ":"), "path": f"/elsewhere/frames/{name}"})
    (work / "source.json").write_text(json.dumps({"frames": frames}), encoding="utf-8")
    d = distilled()
    d["useless_frames"] = [2]
    (work / "distilled.json").write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(work / "distilled.json"), "--dry-run"], env)
    assert "2 frames to references, 1 marked useless" in r.stdout
    assert run_forge([str(work / "distilled.json")], env).returncode == 0
    refs = tmp_path / "skills" / "demo-task" / "references"
    assert sorted(p.name for p in (refs / "frames" / "abc").iterdir()) == ["001_0-10.jpg", "003_0-30.jpg"]
    notes = (refs / "source-abc.md").read_text(encoding="utf-8")
    assert "frames/abc/003_0-30.jpg" in notes and "002_0-20" not in notes
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text(encoding="utf-8"))
    assert "useless_frames" not in stored and stored["sources"][0]["useless_frames"] == [2]

    d["useless_frames"] = [1, 2]                              # re-study changes only the frame verdicts
    (work / "distilled.json").write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(work / "distilled.json")], env)
    assert json.loads(r.stdout)["written"] == "unchanged"
    assert [p.name for p in (refs / "frames" / "abc").iterdir()] == ["003_0-30.jpg"]


def test_merged_pitfalls_and_decisions_cite_their_source(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    first = distilled()
    first["pitfalls"] = ["Old trap fires → wrong setting → reset it (0:15)"]
    first["decision_points"] = [{"when": "Small job", "then": "do it by hand"}]
    f.write_text(json.dumps(first), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "- Old trap fires → wrong setting → reset it (0:15)" in md, "single-source skills render pitfalls unchanged"

    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["pitfalls"] = first["pitfalls"] + ["A red banner shows up → new trap → dismiss it (1:20)"]
    second["decision_points"] = first["decision_points"] + [{"when": "Big job", "then": "use the machine", "timestamp": "2:05"}]
    f.write_text(json.dumps(second), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "- Old trap fires → wrong setting → reset it. _(S1 0:15)_" in md
    assert "- A red banner shows up → new trap → dismiss it. _(S2 1:20)_" in md
    assert "- **Small job** → do it by hand _(S1)_" in md and "- **Big job** → use the machine _(S2 2:05)_" in md

    r = run_forge([str(f)], env)                                   # idempotent with the normalised pitfalls
    assert json.loads(r.stdout)["written"] == "unchanged"


def test_legacy_per_video_note_is_replaced_by_the_topic_section(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    notes = tmp_path / "home" / "notes"
    notes.mkdir(parents=True)
    legacy = notes / "2026-11-07-old-video-name.md"
    legacy.write_text("# Demo video\n\n- Skill-worthy: no\n", encoding="utf-8")
    d = distilled(worthy=False)
    (tmp_path / "home" / "catalog.json").write_text(json.dumps({"skills": {}, "notes": [
        {"date": "2026-11-07", "url": d["source"]["url"], "title": "Demo video", "file": str(legacy), "reason": "old"}]}), encoding="utf-8")
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    assert not legacy.exists() and (notes / "demo-task.md").is_file()


def test_restudied_source_updates_its_confidence(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    d = distilled()
    f.write_text(json.dumps(d), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    d["verdict"]["confidence"] = 0.62
    d["pitfalls"].append("Save is ignored → field locked → unlock it")
    f.write_text(json.dumps(d), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "(confidence 0.62)" in md and "(confidence 0.8)" not in md


sys.path.insert(0, str(INGEST.parent))
import clusters  # noqa: E402


def test_clusters_group_videos_on_the_same_task():
    entries = [
        {"key": "a1", "title": "Postgres backup tutorial", "text": "create a backup with pg_dump and restore it"},
        {"key": "b1", "title": "Sharpening a chisel on whetstones", "text": "flatten the back then hone the bevel"},
        {"key": "a2", "title": "Postgres backups with pg_dump", "text": "nightly backup schedule then a test restore of the dump"},
        {"key": "c1", "title": "Proofing sourdough bread", "text": "bulk fermentation and shaping the loaf"},
        {"key": "b2", "title": "Chisel sharpening jig explained", "text": "honing guide angle for the chisel bevel"},
        {"key": "a3", "title": "Restore a Postgres backup with pg_restore", "text": "restore the dump into a scratch database"},
    ]
    got = clusters.cluster_entries(entries)
    groups = sorted(sorted(c["keys"]) for c in got)
    assert groups == [["a1", "a2", "a3"], ["b1", "b2"], ["c1"]]
    assert got[0]["keys"] == ["a1", "a2", "a3"] and "backup" in got[0]["label"]
    assert "chisel" in next(c["label"] for c in got if c["keys"] == ["b1", "b2"])


def test_clusters_edge_cases():
    assert clusters.cluster_entries([]) == []
    one = clusters.cluster_entries([{"key": "x", "title": "Sourdough basics"}])
    assert one == [{"keys": ["x"], "label": "sourdough basics"}]
    loose = clusters.cluster_entries([{"key": "x", "title": "Postgres 16 replication"}, {"key": "y", "title": "Postgres 16 vacuum"}])
    assert len(loose) == 2, "sharing only the product name is not the same task"


def test_cluster_tokens_fold_accents_plurals_and_stopwords():
    assert clusters.tokens("How to sharpen Chisels in a Café") == ["sharpe", "chisel", "cafe"]
    assert clusters.tokens("automated automation automate") == ["automa"] * 3
    assert clusters.tokens("rules Rule fields") == ["rule", "rule", "field"]


def cluster_distilled(tmp_path):
    for name in ("01-aaa", "02-bbb"):
        (tmp_path / name / "frames").mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "frames" / "001_0-05.jpg").write_bytes(b"jpg")
        (tmp_path / name / "frames" / "002_0-09.jpg").write_bytes(b"jpg")
        (tmp_path / name / "source.json").write_text(json.dumps({"frames": [
            {"index": 1, "stamp": "0:05", "path": "x/001_0-05.jpg"}, {"index": 2, "stamp": "0:09", "path": "x/002_0-09.jpg"}]}), encoding="utf-8")
    d = distilled()
    del d["source"]
    d["sources"] = [
        {"url": "https://example.com/v/aaa", "id": "aaa", "title": "Video A", "ingested_at": "2026-11-07T10:00:00+00:00",
         "work_dir": "01-aaa", "useless_frames": [2]},
        {"url": "https://example.com/v/bbb", "id": "bbb", "title": "Video B", "ingested_at": "2026-11-07T10:00:00+00:00",
         "work_dir": "02-bbb", "confidence": 0.6},
    ]
    d["procedure"] = [{"step": "One", "refs": [{"source": "aaa", "timestamp": "0:10"}, {"source": "bbb", "timestamp": "1:10"}]},
                      {"step": "Two", "source": "bbb", "timestamp": "2:00"}, {"step": "Three"}]
    return d


def test_a_playlist_cluster_forges_one_skill_from_several_videos(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = cluster_distilled(tmp_path)
    (tmp_path / "cluster.json").write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(tmp_path / "cluster.json"), "--dry-run"], env)
    assert "would create" in r.stdout and "2 sources; 3 frames to references, 1 marked useless" in r.stdout
    assert "cite no source" in r.stdout and "'Three'" in r.stdout
    r = run_forge([str(tmp_path / "cluster.json")], env)
    assert json.loads(r.stdout)["sources"] == 2
    skill = tmp_path / "skills" / "demo-task"
    md = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert "**One** _(S1 0:10; S2 1:10)_" in md and "**Two** _(S2 2:00)_" in md and "(confidence 0.6)" in md
    assert sorted(p.name for p in (skill / "references" / "frames" / "aaa").iterdir()) == ["001_0-05.jpg"]
    assert len(list((skill / "references" / "frames" / "bbb").iterdir())) == 2
    stored = json.loads((skill / ".forge" / "skill.json").read_text(encoding="utf-8"))
    assert all("work_dir" not in s and "_work_dir" not in s for s in stored["sources"])


def test_combined_dry_run_over_several_files_and_cluster_notes_refused(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = cluster_distilled(tmp_path)
    (tmp_path / "c1.json").write_text(json.dumps(d), encoding="utf-8")
    n = distilled(url="https://example.com/v/ccc", worthy=False)
    n["slug"] = "a-topic"
    (tmp_path / "n1.json").write_text(json.dumps(n), encoding="utf-8")
    r = run_forge([str(tmp_path / "c1.json"), str(tmp_path / "n1.json"), "--dry-run"], env)
    assert r.returncode == 0 and r.stdout.count("======== ") == 2 and "would create learning note" in r.stdout
    bad = cluster_distilled(tmp_path)
    bad["verdict"]["skill_worthy"] = False
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    r = run_forge([str(tmp_path / "bad.json"), "--dry-run"], env)
    assert r.returncode == 2 and "one distilled.json per video" in r.stderr


def test_whisper_backend_order(monkeypatch):
    import platform
    monkeypatch.setattr(ingest, "setting", lambda name: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert ingest.whisper_backends() == ["mlx", "faster"], "Apple Silicon tries MLX first, faster-whisper as fallback"
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert ingest.whisper_backends() == ["faster"]
    monkeypatch.setattr(ingest, "setting", lambda name: "faster" if name == "SKILLFORGE_WHISPER_BACKEND" else None)
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert ingest.whisper_backends() == ["faster"]


def test_mlx_helper_drops_non_speech_and_loops():
    import whisper_mlx
    assert whisper_mlx.keep({"no_speech_prob": 0.1, "avg_logprob": -0.3, "compression_ratio": 1.4})
    assert not whisper_mlx.keep({"no_speech_prob": 0.9, "avg_logprob": -1.4, "compression_ratio": 1.2})
    assert not whisper_mlx.keep({"no_speech_prob": 0.0, "avg_logprob": -0.2, "compression_ratio": 3.1})
    assert whisper_mlx.MODELS["small"] == "mlx-community/whisper-small-mlx"


def test_frames_download_no_more_video_than_needed():
    assert ingest.video_format(768).startswith("bv*[height<=480][fps<=30]")
    assert ingest.video_format(1280).startswith("bv*[height<=720][fps<=30]")
    assert ingest.video_format(768).endswith("/b")


def test_dry_run_lints_loose_skills(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    d["procedure"][0]["detail"] = "Open the settings (Docs 0:43) " + "and then do many more things " * 10
    d["pitfalls"] = ["Watch out for the trigger", "Rule fires on unrelated edits → no trigger field set → set Trigger Fields"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    out = run_forge([str(f), "--dry-run"], env).stdout
    assert "# lint: step 1 is" in out and "step 1 has a timestamp in its text" in out
    assert "'Watch out for the trigger'" in out and "Rule fires on unrelated" not in out.split("# lint")[-1].split("\n")[0]
    assert out.count("# lint: pitfall") == 1
    tight = distilled()
    tight["pitfalls"] = ["Nothing happens on save → rule inactive → toggle Active"]
    tight["verification"] = ["The field shows the new value after saving"]
    f.write_text(json.dumps(tight), encoding="utf-8")
    assert "# lint" not in run_forge([str(f), "--dry-run"], env).stdout


def loose_skill(url="https://example.com/v/loose", slug="loose-task"):
    """Fails exactly the three tightness rules a real forge slipped past: word budget, step length, pitfall arrows."""
    d = distilled(url=url)
    d["source"]["id"] = slug
    d["slug"] = slug
    d["summary"] = "This summary pads the skill well past its budget. " * 100
    d["procedure"][0]["detail"] = "and then do many more things " * 10
    d["pitfalls"] = ["Watch out for the trigger"]
    return d


LOOSE_FINDINGS = ("SKILL.md is ", "step 1 is ", "pitfall does not lead with the symptom")


def catalog_skills(tmp_path):
    cat = tmp_path / "home" / "catalog.json"
    return json.loads(cat.read_text(encoding="utf-8"))["skills"] if cat.is_file() else {}


def test_write_refuses_a_skill_that_fails_lint(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(loose_skill()), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 2
    assert all(x in r.stderr for x in LOOSE_FINDINGS), r.stderr
    assert "--allow-lint" in r.stderr and str(f) in r.stderr
    assert r.stdout == ""
    assert not (tmp_path / "skills" / "loose-task").exists()
    assert "loose-task" not in catalog_skills(tmp_path)


def test_allow_lint_writes_anyway_and_records_the_findings(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(loose_skill()), encoding="utf-8")
    r = run_forge([str(f), "--allow-lint"], env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "skills" / "loose-task" / "SKILL.md").is_file()
    assert "loose-task" in catalog_skills(tmp_path)
    overridden = json.loads(r.stdout)["lint_overridden"]
    assert len(overridden) == 3 and all(any(x in w for w in overridden) for x in LOOSE_FINDINGS)
    assert all(f"lint overridden (--allow-lint): {w}" in r.stderr for w in overridden)


def test_clean_write_reports_no_overrides(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    assert "lint_overridden" not in json.loads(r.stdout) and "lint" not in r.stderr


def test_update_that_fails_lint_leaves_the_stored_skill_untouched(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    clean = loose_skill()
    clean.update(summary="A summary.", procedure=distilled()["procedure"], pitfalls=distilled()["pitfalls"])
    f.write_text(json.dumps(clean), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    skill = tmp_path / "skills" / "loose-task"
    before = {p: p.read_bytes() for p in (skill / "SKILL.md", skill / ".forge" / "skill.json")}

    f.write_text(json.dumps(loose_skill()), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 2 and all(x in r.stderr for x in LOOSE_FINDINGS)
    assert {p: p.read_bytes() for p in before} == before
    assert catalog_skills(tmp_path)["loose-task"]["version"] == 1


def test_one_failing_file_stops_the_whole_run_before_any_write(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    ok, note, bad = tmp_path / "ok.json", tmp_path / "note.json", tmp_path / "bad.json"
    ok.write_text(json.dumps(distilled(url="https://example.com/v/ok")), encoding="utf-8")
    note.write_text(json.dumps(distilled(url="https://example.com/v/note", worthy=False)), encoding="utf-8")
    bad.write_text(json.dumps(loose_skill()), encoding="utf-8")
    r = run_forge([str(ok), str(note), str(bad)], env)              # the failing file comes last: nothing before it lands
    assert r.returncode == 2
    assert str(bad) in r.stderr and str(ok) not in r.stderr and str(note) not in r.stderr
    assert not (tmp_path / "skills").exists()
    assert not (tmp_path / "home" / "notes").exists()
    assert catalog_skills(tmp_path) == {}


def test_allow_drop_does_not_bypass_the_lint_gate(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(loose_skill()), encoding="utf-8")
    r = run_forge([str(f), "--allow-drop"], env)
    assert r.returncode == 2 and "--allow-lint" in r.stderr
    assert not (tmp_path / "skills" / "loose-task").exists()


def test_dry_run_says_a_write_would_be_refused(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(loose_skill()), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert r.returncode == 0, r.stderr
    assert "# lint: 3 finding(s); a write will refuse without --allow-lint" in r.stdout
    assert not (tmp_path / "skills").exists()


def test_citations_must_name_real_sources_and_claims_go_to_their_video(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = cluster_distilled(tmp_path)
    d["procedure"][1]["source"] = "bbbb"                               # typo
    (tmp_path / "c.json").write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(tmp_path / "c.json"), "--dry-run"], env)
    assert r.returncode == 2 and "['bbbb']" in r.stderr
    d = cluster_distilled(tmp_path)
    d["key_claims"] = [{"claim": "Only in A", "timestamp": "0:12", "source": "aaa"}, {"claim": "General", "timestamp": "0:20"}]
    (tmp_path / "c.json").write_text(json.dumps(d), encoding="utf-8")
    assert run_forge([str(tmp_path / "c.json")], env).returncode == 0
    refs = tmp_path / "skills" / "demo-task" / "references"
    a, b = (refs / "source-aaa.md").read_text(encoding="utf-8"), (refs / "source-bbb.md").read_text(encoding="utf-8")
    assert "Only in A" in a and "Only in A" not in b and "General" in a and "General" in b


def test_playlist_manifest_source_record_is_copy_ready(tmp_path):
    import playlist
    (tmp_path / "03-abc").mkdir()
    (tmp_path / "03-abc" / "source.json").write_text(json.dumps({"meta": {"url": "https://y/watch?v=abc", "id": "abc", "title": "T",
        "uploader": "U", "duration": 258, "ingested_at": "2026-11-07T10:00:00+00:00", "description": "long"}}), encoding="utf-8")
    rec = playlist.source_record(tmp_path / "03-abc", "03-abc")
    assert rec == {"url": "https://y/watch?v=abc", "id": "abc", "title": "T", "uploader": "U", "duration": 258,
                   "ingested_at": "2026-11-07T10:00:00+00:00", "work_dir": "03-abc"}


def test_combined_dry_run_shows_second_note_on_a_topic_as_append(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    for name, url in (("a.json", "https://e/v/1"), ("b.json", "https://e/v/2")):
        d = distilled(url=url, worthy=False)
        d["slug"] = "shared-topic"
        (tmp_path / name).write_text(json.dumps(d), encoding="utf-8")
    out = run_forge([str(tmp_path / "a.json"), str(tmp_path / "b.json"), "--dry-run"], env).stdout
    assert "would create learning note" in out and "would append to learning note" in out


def test_one_video_feeds_at_most_one_skill_and_one_note_per_run(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    (tmp_path / "a.json").write_text(json.dumps(distilled()), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(distilled(worthy=False)), encoding="utf-8")
    r = run_forge([str(tmp_path / "a.json"), str(tmp_path / "b.json"), "--dry-run"], env)
    assert r.returncode == 0, "a long course may yield a skill and a note"
    (tmp_path / "c.json").write_text(json.dumps(distilled(worthy=False)), encoding="utf-8")
    r = run_forge([str(tmp_path / "a.json"), str(tmp_path / "b.json"), str(tmp_path / "c.json"), "--dry-run"], env)
    assert r.returncode == 2 and "appears as a note in both" in r.stderr


def test_list_shows_note_topics(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    (tmp_path / "home" / "notes").mkdir(parents=True)
    (tmp_path / "home" / "notes" / "docker.md").write_text("x", encoding="utf-8")
    assert json.loads(run_forge(["--list"], env).stdout)["note_topics"] == ["docker"]


def test_music_only_captions_are_flagged():
    music = [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(["[Music]"] * 8 + ["Heat. Heat."] * 6 + ["hello there everyone"])]
    assert ingest.non_speech_share(music) > 0.9
    speech = [{"start": i, "end": i + 1, "text": f"step {i} open the menu"} for i in range(20)] + [{"start": 30, "end": 31, "text": "[Music]"}]
    assert ingest.non_speech_share(speech) < 0.1


def test_tightening_rewrite_of_a_merged_skill_is_allowed_but_deletion_is_not(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"].append({"step": "Four, only in the second video, explained at length", "timestamp": "2:00"})
    f.write_text(json.dumps(second), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0          # step Four now cites ('def', '2:00')
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text(encoding="utf-8"))
    tight = {k: v for k, v in stored.items() if k not in ("sources", "forged_at", "version")}
    tight["source"] = second["source"]
    tight["procedure"] = [dict(st) for st in stored["procedure"]]
    tight["procedure"][-1]["step"] = "Do four"                # reworded, citations kept
    f.write_text(json.dumps(tight), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    first_again = distilled()                                  # old single-source file: step Four's evidence is gone
    f.write_text(json.dumps(first_again), encoding="utf-8")
    r = run_forge([str(f)], env)
    assert r.returncode == 2 and "Do four" in r.stderr


def test_legacy_notes_can_be_adopted_into_a_topic(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    notes = tmp_path / "home" / "notes"
    notes.mkdir(parents=True)
    for i in (1, 2):
        (notes / f"2026-11-07-old-{i}.md").write_text(
            f"# Old video {i}\n\n- URL: https://e/v/{i}\n- By: Someone\n- Date: 2026-11-07\n- Skill-worthy: no (confidence 0.8)\n\n## Takeaway\nPoint {i}.\n", encoding="utf-8")
    r = run_forge(["--adopt-note", str(notes / "2026-11-07-old-1.md"), str(notes / "2026-11-07-old-2.md"), "--topic", "Bike Tubes"], env)
    assert r.returncode == 0, r.stderr
    text = (notes / "bike-tubes.md").read_text(encoding="utf-8")
    assert text.count("<!-- forge-note: ") == 2 and "## 2026-11-07 · Old video 1" in text and "### Takeaway" in text
    assert not list(notes.glob("2026-11-07-*"))
    cat = json.loads((tmp_path / "home" / "catalog.json").read_text(encoding="utf-8"))
    assert {n["slug"] for n in cat["notes"]} == {"bike-tubes"}
    d = distilled(url="https://e/v/3", worthy=False)                 # a new source on the topic appends
    d["slug"] = "bike-tubes"
    (tmp_path / "d.json").write_text(json.dumps(d), encoding="utf-8")
    assert json.loads(run_forge([str(tmp_path / "d.json")], env).stdout)["action"] == "append"


def test_rewrite_without_evidence_keeps_frames_already_in_references(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    work = tmp_path / "work"
    (work / "frames").mkdir(parents=True)
    (work / "frames" / "001_0-10.jpg").write_bytes(b"jpg")
    (work / "source.json").write_text(json.dumps({"frames": [{"index": 1, "stamp": "0:10", "path": "x/001_0-10.jpg"}]}), encoding="utf-8")
    (work / "distilled.json").write_text(json.dumps(distilled()), encoding="utf-8")
    assert run_forge([str(work / "distilled.json")], env).returncode == 0
    elsewhere = tmp_path / "rewrite"
    elsewhere.mkdir()
    d = distilled()
    d["procedure"][0]["step"] = "One, tighter"
    (elsewhere / "distilled.json").write_text(json.dumps(d), encoding="utf-8")        # no source.json next to it
    assert run_forge([str(elsewhere / "distilled.json")], env).returncode == 0
    refs = tmp_path / "skills" / "demo-task" / "references"
    assert (refs / "frames" / "abc" / "001_0-10.jpg").is_file()
    assert "frames/abc/001_0-10.jpg" in (refs / "source-abc.md").read_text(encoding="utf-8")


def test_section_parsing_and_frame_offsets(tmp_path):
    import shutil
    assert ingest.parse_section("1:17:00-1:37:30") == (4620.0, 5850.0)
    assert ingest.parse_section("90-120") == (90.0, 120.0)
    if not shutil.which("ffmpeg"):
        return
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=10:r=25", str(video)], check=True)
    frames = ingest.extract_frames(video, tmp_path, max_frames=4, duration=10.0, width=320, no_dedup=True, offset=4620.0)
    assert frames and all(f["time"] >= 4620 for f in frames) and frames[0]["stamp"].startswith("1:17:")


def test_thinning_fills_the_largest_gaps_not_the_intro_burst():
    intro = [(Path(f"i{i}.jpg"), float(i)) for i in range(10)]                  # a 10 s burst of "starting soon" cuts
    work = [(Path(f"w{t}.jpg"), float(t)) for t in (100, 150, 200, 250, 300, 350, 400, 450, 500)]
    kept = ingest.thin_by_time(intro + work, max_frames=6, duration=1000)
    times = sorted(t for _, t in kept)
    assert len(kept) == 6 and sum(1 for t in times if t < 10) == 1, f"spare slots went to the intro burst: {times}"
    assert 100.0 in times, "the frame splitting the largest gap is taken first"


def test_bare_media_url_keeps_the_given_url():
    src = INGEST.read_text(encoding="utf-8")
    assert 'url if info.get("extractor_key") == "Generic"' in src
    assert 'meta["duration"] = local_metadata(media).get("duration")' in src


def test_lint_flags_demo_values_in_instructions(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    d["procedure"][1]["detail"] = "Set the margin (the demo uses 15%)"
    d["verification"] = ["Check it"]
    d["pitfalls"] = ["Nothing happens → wrong field → set it"]
    (tmp_path / "d.json").write_text(json.dumps(d), encoding="utf-8")
    out = run_forge([str(tmp_path / "d.json"), "--dry-run"], env).stdout
    assert "# lint: demo values in step 2" in out


PROFILE_TEXT = """# profile
## Role
Example role
## Tools
- PostgreSQL: 16
- ExampleERP: 17.0   # self-hosted
## Core work
- nightly backups
"""


def profile_env(tmp_path, monkeypatch, text=PROFILE_TEXT):
    env = env_for(tmp_path, monkeypatch)
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "profile.md").write_text(text, encoding="utf-8")
    return env


def test_profile_tools_are_parsed_and_printed(tmp_path, monkeypatch):
    env = profile_env(tmp_path, monkeypatch)
    out = run_forge(["--profile"], env).stdout
    assert '"postgresql": "16"' in out and '"exampleerp": "17.0"' in out and "Example role" in out
    env2 = env_for(tmp_path / "other", monkeypatch)
    assert "no profile at" in run_forge(["--profile"], env2).stdout


def test_version_mismatch_with_profile_becomes_a_pitfall_and_is_idempotent(tmp_path, monkeypatch):
    env = profile_env(tmp_path, monkeypatch)
    d = distilled()
    d["source"]["versions_shown"] = {"ExampleERP": "19", "PostgreSQL": "16.2"}
    d["apply_to_me"] = ["Use it for the nightly backup"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    dry = run_forge([str(f), "--dry-run"], env).stdout
    assert "# profile: version mismatch" in dry and "ExampleERP 19" in dry and "PostgreSQL 16.2" not in dry
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "it uses ExampleERP 19, your profile says 17.0" in md
    assert json.loads(run_forge([str(f)], env).stdout)["written"] == "unchanged"


def test_profile_without_apply_to_me_is_flagged(tmp_path, monkeypatch):
    env = profile_env(tmp_path, monkeypatch)
    d = distilled()
    d.pop("apply_to_me")
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    assert "apply_to_me is empty although a profile exists" in run_forge([str(f), "--dry-run"], env).stdout


def test_no_profile_means_no_version_pitfalls(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    d["source"]["versions_shown"] = {"ExampleERP": "19"}
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    out = run_forge([str(f), "--dry-run"], env).stdout
    assert "# profile" not in out and "your profile says" not in out


def test_only_the_example_profile_ships():
    refs = ROOT / "skills" / "forge" / "references"
    assert (refs / "profile.example.md").is_file()
    assert "<Tool name>: <version>" in (refs / "profile.example.md").read_text(encoding="utf-8")
    assert not list(ROOT.rglob("profile.md")), "a real profile must never be committed"


def test_export_zips_skill_with_skill_md_at_root_and_no_forge_dir(tmp_path, monkeypatch):
    import zipfile
    env = env_for(tmp_path, monkeypatch)
    env["SKILLFORGE_EXPORT_DIR"] = str(tmp_path / "export")
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    (tmp_path / "skills" / "not-forged").mkdir()
    (tmp_path / "skills" / "not-forged" / "SKILL.md").write_text("x", encoding="utf-8")
    r = run_forge(["--export", "all"], env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert [o["skill"] for o in out] == ["demo-task"], "only forged skills are exported"
    names = zipfile.ZipFile(tmp_path / "export" / "demo-task.zip").namelist()
    assert "SKILL.md" in names and any(n.startswith("references/") for n in names)
    assert not any(n.startswith(".forge") for n in names)
    assert run_forge(["--export", "no-such-skill"], env).returncode == 2


def test_ingest_never_lets_child_processes_read_stdin(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        __import__("pytest").skip("ffmpeg missing")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=160x120:d=3:r=25", str(video)], check=True)
    # Run frame extraction with a pipe on stdin, like a `while read` loop; the pipe must still be unread afterwards.
    code = ("import sys; sys.path.insert(0, %r); import ingest, pathlib; "
            "ingest.extract_frames(pathlib.Path(%r), pathlib.Path(%r), 4, 3.0, 160, True); print(sys.stdin.read())"
            % (str(INGEST.parent), str(video), str(tmp_path)))
    r = subprocess.run([sys.executable, "-c", code], input="next line one\nnext line two\n", capture_output=True, text=True, encoding="utf-8")
    assert "next line one" in r.stdout and "next line two" in r.stdout, r.stderr


def lens_distilled(url="https://example.com/v/l1", sid="l1", n=6):
    return {
        "verdict": {"kind": "lens", "confidence": 0.7, "reason": "principles with timestamps"},
        "source": {"url": url, "id": sid, "title": "Talk", "uploader": "Someone", "ingested_at": "2026-11-07T10:00:00+00:00"},
        "slug": "communication-lens", "name": "Communication lens",
        "description": "Principles for clear speaking. Use when the user asks to prepare a talk or a hard conversation.",
        "when_to_apply": ["the user is preparing a talk", "the user asks how to phrase a hard message"],
        "principles": [{"text": f"Principle {i}", "detail": "Why it works.", "timestamp": f"0:{10 + i}"} for i in range(n)],
        "mistakes": [{"text": "Rambling before the point", "timestamp": "1:00"}],
        "examples": [{"situation": "Asking for a deadline change", "apply": "Lead with the ask, then one reason."},
                     {"situation": "Opening a talk", "apply": "Start with the outcome for the listener."}],
        "apply_to_me": ["Use it for supplier calls"],
    }


def test_lens_renders_principles_mistakes_and_examples(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(lens_distilled()), encoding="utf-8")
    dry = run_forge([str(f), "--dry-run"], env).stdout
    assert "would create lens" in dry and "# lint" not in dry
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "communication-lens" / "SKILL.md").read_text(encoding="utf-8")
    for part in ("## When to apply", "## Principles", "1. **Principle 0** Why it works. _(source 0:10)_",
                 "## Common mistakes", "## Worked examples", "**Opening a talk**"):
        assert part in md
    cat = json.loads((tmp_path / "home" / "catalog.json").read_text(encoding="utf-8"))
    assert cat["skills"]["communication-lens"]["kind"] == "lens"


def test_lens_needs_five_to_fifteen_principles_and_two_to_three_examples(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(lens_distilled(n=3)), encoding="utf-8")
    assert run_forge([str(f), "--dry-run"], env).returncode == 2
    d = lens_distilled()
    d["examples"] = d["examples"][:1]
    f.write_text(json.dumps(d), encoding="utf-8")
    assert run_forge([str(f), "--dry-run"], env).returncode == 2


def test_lens_merges_by_topic_and_supersedes_the_notes_it_absorbs(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    for url in ("https://example.com/v/l1", "https://example.com/v/l2"):
        n = distilled(url=url, worthy=False)
        n["slug"] = "communication"
        f.write_text(json.dumps(n), encoding="utf-8")
        assert run_forge([str(f)], env).returncode == 0
    a = lens_distilled()
    a["supersedes_notes"] = True
    f.write_text(json.dumps(a), encoding="utf-8")
    out = json.loads(run_forge([str(f)], env).stdout)
    assert out["notes_moved"] == 1
    note = (tmp_path / "home" / "notes" / "communication.md").read_text(encoding="utf-8")
    assert "v/l1" not in note and "v/l2" in note
    b = lens_distilled(url="https://example.com/v/l2", sid="l2")
    b["principles"] = a["principles"] + [{"text": "A principle only the second talk has", "timestamp": "2:00"}]
    b["supersedes_notes"] = True
    f.write_text(json.dumps(b), encoding="utf-8")
    out = json.loads(run_forge([str(f)], env).stdout)
    assert out["sources"] == 2 and out["version"] == 2
    md = (tmp_path / "skills" / "communication-lens" / "SKILL.md").read_text(encoding="utf-8")
    assert "**Principle 0** Why it works. _(S1 0:10)_" in md and "_(S2 2:00)_" in md
    assert not (tmp_path / "home" / "notes" / "communication.md").exists()
    stale = lens_distilled()                                    # an old single-source file cannot drop S2's principle
    f.write_text(json.dumps(stale), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 2


def test_export_for_chat_has_no_frames_and_no_profile_context(tmp_path, monkeypatch):
    import zipfile
    env = env_for(tmp_path, monkeypatch)
    env["SKILLFORGE_EXPORT_DIR"] = str(tmp_path / "export")
    work = tmp_path / "work"
    (work / "frames").mkdir(parents=True)
    (work / "frames" / "001_0-10.jpg").write_bytes(b"jpg")
    (work / "source.json").write_text(json.dumps({"frames": [{"index": 1, "stamp": "0:10", "path": "x/001_0-10.jpg"}]}), encoding="utf-8")
    d = distilled()
    d["apply_to_me"] = ["Private line about my own stack"]
    (work / "distilled.json").write_text(json.dumps(d), encoding="utf-8")
    assert run_forge([str(work / "distilled.json")], env).returncode == 0
    assert "Private line" in (tmp_path / "skills" / "demo-task" / "references" / "source-abc.md").read_text(encoding="utf-8")
    assert run_forge(["--export", "demo-task"], env).returncode == 0
    z = zipfile.ZipFile(tmp_path / "export" / "demo-task.zip")
    assert not any("frames" in n for n in z.namelist())
    text = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".md"))
    assert "Private line" not in text and "Worth trying" not in text and "Key claims" in text


def test_a_promotion_and_the_old_note_cannot_share_a_run(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    lens = lens_distilled()
    lens["supersedes_notes"] = True
    (tmp_path / "lens.json").write_text(json.dumps(lens), encoding="utf-8")
    (tmp_path / "note.json").write_text(json.dumps(distilled(url=lens["source"]["url"], worthy=False)), encoding="utf-8")
    r = run_forge([str(tmp_path / "lens.json"), str(tmp_path / "note.json"), "--dry-run"], env)
    assert r.returncode == 2 and "supersedes the note" in r.stderr


def test_scripts_print_utf8_even_when_the_pipe_defaults_to_a_legacy_codepage(tmp_path, monkeypatch):
    # Windows pipes default to cp1252, which has no "→": forge.py crashed printing its own lint lines.
    env = env_for(tmp_path, monkeypatch)
    env["PYTHONIOENCODING"] = "cp1252"
    d = distilled()
    d["pitfalls"] = ["Nothing happens → wrong field → set it"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    r = run_forge([str(f), "--dry-run"], env)
    assert r.returncode == 0, r.stderr
    assert "→" in r.stdout


def test_text_io_always_names_its_encoding():
    # Without encoding=, Python uses the locale codepage on Windows and garbles or crashes on non-ASCII.
    import ast
    files = sorted((ROOT / "skills" / "forge" / "scripts").glob("*.py")) + [Path(__file__)]
    bad = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            kws = {k.arg for k in node.keywords}
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            text_mode = any(k.arg in ("text", "universal_newlines") and getattr(k.value, "value", False) is True
                            for k in node.keywords)
            if (name in ("read_text", "write_text") or (name == "run" and text_mode)) and "encoding" not in kws:
                bad.append(f"{path.name}:{node.lineno} {name}")
    assert not bad, bad


def test_entry_scripts_force_utf8_stdout():
    for name in ("forge", "ingest", "playlist", "whisper_local", "whisper_mlx"):
        src = (ROOT / "skills" / "forge" / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        main_block = src.split('if __name__ == "__main__":', 1)[1]
        assert "utf8_stdio()" in main_block or "reconfigure(" in main_block, name


def test_lint_all_reports_only_the_skills_that_fail(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(distilled()), encoding="utf-8")
    assert run_forge([str(f)], env).returncode == 0
    r = run_forge(["--lint-all"], env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"checked": 1, "failing": {}}

    f.write_text(json.dumps(loose_skill()), encoding="utf-8")
    assert run_forge([str(f), "--allow-lint"], env).returncode == 0
    r = run_forge(["--lint-all"], env)
    assert r.returncode == 1
    report = json.loads(r.stdout)
    assert report["checked"] == 2 and list(report["failing"]) == ["loose-task"]
    assert all(any(x in w for w in report["failing"]["loose-task"]) for x in LOOSE_FINDINGS)


def test_two_skill_files_with_one_slug_are_refused_before_any_write(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    first, second = distilled(url="https://example.com/v/one"), distilled(url="https://example.com/v/two")
    second["source"]["id"] = "two"
    second["verdict"]["kind"] = "lens"          # a lens shares the skill folder namespace, so it collides too
    a.write_text(json.dumps(first), encoding="utf-8")
    b.write_text(json.dumps(second), encoding="utf-8")
    r = run_forge([str(a), str(b)], env)
    assert r.returncode == 2 and "'demo-task'" in r.stderr and str(a) in r.stderr and str(b) in r.stderr
    assert not (tmp_path / "skills").exists()


def test_notes_on_one_topic_still_share_a_slug_in_one_run(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    first, second = distilled(url="https://example.com/v/one", worthy=False), distilled(url="https://example.com/v/two", worthy=False)
    second["source"]["id"] = "two"
    a.write_text(json.dumps(first), encoding="utf-8")
    b.write_text(json.dumps(second), encoding="utf-8")
    r = run_forge([str(a), str(b)], env)
    assert r.returncode == 0, r.stderr
    notes = list((tmp_path / "home" / "notes").glob("*.md"))
    assert [n.name for n in notes] == ["demo-task.md"]
    text = notes[0].read_text(encoding="utf-8")
    assert "https://example.com/v/one" in text and "https://example.com/v/two" in text


def test_plugin_version_matches_the_newest_changelog_entry():
    version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = next(line[3:].strip() for line in changelog.splitlines() if line.startswith("## "))
    assert newest == version, f"plugin.json says {version}, CHANGELOG's newest entry is {newest}"


def test_every_user_facing_flag_is_in_the_skill_flags_reference():
    import re
    ref = (ROOT / "skills" / "forge" / "SKILL.md").read_text(encoding="utf-8").split("## Flags reference", 1)[1]
    missing = []
    for name in ("forge", "ingest", "playlist"):           # the whisper helpers are only called by ingest.py
        src = (ROOT / "skills" / "forge" / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        flags = re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src)
        assert flags, name
        missing += [f"{name}.py {x}" for x in flags if not re.search(re.escape(x) + r"(?![a-z0-9-])", ref)]
    assert not missing, f"document these in SKILL.md's Flags reference: {missing}"


def test_no_tracked_file_contains_a_home_directory_path():
    import re
    home = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/|[A-Za-z]:\\+Users\\+")
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    hits = []
    for name in filter(None, files):
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits += [f"{name}:{i}" for i, line in enumerate(text.splitlines(), 1) if home.search(line)]
    assert not hits, f"machine-specific home paths in tracked files (use ~ or a placeholder): {hits}"
