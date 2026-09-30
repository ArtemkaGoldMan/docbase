"""Does the base still hold, and still find, what the work depends on.

Three layers, each catching a failure the others cannot see:

* integrity — every passage the original sets apart (italic, a copy-text
  macro: in a wiki these are the ready-made messages) is in the converted
  text. Losing them is silent: the page still converts and the search still
  answers; only the templates are gone.
* anchors — facts whose error costs money or points, written down in
  ``kb/tests/``, are still in their documents word for word.
* questions — a working question finds its answer in exactly what `find`
  shows, cut the way it cuts, or a card holds it. One that does neither is
  dangerous: the agent will answer it from something else.

Cases live in ``kb/tests/*.json``, written by hand or by an agent::

    {
      "document": "travel-booking.md",
      "anchors": [{"fact": "five working days", "why": "lead time"}],
      "questions": [
        {"question": "how early do I book a trip", "answer": "five working days"},
        {"question": "who pays for a lost bag", "absent": true}
      ]
    }

``answer`` may be a list; every part must be there. ``absent`` marks a
question the base cannot answer: the right result is a doubt, not an answer.

    docbase selftest            all three layers
    docbase selftest -v         and every case
    docbase selftest --quick    without re-reading the originals
"""
from __future__ import annotations

import json
import os
import re

from . import config as config_module
from .messages import say

TESTS = "kb/tests"

#: Shortest passage worth checking. A two-word italic is emphasis, not a
#: message.
MIN_PASSAGE_WORDS = 4
#: A passage is checked in pieces this long, so that a page break or a column
#: the converter reordered costs one piece, not the whole passage.
PIECE_WORDS = 6

RE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def flat(text):
    """Text reduced to its letters and digits, for comparing what was said
    regardless of emphasis, links, line breaks and apostrophes."""
    text = RE_LINK.sub(r"\1", text or "")
    return re.sub(r"[\W_]+", "", text.lower().replace("’", "").replace("'", ""))


# ------------------------------------------------------------ integrity
def _html_passages(path):
    from bs4 import BeautifulSoup
    from .importers import html
    page, _attached = html.load_html(path)
    soup = BeautifulSoup(page, "html.parser")
    for selector in html.JUNK_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    found = []
    for node in soup.find_all(["em", "i"]) + soup.select('[class*="copy"]'):
        found.append(node.get_text(" ", strip=True))
    return found


def _pdf_passages(path):
    import pdfplumber
    from .importers import pdf
    found = []
    with pdfplumber.open(path) as document:
        for page in document.pages:
            run = []
            for word in page.extract_words(extra_attrs=["fontname"],
                                           x_tolerance=pdf.WORD_GAP):
                if word["top"] < pdf.HEADER_ZONE or \
                        word["bottom"] > page.height - pdf.FOOTER_ZONE:
                    continue
                if any(f in word["fontname"] for f in pdf.ICON_FONTS):
                    continue
                text = pdf.RE_PUA.sub("", word["text"]).strip()
                if not text:
                    continue
                if "Italic" in word["fontname"] or "Oblique" in word["fontname"]:
                    run.append(text)
                else:
                    if run:
                        found.append(" ".join(run))
                    run = []
            if run:
                found.append(" ".join(run))
    return found


def passages(path):
    """What an original sets apart, as plain text."""
    extension = os.path.splitext(path)[1].lower()
    if extension in (".html", ".htm", ".doc", ".mhtml", ".mht"):
        found = _html_passages(path)
    elif extension == ".pdf":
        found = _pdf_passages(path)
    else:
        return []           # the text is its own original
    seen, out = set(), []
    for text in found:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text.split()) >= MIN_PASSAGE_WORDS and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def missing_passages(passages_found, converted):
    """The passages the converted text does not hold."""
    target = flat(converted)
    out = []
    for passage in passages_found:
        words = passage.split()
        pieces = [" ".join(words[i:i + PIECE_WORDS])
                  for i in range(0, len(words), PIECE_WORDS)]
        lost = sum(1 for piece in pieces if flat(piece) not in target)
        if lost * 2 > len(pieces):
            out.append(passage)
    return out


def check_integrity(cfg):
    """-> [(original, converted, passages checked, [passages missing])]."""
    from .sync import read_manifest
    layout = cfg.layout
    manifest = read_manifest(layout.path("manifest"))
    results = []
    for name, entry in sorted(manifest.items()):
        out = entry.get("out")
        source = os.path.join(layout.path("originals"), name)
        converted = os.path.join(layout.path("text"), out or "")
        if not out or not os.path.isfile(source) or not os.path.isfile(converted):
            continue
        try:
            found = passages(source)
        except Exception:                                  # noqa: BLE001
            continue        # an original the importer could read is enough
        if not found:
            continue
        text = open(converted, encoding="utf-8").read()
        results.append((name, out, len(found), missing_passages(found, text)))
    return results


# ------------------------------------------------------------ cases
def load_cases(cfg):
    """-> [(file, document, anchors, questions)] from kb/tests/."""
    folder = os.path.join(cfg.layout.root, TESTS)
    out = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            out.append((name, "", [], [{"broken": str(error)[:80]}]))
            continue
        if isinstance(data, list):
            data = {"questions": data}
        out.append((name, data.get("document", ""), data.get("anchors", []),
                    data.get("questions", [])))
    return out


