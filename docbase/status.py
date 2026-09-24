"""What is stale, what changed, and what is missing.

One place to answer "is the base current?", which otherwise means opening
three generated files and the manifest.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import config as config_module
from . import sync as sync_module

#: Status answers "is the base current?", and this report is also read by an
#: agent that pays for every line. Naming five hundred documents to say they
#: are all fine is not an answer worth ten thousand tokens.
DOCUMENT_LIMIT = 40


def _recent_changes(cfg, limit=5):
    history = cfg.layout.path("history")
    if not os.path.isdir(history):
        return []
    entries = []
    for document in sorted(os.listdir(history)):
        folder = os.path.join(history, document)
        if not os.path.isdir(folder):
            continue
        diffs = sorted(f for f in os.listdir(folder) if f.endswith(".diff"))
        if diffs:
            newest = diffs[-1]
            entries.append((newest.replace(".diff", ""), document,
                            os.path.join(folder, newest)))
    entries.sort(reverse=True)
    return entries[:limit]


def _stale_cards(cfg):
    cards = cfg.layout.path("cards")
    if not os.path.isdir(cards):
        return []
    out = []
    for topic in sorted(os.listdir(cards)):
        if os.path.isfile(os.path.join(cards, topic, "_stale")):
            out.append(topic)
    return out


def _missing_pages(cfg):
    path = os.path.join(cfg.layout.root, "kb", "linkmap.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("missing", {})
    except (json.JSONDecodeError, OSError):
        return {}


def report(cfg=None):
    cfg = cfg or config_module.load()
    layout = cfg.layout
    text_dir = layout.path("text")
    documents = sorted(f for f in os.listdir(text_dir)
                       if f.endswith(".md")) if os.path.isdir(text_dir) else []

    if not documents:
        print("The base is empty. Drop an exported page into this folder "
              "and run: docbase sync")
        return 1

    print(f"Documents: {len(documents)}")
    for name in documents[:DOCUMENT_LIMIT]:
        path = os.path.join(text_dir, name)
        changed = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
        size = os.path.getsize(path)
        print(f"  {name:<44} {size // 1024:>4} KB   updated {changed}")
    if len(documents) > DOCUMENT_LIMIT:
        print(f"  … and {len(documents) - DOCUMENT_LIMIT} more "
              f"(docbase map lists them all)")

    manifest = sync_module.read_manifest(layout.path("manifest"))
    broken = [(n, e["failed"]) for n, e in manifest.items() if e.get("failed")]
    if broken:
        print("\nFiles that could not be read:")
        for name, why in broken:
            print(f"  ! {name}: {why}")

    stale = _stale_cards(cfg)
    if stale:
        print("\nCards waiting to be rebuilt (their source changed):")
        for topic in stale:
            print(f"  - {topic}")

    changes = _recent_changes(cfg)
    if changes:
        print("\nRecent document changes:")
        for stamp, document, path in changes:
            relative = os.path.relpath(path, layout.root)
            print(f"  {stamp}  {document}")
            print(f"            {relative}")

    missing = _missing_pages(cfg)
    if missing:
        print(f"\nReferenced but not imported: {len(missing)} pages")
        ordered = sorted(missing.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for page_id, labels in ordered[:8]:
            names = "; ".join(labels[:2]) if isinstance(labels, list) else str(labels)
            print(f"  {page_id}  {names[:60]}")
        if len(missing) > 8:
            print(f"  ... and {len(missing) - 8} more (see kb/graph.md)")
        print("  Most cited first — those are the ones worth importing next.")

    if not (broken or stale or missing):
        print("\nEverything is current.")
    return 0
