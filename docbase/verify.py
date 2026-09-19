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

RE_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
RE_SOURCE = re.compile(r"source:\s*\S*?([\w.-]+\.md)")
RE_NUMBER = re.compile(r"(?<![\w.])(\d[\d\s]*(?:[.,]\d+)?)\s*(%|[A-Za-z]{2,12})?")
RE_QUOTED = re.compile(r"`([^`\n]{4,80})`|«([^»\n]{4,80})»|\"([^\"\n]{4,80})\"")

#: Numbers that are structure rather than content.
SKIP_NUMBER_CONTEXT = re.compile(r"^\s*(\d+)[.)]\s")


def _normalise(text):
    """Compare on words and digits only: markdown emphasis and the odd
    non-breaking space should not make a match fail."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`~|]", "", text)).strip().lower()


def _numbers(text):
    """Numbers a card asserts, with the word that follows them for context."""
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


def check_card(card_path, source_text):
    """-> (confirmed, [(kind, claim, line)]) for one card."""
    raw = open(card_path, encoding="utf-8").read()
    body = _card_body(raw)
    haystack = _normalise(source_text)
    haystack_digits = re.sub(r"[^\d]", "", source_text)

    confirmed, problems = 0, []

    for value, unit, line in _numbers(body):
        compact = value.replace(" ", "")
        variants = {compact, compact.replace(",", "."), compact.replace(".", ",")}
        if any(v.lower() in haystack for v in variants) or compact in haystack_digits:
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
            confirmed, problems = check_card(path, source_text)
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
