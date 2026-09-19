"""The identity of a document, and where it is written down.

Every converted document carries a ``source_id`` in its frontmatter. That id is
what ties three things together: the file on disk, the page it came from, and
any extract an agent built from it. Names on disk change — a retitled page, a
rebuilt base — and anything keyed on a name breaks quietly when they do.

Wiki exports supply a numeric page id. Files that have no such thing, a
markdown handbook checked into a repository, get a short digest of their
content instead: stable across runs, and distinct between documents.
"""
from __future__ import annotations

import hashlib
import re

FIELD = "source_id"

#: Older bases wrote a wiki-specific field name. Read both, write the new one.
LEGACY_FIELDS = ("confluence_page_id", "source_page_id")

_PATTERN = re.compile(
    r'(?:{}):\s*"?([\w-]*)"?'.format("|".join((FIELD,) + LEGACY_FIELDS))
)


def read_id(text):
    """The document id declared in a chunk of markdown, or ''."""
    match = _PATTERN.search(text or "")
    return match.group(1) if match else ""


def read_id_from(path, limit=400):
    try:
        with open(path, encoding="utf-8") as handle:
            return read_id(handle.read(limit))
    except (OSError, UnicodeDecodeError):
        return None


def derive_id(*parts):
    """A stable id for sources that do not carry one of their own."""
    digest = hashlib.sha1("\x00".join(str(p) for p in parts).encode("utf-8"))
    return "d" + digest.hexdigest()[:11]


def block(source_name, source_id, url="", extracted="", extra=None):
    """The frontmatter every importer emits, so they cannot drift apart."""
    lines = ["---",
             f'source_file: "{source_name}"',
             f'{FIELD}: "{source_id}"']
    if url:
        lines.append(f'source_url: "{url}"')
    if extracted:
        lines.append(f"extracted: {extracted}")
    for key, value in (extra or {}).items():
        lines.append(f'{key}: "{value}"')
    lines += ["---", ""]
    return lines
