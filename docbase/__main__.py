"""Command line entry point.

    docbase init      create a config in the current folder
    docbase sync      import new files, refresh what changed
    docbase find      search the base, return fragments
    docbase map       list documents and their sections
    docbase read      read a document from a given line
    docbase status    what is stale, changed or missing
    docbase eval      measure retrieval quality
    docbase verify    check extracts still match their source
    docbase selftest  check the base still holds and finds what work needs
    docbase doctor    check the environment
    docbase serve     expose the base to any MCP-capable agent

Agents talk to the base through this CLI, which keeps the tool usable from
anything that can run a command, not just one assistant.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import config as config_module
from . import messages
from .messages import say


def cmd_init(args, cfg):
    root = os.path.abspath(args.path or os.getcwd())
    os.makedirs(root, exist_ok=True)
    # A language the tool can speak is the one it speaks to this base.
    interface = args.language if args.language in messages.CATALOGS else None
    path = config_module.write_default(
        root, language=args.language, internal_hosts=args.internal_host or (),
        interface=interface)
    loaded = config_module.load(root)
    loaded.layout.ensure("originals", "text")
    messages.use(loaded.interface)
    print(say("Created {path}", path=os.path.relpath(path, root)))
    print(say("Drop an exported page into this folder and run: docbase sync"))
    return 0


def cmd_sync(args, cfg):
    from . import sync
    # Sync moves every document it finds in the folder into the base. Run in
    # a folder that is not one — the hook fires in whatever project the agent
    # was opened in — it would have taken the README, and unpacked and
    # deleted any archive lying there.
    if not os.path.isfile(os.path.join(cfg.layout.root, config_module.CONFIG_NAME)):
        if not args.quiet:
            print(say("There is no base here: no {config} in this folder or above "
                      "it. Create one with: docbase init",
                      config=config_module.CONFIG_NAME))
            return 1
        return 0
    if not args.quiet:
        sync.run(cfg, quiet=False, force=args.force)
        return 0
    # Quiet is how the hook runs it before every message, and a hook's error
    # output reaches nobody: the base silently stopped updating and nothing
    # said so. Whatever goes wrong is said on stdout, which the agent reads.
    try:
        sync.run(cfg, quiet=True, force=args.force)
    except SystemExit as stop:
        if stop.code in (None, 0):
            return 0
        print(say("The base was not updated: {why}\nRun `python -m docbase sync` "
                  "to see what went wrong.", why=stop.code))
    except Exception as error:                        # noqa: BLE001
        print(say("The base was not updated: {why}\nRun `python -m docbase sync` "
                  "to see what went wrong.", why=f"{type(error).__name__}: {error}"))
    return 0


def cmd_find(args, cfg):
    from .search import Index

    index = Index(cfg).build()
    if not index.files():
        print(say("The base is empty. Drop an exported page into this folder "
                  "and run: docbase sync"))
        return 1

    query = " ".join(args.query)
    hits = index.search(query, limit=args.limit)
    settings = cfg.search

    if not hits:
        print(say("LOW CONFIDENCE — nothing in the base matched the question."))
        unknown = index.unknown_words(query)
        if unknown:
            print(say("Words the documentation never uses: {words}",
                      words=", ".join(unknown)))
        print()
        _print_map(index, cap=25)
        print("\n" + say("Ask again in the documentation's words, taken from the "
                         "map above. Only after that conclude the base does not "
                         "cover it."))
        return 0

    from .search import low_confidence_notice
    notice = low_confidence_notice(index, query, hits)
    if notice:
        print("\n".join(notice))
        print()

    from .search import shown_fragments
    print("\n".join(shown_fragments(settings, hits)))
    return 0


def _print_map(index, wanted="", cap=0):
    from .search import outline
    for line in outline(index, wanted, cap):
        print(line)


def cmd_map(args, cfg):
    from .search import Index
    index = Index(cfg).build()
    if not index.files():
        print(say("The base is empty."))
        return 1
    _print_map(index, getattr(args, "document", "") or "")
    return 0


def cmd_read(args, cfg):
    """A document from a given line — what `sed -n` did, on any system."""
    from .mcp import _read_section
    print(_read_section(cfg, {"file": args.document, "line": args.line,
                              "lines": args.lines}))
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


def cmd_selftest(args, cfg):
    from . import selftest
    return selftest.report(cfg, verbose=args.verbose, quick=args.quick)


def cmd_serve(args, cfg):
    from . import mcp
    return mcp.serve(cfg)


def cmd_doctor(args, cfg):
    ok = True
    print(f"Python      {sys.version.split()[0]}")
    for module in ("pypdf", "pdfplumber", "bs4"):
        try:
            __import__(module)
            print(f"{module:<12}" + say("installed"))
        except ImportError:
            print(f"{module:<12}" + say("MISSING — run: pip install pypdf "
                                        "pdfplumber beautifulsoup4"))
            ok = False
    from . import languages as languages_module
    custom = dict(cfg.custom_stopwords)
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
        print(f"  {which:<10}" + say("{count} entries", count=count))

    from . import skills as skills_module
    count, problems = skills_module.report(cfg.layout.root, cfg.layout.text)
    if count or problems:
        print("  skills    " + say("{count} written for this base", count=count))
    for name, complaint in problems:
        print(f"            ! {name}: {complaint}")
        ok = False
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
    p.add_argument("document", nargs="?", default="",
                   help="name or title fragment; shows that document's sections")
    p.set_defaults(func=cmd_map)

    p = subparsers.add_parser("read", help="read a document from a given line")
    p.add_argument("document", help="document name, as find and map print it")
    p.add_argument("line", nargs="?", type=int, default=1)
    p.add_argument("-n", "--lines", type=int, default=40,
                   help="how many lines (at most 200)")
    p.set_defaults(func=cmd_read)

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

    p = subparsers.add_parser("selftest",
                              help="integrity, anchors and working questions")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--quick", action="store_true",
                   help="skip re-reading the originals")
    p.set_defaults(func=cmd_selftest)

    p = subparsers.add_parser("serve", help="run an MCP server on stdio")
    p.set_defaults(func=cmd_serve)

    p = subparsers.add_parser("doctor",
                              help="check the environment and generated skills")
    p.set_defaults(func=cmd_doctor)
    return parser


def speak_utf8():
    """Read and write UTF-8 on stdin, stdout and stderr, whatever the system
    code page says.

    On Windows a pipe is encoded in the ANSI code page — cp1252 on an English
    system, cp1251 on a Ukrainian one — and every consumer of this tool reads
    it through a pipe: the pre-turn hook, an agent's shell, an MCP client.
    They all expect UTF-8. On an English system the first Cyrillic fragment
    crashed the search; on a Ukrainian one the MCP server decoded a client's
    question as cp1251 and answered every Ukrainian question with "nothing in
    the base matched". A console is unaffected: it already speaks UTF-8.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass            # a detached or already-closed stream


def main(argv=None):
    speak_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfg = config_module.load(args.root)
    messages.use(cfg.interface)
    return args.func(args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
