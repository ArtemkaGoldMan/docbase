"""Measure retrieval quality on whatever corpus happens to be loaded.

The old test suite hardcoded questions about one company's documentation,
which made it useless for anyone else. Cases are now derived from the corpus
itself: pick passages that are distinctive, build a query from the words that
make them distinctive, and check that the passage comes back.

A case is ``{question, marker, file}``. The marker is a literal phrase from
the right answer, and it is deliberately taken from a part of the passage the
query does not mention — otherwise the test would only prove that a string
matches itself.

Generated cases are a floor, not a ceiling. They measure "given good
keywords, is the passage retrievable", which is easier than a real question.
Edit ``kb/eval.json`` by hand, or have an agent rewrite the questions into
natural phrasing, and the same runner scores those too.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import config as config_module
from .search import Index

EVAL_NAME = "kb/eval.json"


def _path(cfg):
    return os.path.join(cfg.layout.root, EVAL_NAME)


def generate(cfg=None, count=20):
    cfg = cfg or config_module.load()
    index = Index(cfg).build()
    if not index.chunks:
        print("The base is empty; nothing to generate cases from.")
        return 1

    scored = []
    for name, line_no, heading, body, body_stems, _head in index.chunks:
        if len(body) < 180:
            continue
        weight = sum(index.idf.get(token, 0) for token in body_stems)
        scored.append((weight / max(len(body_stems), 1), weight, name, line_no, heading, body))
    scored.sort(reverse=True)

    cases, used_files = [], {}
    for _density, _weight, name, _line, heading, body in scored:
        # Spread cases across documents instead of piling onto the richest one.
        if used_files.get(name, 0) >= max(2, count // max(len(index.files()), 1) + 1):
            continue

        tokens = [(index.idf.get(t, 0), t) for t in index.stems(body)]
        tokens.sort(reverse=True)
        if len(tokens) < 6:
            continue

        # Query: informative words, minus the single most distinctive one, so
        # the case does not reduce to an exact-phrase lookup.
        query_stems = [t for _w, t in tokens[1:7]]
        query_words = []
        for word in re.findall(r"[\w'’-]+", body, re.UNICODE):
            token = word.lower()[:cfg.search.stem_length]
            if token in query_stems and word.lower() not in query_words:
                query_words.append(word.lower())
            if len(query_words) >= 6:
                break
        if len(query_words) < 4:
            continue

        marker = _pick_marker(body, query_words)
        if not marker:
            continue

        cases.append({"question": " ".join(query_words),
                      "marker": marker, "file": name,
                      "origin": "generated", "heading": heading[:70]})
        used_files[name] = used_files.get(name, 0) + 1
        if len(cases) >= count:
            break

    existing = _load(cfg)
    handwritten = [c for c in existing if c.get("origin") != "generated"]

    if not cases:
        # Silence here would mean the whole evaluation quietly stops existing.
        print(f"Could not build any cases from {len(index.chunks)} fragments.")
        print("Every candidate was rejected: too short, too few distinctive "
              "words, or no phrase that avoids the query terms.")
        print("The corpus may be very repetitive, or mostly tables. "
              "Write a few cases by hand in " + EVAL_NAME + " instead.")
        return 1

    _save(cfg, handwritten + cases)
    print(f"Wrote {len(cases)} generated cases "
          f"({len(handwritten)} hand-written kept) to {EVAL_NAME}")
    if len(cases) < count:
        print(f"Asked for {count}; the corpus yielded {len(cases)} usable ones.")
    print("Review them: a good case reads like a real question, not keywords.")
    return 0


def _pick_marker(body, query_words):
    """A literal phrase from the passage that mostly avoids the query words.

    Sentences are tried first because they read well in a test file. Long-form
    documentation often has none that fit — a regulation paragraph can be a
    single 400-character sentence — so a plain character window is the
    fallback. Without it, generation silently produced nothing on exactly the
    corpora it matters for.
    """
    candidates = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body)]
    candidates = [s for s in candidates if 25 <= len(s) <= 200]

    if not candidates:
        # Slide a window across the passage instead of giving up.
        width = 90
        candidates = [body[start:start + width].strip()
                      for start in range(0, max(len(body) - width, 1), width // 2)]
        candidates = [c for c in candidates if len(c) >= 40]

    best, best_overlap = "", 99
    for candidate in candidates:
        lowered = candidate.lower()
        overlap = sum(1 for word in query_words if word in lowered)
        if overlap < best_overlap:
            best, best_overlap = candidate, overlap
        if overlap == 0:
            break
    return best if best_overlap <= 2 else ""


def _load(cfg):
    path = _path(cfg)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []


def _save(cfg, cases):
    path = _path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _flatten(text):
    """Compare without markdown.

    Markers are taken from fragments the indexer has already stripped of
    emphasis, so a literal search against the raw file misses every passage
    that happened to contain bold or a link — which silently dropped a
    quarter of the generated cases.
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", re.sub(r"[*_`~]", "", text)).strip().lower()


def _marker_present(cfg, marker, filename):
    """Confirm the marker really is in that file, independently of search."""
    for name, path in Index(cfg).files():
        if name == filename:
            return _flatten(marker) in _flatten(open(path, encoding="utf-8").read())
    return False


def run(cfg=None, verbose=False):
    cfg = cfg or config_module.load()
    cases = _load(cfg)
    if not cases:
        print("No cases yet. Build some with: docbase eval --generate")
        return 1

    index = Index(cfg).build()
    limit = cfg.search.default_hits
    at1 = at3 = at5 = anywhere = right_file = skipped = 0
    times, sizes = [], []

    for case in cases:
        marker, filename = case["marker"], case["file"]
        if not _marker_present(cfg, marker, filename):
            skipped += 1
            if verbose:
                print(f"  skipped   marker not in {filename}")
            continue

        started = time.perf_counter()
        hits = index.search(case["question"], limit=limit)
        times.append((time.perf_counter() - started) * 1000)
        sizes.append(sum(len(hit[4]) for hit in hits))

        position = None
        for rank, (_score, name, _line, heading, body) in enumerate(hits, 1):
            if name == filename and marker.lower() in (heading + " " + body).lower():
                position = rank
                break

        if hits and hits[0][1] == filename:
            right_file += 1
        if position:
            anywhere += 1
            at5 += position <= 5
            at3 += position <= 3
            at1 += position == 1
            label = {1: "hit  #1", 2: "hit  #2", 3: "hit  #3"}.get(position, f"hit  #{position}")
        else:
            label = "MISS   "

        if verbose:
            print(f"  {label}  {case['question'][:58]}")

    total = len(cases) - skipped
    if not total:
        print("No usable cases: markers no longer match the corpus. "
              "Regenerate with: docbase eval --generate")
        return 1

    def pct(value):
        return f"{value}/{total} ({100 * value // total}%)"

    print(f"\nCases: {total}" + (f" ({skipped} skipped)" if skipped else ""))
    print(f"  top-1              {pct(at1)}")
    print(f"  top-3              {pct(at3)}")
    print(f"  top-5              {pct(at5)}")
    print(f"  in returned {limit:<6} {pct(anywhere)}   <- what the agent actually sees")
    print(f"  right document #1  {pct(right_file)}   <- guards against confident wrong answers")
    print(f"  search time        {sum(times) / len(times):.0f} ms average, "
          f"{max(times):.0f} ms worst")
    print(f"  context returned   ~{sum(sizes) // len(sizes) // 4} tokens per query")
    return 0
