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

Cases live in ``kb/tests/``, written by hand or by an agent. The plain
format is a file named after its document, ``kb/tests/<document>.md``::

    # comments start with a hash
    ? how early do I book a trip       <- a working question, as it is asked
    = five working days                <- a literal piece of the right answer
    ! five working days                <- a fact that must be in the document

The same in JSON, ``kb/tests/<name>.json``, when a case needs more::

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

A question whose answer is neither shown nor in a card is *missing*; if the
search was sure of itself while missing it, it is *dangerous* — nothing will
make the agent look again. A doubted miss is asked again by the skill.

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
    # The importer writes a picture as "(alt)" — a wiki's emoji are pictures —
    # so the passage has to say the same, or a whole message looks lost.
    for image in soup.find_all("img"):
        alt = (image.get("alt") or "").strip()
        image.replace_with(f" ({alt}) " if alt else " ")
    # Buttons are the page's controls — a macro's "Copy" and "Plain" —
    # not its text, and the importer rightly leaves them out.
    for control in soup.find_all(["button", "script", "style"]):
        control.decompose()
    found = []
    boxes = [node for node in soup.select('[class*="copy"]')
             if not node.select('[class*="copy"]')]      # the innermost only
    for node in soup.find_all(["em", "i"]) + boxes:
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


#: A macro the export did not expand leaves its template variable behind.
RE_UNEXPANDED = re.compile(r"\$body\b")
def notes_on(text):
    """What looks wrong with a converted document, whatever its original."""
    notes = []
    stubs = len(RE_UNEXPANDED.findall(text))
    if stubs:
        notes.append(say("{count} unexpanded [[count:macro|macros]] ($body) — the "
                         "export itself lost them", count=stubs))
    return notes


def check_integrity(cfg):
    """-> {document: (original, passages checked, [passages missing])}.

    One original per converted document. When a page came as both PDF and
    HTML the base reads the HTML, so that is the one to hold it against —
    checking the PDF would pass while the text the agent reads lost things.
    """
    from .sync import read_manifest
    layout = cfg.layout
    by_document = {}
    for name, entry in read_manifest(layout.path("manifest")).items():
        if entry.get("out"):
            by_document.setdefault(entry["out"], []).append(name)
    results = {}
    for document, names in sorted(by_document.items()):
        converted = os.path.join(layout.path("text"), document)
        if not os.path.isfile(converted):
            continue
        names.sort(key=lambda n: (not n.lower().endswith(HTML_ORIGINALS), n))
        source = next((n for n in names
                       if os.path.isfile(os.path.join(layout.path("originals"), n))), "")
        try:
            found = passages(os.path.join(layout.path("originals"), source)) \
                if source else []
        except Exception:                                  # noqa: BLE001
            found = []          # an original the importer could read is enough
        text = open(converted, encoding="utf-8").read()
        results[document] = (source, len(found), missing_passages(found, text))
    return results


HTML_ORIGINALS = (".html", ".htm", ".doc", ".mhtml", ".mht")


# ------------------------------------------------------------ cases
def _parse_plain(path):
    """kb/tests/<document>.md -> (anchors, questions)."""
    anchors, questions, question = [], [], None
    for raw in open(path, encoding="utf-8").read().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tag, _, rest = line.partition(" ")
        rest = rest.strip()
        if tag == "?" and rest:
            question = rest
        elif tag == "=" and rest and question:
            questions.append({"question": question, "answer": rest})
        elif tag == "!" and rest:
            anchors.append({"fact": rest})
    return anchors, questions


