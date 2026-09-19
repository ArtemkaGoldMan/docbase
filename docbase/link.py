#!/usr/bin/env python3
"""Stitch the base together: cross-document links become local paths.

A second pass, run after import. It reads the frontmatter of every converted
document, builds a ``page id -> file name`` registry, and rewrites links::

    [Billing rules](https://wiki.example.com/pages/viewpage.action?pageId=42)
    -> [Billing rules](billing.md)

A separate pass on purpose: a newly added document has to repair links inside
the documents that already existed.

Idempotent. Links that are already local are left alone; links to pages that
have not been imported yet stay absolute and are reported as missing.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import unquote

SOURCES = "kb/sources"
LINKMAP = "kb/linkmap.json"
GRAPH = "kb/graph.md"

RE_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
from . import frontmatter
from . import resolve as resolve_module
RE_TITLE = re.compile(r"^#\s+(?:\[)?(.+?)(?:\]\(|$)", re.M)
RE_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
PAGE_ID_TAIL = r"/.*?(?:/pages/(\d+)/|[?&]pageId=(\d+))"


def page_pattern(internal_hosts):
    """Match wiki page URLs on any of the configured hosts."""
    if not internal_hosts:
        return re.compile(r"(?:/pages/(\d+)/|[?&]pageId=(\d+))")
    hosts = "|".join(re.escape(h) for h in internal_hosts)
    return re.compile(f"(?:{hosts})" + PAGE_ID_TAIL)


RE_CONF_PAGE = page_pattern(())
RE_ANCHOR = re.compile(r"#(.+)$")


def page_id_of(url, pattern=None):
    m = (pattern or RE_CONF_PAGE).search(url)
    return (m.group(1) or m.group(2)) if m else None


def _node(name):
    """A mermaid-safe node id."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def scan(sources_dir):
    """{file name: what is known about it} from every document's frontmatter."""
    documents = {}
    for name in sorted(os.listdir(sources_dir)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        text = open(os.path.join(sources_dir, name), encoding="utf-8").read()
        fm = RE_FRONTMATTER.match(text)
        meta = fm.group(1) if fm else ""
        title = RE_TITLE.search(text)
        declared = re.search(r'title:\s*"(.+?)"', meta)
        documents[name] = {
            "page_id": frontmatter.read_id(meta),
            "url": (re.search(r'source_url:\s*"(.*?)"', meta) or [None, ""])[1],
            "source_file": (re.search(r'source_file:\s*"(.*?)"', meta) or [None, ""])[1],
            "title": declared.group(1) if declared
                     else (title.group(1).strip() if title else name),
        }
    return documents


def relink(text, self_name, resolver, stats, pattern=None,
           is_internal=lambda url: False):
    """Rewrite the links inside one document."""
    def repl(m):
        label, url = m.group(1), m.group(2)
        if not url.startswith(("http://", "https://")):
            return m.group(0)                       # already local
        target = resolver.resolve(url, pattern, exclude=self_name)
        if target:
            anchor = RE_ANCHOR.search(unquote(url))
            hint = ""
            if anchor:
                frag = re.sub(r"[:~].*$", "", anchor.group(1)).replace("id-", "")
                frag = re.sub(r"[^\w\s-]", " ", frag, flags=re.UNICODE).strip()
                if frag:
                    hint = f' "{frag[:60]}"'
            stats["linked"] += 1
            stats["edges"].add((self_name, target))
            return f"[{label}]({target}{hint})"

        if resolver.resolve(url, pattern) == self_name:
            stats["self"] += 1
            return label                            # a link back to itself

        # "Missing" means a document this base ought to hold, not any link to
        # the outside world. Without that distinction the list fills with DOI
        # prefixes, dates and section numbers, and stops being read.
        page_id = resolver.page_id_of(url, pattern) if pattern is not None else None
        if page_id:
            stats["missing"].setdefault(page_id, set()).add(label[:60])
        elif is_internal(url):
            found = resolve_module.identifiers(url)
            if found:
                key = sorted(found, key=len, reverse=True)[0]
                stats["missing"].setdefault(key, set()).add(label[:60])
        return m.group(0)

    return RE_MD_LINK.sub(repl, text)


def run(sources=SOURCES, check=False, quiet=False, internal_hosts=(),
        linkmap_path=None, graph_path=None):
    """Rewrite cross-document links and regenerate the map.

    Output paths are explicit: resolving them against the current working
    directory only works when the caller happens to stand in the base root.
    """
    linkmap_path = linkmap_path or LINKMAP
    graph_path = graph_path or GRAPH
    # Passed down rather than stashed in a module global: two calls with
    # different hosts in one process would otherwise interfere, and a
    # long-running server is exactly such a process.
    pattern = page_pattern(internal_hosts)

    def is_internal(url):
        return any(host in (url or "") for host in internal_hosts)
    class _A: pass
    args = _A(); args.sources = sources; args.check = check

    documents = scan(args.sources)
    stats = {"linked": 0, "self": 0, "missing": {}, "edges": set()}
    if not documents:
        return documents, stats         # an empty base is a state, not an error

    resolver = resolve_module.Resolver(documents)
    for name in sorted(documents):
        path = os.path.join(args.sources, name)
        text = open(path, encoding="utf-8").read()
        new = relink(text, name, resolver, stats, pattern, is_internal)
        if new != text and not args.check:
            open(path, "w", encoding="utf-8").write(new)

    # linkmap.json is derived; it is never edited by hand.
    linkmap = {
        "_comment": "Generated by docbase. Do not edit by hand.",
        "documents": {name: {k: v for k, v in info.items() if v}
                      for name, info in sorted(documents.items())},
        "missing": {key: sorted(labels)
                    for key, labels in sorted(stats["missing"].items())},
    }
    if not args.check:
        os.makedirs(os.path.dirname(os.path.abspath(linkmap_path)), exist_ok=True)
        with open(linkmap_path, "w", encoding="utf-8") as handle:
            json.dump(linkmap, handle, ensure_ascii=False, indent=2)

    # graph.md is the human- and agent-readable map of the base.
    lines = ["# Base map", "",
             "Generated by `docbase sync`. Shows which documents exist and what",
             "they reference.", "", "## Imported", "",
             "| Document | File | id |", "|---|---|---|"]
    for name, info in sorted(documents.items()):
        lines.append(f"| {info['title']} | [{name}](sources/{name}) | "
                     f"`{info.get('page_id') or '—'}` |")

    lines += ["", "## Referenced but not imported yet", ""]
    if stats["missing"]:
        lines += ["| identifier | Referred to as |", "|---|---|"]
        for key, labels in sorted(stats["missing"].items()):
            shown = "; ".join(sorted(labels)[:3]).replace("|", "/") or "—"
            lines.append(f"| `{key}` | {shown} |")
    else:
        lines.append("Everything referenced is already available locally.")

    if stats["edges"]:
        lines += ["", "## Reference graph", "", "```mermaid", "graph LR"]
        for a, b in sorted(stats["edges"]):
            lines.append(f'  {_node(a)}["{a}"] --> {_node(b)}["{b}"]')
        lines.append("```")
    lines.append("")

    if not args.check:
        os.makedirs(os.path.dirname(os.path.abspath(graph_path)), exist_ok=True)
        with open(graph_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    if not quiet:
        mode = "checked" if args.check else "written"
        print(f"[{mode}] documents: {len(documents)}  "
              f"links localized: {stats['linked']}  "
              f"self-links dropped: {stats['self']}  "
              f"references not imported: {len(stats['missing'])}", file=sys.stderr)
    return documents, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=SOURCES)
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    args = ap.parse_args()
    documents, _ = run(sources=args.sources, check=args.check)
    if not documents:
        raise SystemExit(f"no documents found in {args.sources}")


if __name__ == "__main__":
    main()
