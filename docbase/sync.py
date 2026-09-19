"""Bring the base up to date.

One entry point for the whole pipeline: pick up dropped files, convert only
what changed, keep a diff of what changed, stitch cross-document links, and
mark derived cards stale.

Everything here is deterministic — no model calls, no tokens spent. It is
cheap enough to run before every agent turn.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile

from . import frontmatter
from . import config as config_module
from . import languages as languages_module
from . import link as link_module

DOC_EXTENSIONS = (".pdf", ".html", ".htm", ".doc", ".mhtml", ".mht",
                  ".md", ".markdown", ".txt", ".rst", ".text")
ARCHIVE_EXTENSIONS = (".zip",)


# ---------------------------------------------------------------- helpers
def slugify(text, extra_map=None, fallback="document"):
    """Kept as a module-level helper; the logic lives in `languages`."""
    return languages_module.slugify(text, extra_map, fallback)


def quick_stat(path):
    info = os.stat(path)
    return [int(info.st_mtime), info.st_size]


def fingerprint(path):
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}


def write_manifest(path, data):
    """Atomic: two agent turns can fire at once, and a half-written manifest
    would lose track of the whole base."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def page_id_of(markdown):
    return frontmatter.read_id(markdown)


def page_id_of_file(path):
    if not os.path.isfile(path):
        return None
    return frontmatter.read_id_from(path)


# ------------------------------------------------------------ collecting
def collect_dropped(cfg, log):
    """Files dropped anywhere in the base root move into the originals dir."""
    layout = cfg.layout
    layout.ensure("originals")
    originals = layout.path("originals")

    unpack_archives(cfg, log)

    dropped = [n for n in sorted(os.listdir(layout.root))
               if n.lower().endswith(DOC_EXTENSIONS)]
    if not dropped:
        return                       # the common case reads nothing at all

    # Hash the existing files only when there is something to compare against,
    # otherwise every agent turn would checksum the entire corpus.
    known = {}
    for name in sorted(os.listdir(originals)):
        if name.lower().endswith(DOC_EXTENSIONS):
            known[fingerprint(os.path.join(originals, name))] = name

    for name in dropped:
        source = os.path.join(layout.root, name)
        digest = fingerprint(source)
        if digest in known:
            os.remove(source)
            log.append(f"{name} is already in the base as {known[digest]}; removed the copy")
            continue
        extension = os.path.splitext(name)[1].lower()
        base = os.path.join(originals, cfg.slug(os.path.splitext(name)[0]))
        target, counter = base + extension, 1
        while os.path.exists(target):
            target = f"{base}-{counter}{extension}"
            counter += 1
        os.rename(source, target)
        known[digest] = os.path.basename(target)
        log.append(f"{name} -> {os.path.relpath(target, layout.root)}")


def unpack_archives(cfg, log):
    """A space export arrives as one zip holding hundreds of pages.

    Unpacking by hand and dropping the files one at a time is the kind of
    friction that stops a base from being kept current at all.
    """
    layout = cfg.layout
    archives = [n for n in sorted(os.listdir(layout.root))
                if n.lower().endswith(ARCHIVE_EXTENSIONS)]
    for name in archives:
        path = os.path.join(layout.root, name)
        taken = 0
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if member.endswith("/") or not member.lower().endswith(DOC_EXTENSIONS):
                        continue
                    # Flatten: a member path is untrusted input, and nested
                    # directories would escape the base with ../ entries.
                    flat = os.path.basename(member)
                    if not flat:
                        continue
                    target = os.path.join(layout.root, flat)
                    counter = 1
                    stem, suffix = os.path.splitext(flat)
                    while os.path.exists(target):
                        target = os.path.join(layout.root, f"{stem}-{counter}{suffix}")
                        counter += 1
                    with archive.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    taken += 1
        except (zipfile.BadZipFile, OSError) as error:
            log.append(f"could not open {name}: {str(error)[:60]}")
            continue
        os.remove(path)
        log.append(f"unpacked {name}: {taken} documents"
                   if taken else f"{name} held nothing importable")


# -------------------------------------------------------------- naming
def free_text_name(cfg, base, page_id):
    """A name that will not clobber a different document.

    Two unrelated pages with similar titles slugify to the same string; without
    this check the second one would silently overwrite the first.
    """
    text_dir = cfg.layout.path("text")
    candidate, counter = f"{base}.md", 1
    while True:
        owner = page_id_of_file(os.path.join(text_dir, candidate))
        if owner is None or not owner or owner == page_id:
            return candidate
        candidate = f"{base}-{counter}.md"
        counter += 1


