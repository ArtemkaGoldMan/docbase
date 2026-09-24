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
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .. import frontmatter

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
    """A Word export is MHTML; pull the text/html part out of it."""
    raw = open(path, "rb").read()
    head = raw[:2048].lstrip().lower()
    if path.lower().endswith((".doc", ".mhtml", ".mht")) or head.startswith(b"mime-version"):
        msg = email.message_from_bytes(raw)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, "replace")
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def inline(node):
    """Element contents -> a markdown string, keeping links and emphasis."""
    out = []
    for child in node.children:
        name = getattr(child, "name", None)
        if name is None:
            out.append(re.sub(r"\s+", " ", str(child)))
        elif name in ("strong", "b"):
            text = inline(child).strip()
            out.append(f"**{text}**" if text else "")
        elif name in ("em", "i"):
            text = inline(child).strip()
            out.append(f"*{text}*" if text else "")
        elif name in ("code", "tt"):
            out.append(f"`{inline(child).strip()}`")
        elif name == "a":
            text = inline(child).strip()
            href = child.get("href", "")
            out.append(f"[{text}]({href})" if href and text else text)
        elif name == "br":
            out.append(" ")
        elif name == "img":
            alt = child.get("alt", "").strip()
            if alt:
                out.append(f"({alt})")
        else:
            out.append(inline(child))
    return re.sub(r" {2,}", " ", "".join(out))


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


def walk(node, blocks, depth=0):
    for child in node.children:
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
            ordered = name == "ol"
            for n, li in enumerate(child.find_all("li", recursive=False), 1):
                text = inline(li).strip()
                if text:
                    blocks.append(("  " * depth) + (f"{n}. " if ordered else "- ") + text)
            blocks.append("")
        elif name == "pre":
            blocks.append("```\n" + child.get_text().strip() + "\n```")
        elif name in ("div", "section", "article", "td", "span", "li", "blockquote", "body"):
            walk(child, blocks, depth + (name == "li"))
        # Everything else is deliberately ignored.


def convert(path):
    soup = BeautifulSoup(load_html(path), "html.parser")

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
