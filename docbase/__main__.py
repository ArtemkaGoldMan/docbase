"""Command line entry point.

    docbase init      create a config in the current folder
    docbase sync      import new files, refresh what changed
    docbase find      search the base, return fragments
    docbase map       list documents and their sections
    docbase status    what is stale, changed or missing
    docbase eval      measure retrieval quality
    docbase verify    check extracts still match their source
    docbase doctor    check the environment

Agents talk to the base through this CLI, which keeps the tool usable from
anything that can run a command, not just one assistant.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import config as config_module


def cmd_init(args, cfg):
    root = os.path.abspath(args.path or os.getcwd())
    os.makedirs(root, exist_ok=True)
    path = config_module.write_default(
        root, language=args.language, internal_hosts=args.internal_host or ())
    layout = config_module.load(root).layout
    layout.ensure("originals", "text")
    print(f"Created {os.path.relpath(path, root)}")
    print("Drop an exported page into this folder and run: docbase sync")
    return 0


def cmd_sync(args, cfg):
    from . import sync
    sync.run(cfg, quiet=args.quiet, force=args.force)
    return 0


def cmd_find(args, cfg):
    from .search import Index

    index = Index(cfg).build()
    if not index.files():
        print("The base is empty. Drop an exported page into this folder "
              "and run: docbase sync")
        return 1

    query = " ".join(args.query)
    hits = index.search(query, limit=args.limit)
    settings = cfg.search

    if not hits:
        print("LOW CONFIDENCE: nothing matched at all.\n")
        _print_map(index)
        print("\nThis topic may not be in the base. Check the map above "
              "before concluding the answer is missing.")
        return 0

    if hits[0][0] < settings.low_confidence:
        print(f"LOW CONFIDENCE (best score {hits[0][0]:.2f} "
              f"< {settings.low_confidence}).")
        print("The fragments below may be the wrong ones. Where to look next:\n")
        shown = []
        for _score, name, _line, _heading, _body in hits:
            if name in shown:
                continue
            shown.append(name)
            print(f"  {name}")
            for line_no, heading in index.headings(name)[:12]:
                print(f"     {line_no:>5}  {heading}")
            if len(shown) == 2:
                break
        print("\n  Next: search again using the wording of the documentation")
        print("  rather than the wording of the question, or read the section")
        print("  directly: sed -n '<line>,+40p' <file>\n")

    budget = settings.total_chars
    for score, name, line_no, heading, body in hits:
        text = "\n".join(l for l in body.splitlines() if l.strip())
        text = text[:min(settings.per_hit_chars, budget)]
        print(f"\n=== {name}:{line_no}  [{score:.2f}]  {heading or '(no heading)'}")
        print(text)
        budget -= len(text)
        if budget <= 0:
            print("\n(truncated — narrow the query)")
            break
    return 0


def _print_map(index):
    import re
    for name, path in index.files():
        text = open(path, encoding="utf-8").read()
        title = re.search(r"^#\s+(?:\[)?(.+?)(?:\]\(|$)", text, re.M)
        from .search import clean
        print(f"\n## {name} — {clean(title.group(1)) if title else name}")
        for line_no, heading in index.headings(name):
            print(f"  {line_no:>5}  {heading}")


def cmd_map(args, cfg):
    from .search import Index
    index = Index(cfg).build()
    if not index.files():
        print("The base is empty.")
        return 1
    _print_map(index)
    return 0


def cmd_status(args, cfg):
    from . import status
    return status.report(cfg)


def cmd_eval(args, cfg):
    from . import evaluate
    if args.generate:
        return evaluate.generate(cfg, count=args.count)
    return evaluate.run(cfg, verbose=args.verbose)


def cmd_verify(args, cfg):
    from . import verify
    return verify.report(cfg, verbose=args.verbose)


def cmd_doctor(args, cfg):
    ok = True
    print(f"Python      {sys.version.split()[0]}")
    for module in ("pypdf", "pdfplumber", "bs4"):
        try:
            __import__(module)
            print(f"{module:<12}installed")
        except ImportError:
            print(f"{module:<12}MISSING — run: pip install pypdf pdfplumber beautifulsoup4")
            ok = False
    from . import languages as languages_module
    custom, _ = languages_module.load_custom(cfg.layout.root)
    active = ", ".join(cfg.languages)
    print(f"Languages   {active}"
          + (f"  (custom: {', '.join(sorted(custom))})" if custom else ""))
    unknown = [c for c in cfg.languages
               if c not in languages_module.STOPWORDS and c not in custom]
    if unknown:
        print(f"            ! no stopwords for: {', '.join(unknown)} — "
              f"see docs/LANGUAGES.md")
        print(f"            built in: {', '.join(languages_module.available())}")
    print(f"Base root   {cfg.layout.root}")
    for which in ("originals", "text", "assets", "cards"):
        path = cfg.layout.path(which)
        count = len(os.listdir(path)) if os.path.isdir(path) else 0
        print(f"  {which:<10}{count} entries")
    return 0 if ok else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="docbase", description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="knowledge base root (default: nearest config)")
    subparsers = parser.add_subparsers(dest="command")

    p = subparsers.add_parser("init", help="create a config in this folder")
    p.add_argument("path", nargs="?")
    p.add_argument("--language", default="en")
    p.add_argument("--internal-host", action="append",
                   help="wiki host whose links should resolve locally")
    p.set_defaults(func=cmd_init)

    p = subparsers.add_parser("sync", help="import and refresh")
    p.add_argument("--quiet", action="store_true", help="say nothing when unchanged")
    p.add_argument("--force", action="store_true", help="reconvert everything")
    p.set_defaults(func=cmd_sync)

    p = subparsers.add_parser("find", help="search the base")
    p.add_argument("query", nargs="*")
    p.add_argument("-n", "--limit", type=int, default=None)
    p.set_defaults(func=cmd_find)

    p = subparsers.add_parser("map", help="documents and their sections")
    p.set_defaults(func=cmd_map)

    p = subparsers.add_parser("status", help="what is stale, changed or missing")
    p.set_defaults(func=cmd_status)

    p = subparsers.add_parser("eval", help="measure retrieval quality")
    p.add_argument("--generate", action="store_true",
                   help="build test cases from the corpus itself")
    p.add_argument("--count", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = subparsers.add_parser("verify", help="check extracts against their source")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = subparsers.add_parser("doctor", help="check the environment")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfg = config_module.load(args.root)
    return args.func(args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
