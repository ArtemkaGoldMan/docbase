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

    if extension in TEXT_EXTENSIONS:
        from . import text
        body, links, doc_id = text.convert(path)
        return text.build_markdown(path, body, links, doc_id, is_internal), ""

    from . import html
    blocks, links, page_id = html.convert(path)
    return html.build_markdown(path, blocks, links, page_id, is_internal), ""


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