def load_cases(cfg):
    """-> [{file, document, anchors, questions}] from kb/tests/."""
    folder = os.path.join(cfg.layout.root, TESTS)
    out = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name.endswith(".md"):
            anchors, questions = _parse_plain(path)
            out.append({"file": name, "document": name, "anchors": anchors,
                        "questions": questions})
            continue
        if not name.endswith(".json"):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            out.append({"file": name, "document": "", "anchors": [],
                        "questions": [{"broken": str(error)[:80]}]})
            continue
        if isinstance(data, list):
            data = {"questions": data}
        out.append({"file": name, "document": data.get("document", ""),
                    "anchors": data.get("anchors", []),
                    "questions": data.get("questions", [])})
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
    """-> ([(file, document, fact, why)] for every anchor not there, total)."""
    texts = {name: flat(text) for name, text in _texts(cfg).items()}
    everything = "".join(texts.values())
    missing, total = [], 0
    for case in cases:
        for anchor in case["anchors"]:
            if not isinstance(anchor, dict):
                anchor = {"fact": str(anchor)}
            fact = anchor.get("fact", "")
            where = anchor.get("document") or case["document"]
            haystack = texts.get(where, "") if where else everything
            if not flat(fact):
                continue
            total += 1
            if flat(fact) not in haystack:
                missing.append((case["file"], where or "*", fact, anchor.get("why", "")))
    return missing, total


def check_questions(cfg, cases):
    """-> [(file, question, answers, outcome, doubted)]. The outcome is one of
    search, card, missing (doubted: the skill asks again), dangerous (the
    search was sure and wrong), honest, confident (a question the base cannot
    answer, doubted or not) and broken (a test file that does not parse)."""
    from .search import Index, shown_fragments
    index = Index(cfg).build()
    cards = _cards(cfg)
    results = []
    for case in cases:
        for item in case["questions"]:
            if "broken" in item:
                results.append((case["file"], item["broken"], [], "broken", False))
                continue
            question = item.get("question", "")
            hits = index.search(question)
            doubted = bool(index.doubts(question, hits))
            if item.get("absent"):
                results.append((case["file"], question, [],
                                "honest" if doubted else "confident", doubted))
                continue
            answers = _answers(item)
            shown = flat("\n".join(shown_fragments(cfg.search, hits)))
            wanted = [flat(a) for a in answers if flat(a)]
            if wanted and all(w in shown for w in wanted):
                outcome = "search"
            elif wanted and all(w in cards for w in wanted):
                outcome = "card"
            else:
                outcome = "missing" if doubted else "dangerous"
            results.append((case["file"], question, answers, outcome, doubted))
    return results


# ------------------------------------------------------------ report
STATE = "kb/.selftest.json"
BAD = ("missing", "dangerous", "confident", "broken")


