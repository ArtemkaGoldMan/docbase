"""Format-specific importers.

Each importer turns one exported file into markdown that carries its own
frontmatter, its links, and markers for any images worth keeping. Adding a
new source (Notion, GitBook, plain markdown) means adding a module here and
one line to ``convert_document``.
"""
from __future__ import annotations

import os

from .. import config as config_module

#: Files that are already text need no conversion, only an identity.
TEXT_EXTENSIONS = (".md", ".markdown", ".txt", ".rst", ".text")


def _shipped_with(cfg):
    """-> a lookup from a page's `img src` to the bytes an export shipped.

    A space export keeps its pictures as files beside the pages, so the page
    points at a path inside the archive rather than carrying the picture. The
    unpacker keeps those files under the same flattened name a document gets,
    which is what makes the two ends meet.
    """
    from ..sync import flatten_member

    folder = cfg.layout.path("attachments")
    if not os.path.isdir(folder):
        return None
    available = set(os.listdir(folder))

    def find(source):
        from urllib.parse import unquote, urlsplit

        path = unquote(urlsplit(source or "").path).lstrip("/")
        for candidate in (flatten_member(path), os.path.basename(path)):
            if candidate and candidate in available:
                with open(os.path.join(folder, candidate), "rb") as handle:
                    return handle.read()
        return None

    return find


def convert_document(path, cfg=None):
    """Exported file -> (markdown, asset slug).

    The asset slug is the folder images were written to; the caller renames it
    once the document's final name is known.
    """
    cfg = cfg or config_module.load()
    extension = os.path.splitext(path)[1].lower()
    is_internal = cfg.is_internal

    if extension == ".pdf":
        from . import pdf

        slug = cfg.slug(os.path.splitext(os.path.basename(path))[0])
        assets_dir = os.path.join(cfg.layout.path("assets"), slug)
        pdf.MIN_IMG_BYTES = cfg.importer.min_image_bytes
        prefix = os.path.relpath(cfg.layout.path("assets"),
                                 cfg.layout.root).replace(os.sep, "/")
        blocks, links = pdf.convert(path, assets_dir=assets_dir, slug=slug,
                                    url_prefix=prefix)
        return pdf.build_markdown(path, blocks, links, is_internal), slug

    if extension == ".docx":
        from . import docx
        blocks, links, doc_id = docx.convert(path)
        return docx.build_markdown(path, blocks, links, doc_id, is_internal), ""

    if extension in TEXT_EXTENSIONS:
        from . import text
        body, links, doc_id = text.convert(path)
        return text.build_markdown(path, body, links, doc_id, is_internal), ""

    from . import html

    slug = cfg.slug(os.path.splitext(os.path.basename(path))[0])
    assets_dir = os.path.join(cfg.layout.path("assets"), slug)
    prefix = os.path.relpath(cfg.layout.path("assets"),
                             cfg.layout.root).replace(os.sep, "/")
    blocks, links, page_id = html.convert(
        path, assets_dir=assets_dir, slug=slug, url_prefix=prefix,
        min_image_bytes=cfg.importer.min_image_bytes,
        shipped=_shipped_with(cfg))
    return html.build_markdown(path, blocks, links, page_id, is_internal), slug


#: Import-time dependencies, reported once rather than per file.
REQUIREMENTS = {"pypdf": "pypdf", "pdfplumber": "pdfplumber", "bs4": "beautifulsoup4"}


def missing_dependencies():
    """Package names that need installing, in pip's spelling."""
    missing = []
    for module, package in REQUIREMENTS.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing
