#!/usr/bin/env python3
"""PDF -> Markdown, locally and without losses.

Wiki exports keep their hyperlinks as PDF link annotations (``/Annots``
entries carrying a ``/URI``) and lay text out by coordinate. Generic
converters throw both away. This one:

* reads words together with their coordinates, font size and weight;
* rebuilds reading order via a recursive XY-cut, so multi-column pages do
  not interleave;
* restores hyperlinks by matching annotation rectangles to the words under
  them;
* derives headings from font sizes, bold and italic from font weight;
* strips running headers, page numbers and icon fonts;
* extracts meaningful images and leaves a marker where they belonged.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from urllib.parse import unquote

import logging

import pdfplumber
from pypdf import PdfReader

from .. import frontmatter

# pdfminer warns about fonts on every page; the warnings are harmless.
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pypdf").setLevel(logging.CRITICAL)   # broken-file noise is
                                                        # reported by sync itself

# Font size -> heading level. Body text in these exports is around 9.9pt.
HEADING_SIZES = [(16.0, "#"), (11.4, "##"), (10.2, "###")]
ICON_FONTS = ("ADGSIcons",)             # wiki icon fonts render as junk glyphs
LINE_TOL = 3.0          # vertical tolerance for grouping words into a line
PARA_GAP = 1.7          # gap (in line heights) that starts a new paragraph
HEADER_ZONE = 40        # running header band, in points
FOOTER_ZONE = 45        # running footer band, in points

# Images: drop icons and decoration, keep meaningful screenshots.
MIN_IMG_BYTES = 20_000

RE_PRINT_HEADER = re.compile(r"^\d{1,2}/\d{1,2}/\d{2},\s+\d{1,2}:\d{2}\s*[AP]M")
RE_PAGE_NUM = re.compile(r"^\d+/\d+$")
RE_PUA = re.compile(r"[\ue000-\uf8ff]")     # private use area: icon glyphs
RE_CONFLUENCE_PAGE = re.compile(r"(?:/pages/(\d+)/|[?&]pageId=(\d+))")


MIN_GUTTER = 20.0       # narrowest gap counted as a column gutter, in points
MIN_ROW_GAP = 13.0      # narrowest gap counted as a band separator, in points


def _gaps(intervals, lo, hi, min_size):
    """Gaps inside [lo, hi] that no interval covers."""
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, prev = [], lo
    for a, b in merged:
        if a - prev >= min_size:
            out.append((prev, a))
        prev = max(prev, b)
    if hi - prev >= min_size:
        out.append((prev, hi))
    return out


def xy_cut(words, depth=0):
    """Page -> blocks in reading order: bands top to bottom, and inside a
    band, columns left to right."""
    if len(words) <= 1 or depth > 12:
        return [words] if words else []

    top = min(w["top"] for w in words)
    bottom = max(w["bottom"] for w in words)
    left = min(w["x0"] for w in words)
    right = max(w["x1"] for w in words)

    # Cut horizontally first, into bands.
    row_gaps = _gaps([(w["top"], w["bottom"]) for w in words], top, bottom, MIN_ROW_GAP)
    if row_gaps:
        bounds = [top] + [g[0] for g in row_gaps] + [bottom + 1]
        out = []
        for a, b in zip(bounds, bounds[1:]):
            band = [w for w in words if a <= w["top"] < b]
            if band:
                out.extend(xy_cut(band, depth + 1) if len(band) < len(words) else [band])
        return out

    # A uniform band: try cutting vertically, into columns.
    col_gaps = _gaps([(w["x0"], w["x1"]) for w in words], left, right, MIN_GUTTER)
    if col_gaps:
        bounds = [left] + [g[0] for g in col_gaps] + [right + 1]
        out = []
        for a, b in zip(bounds, bounds[1:]):
            col = [w for w in words if a <= w["x0"] < b]
            if col:
                out.extend(xy_cut(col, depth + 1) if len(col) < len(words) else [col])
        return out

    return [words]


def page_links(pypdf_page, page_height):
    """Link rectangles for one page as [(x0, top, x1, bottom, url)]."""
    out = []
    for annot in (pypdf_page.get("/Annots") or []):
        obj = annot.get_object()
        if obj.get("/Subtype") != "/Link":
            continue
        action = obj.get("/A")
        if not action:
            continue
        uri = action.get_object().get("/URI")
        if not uri:
            continue
        x0, y0, x1, y1 = [float(v) for v in obj["/Rect"]]
        out.append((x0, page_height - y1, x1, page_height - y0, str(uri)))
    return out


def url_for(word, links):
    cx = (word["x0"] + word["x1"]) / 2
    cy = (word["top"] + word["bottom"]) / 2
    for x0, top, x1, bottom, uri in links:
        if x0 - 2 <= cx <= x1 + 2 and top - 3 <= cy <= bottom + 3:
            return uri
    return None


def group_lines(words):
    """Words -> lines, ordered top to bottom then left to right."""
    lines, cur = [], []
    for w in sorted(words, key=lambda w: (round(w["top"] / LINE_TOL), w["x0"])):
        if cur and abs(w["top"] - cur[0]["top"]) > LINE_TOL:
            lines.append(sorted(cur, key=lambda w: w["x0"]))
            cur = []
        cur.append(w)
    if cur:
        lines.append(sorted(cur, key=lambda w: w["x0"]))
    return lines


def render_line(words):
    """A line of words -> markdown with bold, italic and links preserved."""
    parts, run, style = [], [], None

    def flush():
        if not run:
            return
        text = " ".join(run)
        bold, italic, uri = style
        if italic:
            text = f"*{text}*"
        if bold:
            text = f"**{text}**"
        if uri:
            text = f"[{text}]({uri})"
        parts.append(text)

    for w in words:
        font = w["fontname"]
        cur = ("Bold" in font, "Italic" in font or "Oblique" in font, w.get("uri"))
        if cur != style:
            flush()
            run, style = [], cur
        run.append(w["text"])
    flush()
    return " ".join(parts)


def heading_prefix(words):
    size = max(w["size"] for w in words)
    plain = " ".join(w["text"] for w in words)
    if any("Italic" in w["fontname"] for w in words):
        return ""                      # italic marks quoted text, not a heading
    for threshold, hashes in HEADING_SIZES:
        if size >= threshold:
            if hashes == "###":
                bold = any("Bold" in w["fontname"] for w in words)
                if not bold or len(plain) > 110:
                    return ""
            return hashes + " "
    return ""


def extract_images(reader, out_dir, slug, url_prefix="kb/assets"):
    """Meaningful images -> files plus markers. Icons and repeated decoration
    (a logo on every page) are filtered out by size and content hash."""
    if out_dir is None:
        return {}
    seen, per_page = {}, {}
    for page_no, page in enumerate(reader.pages, 1):
        for idx, image in enumerate(page.images, 1):
            data = image.data
            if len(data) < MIN_IMG_BYTES:
                continue                                  # icon
            digest = hashlib.sha1(data).hexdigest()
            if digest in seen:
                continue                                  # decoration, seen before
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            name = f"p{page_no:02d}-{idx}{ext}"
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, name), "wb") as fh:
                fh.write(data)
            seen[digest] = name
            per_page.setdefault(page_no, []).append(
                f"{url_prefix}/{slug}/{name}")
    return per_page


def convert(pdf_path, assets_dir=None, slug="", url_prefix="kb/assets"):
    reader = PdfReader(pdf_path)
    blocks, all_links = [], {}
    images = extract_images(reader, assets_dir, slug, url_prefix)

    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages):
            height = float(reader.pages[page_no].mediabox.height)
            links = page_links(reader.pages[page_no], height)
            for *_, uri in links:
                all_links.setdefault(unquote(uri), page_no + 1)

            words = []
            for w in page.extract_words(extra_attrs=["size", "fontname"]):
                if w["top"] < HEADER_ZONE or w["bottom"] > page.height - FOOTER_ZONE:
                    continue                                   # running header/footer
                if any(f in w["fontname"] for f in ICON_FONTS):
                    continue                                   # icon font
                w["text"] = RE_PUA.sub("", w["text"]).strip()
                if not w["text"]:
                    continue
                w["uri"] = url_for(w, links)
                words.append(w)

            for block in xy_cut(words):
                prev_bottom, prev_size = None, 10.0
                for line in group_lines(block):
                    text = render_line(line).strip()
                    plain = re.sub(r"[*\[\]]|\(https?://[^)]*\)", "", text).strip()
                    if not plain or RE_PRINT_HEADER.match(plain) or RE_PAGE_NUM.match(plain):
                        continue

                    top = min(w["top"] for w in line)
                    gap = (top - prev_bottom) if prev_bottom is not None else 999
                    new_para = gap > prev_size * PARA_GAP
                    prefix = heading_prefix(line)

                    if prefix or new_para or not blocks:
                        blocks.append(prefix + text)
                    else:
                        blocks[-1] += " " + text

                    prev_bottom = max(w["bottom"] for w in line)
                    prev_size = max(w["size"] for w in line)

            for path in images.get(page_no + 1, []):
                blocks.append(
                    f"![image from page {page_no + 1}]({path})\n"
                    f"<!-- This image carries content that is not in the text. "
                    f"Open the file only if the answer is not nearby. -->")

    return blocks, all_links


def build_markdown(pdf_path, blocks, links, is_internal=lambda url: False):
    """The document's own page id.

    Wiki exports carry a "last modified" link that always points at the
    current page; use it when present, otherwise the most frequent id.
    """
    page_id, self_url = "", ""
    for url in links:
        m = re.search(r"diffpagesbyversion\.action\?pageId=(\d+)", url)
        if m:
            page_id = m.group(1)
            break
    if not page_id:
        counts = {}
        for url in links:
            m = RE_CONFLUENCE_PAGE.search(url)
            if m and "diffpages" not in url:
                pid = m.group(1) or m.group(2)
                counts[pid] = counts.get(pid, 0) + 1
        page_id = max(counts, key=counts.get) if counts else ""
    for url in links:
        m = RE_CONFLUENCE_PAGE.search(url)
        if m and (m.group(1) or m.group(2)) == page_id and "diffpages" not in url:
            self_url = url
            break

    head = frontmatter.block(
        os.path.basename(pdf_path),
        page_id or frontmatter.derive_id(os.path.basename(pdf_path)),
        url=self_url,
        extracted=dt.date.today().isoformat())

    internal, external = [], []
    for url, page in sorted(links.items(), key=lambda kv: kv[1]):
        m = RE_CONFLUENCE_PAGE.search(url)
        pid = (m.group(1) or m.group(2)) if m else ""
        row = f"| {page} | `{pid}` | {url} |"
        (internal if is_internal(url) else external).append(row)

    tail = ["", "---", "", "## Links found on this page", "",
            "### Internal", "", "| page | id | URL |", "|---|---|---|",
            *internal, "", "### External", "", "| page | id | URL |",
            "|---|---|---|", *external, ""]

    return "\n".join(head + [b + "\n" for b in blocks] + tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--links-json", help="write the collected links to this file")
    args = ap.parse_args()

    blocks, links = convert(args.pdf)
    md = build_markdown(args.pdf, blocks, links)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(md)

    if args.links_json:
        with open(args.links_json, "w", encoding="utf-8") as fh:
            json.dump(links, fh, ensure_ascii=False, indent=2)

    print(f"{args.pdf} -> {args.out}", file=sys.stderr)
    print(f"  blocks: {len(blocks)}  links: {len(links)}  chars: {len(md)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
