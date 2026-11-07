#!/usr/bin/env python3
"""Propose topic clusters for a playlist: which videos teach the same task and should become one skill.

Deterministic and standard-library only. Each entry is a dict with `key` and any of `title`, `chapters` (list of str),
`tags` (list of str), `text` (opening of the transcript). Words that appear in most of the playlist ("ERP", "tutorial")
get almost no weight through IDF, so clusters form on what makes videos different. Claude reviews the proposal; it is a
starting point, not a verdict.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

STOP = set("""
a about after again all also an and any are as at be because been before being but by can could did do does doing done
down each en for from get got had has have having he her here his how i if in into is it its just let lets like make
me more most my need new no not now of off on once one only or other our out over own part really right same see she
should so some still such than that the their them then there these they this those through to too up us use used using
very video want was way we well were what when where which while who why will with would you your yours
today going gonna know thing things show shows showing look looking click go first next also okay ok yeah so
how-to tutorial tutorials guide beginner beginners full complete step steps tips trick tricks easy quick learn
""".split())
FIELD_WEIGHTS = {"title": 3.0, "chapters": 2.0, "tags": 1.0, "text": 1.0}


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z][a-z0-9]+", text)
    # Prefix truncation is a crude but robust stemmer: automated/automation/automate -> automa, invoices -> invoic.
    return [w[:6] if len(w) > 6 else w.rstrip("s") if len(w) > 4 and not w.endswith("ss") else w
            for w in words if w not in STOP and len(w) > 2 and not w.isdigit()]


def surface_forms(entries: list[dict]) -> dict:
    """stem -> the most common full word behind it, so labels read 'reconciliation', not 'reconc'."""
    seen: dict = {}
    for e in entries:
        for field in FIELD_WEIGHTS:
            value = e.get(field) or ""
            text = " ".join(value) if isinstance(value, list) else value
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
            for w in re.findall(r"[a-z][a-z0-9]+", plain):
                for stem in tokens(w):
                    seen.setdefault(stem, Counter())[w] += 1
    return {stem: c.most_common(1)[0][0] for stem, c in seen.items()}


def bag(entry: dict) -> Counter:
    c: Counter = Counter()
    for field, weight in FIELD_WEIGHTS.items():
        value = entry.get(field) or ""
        text = " ".join(value) if isinstance(value, list) else value
        for t in tokens(text):
            c[t] += weight
    return c


def vectors(entries: list[dict]) -> list[dict]:
    bags = [bag(e) for e in entries]
    n = len(bags)
    df = Counter(t for b in bags for t in b)
    idf = {t: math.log((1 + n) / d) for t, d in df.items()}     # a word in every entry gets a small weight, a rare one a large one
    out = []
    for b in bags:
        v = {t: (1 + math.log(w)) * idf[t] for t, w in b.items() if idf[t] > 0}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({t: x / norm for t, x in v.items()})
    return out


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def cluster_entries(entries: list[dict], threshold: float = 0.15) -> list[dict]:
    """Average-linkage agglomerative clustering. Returns [{"keys": [...], "label": "top words"}], largest first,
    entries in playlist order inside each cluster."""
    if not entries:
        return []
    vec = vectors(entries)
    groups = [[i] for i in range(len(entries))]

    def link(g1: list[int], g2: list[int]) -> float:
        return sum(cosine(vec[i], vec[j]) for i in g1 for j in g2) / (len(g1) * len(g2))

    while len(groups) > 1:
        best, pair = -1.0, None
        for x in range(len(groups)):
            for y in range(x + 1, len(groups)):
                s = link(groups[x], groups[y])
                if s > best:
                    best, pair = s, (x, y)
        if best < threshold:
            break
        x, y = pair
        groups[x] = sorted(groups[x] + groups[y])
        del groups[y]

    surface = surface_forms(entries)
    result = []
    for g in groups:
        weight: Counter = Counter()
        for i in g:
            weight.update(vec[i])
        label = " ".join(surface.get(t, t) for t, _ in weight.most_common(3))
        result.append({"keys": [entries[i]["key"] for i in g], "label": label})
    result.sort(key=lambda c: (-len(c["keys"]), [e["key"] for e in entries].index(c["keys"][0])))
    return result
