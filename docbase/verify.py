"""Check that extracts still say what their source says.

The whole point of the base is that an agent answers from the documentation
rather than from memory. Cards undermine that quietly: they are written once,
by a model, from fragments — and then they sit there while the document moves
underneath them, or they carry a number that was never in the source at all.

Nothing else catches this. Staleness only notices that a file changed; it
cannot tell whether the card was ever right.

Two kinds of claim are checked, chosen because they are both verifiable and
the expensive ones to get wrong:

* **numbers** — limits, deadlines, penalties, thresholds. A paraphrase that
  drifts is survivable; a wrong number is what people act on.
* **quoted text** — anything in backticks or quotation marks claims to be the
  document's own words, so it either appears verbatim or it does not.

Prose is deliberately not checked. A card is supposed to summarise, and
flagging every reworded sentence would bury the findings that matter.
"""
from __future__ import annotations

import os
import re

from . import config as config_module
from . import frontmatter
from . import languages

RE_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
RE_SOURCE = re.compile(r"source:\s*\S*?([\w.-]+\.md)")
RE_NUMBER = re.compile(r"(?<![\w.])(\d[\d\s]*(?:[.,]\d+)?)\s*(%|[A-Za-z]{2,12})?")
RE_QUOTED = re.compile(r"`([^`\n]{4,80})`|«([^»\n]{4,80})»|\"([^\"\n]{4,80})\"")

#: Every number written in the source.
RE_ANY_NUMBER = re.compile(
    r"\d{1,3}(?:[ .,]\d{3})*(?:[.,]\d+)?|\d+")

#: Numbers that are structure rather than content.
SKIP_NUMBER_CONTEXT = re.compile(r"^\s*(\d+)[.)]\s")


