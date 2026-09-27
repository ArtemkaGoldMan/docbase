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


# ------------------------------------------------------------------ structure
_STRUCTURE = None


def structural_heading(text):
    """-> the markdown heading this paragraph is, or "" if it is not one.

    "Стаття 8. Права споживача у разі придбання товару" and "Розділ I
    ЗАГАЛЬНІ ПОЛОЖЕННЯ" are headings that a stylesheet made look like
    headings; the importer only sees a paragraph. A numbered part at the start
    of a short paragraph that does not read as a sentence is one.
    """
    global _STRUCTURE
    if _STRUCTURE is None:
        from .languages import structure_levels
        words = structure_levels()
        alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        # The word in any case — "РОЗДІЛ" as often as "Розділ" — but a Roman
        # numeral only in capitals: "Part did not arrive" is not Part DID.
        _STRUCTURE = (words, re.compile(
            r"^(?i:(%s))\s*(\d+(?:[-–.]\d+)*|[IVXLCDM]+)\b\.?\s*(.*)$"
            % alternatives))
    words, pattern = _STRUCTURE
    plain = text.strip()
    if (not plain or len(plain) > 160 or plain[0] in "#|>`"
            or plain.startswith(("- ", "* ", "+ "))):
        return ""                    # already a heading, a table, a list item
    # "**Стаття 1.** Визначення термінів": the label is often bold.
    plain = plain.replace("**", "").replace("__", "").strip()
    match = pattern.match(plain)
    if not match:
        return ""
    rest = match.group(3).strip()
    if rest.endswith((".", ";", ":", ",")) and len(rest) > 40:
        return ""                    # a sentence that begins with a reference
    level = words.get(match.group(1).lower(), 3)
    return "#" * level + " " + plain