def _load_state(cfg):
    try:
        with open(os.path.join(cfg.layout.root, STATE), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_state(cfg, state):
    try:
        with open(os.path.join(cfg.layout.root, STATE), "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError:
        pass


def report(cfg=None, verbose=False, quick=False):
    cfg = cfg or config_module.load()
    problems = 0
    documents = sorted(_texts(cfg))
    cases = load_cases(cfg)
    previous = _load_state(cfg)
    state = {"questions": {}, "integrity": {}}
    print(say("Documents: {count}", count=len(documents)))

    integrity = {} if quick else check_integrity(cfg)
    missing_anchors, total_anchors = check_anchors(cfg, cases)
    anchors_of, questions_of = {}, {}
    for case in cases:
        anchors_of[case["document"]] = anchors_of.get(case["document"], 0) + len(case["anchors"])
        questions_of[case["document"]] = (questions_of.get(case["document"], 0)
                                          + len(case["questions"]))

    print()
    print(f"{say('document'):<44}{say('templates'):>12}{say('anchors'):>10}"
          f"{say('questions'):>9}")
    print("-" * 75)
    texts = _texts(cfg)
    for document in documents:
        checked, lost = 0, []
        if document in integrity:
            _source, checked, lost = integrity[document]
            state["integrity"][document] = len(lost)
        templates = "—" if not checked else (
            say("{count} ok", count=checked) if not lost else
            say("LOST {lost}/{count}", lost=len(lost), count=checked))
        mine = [m for m in missing_anchors if m[1] == document]
        count = anchors_of.get(document, 0)
        anchors = "—" if not count else (
            say("{count} ok", count=count) if not mine else
            say("MISSING {lost}/{count}", lost=len(mine), count=count))
        print(f"{document[:42]:<44}{templates:>12}{anchors:>10}"
              f"{questions_of.get(document, 0) or '—':>9}")
        for note in notes_on(texts[document]):
            print(f"   ! {note}")
            problems += 1
        for passage in lost[:5 if not verbose else None]:
            print(say("   lost: «{text}»", text=passage[:100]))
        problems += len(lost)
        for _file, _where, fact, why in mine:
            print(say("   anchor missing: «{fact}»{why}", fact=fact[:70],
                      why=f" — {why}" if why else ""))
            problems += 1
    others = [m for m in missing_anchors if m[1] not in documents]
    for file, where, fact, why in others:
        print(say("   anchor missing: «{fact}»{why}", fact=fact[:70],
                  why=f" — {why}" if why else "") + f"   [{file} → {where}]")
        problems += 1

    untested = [d for d in documents if not anchors_of.get(d) and not questions_of.get(d)]
    if untested and cases:
        print(say("\nWithout a single test ({count}): {names}", count=len(untested),
                  names=", ".join(d[:-3] for d in untested)))
    if not cases:
        print(say("\nNo cases in {folder} yet. Add some: see docbase/selftest.py "
                  "for the format.", folder=TESTS))

    results = check_questions(cfg, cases) if cases else []
    if results:
        counted = {}
        for result in results:
            counted[result[3]] = counted.get(result[3], 0) + 1
        answerable = sum(counted.get(k, 0)
                         for k in ("search", "card", "missing", "dangerous"))
        print("\n" + "-" * 75)
        print(say("Questions: {total} — search finds {search}, cards cover {card}, "
                  "missing {missing}, dangerous {dangerous}", total=answerable,
                  search=counted.get("search", 0), card=counted.get("card", 0),
                  missing=counted.get("missing", 0),
                  dangerous=counted.get("dangerous", 0)))
        absent = counted.get("honest", 0) + counted.get("confident", 0)
        if absent:
            print(say("Not in the base: {total} — doubted honestly {honest}, answered "
                      "anyway {confident}", total=absent,
                      honest=counted.get("honest", 0),
                      confident=counted.get("confident", 0)))
        labels = {"search": say("search"), "card": say("card"),
                  "missing": say("missing"), "dangerous": say("DANGEROUS"),
                  "honest": say("doubted"), "confident": say("ANSWERED"),
                  "broken": say("BROKEN FILE")}
        for file, question, answers, outcome, doubted in results:
            state["questions"][question] = outcome
            bad = outcome in BAD
            problems += bad
            if bad or verbose:
                print(f"  {labels[outcome]:<11} {question[:70]}   [{file}]")
                if bad and answers:
                    print(say("              looked for «{answer}»",
                              answer="», «".join(a[:50] for a in answers)))

    changes = []
    for question, outcome in state["questions"].items():
        was = previous.get("questions", {}).get(question)
        if was and was != outcome:
            changes.append(say("  {question}: was {was}, now {now}",
                               question=question[:60], was=say(was), now=say(outcome)))
    for document, lost in state["integrity"].items():
        was = previous.get("integrity", {}).get(document)
        if was is not None and was != lost:
            changes.append(say("  {document}: lost templates {was} → {now}",
                               document=document, was=was, now=lost))
    if changes:
        print("\n" + say("Since the last run:"))
        for line in changes:
            print(line)
    if quick:
        state["integrity"] = previous.get("integrity", {})
    _save_state(cfg, state)

    print("\n" + "-" * 75)
    print(say("All clear.") if not problems
          else say("Problems: {count}", count=problems))
    return 1 if problems else 0
