#!/usr/bin/env python3
"""HTML and Word (.doc) -> Markdown.

Better than PDF for table-heavy pages: a table row stays a row, so retrieval
lands on the right cell instead of a wall of text. Headings are explicit too,
rather than guessed from font size. Hidden macro tabs survive as well.

Accepts:
  .html/.htm   browser "Save page as HTML", or a space export
  .doc/.mhtml  a wiki "Export to Word" (MHTML on the inside)
"""
import argparse
import datetime as dt
import email
import os
import re
import sys
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from bs4.element import PreformattedString

from .. import frontmatter
from . import images

RE_CONFLUENCE_PAGE = re.compile(r"(?:/pages/(\d+)/|[?&]pageId=(\d+))")

# Wiki chrome that only gets in the way of retrieval.
JUNK_SELECTORS = [
    "script", "style", "noscript", "#breadcrumbs", ".page-metadata",
    "#footer", ".footer-body", "#comments-section", ".comment-actions",
    ".pageSection.group", "#likes-and-labels-container", ".hidden",
    "#navigation", ".aui-nav", ".confluence-information-macro-information",
]
CONTENT_SELECTORS = ["#main-content", ".wiki-content", "#content", "main", "body"]


def load_html(path):
    """-> (the page, {name: bytes} for anything travelling with it).

    A Word export is MHTML: the page and its pictures arrive in one file, and
    reading only the text/html part threw the pictures away. A wiki page whose
    subject is a diagram then imported as prose about a diagram nobody has.
    """
    raw = open(path, "rb").read()
    head = raw[:2048].lstrip().lower()
    if path.lower().endswith((".doc", ".mhtml", ".mht")) or head.startswith(b"mime-version"):
        msg = email.message_from_bytes(raw)
        page, attached = "", {}
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            if not page and part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                page = payload.decode(charset, "replace")
                continue
            location = part.get("Content-Location") or part.get("Content-ID") or ""
            key = _attachment_key(location)
            if key:
                attached[key] = payload
        if page:
            return page, attached
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc), {}
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), {}


def _attachment_key(reference):
    """What an `img src` and a part's location have in common: the file name."""
    tail = urlsplit(unquote((reference or "").strip().strip("<>"))).path
    return os.path.basename(tail.rstrip("/"))


def save_attachments(soup, attached, out_dir, slug, url_prefix, min_bytes):
    """Pictures that came with the page -> files, and markers in their place.

    The same bargain the PDF importer strikes: the file is kept, and the text
    carries a marker saying where it is, so an agent can decide whether the
    picture is worth opening rather than never learning it existed.
    """
    if not attached or out_dir is None:
        return 0
    written, kept = {}, 0
    for tag in soup.find_all("img"):
        key = _attachment_key(tag.get("src", ""))
        data = attached.get(key)
        if not data or len(data) < min_bytes or not images.is_a_figure(data):
            continue
        if key not in written:
            extension = images.extension(data)
            if not extension:
                continue                  # bytes nothing can open
            kept += 1
            name = f"attachment-{kept}{extension}"
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, name), "wb") as handle:
                handle.write(data)
            written[key] = f"{url_prefix}/{slug}/{name}"
        alt = (tag.get("alt") or "").strip()
        tag.replace_with(f"![{alt or 'image from this page'}]({written[key]})")
    return len(written)


def inline_child(child):
    """One node -> markdown. Split out so a caller can render part of an
    element: a list item's own text, without the list nested inside it."""
    if isinstance(child, PreformattedString):
        return ""                # a comment or doctype is not page text
    name = getattr(child, "name", None)
    if name is None:
        return re.sub(r"\s+", " ", str(child))
    if name in ("strong", "b"):
        text = inline(child).strip()
        return f"**{text}**" if text else ""
    if name in ("em", "i"):
        text = inline(child).strip()
        return f"*{text}*" if text else ""
    if name in ("code", "tt"):
        return f"`{inline(child).strip()}`"
    if name == "a":
        text = inline(child).strip()
        href = child.get("href", "")
        return f"[{text}]({href})" if href and text else text
    if name == "br":
        return " "
    if name == "img":
        alt = child.get("alt", "").strip()
        return f"({alt})" if alt else ""
    return inline(child)


def inline(node):
    """Element contents -> a markdown string, keeping links and emphasis."""
    return re.sub(r" {2,}", " ",
                  "".join(inline_child(child) for child in node.children))


def item_content(li):
    """An item's own text, and the lists nested inside it.

    Rendering a nested list as part of its parent's text collapses a whole
    sub-tree into one bullet: a real wiki page's table of contents arrived as
    a single line carrying eight links.
    """
    own, nested = [], []
    for child in li.children:
        if getattr(child, "name", None) in ("ul", "ol"):
            nested.append(child)
        else:
            own.append(child)
    text = re.sub(r" {2,}", " ", "".join(inline_child(c) for c in own))
    return text.strip(), nested


