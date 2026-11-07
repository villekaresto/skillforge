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


def test_frontmatter_description_is_quoted_yaml(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled()
    d["description"] = 'Reconcile banks in ERP: match lines, #tags, "quotes" - use for \'reconcile\''
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
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
    f.write_text(json.dumps(distilled()))
    assert run_forge([str(f)], env).returncode == 0
    r = run_forge([str(f)], env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["written"] == "unchanged"
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text())
    assert stored["version"] == 1 and len(stored["sources"]) == 1

    d = distilled()
    d["pitfalls"].append("A second pitfall found on re-study")
    f.write_text(json.dumps(d))
    r = run_forge([str(f)], env)
    assert json.loads(r.stdout)["written"] == "update"
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text())
    assert stored["version"] == 2 and len(stored["sources"]) == 1


def test_playlist_urls_are_detected_but_single_videos_are_not():
    yes = ["https://www.youtube.com/playlist?list=PL1oT_5mNYVTOFA45zQO939vwvyodldvIk", "https://www.youtube.com/@ERP",
           "https://www.youtube.com/channel/UCkQPikELWZFLgQNHd73jkdg/videos"]
    no = ["https://www.youtube.com/watch?v=1IQB_q-2YW8&list=PL1oT_5mNYVTOFA45zQO939vwvyodldvIk", "https://youtu.be/1IQB_q-2YW8",
          "https://www.youtube.com/shorts/96HKB_QcRN0", "https://www.tiktok.com/@ERP/video/7695791676148796693", "https://vimeo.com/123"]
    assert all(ingest.is_playlist_url(u) for u in yes)
    assert not any(ingest.is_playlist_url(u) for u in no)


def test_reused_out_dir_drops_previous_evidence(tmp_path):
    for name in ("source.json", "transcript.md", "gemini.md", "playlist.json", "media.en.vtt", "audio.m4a"):
        (tmp_path / name).write_text("stale")
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
    assert "av<" not in INGEST.read_text(), "pinning PyAV forces source builds on platforms without wheels"


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
    f.write_text(json.dumps(first))
    assert run_forge([str(f)], env).returncode == 0

    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"].insert(1, {"step": "One and a half", "timestamp": "1:14"})
    f.write_text(json.dumps(second))
    r = run_forge([str(f)], env)
    assert json.loads(r.stdout)["written"] == "update"
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text()
    assert "**One** _(S1 0:10)_" in md and "**One and a half** _(S2 1:14)_" in md and "**Three** _(S1 0:30)_" in md
    assert "- S1: [Demo video]" in md and "- S2: [Demo video]" in md
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text())
    assert [s["step"] for s in stored["procedure"]].count("One") == 1, "no duplicated steps after a merge"

    r = run_forge([str(f)], env)          # merging the same second source again changes nothing
    assert json.loads(r.stdout)["written"] == "unchanged"


