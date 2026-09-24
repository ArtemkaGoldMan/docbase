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

#: reStructuredText and many plain-text documents underline their headings
#: instead of prefixing them. Passing such a file through untouched loses its
#: structure completely: a 49 KB style guide arrived with three headings.
UNDERLINE_CHARS = "=-`:'\"~^_*+#<>"

#: A fenced code block, whose contents are a sample rather than prose.
RE_FENCE = re.compile(r"^\s*(?:```|~~~)")

#: A field list at the top of the document, as PEPs and many RST files use.
RE_FIELD = re.compile(r"^([A-Z][A-Za-z-]{2,20}):\s+(.+)$")


def _rule(line):
    """A row of one repeated character, as RST underlines a title with.

    Left margin only: indented text is a code block, and a docstring's closing
    \"\"\" under a line of example output is not a heading underline — which is
    how the Python doctest manual came to be titled "120".
    """
    if line[:1] in (" ", "\t"):
        return ""
    bare = line.strip()
    if len(bare) >= 3 and bare[0] in UNDERLINE_CHARS and bare == bare[0] * len(bare):
        return bare[0]
    return ""


RE_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def headings_outside_fences(text):
    """-> [(level, text)] for every markdown heading that is not a code sample."""
    out, fenced = [], False
    for line in text.splitlines():
        if RE_FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = RE_MD_HEADING.match(line)
        if match:
            out.append((len(match.group(1)), match.group(2)))
    return out


def promote_headings(text):
    """Give a document whose top heading is ## its own title back.

    A book or a documentation site supplies the first-level heading itself, so
    the file on disk starts a level down. Without this the document has no
    title of its own and is named after its file instead: 103 of the 112
    chapters of a published manual arrived as "Ch08 01 vectors", while the
    first line of the file reads "Storing Lists of Values with Vectors".

    Only when the document opens with one of its shallowest headings, which
    is what makes it the subject rather than a section of something else. A
    file that is a bag of equal sections is then titled after the first of
    them, which is still the document saying what it is about instead of a
    file name saying nothing.
    """
    found = headings_outside_fences(text)
    if not found:
        return text
    shallowest = min(level for level, _ in found)
    if shallowest == 1:
        return text
    if found[0][0] != shallowest:
        return text

    shift = shallowest - 1
    out, fenced = [], False
    for line in text.splitlines():
        if RE_FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        match = None if fenced else RE_MD_HEADING.match(line)
        out.append("#" * (len(match.group(1)) - shift) + " " + match.group(2)
                   if match else line)
    return "\n".join(out)


def underlined_headings(text):
    """Turn underlined headings into markdown ones.

    Levels follow first appearance: the first underline character seen is the
    top level, the next new one the level below, and so on — which is exactly
    how reStructuredText defines them.
    """
    lines = text.splitlines()
    order, out, skip, fenced = [], [], False, False

    for index, line in enumerate(lines):
        if skip:
            skip = False
            continue
        if RE_FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)         # a code sample keeps its own punctuation
            continue
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        after = lines[index + 2] if index + 2 < len(lines) else ""
        title = line.strip()

        # An overline: the same rule above the title as below it. Dropping it
        # here lets the title be read on the next pass.
        if _rule(line) and _rule(line) == _rule(after) and nxt.strip():
            continue

        char = _rule(nxt)
        is_heading = (
            char
            and title
            and not _rule(line)
            and line[:1] not in (" ", "\t")
            and len(nxt.strip()) >= len(title) - 2
        )
        if is_heading:
            if char not in order:
                order.append(char)
            level = min(order.index(char) + 1, 4)
            out.append("#" * level + " " + title)
            skip = True                    # drop the underline itself
        else:
            out.append(line)
    return "\n".join(out)


def declared_title(text):
    """A title the document states about itself, as in an RST field list."""
    for line in text.splitlines()[:40]:
        if not line.strip():
            continue
        match = RE_FIELD.match(line)
        if match and match.group(1).lower() == "title":
            return match.group(2).strip()
        if not match:
            break                          # the field list has ended
    return ""


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
        # No declared identity, so derive one from the file name, as the other
        # importers do. Keying on the content instead forked the document on
        # every edit: a handbook synced from a repository grew a second copy
        # each time a sentence changed, and the base filled with stale twins.
        # A file that must survive a rename can declare its own id in
        # frontmatter, which is read above and kept.
        doc_id = frontmatter.derive_id(os.path.basename(path))

    stated = declared_title(body)
    body = promote_headings(underlined_headings(body))
    titled = any(level == 1 for level, _ in headings_outside_fences(body))

    if stated:
        body = f"# {stated}\n\n{body.lstrip()}"
    elif not titled:
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