def _normalise(text):
    """Compare on words and digits only: markdown emphasis and the odd
    non-breaking space should not make a match fail."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`~|]", "", text)).strip().lower()


def _forms(value):
    """The ways one quantity gets written: 1 000, 1,000, 1000, 1.000."""
    bare = re.sub(r"\s", "", value.strip()).rstrip(".,")
    return {bare, bare.replace(",", ""), bare.replace(",", "."),
            bare.replace(".", ","), bare.replace(".", "")}


def _numbers(text):
    """Numbers a card asserts, each with the line that asserts it."""
    found = []
    for line in text.splitlines():
        if SKIP_NUMBER_CONTEXT.match(line):
            line = SKIP_NUMBER_CONTEXT.sub("", line)
        for match in RE_NUMBER.finditer(line):
            value = match.group(1).strip()
            if not value or len(value.replace(" ", "")) > 12:
                continue
            unit = (match.group(2) or "").strip()
            found.append((value, unit, line.strip()))
    return found


#: Words as the document writes them, for reading numbers out of prose.
RE_WORD = re.compile(r"[^\W\d_]+", re.U)

#: How much of the sentence around a number counts as its context.
CONTEXT_CHARS = 90


def number_contexts(text, spelled=None):
    """-> {number: the passages that number appears in}.

    A number alone proves nothing: a page listing four hundred proposals
    contains "96" in a page id whether or not it ever says ninety-six hours.
    What confirms a card is the number appearing *where the card says it
    does*, so each occurrence keeps the words around it.
    """
    out = {}

    def record(value, at):
        window = _normalise(text[max(at - CONTEXT_CHARS, 0):at + CONTEXT_CHARS])
        for form in _forms(value):
            out.setdefault(form, []).append(window)

    structural = set()
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = SKIP_NUMBER_CONTEXT.match(line)
        if marker:
            structural.add(offset + marker.start(1))
        offset += len(line)

    for match in RE_ANY_NUMBER.finditer(text):
        if match.start() in structural:
            continue        # a list marker is structure, as it is in a card
        record(match.group(), match.start())

    # A document may write the number out; a card written from it will not.
    if spelled:
        for match in RE_WORD.finditer(text):
            digits = spelled.get(match.group().lower())
            if digits:
                record(digits, match.start())
    return out


def _confirms(value, unit, line, contexts, stopwords):
    """Does the source carry this number where the card claims it?

    What the number counts is the strongest evidence there is: "72 hours"
    and "72 days" are the same digits and a different rule. Failing that —
    a number the card leaves bare — any distinctive word from the card's own
    line will do to place it.
    """
    windows = [w for form in _forms(value) for w in contexts.get(form, ())]
    if not windows:
        return False

    counted = unit.lower().rstrip("s")
    if len(counted) > 2 and counted not in stopwords:
        return any(counted in window for window in windows)

    keywords = {word for word in RE_WORD.findall(line.lower())
                if len(word) > 2 and word not in stopwords}
    if not keywords:
        return True                 # nothing to place it by; presence is all
    return any(any(word in window for word in keywords) for window in windows)


def _quotes(text):
    out = []
    for match in RE_QUOTED.finditer(text):
        quote = next(g for g in match.groups() if g)
        if len(quote.split()) >= 2:          # single words are rarely a claim
            out.append(quote)
    return out


def _card_body(text):
    match = RE_FRONTMATTER.match(text)
    return text[match.end():] if match else text


def check_card(card_path, source_text, spelled=None, stopwords=frozenset()):
    """-> (confirmed, [(kind, claim, line)]) for one card."""
    raw = open(card_path, encoding="utf-8").read()
    body = _card_body(raw)
    haystack = _normalise(source_text)
    contexts = number_contexts(source_text, spelled)

    confirmed, problems = 0, []

    for value, unit, line in _numbers(body):
        if _confirms(value, unit, line, contexts, stopwords):
            confirmed += 1
        else:
            shown = f"{value} {unit}".strip()
            problems.append(("number", shown, line[:70]))

    for quote in _quotes(body):
        if _normalise(quote) in haystack:
            confirmed += 1
        else:
            problems.append(("quote", quote[:60], ""))

    return confirmed, problems


def report(cfg=None, verbose=False):
    cfg = cfg or config_module.load()
    cards_dir = cfg.layout.path("cards")
    text_dir = cfg.layout.path("text")

    if not os.path.isdir(cards_dir) or not os.listdir(cards_dir):
        print("No cards to verify. They are written by an agent as it works;")
        print("see the docs-tailor skill.")
        return 0

    by_id = {}
    if os.path.isdir(text_dir):
        for name in sorted(os.listdir(text_dir)):
            if name.endswith(".md"):
                doc_id = frontmatter.read_id_from(os.path.join(text_dir, name))
                if doc_id:
                    by_id[doc_id] = name

    spelled = languages.numbers(cfg.languages)
    total_cards = total_confirmed = 0
    findings, orphans, stale, cleared = [], [], [], []
    passed_by_topic = {}

    for topic in sorted(os.listdir(cards_dir)):
        folder = os.path.join(cards_dir, topic)
        if not os.path.isdir(folder):
            continue
        if os.path.isfile(os.path.join(folder, "_stale")):
            stale.append(topic)

        for card in sorted(os.listdir(folder)):
            if not card.endswith(".md"):
                continue
            path = os.path.join(folder, card)
            raw = open(path, encoding="utf-8").read()
            head = RE_FRONTMATTER.match(raw)
            meta = head.group(1) if head else ""

            source_name = None
            declared_id = frontmatter.read_id(meta)
            if declared_id and declared_id in by_id:
                source_name = by_id[declared_id]
            else:
                named = RE_SOURCE.search(meta)
                if named and os.path.isfile(os.path.join(text_dir, named.group(1))):
                    source_name = named.group(1)

            if not source_name:
                orphans.append(f"{topic}/{card}")
                continue

            source_text = open(os.path.join(text_dir, source_name),
                               encoding="utf-8").read()
            confirmed, problems = check_card(
                path, source_text, spelled, cfg.stopwords)
            total_cards += 1
            total_confirmed += confirmed
            if problems:
                findings.append((f"{topic}/{card}", source_name, problems))
                passed_by_topic[topic] = False
            elif confirmed:
                # Only a card that actually asserted something counts as
                # checked; one with no numbers or quotations proves nothing.
                passed_by_topic.setdefault(topic, True)

    # A stale marker means the source moved. If every claim still holds
    # against the new source, the card is consistent with it by definition,
    # and leaving the marker up teaches people to ignore the signal.
    for topic in list(stale):
        if passed_by_topic.get(topic):
            try:
                os.remove(os.path.join(cards_dir, topic, "_stale"))
                stale.remove(topic)
                cleared.append(topic)
            except OSError:
                pass

    print(f"Cards checked: {total_cards}   claims confirmed: {total_confirmed}")

    if cleared:
        print("\nStale markers cleared (claims still hold against the new source):")
        for topic in cleared:
            print(f"  {topic}")

    if orphans:
        print("\nCards whose source is missing:")
        for name in orphans:
            print(f"  ? {name}")
        print("  The document was removed or never imported; the card cannot")
        print("  be trusted until it is restored.")

    if stale:
        print("\nCards marked stale (their source changed since):")
        for topic in stale:
            print(f"  ! {topic}")

    if findings:
        print("\nClaims not found in the source:")
        for card, source, problems in findings:
            print(f"\n  {card}  (against {source})")
            for kind, claim, line in problems[:8] if not verbose else problems:
                detail = f"   <- {line}" if line else ""
                print(f"    {kind:<7} {claim}{detail}")
            if not verbose and len(problems) > 8:
                print(f"    … and {len(problems) - 8} more (-v to see all)")
        print("\nA number or quotation that is not in the document is either a")
        print("transcription error or drift. Fix the card against its source.")

    if not (findings or orphans):
        print("\nEvery number and quotation is present in its source.")
    return 1 if (findings or orphans) else 0