def _texts(cfg):
    folder = cfg.layout.path("text")
    if not os.path.isdir(folder):
        return {}
    return {name: open(os.path.join(folder, name), encoding="utf-8").read()
            for name in os.listdir(folder) if name.endswith(".md")}


def _cards(cfg):
    folder = cfg.layout.path("cards")
    parts = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if name.endswith(".md"):
                parts.append(open(os.path.join(root, name), encoding="utf-8").read())
    return flat("\n".join(parts))


def _answers(case):
    answer = case.get("answer") or case.get("marker") or []
    return [answer] if isinstance(answer, str) else list(answer)


def check_anchors(cfg, cases):
    """-> [(file, fact, why, where)] for every anchor that is not there."""
    texts = {name: flat(text) for name, text in _texts(cfg).items()}
    everything = "".join(texts.values())
    missing = []
    for file, document, anchors, _questions in cases:
        for anchor in anchors:
            fact = anchor.get("fact", "") if isinstance(anchor, dict) else str(anchor)
            where = (anchor.get("document") if isinstance(anchor, dict) else "") or document
            haystack = texts.get(where, "") if where else everything
            if flat(fact) and flat(fact) not in haystack:
                why = anchor.get("why", "") if isinstance(anchor, dict) else ""
                missing.append((file, fact, why, where or "*"))
    return missing, sum(len(c[2]) for c in cases)


def check_questions(cfg, cases):
    """-> list of (file, question, outcome, doubted) where outcome is one of
    "search", "card", "dangerous", "honest", "confident", "broken"."""
    from .search import Index, shown_fragments
    index = Index(cfg).build()
    cards = _cards(cfg)
    results = []
    for file, _document, _anchors, questions in cases:
        for case in questions:
            if "broken" in case:
                results.append((file, case["broken"], "broken", False))
                continue
            question = case.get("question", "")
            hits = index.search(question)
            doubted = bool(index.doubts(question, hits))
            if case.get("absent"):
                results.append((file, question,
                                "honest" if doubted else "confident", doubted))
                continue
            shown = flat("\n".join(shown_fragments(cfg.search, hits)))
            wanted = [flat(a) for a in _answers(case) if flat(a)]
            if wanted and all(w in shown for w in wanted):
                outcome = "search"
            elif wanted and all(w in cards for w in wanted):
                outcome = "card"
            else:
                outcome = "dangerous"
            results.append((file, question, outcome, doubted))
    return results


# ------------------------------------------------------------ report
def report(cfg=None, verbose=False, quick=False):
    cfg = cfg or config_module.load()
    problems = 0

    if not quick:
        integrity = check_integrity(cfg)
        checked = sum(r[2] for r in integrity)
        lost = sum(len(r[3]) for r in integrity)
        print(say("Integrity: {documents} [[documents:document|documents]], "
                  "{checked} set-apart [[checked:passage|passages]], {lost} missing",
                  documents=len(integrity), checked=checked, lost=lost))
        for name, out, count, missing in integrity:
            if missing:
                problems += len(missing)
                print(say("  ! {name} -> {out}: {lost} of {count} missing",
                          name=name, out=out, lost=len(missing), count=count))
                for passage in missing[:5 if not verbose else None]:
                    print(f"      «{passage[:110]}»")

    cases = load_cases(cfg)
    if not cases:
        print(say("No cases in {folder} yet. Add some: see docbase/selftest.py "
                  "for the format.", folder=TESTS))
        return 1 if problems else 0

    missing, total = check_anchors(cfg, cases)
    print(say("Anchors: {ok} of {total} in place", ok=total - len(missing), total=total))
    for file, fact, why, where in missing:
        problems += 1
        print(say("  ! {fact} — not in {where} ({file}){why}", fact=f"«{fact}»",
                  where=where, file=file, why=f": {why}" if why else ""))

    results = check_questions(cfg, cases)
    counted = {}
    for _file, _question, outcome, _doubted in results:
        counted[outcome] = counted.get(outcome, 0) + 1
    answerable = sum(counted.get(k, 0) for k in ("search", "card", "dangerous"))
    print(say("Questions: {total} — search finds {search}, cards cover {card}, "
              "dangerous {dangerous}", total=answerable,
              search=counted.get("search", 0), card=counted.get("card", 0),
              dangerous=counted.get("dangerous", 0)))
    absent = counted.get("honest", 0) + counted.get("confident", 0)
    if absent:
        print(say("Not in the base: {total} — doubted honestly {honest}, answered "
                  "anyway {confident}", total=absent, honest=counted.get("honest", 0),
                  confident=counted.get("confident", 0)))
    labels = {"search": say("search"), "card": say("card"),
              "dangerous": say("DANGEROUS"), "honest": say("doubted"),
              "confident": say("ANSWERED"), "broken": say("BROKEN FILE")}
    for file, question, outcome, doubted in results:
        bad = outcome in ("dangerous", "confident", "broken")
        problems += bad
        if bad or verbose:
            mark = say(" (with doubt)") if doubted and outcome == "search" else ""
            print(f"  {labels[outcome]:<11} {question[:80]}{mark}   [{file}]")
    return 1 if problems else 0
