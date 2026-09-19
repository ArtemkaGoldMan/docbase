"""Decide which imported document a link points at.

Cross-document linking used to understand exactly one shape of URL — a wiki
page id. That covered the export it was written for and nothing else: a
standards body links to a landing page, a docs site links to a file, a
handbook links by document number. All of those went unresolved, so the base
knew nothing about how its own documents relate.

Four strategies, tried in order of how much they prove:

1. **page id** — a wiki URL carrying the target's own identifier.
2. **declared URL** — the document said where it came from.
3. **file name** — the URL ends in the file that was imported.
4. **document number** — the URL and the document share an identifier like
   ``800-207``, ``RFC 2119`` or ``POL-042``.

The last is the loose one, so it carries a guard: an identifier that matches
more than one document resolves to none of them. A missing link costs a
lookup; a wrong one sends the reader to the wrong document.
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlsplit

#: Something that looks like a document number rather than a word: letters and
#: digits joined by a separator, or a long run of digits with structure.
RE_IDENTIFIER = re.compile(r"\d{2,5}(?:-\d{1,4}){1,3}[a-z]?(?![a-z0-9])")

#: Years and similar would match everything and mean nothing.
RE_BARE_YEAR = re.compile(r"^(19|20)\d{2}$")


def _normalise_url(url):
    parts = urlsplit(unquote(url or ""))
    path = parts.path.rstrip("/").lower()
    return f"{parts.netloc.lower()}{path}"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def identifiers(text):
    """Document numbers found in a string, normalised for comparison.

    Works on the slug, where every separator is already a hyphen — the point
    is the structure, so flattening it away would destroy the very thing that
    tells 800-63 from the word "digital".
    """
    out = set()
    for match in RE_IDENTIFIER.finditer(_slug(text)):
        token = match.group(0).strip("-")
        if not token or "-" not in token:
            continue                     # a lone number proves nothing
        if RE_BARE_YEAR.match(token.replace("-", "")):
            continue
        out.add(token)
        # A revision suffix still names the same document: 800-63-3 answers a
        # reference to 800-63. A letter suffix does not — 800-63a is a
        # different volume, and treating it as the parent sends the reader to
        # the wrong text.
        parts = token.split("-")
        if len(parts) > 2 and parts[-1].isdigit():
            out.add("-".join(parts[:-1]))
    return out


class Resolver:
    """URL -> imported document name, or None."""

    def __init__(self, documents):
        """`documents` maps a file name to what is known about it:
        page id, declared url, original file name, title."""
        self.by_page_id = {}
        self.by_url = {}
        self.by_name = {}
        self.by_identifier = {}
        ambiguous = set()

        for name, info in documents.items():
            page_id = info.get("page_id")
            if page_id:
                self.by_page_id[str(page_id)] = name

            url = info.get("url")
            if url:
                self.by_url[_normalise_url(url)] = name

            for candidate in (name, info.get("source_file", "")):
                stem = _slug(os.path.splitext(candidate)[0])
                if stem:
                    self.by_name.setdefault(stem, name)

            found = identifiers(info.get("source_file", "")) | identifiers(
                info.get("title", "")) | identifiers(name)
            for token in found:
                if token in self.by_identifier and self.by_identifier[token] != name:
                    ambiguous.add(token)
                self.by_identifier[token] = name

        # An identifier shared by several documents identifies none of them.
        for token in ambiguous:
            self.by_identifier.pop(token, None)

    def page_id_of(self, url, pattern):
        match = pattern.search(url or "")
        if not match:
            return None
        return match.group(1) or match.group(2)

    def resolve(self, url, pattern=None, exclude=None):
        """The document this URL refers to, if any."""
        if not url:
            return None

        if pattern is not None:
            page_id = self.page_id_of(url, pattern)
            if page_id and page_id in self.by_page_id:
                found = self.by_page_id[page_id]
                return None if found == exclude else found

        normalised = _normalise_url(url)
        if normalised in self.by_url:
            found = self.by_url[normalised]
            return None if found == exclude else found

        tail = os.path.basename(urlsplit(unquote(url)).path)
        stem = _slug(os.path.splitext(tail)[0])
        if stem and stem in self.by_name:
            found = self.by_name[stem]
            return None if found == exclude else found

        for token in sorted(identifiers(url), key=len, reverse=True):
            if token in self.by_identifier:
                found = self.by_identifier[token]
                return None if found == exclude else found

        return None