def text_by_page_id(cfg, page_id):
    text_dir = cfg.layout.path("text")
    if not page_id or not os.path.isdir(text_dir):
        return None
    for name in sorted(os.listdir(text_dir)):
        if name.endswith(".md") and page_id_of_file(os.path.join(text_dir, name)) == page_id:
            return name
    return None


# -------------------------------------------------------------- history
def record_history(cfg, name, old_text, new_text, log):
    """Keep what changed, so a card can be updated surgically.

    Marking a card stale is not enough on its own: the agent then has to
    re-read the whole document. A diff tells it which sections moved.
    """
    if old_text == new_text:
        return False
    folder = os.path.join(cfg.layout.path("history"), os.path.splitext(name)[0])
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")

    diff = list(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=f"{name} (previous)", tofile=f"{name} (current)",
        lineterm="", n=1))
    with open(os.path.join(folder, f"{stamp}.diff"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(diff) + "\n")
    with open(os.path.join(folder, "previous.md"), "w", encoding="utf-8") as handle:
        handle.write(old_text)

    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    log.append(f"{name} changed: +{added}/-{removed} lines "
               f"(diff in {os.path.relpath(folder, cfg.layout.root)})")
    return True


# ---------------------------------------------------------------- cards
def heal_card_sources(cfg, manifest, log):
    """A card knows its page id; repair its `source:` line if the file was
    renamed, instead of leaving it pointing at nothing."""
    cards = cfg.layout.path("cards")
    if not os.path.isdir(cards):
        return
    by_page = {entry.get("page_id"): entry["out"]
               for entry in manifest.values() if entry.get("page_id") and entry.get("out")}
    for topic in sorted(os.listdir(cards)):
        folder = os.path.join(cards, topic)
        if not os.path.isdir(folder):
            continue
        for card in sorted(os.listdir(folder)):
            if not card.endswith(".md"):
                continue
            path = os.path.join(folder, card)
            text = open(path, encoding="utf-8").read()
            page = re.search(r'(?:source_id|source_page_id):\s*"?([\w-]+)"?', text)
            current = re.search(r"source:\s*\S*?([\w.-]+\.md)", text)
            if not page or not current:
                continue
            wanted = by_page.get(page.group(1))
            if wanted and wanted != current.group(1):
                text = text.replace(current.group(1), wanted)
                open(path, "w", encoding="utf-8").write(text)
                log.append(f"card {topic}/{card} re-bound to {wanted}")


def mark_stale_cards(cfg, rebuilt, log):
    cards = cfg.layout.path("cards")
    if not rebuilt or not os.path.isdir(cards):
        return
    for topic in sorted(os.listdir(cards)):
        folder = os.path.join(cards, topic)
        if not os.path.isdir(folder):
            continue
        for card in os.listdir(folder):
            if not card.endswith(".md"):
                continue
            head = open(os.path.join(folder, card), encoding="utf-8").read(400)
            match = re.search(r"source:\s*\S*?([\w.-]+\.md)", head)
            if match and match.group(1) in rebuilt:
                with open(os.path.join(folder, "_stale"), "w", encoding="utf-8") as handle:
                    handle.write("The source document changed. Rebuild this card.\n")
                log.append(f"cards for '{topic}' marked stale")
                break


# ----------------------------------------------------------------- main
def run(cfg=None, quiet=False, force=False):
    cfg = cfg or config_module.load()
    layout = cfg.layout
    layout.ensure("originals", "text")

    from .importers import convert_document, missing_dependencies

    missing = missing_dependencies()
    if missing:
        raise SystemExit(
            "Missing Python packages: " + ", ".join(missing) + "\n"
            "Install them with:\n"
            "  python -m pip install " + " ".join(missing))

    manifest_path = layout.path("manifest")
    manifest = {} if force else read_manifest(manifest_path)

    log, failed, rebuilt = [], [], set()
    converted = skipped = 0

    collect_dropped(cfg, log)

    originals = layout.path("originals")
    names = [n for n in sorted(os.listdir(originals)) if n.lower().endswith(DOC_EXTENSIONS)]

    # A richer format wins for the same page: HTML keeps tables and hidden tabs.
    rich_pages = {manifest[n].get("page_id") for n in names
                  if not n.lower().endswith(".pdf") and n in manifest}
    rich_pages.discard(None)
    rich_pages.discard("")

    seen_digests = {}
    for name in names:
        path = os.path.join(originals, name)
        known = manifest.get(name)
        stat = quick_stat(path)

        if (name.lower().endswith(".pdf") and known
                and known.get("page_id") in rich_pages):
            log.append(f"{name} skipped: this page is already available as HTML")
            skipped += 1
            continue

        # A broken file is reported once, not on every agent turn.
        if known and known.get("failed") and known.get("stat") == stat:
            skipped += 1
            continue

        if known and known.get("stat") == stat:
            if os.path.exists(os.path.join(layout.path("text"), known.get("out", ""))):
                seen_digests[known["sha1"]] = name
                skipped += 1
                continue

        digest = fingerprint(path)
        if digest in seen_digests:
            log.append(f"{name} is a copy of {seen_digests[digest]}; skipped")
            skipped += 1
            continue
        seen_digests[digest] = name

        if known and known.get("sha1") == digest:
            out = os.path.join(layout.path("text"), known.get("out", ""))
            if os.path.exists(out):
                known["stat"] = stat            # moved, but the content is the same
                skipped += 1
                continue

        try:
            markdown, slug = convert_document(path, cfg)
        except Exception as error:               # noqa: BLE001
            failed.append((name, str(error)[:90]))
            manifest[name] = {"sha1": digest, "stat": stat, "failed": str(error)[:90]}
            continue

        page_id = page_id_of(markdown)
        out_name = (known or {}).get("out") or text_by_page_id(cfg, page_id)
        if not out_name:
            title = re.search(r"^#\s+(?:\[)?(.+?)(?:\]\(|$)", markdown, re.M)
            base = cfg.slug(title.group(1) if title else os.path.splitext(name)[0])
            out_name = free_text_name(cfg, base, page_id)

        markdown = _align_assets(cfg, slug, out_name, markdown)

        destination = os.path.join(layout.path("text"), out_name)
        previous = ""
        if os.path.exists(destination):
            previous = open(destination, encoding="utf-8").read()
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write(markdown)

        if previous:
            record_history(cfg, out_name, previous, markdown, log)
        else:
            log.append(f"added {os.path.relpath(destination, layout.root)}")

        manifest[name] = {"sha1": digest, "stat": stat,
                          "out": out_name, "page_id": page_id}
        converted += 1
        rebuilt.add(out_name)

    heal_card_sources(cfg, manifest, log)
    mark_stale_cards(cfg, rebuilt, log)
    write_manifest(manifest_path, manifest)

    registry, stats = link_module.run(
        sources=layout.path("text"), quiet=True,
        internal_hosts=cfg.importer.internal_hosts,
        linkmap_path=os.path.join(layout.root, "kb", "linkmap.json"),
        graph_path=os.path.join(layout.root, "kb", "graph.md"))

    report = {"documents": len(registry), "converted": converted,
              "skipped": skipped, "failed": failed, "log": log, "link": stats}

    if quiet and not converted and not log and not failed:
        return report

    _print_report(cfg, report)
    return report


def _align_assets(cfg, slug, out_name, markdown):
    """Asset folders follow the document name, not the original file name.

    Merge rather than replace: the target folder may hold a hand-written
    descriptions.md, and losing that would lose knowledge that exists nowhere
    else.
    """
    wanted = os.path.splitext(out_name)[0]
    if not slug or slug == wanted:
        return markdown
    assets = cfg.layout.path("assets")
    old, new = os.path.join(assets, slug), os.path.join(assets, wanted)
    if os.path.isdir(old):
        os.makedirs(new, exist_ok=True)
        for item in os.listdir(old):
            shutil.move(os.path.join(old, item), os.path.join(new, item))
        os.rmdir(old)
    relative = os.path.relpath(assets, cfg.layout.root).replace(os.sep, "/")
    return markdown.replace(f"{relative}/{slug}/", f"{relative}/{wanted}/")


def _print_report(cfg, report):
    for name, why in report["failed"]:
        print(f"  ! could not read '{name}': {why}")
        print("    the file is damaged or in an unsupported format")

    if not report["documents"]:
        print("The base is empty. Export a page from your wiki "
              "(PDF, Word or HTML) and drop the file into this folder.")
        return

    line = f"Base: {report['documents']} documents"
    if report["skipped"]:
        line += f", {report['skipped']} unchanged"
    print(line)
    for entry in report["log"]:
        print(f"  - {entry}")
    stats = report["link"]
    if stats.get("linked"):
        print(f"  - linked {stats['linked']} cross-document references")
    if stats.get("missing"):
        print(f"  - {len(stats['missing'])} referenced pages are not imported yet "
              f"(see kb/graph.md)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    run(quiet="--quiet" in argv, force="--force" in argv)
