"""Markdown and plain text.

The obvious case, and the one that was missing longest: documentation that is
already text. A docs-as-code repository, an Obsidian vault, a Notion or GitBook
export — all of it arrives as markdown, and a tool that converts *into*
markdown had no way to accept it.

Nothing needs converting here, so the importer's job is narrow: give the
document an identity, leave the prose exactly as written, and collect its links
so cross-document stitching works the same as for any other source.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from .. import frontmatter

RE_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
RE_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
RE_BARE_URL = re.compile(r"(?<![(\[<])(https?://[^\s)\]<>]+)")
RE_TITLE = re.compile(r"^#\s+(.+)$", re.M)


def _read(path):
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            with open(path, encoding=encoding) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def convert(path):
    """Text file -> (body, links, id).

    An existing frontmatter block is respected rather than duplicated: a
    document that already declares an id keeps it, so re-importing the same
    file does not fork it into a second document.
    """
    raw = _read(path)
    name = os.path.basename(path)

    existing = RE_FRONTMATTER.match(raw)
    body = raw[existing.end():] if existing else raw
    doc_id = frontmatter.read_id(existing.group(1)) if existing else ""

    if not doc_id:
        # No declared identity, so derive one from the content. Keyed on the
        # text rather than the file name, so renaming a file does not create a
        # second copy of the same document.
        doc_id = frontmatter.derive_id(body.strip())

    if not RE_TITLE.search(body):
        title = os.path.splitext(name)[0].replace("-", " ").replace("_", " ")
        body = f"# {title.strip().capitalize()}\n\n{body.lstrip()}"

    links = {}
    for match in RE_MD_LINK.finditer(body):
        links.setdefault(match.group(1), 1)
    for match in RE_BARE_URL.finditer(body):
        links.setdefault(match.group(1).rstrip(".,;"), 1)

    return body.strip() + "\n", links, doc_id


def build_markdown(path, body, links, doc_id, is_internal=lambda url: False):
    head = frontmatter.block(os.path.basename(path), doc_id,
                             extracted=dt.date.today().isoformat())

    internal, external = [], []
    for url in sorted(links):
        row = f"| — | | {url} |"
        (internal if is_internal(url) else external).append(row)

    tail = []
    if internal or external:
        tail = ["", "---", "", "## Links found in this document", ""]
        if internal:
            tail += ["### Internal", "", "| page | id | URL |", "|---|---|---|",
                     *internal, ""]
        if external:
            tail += ["### External", "", "| page | id | URL |", "|---|---|---|",
                     *external, ""]

    return "\n".join(head) + body + "\n".join(tail) + "\n"