def test_note_keeps_caveats(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled(worthy=False)
    d["contestable"] = ["Title oversells this as B2B integration"]
    d["assumed_knowledge"] = ["what a purchase order is"]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    r = run_forge([str(f), "--dry-run"], env)
    assert "Title oversells this as B2B integration" in r.stdout and "what a purchase order is" in r.stdout
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
    f.write_text(json.dumps(distilled()))
    assert run_forge([str(f)], env).returncode == 0
    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"][0] = {"step": "One", "refs": [{"source": "abc", "timestamp": "0:10"}, {"source": "def", "timestamp": "0:38"}]}
    f.write_text(json.dumps(second))
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text()
    assert "**One** _(S1 0:10; S2 0:38)_" in md


def test_reforging_an_old_source_cannot_silently_drop_merged_steps(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    first = distilled()
    f.write_text(json.dumps(first))
    assert run_forge([str(f)], env).returncode == 0
    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["procedure"].append({"step": "Four, only in the second video", "timestamp": "2:00"})
    f.write_text(json.dumps(second))
    assert run_forge([str(f)], env).returncode == 0

    f.write_text(json.dumps(first))                      # stale single-source distilled for source 1
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
    f.write_text(json.dumps(distilled(worthy=False)))
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
    assert "NO_SPEECH_FRAMES if frames else NO_SPEECH" in INGEST.read_text()


def test_original_language_subtitle_track_wins_over_translation(tmp_path):
    big_translation = tmp_path / "media.en.vtt"
    big_translation.write_text("x" * 5000)
    orig = tmp_path / "media.en-orig.vtt"
    orig.write_text("x" * 10)
    assert ingest.pick_subtitle([big_translation, orig], "en") == orig
    assert ingest.pick_subtitle([big_translation], "en") == big_translation


def test_untimed_claims_render_without_empty_brackets(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = distilled(worthy=False)
    d["key_claims"] = [{"claim": "From the description only"}]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    r = run_forge([str(f), "--dry-run"], env)
    assert "- From the description only" in r.stdout and "[]" not in r.stdout


def test_whisper_language_comes_from_the_user_not_youtube_metadata():
    src = INGEST.read_text()
    assert 'whisper_language(meta.get("language")' not in src


def test_one_note_file_per_topic_with_a_section_per_source(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    a = distilled(worthy=False)
    f.write_text(json.dumps(a))
    assert json.loads(run_forge([str(f)], env).stdout)["action"] == "create"
    a["summary"] = "Re-studied summary."
    f.write_text(json.dumps(a))
    assert json.loads(run_forge([str(f)], env).stdout)["action"] == "update"   # same source: its section is replaced
    b = distilled(url="https://example.com/v/other", worthy=False)
    b["source"]["title"] = "Second video"
    f.write_text(json.dumps(b))
    r = run_forge([str(f), "--dry-run"], env)
    assert "would append to learning note" in r.stdout
    assert json.loads(run_forge([str(f)], env).stdout)["action"] == "append"
    notes = list((tmp_path / "home" / "notes").glob("*.md"))
    assert [n.name for n in notes] == ["demo-task.md"]
    text = notes[0].read_text()
    assert text.count("<!-- forge-note: ") == 2 and "Re-studied summary." in text and "A summary." in text
    assert sum(l.startswith("## ") for l in text.splitlines()) == 2 and "· Demo video" in text and "· Second video" in text
    cat = json.loads((tmp_path / "home" / "catalog.json").read_text())
    assert len(cat["notes"]) == 2 and {n["file"] for n in cat["notes"]} == {str(notes[0])}

    b["slug"] = "another-topic"                                    # the second source is re-filed under another topic
    f.write_text(json.dumps(b))
    assert run_forge([str(f)], env).returncode == 0
    assert notes[0].read_text().count("<!-- forge-note: ") == 1
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
        (self.out / "media.info.json").write_text(json.dumps({"id": "abcdefghijk", "title": "T", "duration": 60}))
        if len(self.calls) > self.succeed_after and "--write-auto-subs" in cmd:
            (self.out / "media.en.vtt").write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n")
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
    (work / "source.json").write_text(json.dumps({"frames": frames}))
    d = distilled()
    d["useless_frames"] = [2]
    (work / "distilled.json").write_text(json.dumps(d))
    r = run_forge([str(work / "distilled.json"), "--dry-run"], env)
    assert "2 frames to references, 1 marked useless" in r.stdout
    assert run_forge([str(work / "distilled.json")], env).returncode == 0
    refs = tmp_path / "skills" / "demo-task" / "references"
    assert sorted(p.name for p in (refs / "frames" / "abc").iterdir()) == ["001_0-10.jpg", "003_0-30.jpg"]
    notes = (refs / "source-abc.md").read_text()
    assert "frames/abc/003_0-30.jpg" in notes and "002_0-20" not in notes
    stored = json.loads((tmp_path / "skills" / "demo-task" / ".forge" / "skill.json").read_text())
    assert "useless_frames" not in stored and stored["sources"][0]["useless_frames"] == [2]

    d["useless_frames"] = [1, 2]                              # re-study changes only the frame verdicts
    (work / "distilled.json").write_text(json.dumps(d))
    r = run_forge([str(work / "distilled.json")], env)
    assert json.loads(r.stdout)["written"] == "unchanged"
    assert [p.name for p in (refs / "frames" / "abc").iterdir()] == ["003_0-30.jpg"]


def test_merged_pitfalls_and_decisions_cite_their_source(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    first = distilled()
    first["pitfalls"] = ["Watch out for the old trap (0:15)"]
    first["decision_points"] = [{"when": "Small job", "then": "do it by hand"}]
    f.write_text(json.dumps(first))
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text()
    assert "- Watch out for the old trap (0:15)" in md, "single-source skills render pitfalls unchanged"

    second = distilled(url="https://example.com/v/def")
    second["source"]["id"] = "def"
    second["pitfalls"] = first["pitfalls"] + ["A new trap shows up as a red banner (1:20)"]
    second["decision_points"] = first["decision_points"] + [{"when": "Big job", "then": "use the machine", "timestamp": "2:05"}]
    f.write_text(json.dumps(second))
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text()
    assert "- Watch out for the old trap. _(S1 0:15)_" in md
    assert "- A new trap shows up as a red banner. _(S2 1:20)_" in md
    assert "- **Small job** → do it by hand _(S1)_" in md and "- **Big job** → use the machine _(S2 2:05)_" in md

    r = run_forge([str(f)], env)                                   # idempotent with the normalised pitfalls
    assert json.loads(r.stdout)["written"] == "unchanged"


def test_legacy_per_video_note_is_replaced_by_the_topic_section(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    notes = tmp_path / "home" / "notes"
    notes.mkdir(parents=True)
    legacy = notes / "2026-11-07-old-video-name.md"
    legacy.write_text("# Demo video\n\n- Skill-worthy: no\n")
    d = distilled(worthy=False)
    (tmp_path / "home" / "catalog.json").write_text(json.dumps({"skills": {}, "notes": [
        {"date": "2026-11-07", "url": d["source"]["url"], "title": "Demo video", "file": str(legacy), "reason": "old"}]}))
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    assert run_forge([str(f)], env).returncode == 0
    assert not legacy.exists() and (notes / "demo-task.md").is_file()


def test_restudied_source_updates_its_confidence(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    f = tmp_path / "d.json"
    d = distilled()
    f.write_text(json.dumps(d))
    assert run_forge([str(f)], env).returncode == 0
    d["verdict"]["confidence"] = 0.62
    d["pitfalls"].append("A sharper pitfall")
    f.write_text(json.dumps(d))
    assert run_forge([str(f)], env).returncode == 0
    md = (tmp_path / "skills" / "demo-task" / "SKILL.md").read_text()
    assert "(confidence 0.62)" in md and "(confidence 0.8)" not in md


sys.path.insert(0, str(INGEST.parent))
import clusters  # noqa: E402


def test_clusters_group_videos_on_the_same_task():
    entries = [
        {"key": "a1", "title": "ERP 17 automation rules tutorial", "text": "create an automation rule with a trigger and an action"},
        {"key": "b1", "title": "Bank reconciliation in ERP 17", "text": "reconcile bank statement lines with invoices"},
        {"key": "a2", "title": "Automated actions in ERP Studio", "text": "automation trigger on save then add an action to update the record"},
        {"key": "c1", "title": "ERP 17 inventory reordering rules", "text": "replenishment with min max reordering rules"},
        {"key": "b2", "title": "ERP bank reconciliation models explained", "text": "reconciliation models match bank lines automatically"},
        {"key": "a3", "title": "Server actions and automation rules in ERP 17", "text": "automation rules trigger server actions"},
    ]
    got = clusters.cluster_entries(entries)
    groups = sorted(sorted(c["keys"]) for c in got)
    assert groups == [["a1", "a2", "a3"], ["b1", "b2"], ["c1"]]
    assert got[0]["keys"] == ["a1", "a2", "a3"] and "automation" in got[0]["label"]
    assert "reconciliation" in next(c["label"] for c in got if c["keys"] == ["b1", "b2"])


def test_clusters_edge_cases():
    assert clusters.cluster_entries([]) == []
    one = clusters.cluster_entries([{"key": "x", "title": "craft basics"}])
    assert one == [{"keys": ["x"], "label": "craft basics"}]
    loose = clusters.cluster_entries([{"key": "x", "title": "ERP 17 sales"}, {"key": "y", "title": "ERP 17 payroll"}])
    assert len(loose) == 2, "sharing only the product name is not the same task"


def test_cluster_tokens_fold_accents_plurals_and_stopwords():
    assert clusters.tokens("How to reconcile Invoices in a Café") == ["reconc", "invoic", "cafe"]
    assert clusters.tokens("automated automation automate") == ["automa"] * 3
    assert clusters.tokens("rules Rule fields") == ["rule", "rule", "field"]


def cluster_distilled(tmp_path):
    for name in ("01-aaa", "02-bbb"):
        (tmp_path / name / "frames").mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "frames" / "001_0-05.jpg").write_bytes(b"jpg")
        (tmp_path / name / "frames" / "002_0-09.jpg").write_bytes(b"jpg")
        (tmp_path / name / "source.json").write_text(json.dumps({"frames": [
            {"index": 1, "stamp": "0:05", "path": "x/001_0-05.jpg"}, {"index": 2, "stamp": "0:09", "path": "x/002_0-09.jpg"}]}))
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
    (tmp_path / "cluster.json").write_text(json.dumps(d))
    r = run_forge([str(tmp_path / "cluster.json"), "--dry-run"], env)
    assert "would create" in r.stdout and "2 sources; 3 frames to references, 1 marked useless" in r.stdout
    assert "1 step(s) cite no source" in r.stdout and "'Three'" in r.stdout
    r = run_forge([str(tmp_path / "cluster.json")], env)
    assert json.loads(r.stdout)["sources"] == 2
    skill = tmp_path / "skills" / "demo-task"
    md = (skill / "SKILL.md").read_text()
    assert "**One** _(S1 0:10; S2 1:10)_" in md and "**Two** _(S2 2:00)_" in md and "(confidence 0.6)" in md
    assert sorted(p.name for p in (skill / "references" / "frames" / "aaa").iterdir()) == ["001_0-05.jpg"]
    assert len(list((skill / "references" / "frames" / "bbb").iterdir())) == 2
    stored = json.loads((skill / ".forge" / "skill.json").read_text())
    assert all("work_dir" not in s and "_work_dir" not in s for s in stored["sources"])


def test_combined_dry_run_over_several_files_and_cluster_notes_refused(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch)
    d = cluster_distilled(tmp_path)
    (tmp_path / "c1.json").write_text(json.dumps(d))
    n = distilled(url="https://example.com/v/ccc", worthy=False)
    n["slug"] = "a-topic"
    (tmp_path / "n1.json").write_text(json.dumps(n))
    r = run_forge([str(tmp_path / "c1.json"), str(tmp_path / "n1.json"), "--dry-run"], env)
    assert r.returncode == 0 and r.stdout.count("======== ") == 2 and "would create learning note" in r.stdout
    bad = cluster_distilled(tmp_path)
    bad["verdict"]["skill_worthy"] = False
    (tmp_path / "bad.json").write_text(json.dumps(bad))
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
