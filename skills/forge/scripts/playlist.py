#!/usr/bin/env python3
"""Playlist mode: ingest every entry of a playlist or channel, then propose one cluster per task.

  playlist.py "<playlist url>" [--limit N] [--select 1,3,5] [--sleep S] [--out-dir DIR] [ingest flags...]

Writes, in the playlist folder (default ~/.skillforge/work/playlist-<title>-<timestamp>):
  NN-<video id>/      one ingest work dir per entry (transcript.md, source.json, frames/)
  playlist.json       manifest: entries with their work dirs, ingest summaries, errors, and the proposed clusters
Unknown flags (--frames 30, --engine local, --lang es ...) are passed to ingest.py for every entry.
Claude then studies each entry, adjusts the clusters, writes one distilled.json per cluster (with "sources": [...]),
and forges them all in one combined dry-run: forge.py <cluster files...> --dry-run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import clusters  # noqa: E402
import ingest  # noqa: E402


def cluster_input(key: str, work: Path) -> dict | None:
    meta_file = work / "source.json"
    if not meta_file.is_file():
        return None
    src = json.loads(meta_file.read_text(encoding="utf-8"))
    meta = src.get("meta", {})
    text = " ".join(s["text"] for s in src.get("transcript", []))[:3000]
    return {"key": key, "title": meta.get("title") or "", "chapters": [c.get("title") or "" for c in meta.get("chapters") or []],
            "tags": meta.get("tags") or [], "text": text}


def source_record(work: Path, key: str) -> dict | None:
    """The entry's `sources[]` item for a cluster distilled.json, ready to copy (add useless_frames after viewing frames)."""
    meta_file = work / "source.json"
    if not meta_file.is_file():
        return None
    m = json.loads(meta_file.read_text(encoding="utf-8")).get("meta", {})
    return {"url": m.get("url"), "id": m.get("id"), "title": m.get("title"), "uploader": m.get("uploader"),
            "duration": m.get("duration"), "ingested_at": m.get("ingested_at"), "work_dir": key}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--limit", type=int, default=25, help="ingest at most N entries (default 25)")
    ap.add_argument("--select", help="comma-separated 1-based entry numbers to ingest instead of the first N")
    ap.add_argument("--sleep", type=float, default=10.0, help="seconds between entries, to stay under YouTube rate limits")
    ap.add_argument("--out-dir", help="playlist folder")
    args, ingest_flags = ap.parse_known_args()

    if not ingest.is_playlist_url(args.url):
        ingest.die("not a playlist or channel URL; use ingest.py for a single video")
    listing = ingest.playlist_entries(args.url, [])
    entries = listing["entries"]
    if args.select:
        wanted = [int(x) for x in args.select.split(",") if x.strip()]
        chosen = [(i, entries[i - 1]) for i in wanted if 0 < i <= len(entries)]
    else:
        chosen = list(enumerate(entries[: args.limit], 1))
    root = Path(args.out_dir).expanduser() if args.out_dir else \
        ingest.WORK_ROOT / f"playlist-{ingest.slugify(listing['playlist'] or 'untitled', 40)}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    root.mkdir(parents=True, exist_ok=True)
    ingest.log(f"playlist '{listing['playlist']}': {listing['count']} entries, ingesting {len(chosen)} into {root}")

    # Re-running into the same folder extends the run: entries already ingested are kept, new ones are added,
    # and the clusters are proposed again over everything.
    manifest_file = root / "playlist.json"
    previous = json.loads(manifest_file.read_text(encoding="utf-8")).get("entries", []) if manifest_file.is_file() else []
    done = {r["key"] for r in previous if r.get("ok")}
    chosen = [(i, e) for i, e in chosen if f"{i:02d}-{e['id']}" not in done]
    if previous:
        ingest.log(f"extending an existing run: {len(done)} entries already ingested, {len(chosen)} new")
    rows = [{**r, "source": r.get("source") or source_record(root / r["work_dir"], r["key"])} for r in previous if r.get("ok")]
    for n, (i, e) in enumerate(chosen):
        if n:
            time.sleep(args.sleep)
        key = f"{i:02d}-{e['id']}"
        work = root / key
        ingest.log(f"[{n + 1}/{len(chosen)}] {e['title']}")
        r = subprocess.run([sys.executable, str(Path(__file__).with_name("ingest.py")), e["url"], "--out-dir", str(work), *ingest_flags],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        try:
            summary = json.loads(r.stdout)
        except json.JSONDecodeError:
            summary = {"errors": [ingest.tail(r.stderr)]}
        rows.append({"index": i, "key": key, "url": e["url"], "title": e["title"], "duration": e.get("duration"),
                     "work_dir": key, "segments": summary.get("segments"), "frames": summary.get("frames"),
                     "transcript_provenance": summary.get("transcript_provenance"), "errors": summary.get("errors", []),
                     "ok": r.returncode == 0, "source": source_record(work, key)})
    rows.sort(key=lambda r: r["index"])

    inputs = [x for x in (cluster_input(r["key"], root / r["work_dir"]) for r in rows if r["ok"]) if x]
    proposed = clusters.cluster_entries(inputs)
    manifest = {"playlist": listing["playlist"], "url": args.url, "count": listing["count"], "ingested": len(rows),
                "entries": rows, "clusters": proposed,
                "next": "Study every entry. Adjust the proposed clusters to one per TASK. For each skill-worthy cluster write "
                        "<root>/<slug>.distilled.json with \"sources\": [each entry's `source` record below, plus its "
                        "useless_frames], refs on every step, key_claims with a source, then: forge.py <root>/*.distilled.json "
                        "<note files> --dry-run. Videos that are not skill-worthy get one distilled.json each in their entry "
                        "folder (notes); one note file per topic, so videos on the same topic share a slug."}
    (root / "playlist.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"playlist_dir": str(root), "manifest": str(root / "playlist.json"), "ingested": len(rows),
                      "failed": [r["key"] for r in rows if not r["ok"]],
                      "clusters": [{"label": c["label"], "keys": c["keys"]} for c in proposed]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