def table_to_md(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = [inline(td).strip().replace("|", "/").replace("\n", " ")
                 for td in tr.find_all(["td", "th"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "---|" * width]
    for row in rows[1:]:
        out.append("| " + " | ".join(row) + " |")
    return out


def emit_list(node, blocks, depth=0):
    """A list and everything nested under it, indented by level."""
    ordered = node.name == "ol"
    for n, li in enumerate(node.find_all("li", recursive=False), 1):
        text, nested = item_content(li)
        if text:
            blocks.append(("  " * depth) + (f"{n}. " if ordered else "- ") + text)
        for sub in nested:
            emit_list(sub, blocks, depth + 1 if text else depth)


def walk(node, blocks, depth=0):
    for child in node.children:
        if isinstance(child, PreformattedString):
            continue             # a comment is not page text, however long
        name = getattr(child, "name", None)
        if name is None:
            text = re.sub(r"\s+", " ", str(child)).strip()
            if text:
                blocks.append(text)
        elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = min(int(name[1]), 4)
            text = inline(child).strip()
            if text:
                blocks.append("#" * level + " " + text)
        elif name == "p":
            text = inline(child).strip()
            if text:
                blocks.append(text)
        elif name == "table":
            blocks.extend(table_to_md(child))
            blocks.append("")
        elif name in ("ul", "ol"):
            emit_list(child, blocks, depth)
            blocks.append("")
        elif name == "pre":
            blocks.append("```\n" + child.get_text().strip() + "\n```")
        elif name in ("div", "section", "article", "td", "span", "li", "blockquote", "body"):
            walk(child, blocks, depth + (name == "li"))
        # Everything else is deliberately ignored.


def convert(path, assets_dir=None, slug="", url_prefix="kb/assets",
            min_image_bytes=0):
    page, attached = load_html(path)
    soup = BeautifulSoup(page, "html.parser")
    save_attachments(soup, attached, assets_dir, slug, url_prefix,
                     min_image_bytes)

    for selector in JUNK_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    root = next((soup.select_one(s) for s in CONTENT_SELECTORS if soup.select_one(s)), soup)

    blocks = []
    walk(root, blocks)

    links = {}
    for a in root.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http"):
            links.setdefault(unquote(href), 1)

    page_id = ""
    meta = soup.find("meta", attrs={"name": "ajs-page-id"})
    if meta and meta.get("content", "").isdigit():
        page_id = meta["content"]
    if not page_id:
        for url in links:
            m = RE_CONFLUENCE_PAGE.search(url)
            if m:
                page_id = m.group(1) or m.group(2)
                break

    # A browser tab title carries the site and space name too: "Contributing
    # Code Changes - Apache Kafka - Apache Software Foundation". Wikis publish
    # the bare page title separately, and that is what the document is called.
    title = ""
    meta_title = soup.find("meta", attrs={"name": "ajs-page-title"})
    if meta_title and meta_title.get("content", "").strip():
        title = meta_title["content"].strip()
    if not title:
        heading = soup.select_one("#title-text")
        if heading:
            title = heading.get_text(" ", strip=True)
    if not title:
        tag = soup.find("title")
        title = tag.get_text().strip() if tag else os.path.basename(path)
    if not any(b.startswith("# ") for b in blocks[:5]):
        blocks.insert(0, f"# {title}")

    # Collapse runs of blank blocks.
    clean, prev_blank = [], False
    for b in blocks:
        blank = not b.strip()
        if blank and prev_blank:
            continue
        clean.append(b)
        prev_blank = blank
    return clean, links, page_id


def build_markdown(path, blocks, links, page_id, is_internal=lambda url: False):
    self_url = next((u for u in links if page_id and page_id in u), "")
    head = frontmatter.block(
        os.path.basename(path),
        page_id or frontmatter.derive_id(os.path.basename(path)),
        url=self_url,
        extracted=dt.date.today().isoformat())
    internal, external = [], []
    for url in sorted(links):
        m = RE_CONFLUENCE_PAGE.search(url)
        pid = (m.group(1) or m.group(2)) if m else ""
        row = f"| — | `{pid}` | {url} |"
        (internal if is_internal(url) else external).append(row)

    tail = ["", "---", "", "## Links found on this page", "",
            "### Internal", "", "| page | id | URL |", "|---|---|---|",
            *internal, "", "### External", "", "| page | id | URL |",
            "|---|---|---|", *external, ""]
    return "\n".join(head + blocks + tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    blocks, links, page_id = convert(args.src)
    md = build_markdown(args.src, blocks, links, page_id)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(md)
    print(f"{args.src} -> {args.out}", file=sys.stderr)
    print(f"  blocks: {len(blocks)}  links: {len(links)}  page id: {page_id or '?'}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
