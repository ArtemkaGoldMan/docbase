"""Word .docx.

A .docx is a zip holding XML, so this needs no third-party library. That is
worth the extra hundred lines: the tool's appeal is that it runs on whatever
Python a non-technical user already has, and every added dependency is another
thing that can fail to install on their machine.

Not to be confused with the .doc a wiki produces on "Export to Word" — that one
is MHTML and goes through the HTML importer.

What survives: headings (from paragraph styles, not guessed from font size),
bold and italic, lists, tables as tables, and hyperlinks resolved through the
relationship file.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import zipfile
from xml.etree import ElementTree

from .. import frontmatter

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

DOCUMENT = "word/document.xml"
RELATIONS = "word/_rels/document.xml.rels"
NUMBERING = "word/numbering.xml"

RE_HEADING_STYLE = re.compile(r"^heading\s*(\d)$", re.I)


def _relationships(archive):
    """Relationship id -> target URL, for hyperlinks."""
    try:
        raw = archive.read(RELATIONS)
    except KeyError:
        return {}
    out = {}
    for node in ElementTree.fromstring(raw):
        if node.get("Type", "").endswith("/hyperlink"):
            out[node.get("Id")] = node.get("Target", "")
    return out


def _numbering_formats(archive):
    """numId -> whether that list is numbered rather than bulleted.

    Word keeps the marker in a separate part, so a list arrives without any
    hint of whether it was "1. 2. 3." or a row of dots. In a procedure that
    distinction is the difference between steps and options.
    """
    try:
        raw = archive.read(NUMBERING)
    except KeyError:
        return {}
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return {}

    abstract = {}
    for node in root.findall(f"{W}abstractNum"):
        fmt = node.find(f"{W}lvl/{W}numFmt")
        abstract[node.get(f"{W}abstractNumId")] = (
            fmt.get(f"{W}val") if fmt is not None else "")

    out = {}
    for node in root.findall(f"{W}num"):
        ref = node.find(f"{W}abstractNumId")
        key = ref.get(f"{W}val") if ref is not None else None
        out[node.get(f"{W}numId")] = abstract.get(key, "") not in ("bullet", "")
    return out


def _run_text(run):
    """One run, with its emphasis applied."""
    text = "".join(node.text or "" for node in run.iter(f"{W}t"))
    if not text:
        return ""
    properties = run.find(f"{W}rPr")
    if properties is not None:
        stripped = text.strip()
        if stripped:
            lead = text[:len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            if properties.find(f"{W}b") is not None:
                stripped = f"**{stripped}**"
            if properties.find(f"{W}i") is not None:
                stripped = f"*{stripped}*"
            text = f"{lead}{stripped}{trail}"
    return text


def _paragraph_text(paragraph, relations, links):
    parts = []
    for child in paragraph:
        if child.tag == f"{W}r":
            parts.append(_run_text(child))
        elif child.tag == f"{W}hyperlink":
            inner = "".join(_run_text(run) for run in child.findall(f"{W}r")).strip()
            target = relations.get(child.get(f"{R}id"), "")
            if target:
                links.setdefault(target, 1)
                parts.append(f"[{inner}]({target})" if inner else target)
            else:
                parts.append(inner)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _style(paragraph):
    """-> (style name, list level or None, numbering id)."""
    properties = paragraph.find(f"{W}pPr")
    if properties is None:
        return "", None, None
    style = properties.find(f"{W}pStyle")
    name = style.get(f"{W}val", "") if style is not None else ""

    numbering = properties.find(f"{W}numPr")
    if numbering is None:
        return name, None, None
    level_node = numbering.find(f"{W}ilvl")
    id_node = numbering.find(f"{W}numId")
    level = int(level_node.get(f"{W}val", "0")) if level_node is not None else 0
    return name, level, (id_node.get(f"{W}val") if id_node is not None else None)


def _table(table, relations, links):
    rows = []
    for row in table.findall(f"{W}tr"):
        cells = []
        for cell in row.findall(f"{W}tc"):
            text = " ".join(
                _paragraph_text(p, relations, links)
                for p in cell.findall(f"{W}p"))
            cells.append(text.replace("|", "/").strip())
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for row in rows[1:]:
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    return out


def convert(path):
    """.docx -> (blocks, links, id)."""
    with zipfile.ZipFile(path) as archive:
        relations = _relationships(archive)
        formats = _numbering_formats(archive)
        root = ElementTree.fromstring(archive.read(DOCUMENT))

    body = root.find(f"{W}body")
    blocks, links = [], {}
    if body is None:
        return blocks, links, ""

    for node in body:
        if node.tag == f"{W}tbl":
            blocks.extend(_table(node, relations, links))
            continue
        if node.tag != f"{W}p":
            continue

        text = _paragraph_text(node, relations, links)
        if not text:
            continue
        style, level, num_id = _style(node)
        heading = RE_HEADING_STYLE.match(style or "")
        if heading:
            blocks.append("#" * min(int(heading.group(1)), 4) + " " + text)
        elif level is not None or style.lower().startswith("listparagraph"):
            indent = "  " * min(level or 0, 5)
            marker = "1." if formats.get(num_id) else "-"
            blocks.append(f"{indent}{marker} {text}")
        else:
            blocks.append(text)

    title = next((b.lstrip("# ").strip() for b in blocks if b.startswith("# ")), "")
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
        blocks.insert(0, f"# {title}")

    return blocks, links, frontmatter.derive_id(title, len(blocks))


def build_markdown(path, blocks, links, doc_id, is_internal=lambda url: False):
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

    return "\n".join(head) + _join(blocks) + "\n" + "\n".join(tail) + "\n"


def _join(blocks):
    """Blank-line separated, except inside a table or a list.

    A markdown table whose rows are separated by blank lines is not a table
    any more, and a list becomes a run of unrelated bullets.
    """
    def contiguous(text):
        stripped = text.lstrip()
        return text.startswith("|") or stripped.startswith(("- ", "1. "))

    out, previous = [], None
    for block in blocks:
        if not block.strip():
            previous = None          # a blank block ends the run
            continue
        if previous is None:
            out.append(("\n\n" if out else "") + block)
        elif contiguous(block) and contiguous(previous):
            out.append("\n" + block)
        else:
            out.append("\n\n" + block)
        previous = block
    return "".join(out)
