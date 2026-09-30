"""Retrieval over the converted corpus.

Returns relevant fragments rather than whole documents: a single imported page
routinely costs 20k tokens, which no agent should spend to answer one question.

How ranking works:

* documents are split by heading, then into ~400-character fragments at
  sentence boundaries (converted exports often put a whole page on one line,
  so splitting by line is useless);
* every word is reduced to a stem (a fixed-length prefix, crude but effective
  for inflected languages);
* stems are weighted by IDF, so words that appear in every fragment — "client",
  "order", "page" — stop drowning out the ones that actually pick out a topic;
* a match in a heading counts double, and a short focused fragment beats a
  long diffuse one with the same hits.

The score is normalised against the query, so it reads as a confidence: real
topics land above 1.0, absent ones below 0.9.
"""
from __future__ import annotations

import json
import math
import os
import re

from . import config as config_module
from .messages import every, say

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
#: The links table the importer appends. It is generated, so it is not a
#: section of the document and does not belong in its outline.
RE_GENERATED_TAIL = re.compile("(?:%s)$" % "|".join(
    re.escape(title[3:]) for message in ("## Links found on this page",
                                          "## Links found in this document")
    for title in every(message)))

#: A fenced code block. Its contents are a sample, not prose: a shell comment
#: opens with the same character a markdown heading does, and a documentation
#: repository is full of them.
RE_FENCE = re.compile(r"^\s*(?:```|~~~)")

#: Bumped whenever what the index stores changes shape or meaning, so an old
#: cache is rebuilt instead of misread. 3: fragments name their true line.
CACHE_FORMAT = 3

RE_DOC_TITLE = re.compile(r"^#\s+(?:\[)?(.+?)(?:\]\(|$)")

#: Past this many documents the map names them and stops. The sections of
#: five hundred manuals are sixty thousand tokens — more than the answer they
#: were meant to help find, and more than most context windows hold.
MAP_DOCUMENT_LIMIT = 40

#: A reference manual can carry hundreds of headings. Enough of them to
#: recognise the document is orientation; all of them is the document.
MAP_SECTION_LIMIT = 30
RE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
RE_SENTENCE = re.compile(r"(?<=[.!?:])\s+|(?=[•▪✅❌⛔⚠])")


def stem(word, length):
    word = word.lower().strip("«»\"'.,:;!?()[]–—-")
    return word[:length] if len(word) > length else word


#: Share of words two fragments have in common before one is a copy of the
#: other. Overlapping neighbours share about a quarter.
NEAR_COPY = 0.8

#: Share of a passage's words that must reappear together elsewhere for the
#: passage to be there too. A page exported twice, by two converters, reaches
#: it for most of its passages; eight different laws, for 9 of 1,400 — the
#: provisions they repeat word for word.
SAME_PASSAGE = 0.9
#: Share of a document's passages another must hold to be a copy of it, and
#: how many passages are checked.
COPY_SHARE = 0.4
COPY_SAMPLE = 40
#: Fewest distinct words that make a passage worth recognising elsewhere —
#: about 180 characters; a full fragment has 40 to 50.
MIN_PASSAGE = 20


def alike(a, b):
    """Whether two fragments' words are nearly the same."""
    return bool(a and b) and len(a & b) >= NEAR_COPY * len(a | b)


def outside_fences(lines):
    """-> (line number, line) for every line that is not inside a code fence."""
    fenced = False
    for number, raw in enumerate(lines, 1):
        if RE_FENCE.match(raw):
            fenced = not fenced
            continue
        if not fenced:
            yield number, raw


def title_of(path):
    """The document's own title, or its file name if it declares none."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return os.path.basename(path)
    for _number, line in outside_fences(text.splitlines()):
        found = RE_DOC_TITLE.match(line)
        if found:
            return clean(found.group(1))
    return os.path.basename(path)


def clean(line):
    """Strip markdown noise so both matching and output stay readable."""
    line = RE_LINK.sub(r"\1", line)
    return re.sub(r"\*{1,3}|`", "", line)


def split_chunks(text, chunk_chars, overlap_chars=0):
    """A long paragraph -> sentence-aligned fragments of about chunk_chars.

    Fragments overlap by default. Without it a rule and its exception land on
    opposite sides of a boundary and neither half answers the question: the
    fragment naming a penalty no longer says what triggers it.

    The overlap is carried back as whole sentences — splitting mid-sentence
    would add noise to the index without adding an answer.
    """
    return [chunk for _, chunk in chunk_spans(text, chunk_chars, overlap_chars)]


def _sentence_spans(text):
    """(offset, sentence) for every sentence, as RE_SENTENCE splits them."""
    spans, start = [], 0
    for match in RE_SENTENCE.finditer(text):
        if match.start() > start:
            spans.append((start, text[start:match.start()]))
        start = max(start, match.end())
    if start < len(text):
        spans.append((start, text[start:]))
    return spans


def chunk_spans(text, chunk_chars, overlap_chars=0):
    """split_chunks, with the offset in ``text`` where each fragment begins.

    With overlap a fragment starts inside the one before it. Counting the
    lengths of the fragments instead put its start ever further down the
    page: in a long document half of them named a line below their own text,
    and whoever read from that line missed the start of the answer.
    """
    sentences = _sentence_spans(text)
    parts, current = [], []

    def length(items):
        return sum(len(piece) + 1 for _, piece in items)

    def emit(items):
        chunk = " ".join(piece for _, piece in items).strip()
        if chunk:
            parts.append((items[0][0], chunk))

    for span in sentences:
        if current and length(current) + len(span[1]) > chunk_chars:
            emit(current)
            carried, size = [], 0
            for previous in reversed(current):
                if overlap_chars <= 0 or size + len(previous[1]) > overlap_chars:
                    break
                carried.insert(0, previous)
                size += len(previous[1])
            current = carried
        current.append(span)

    if current:
        emit(current)
    return parts


def sections(path, chunk_chars, overlap_chars=0):
    """Document -> [(line number, heading, fragment)]."""
    lines = open(path, encoding="utf-8").read().splitlines()
    out, heading, buf, start = [], "", [], 1

    def flush():
        if not buf:
            if heading:                 # a heading with no body is still findable
                out.append((start, heading, heading))
            return
        offsets, position = [], start
        for line in buf:
            if line.strip():
                offsets.append((position, line))
            position += 1
        body = " ".join(line for _, line in offsets)
        starts, seen = [], 0                 # where each line begins in body
        for position, line in offsets:
            starts.append((seen, position))
            seen += len(line) + 1
        for offset, chunk in chunk_spans(body, chunk_chars, overlap_chars):
            line_no = start
            for begins, position in starts:
                if begins > offset:
                    break
                line_no = position
            out.append((line_no, heading, chunk))

    for number, raw in enumerate(lines, 1):
        match = RE_HEADING.match(raw)
        if match:
            flush()
            heading, buf, start = clean(match.group(2))[:120], [], number
        else:
            if not buf:
                start = number
            buf.append(raw)
    flush()
    return out


CACHE_NAME = ".index.json"


class Index:
    """Fragments plus IDF weights, rebuilt only when a file changes.

    The index is also cached on disk. Every CLI call is a fresh process, so
    without it each search would re-parse the whole corpus — which stays
    invisible at ten documents and dominates at a thousand.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._key = None
        self.chunks = []
        self.titles = {}
        self._bodies = (None, [])
        self.idf = {}
        self._copies = {}
        self.loaded_from_cache = False

    # -- corpus discovery -------------------------------------------------
    def files(self):
        """Documents plus image descriptions.

        Descriptions are first-class members of the base: they carry rules
        that exist only inside screenshots and nowhere in the text.
        """
        layout = self.cfg.layout
        out = []
        text_dir = layout.path("text")
        if os.path.isdir(text_dir):
            for name in sorted(os.listdir(text_dir)):
                if name.endswith(".md") and not name.startswith("_"):
                    out.append((name, os.path.join(text_dir, name)))
        assets_dir = layout.path("assets")
        if os.path.isdir(assets_dir):
            for topic in sorted(os.listdir(assets_dir)):
                path = os.path.join(assets_dir, topic, "descriptions.md")
                if os.path.isfile(path):
                    out.append((f"assets/{topic}/descriptions.md", path))
        return out

    def stems(self, text):
        length = self.cfg.search.stem_length
        stops = self.cfg.stopwords
        return {stem(w, length)
                for w in re.findall(r"[\w'’-]+", text, re.UNICODE)
                if w.lower() not in stops and len(w) > 1}

    # -- disk cache -------------------------------------------------------
    def cache_path(self):
        return os.path.join(os.path.dirname(self.cfg.layout.path("manifest")),
                            CACHE_NAME)

    def _load_cache(self, key):
        path = self.cache_path()
        if not os.path.isfile(path):
            return False
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return False                     # a damaged cache is just a miss
        if [list(entry) for entry in key] != data.get("key"):
            return False
        if data.get("settings") != self.settings_key():
            return False
        try:
            self.chunks = [(n, l, h, q, set(bs), set(hs))
                           for n, l, h, q, bs, hs in data["chunks"]]
            self.idf = data["idf"]
            self.titles = {n: set(t) for n, t in data["titles"].items()}
        except (KeyError, TypeError, ValueError):
            return False
        self._key = key
        self.loaded_from_cache = True
        return True

    def _save_cache(self, key):
        try:
            os.makedirs(os.path.dirname(self.cache_path()), exist_ok=True)
            payload = {"key": [list(entry) for entry in key],
                       "settings": self.settings_key(),
                       "chunks": [[n, l, h, q, sorted(bs), sorted(hs)]
                                  for n, l, h, q, bs, hs in self.chunks],
                       "idf": self.idf,
                       "titles": {n: sorted(t) for n, t in self.titles.items()}}
            temporary = self.cache_path() + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(temporary, self.cache_path())
        except OSError:
            pass                            # a read-only base still searches

    # -- indexing ---------------------------------------------------------
    def settings_key(self):
        """Chunking settings belong in the cache key.

        Change chunk_chars or chunk_overlap and the fragments change with them;
        without this the cache silently serves an index built under the old
        settings, and a config change appears to do nothing.
        """
        settings = self.cfg.search
        return [CACHE_FORMAT, settings.chunk_chars, settings.chunk_overlap,
                settings.stem_length, sorted(self.cfg.languages)]

    def build(self):
        files = self.files()
        key = tuple((name, os.path.getmtime(path)) for name, path in files)
        if self._key == key:
            return self
        self._copies = {}
        if self._load_cache(key):
            return self
        chunks, frequency, titles = [], {}, {}
        self._bodies = (None, [])
        for name, path in files:
            titles[name] = self.stems(title_of(path))
            settings = self.cfg.search
            overlap = int(settings.chunk_chars * settings.chunk_overlap)
            parts = sections(path, settings.chunk_chars, overlap)
            for seq, (line_no, heading, body) in enumerate(parts):
                body_stems = self.stems(body)
                head_stems = self.stems(heading)
                chunks.append((name, line_no, heading, seq, body_stems, head_stems))
                for token in body_stems | head_stems:
                    frequency[token] = frequency.get(token, 0) + 1
        total = max(len(chunks), 1)
        self.chunks = chunks
        self.titles = titles
        self.idf = {t: math.log(1 + total / c) for t, c in frequency.items()}
        self._key = key
        self.loaded_from_cache = False
        self._save_cache(key)
        return self

    def snippet(self, name, seq):
        """The text of one fragment, read back from the document.

        The index stores what a fragment *matches*, not what it says: keeping
        the prose as well made the cache twice the size of the base it was
        built from. Only the handful of fragments actually shown need their
        text, and re-splitting one document to get them costs milliseconds.
        """
        if self._bodies[0] != name:
            path = dict(self.files()).get(name)
            settings = self.cfg.search
            overlap = int(settings.chunk_chars * settings.chunk_overlap)
            parts = [body for _, _, body in
                     sections(path, settings.chunk_chars, overlap)] if path else []
            self._bodies = (name, parts)
        parts = self._bodies[1]
        return parts[seq] if 0 <= seq < len(parts) else ""


    # -- querying ---------------------------------------------------------
    def search(self, query, limit=None):
        self.build()
        limit = limit or self.cfg.search.default_hits
        wanted = self.stems(query)
        if not wanted or not self.chunks:
            return []

        # Confidence is measured against the best score this query could
        # reach. A word the documentation never uses is charged at the weight
        # of its rarest word: the question is asked in other words than the
        # answer, and the score says so. Left out, a question in a client's
        # words — "can I return a thing that just did not fit" — matched the
        # law's words it did share as confidently as the law's own phrasing,
        # and answered with the wrong article; charged, it is doubted and
        # asked again. On 32 questions about Ukrainian law the right article
        # went from 20 to 23 and the wrong law from 3 to 1; nothing changed on
        # 64 English ones.
        known = {w for w in wanted if w in self.idf}
        settings = self.cfg.search
        rarest = math.log(1 + len(self.chunks))
        budget = sum(self.idf.get(w, rarest) for w in wanted) or 1.0
        # A query whose words are mostly absent from the corpus is a miss,
        # however well the few remaining words happen to match.
        known_ratio = len(known) / len(wanted)

        hits = []
        for name, line_no, heading, seq, body_stems, head_stems in self.chunks:
            in_body = wanted & body_stems
            in_head = wanted & head_stems
            if not in_body and not in_head:
                continue
            gained = (sum(self.idf.get(w, 0) for w in in_body)
                      + settings.heading_weight * sum(self.idf.get(w, 0) for w in in_head))
            coverage = gained / budget
            density = len(in_body) / max(len(body_stems), 1)
            score = (coverage + settings.density_weight * density) * known_ratio
            hits.append((score, name, line_no, heading, seq, body_stems))
        hits.sort(key=lambda hit: (-hit[0], hit[1], hit[2]))

        # The same page exported twice, or a block a wiki page repeats, would
        # otherwise fill the answer with one fragment shown over and over.
        kept = []
        for hit in hits:
            if any(alike(hit[5], other[5]) for other in kept):
                continue
            kept.append(hit)
            if len(kept) == limit:
                break
        return [(score, name, line_no, heading, self.snippet(name, seq))
                for score, name, line_no, heading, seq, _ in kept]

    def _windows(self, name):
        """Each fragment of a document joined with the next one.

        Two exports of one page are cut into fragments at different places,
        so a passage of one lands across two fragments of the other.
        """
        parts = [chunk[4] for chunk in self.chunks if chunk[0] == name]
        return [a | b for a, b in zip(parts, parts[1:] + [set()])]

    def says(self, name, words):
        """Whether document ``name`` contains the passage these words make.

        Only a passage long enough to mean something: "In this Law the terms
        are used in the following meaning" opens every law, and finding it in
        another law says nothing about which one answers the question.
        """
        return len(words) >= MIN_PASSAGE and any(
            len(words & window) >= SAME_PASSAGE * len(words)
            for window in self._windows(name))

    def copies(self, top, name):
        """Whether document ``name`` says what the best match ``top`` says:
        it holds the same passage, or it is a copy of the whole document —
        the same page dropped as both PDF and Word. Either way it gives the
        same answer, and two copies are not two answers to choose between."""
        if self.says(name, self.stems(top[4])):
            return True
        pair = (top[1], name)
        if pair not in self._copies:
            own = [chunk[4] for chunk in self.chunks
                   if chunk[0] == top[1] and len(chunk[4]) >= MIN_PASSAGE]
            sample = own[::max(1, len(own) // COPY_SAMPLE)][:COPY_SAMPLE]
            shared = sum(1 for words in sample if self.says(name, words))
            self._copies[pair] = bool(sample) and shared >= COPY_SHARE * len(sample)
        return self._copies[pair]

    # -- judging an answer -------------------------------------------------
    def doubts(self, query, hits):
        """-> why the best answer may be the wrong one; empty when it holds up.

        The score alone let most wrong answers through looking confident, and
        a confident wrong answer is the one that never gets checked. Measured
        on 64 questions worded the way people ask rather than the way a manual
        does, and run through the whole loop — search, and ask again in the
        documentation's words when in doubt — the answers that ended up wrong
        went from 21 to 2, and the right ones from 43 to 57. Three signals:

        * the best match sits in a document whose title shares no word with
          the question, while other titles do — "how do I check automatically
          that my code works" landing in "Unsafe Rust" while "Writing
          Automated Tests" exists. Only when some title does: in a small base
          of broad documents no title names most questions, and a perfect
          match under "Expense reports" is no reason for doubt. The other
          title is not named as the answer; it is often there by accident;
        * the match is weak and nothing near it agrees — or very weak, agreeing
          or not. On eight Ukrainian laws, five fragments of one long law
          "agreed" on answers scoring 0.3; doubting those took the right
          article from 16 to 20 of 32 through the loop, and changed nothing on
          the English set;
        * another document is almost as likely. Wrong answers were nearly all
          of this kind: "install" found "Installing Binaries with cargo
          install", one place above "Installation".
        """
        if not hits:
            return [say("nothing in the base matched the question")]
        settings = self.cfg.search
        wanted = self.stems(query)
        top = hits[0]
        reasons = []
        if not wanted & self.titles.get(top[1], set()) and any(
                wanted & title for name, title in self.titles.items()
                if name != top[1]):
            reasons.append(say("the best match is in {name}, whose title shares "
                               "no word with the question, though other titles do",
                               name=top[1]))
        agree = sum(1 for hit in hits[:5] if hit[1] == top[1])
        if top[0] < settings.always_doubt_below:
            reasons.append(say("the best match covers little of the question "
                               "(score {score})", score=f"{top[0]:.2f}"))
        elif top[0] < settings.low_confidence and agree < 2:
            reasons.append(say("the best match covers little of the question "
                               "(score {score}) and nothing near it agrees",
                               score=f"{top[0]:.2f}"))
        # A document that says what the best match says is a copy of it — the
        # same page dropped as both PDF and Word — and gives the same answer.
        # Two copies are not two answers to choose between.
        second = next((hit for hit in hits if hit[1] != top[1]
                       and not self.copies(top, hit[1])), None)
        if second and top[0] < settings.close_race * second[0]:
            reasons.append(say("{name} is almost as likely ({second} against "
                               "{top}) — the question does not tell the two apart",
                               name=second[1], second=f"{second[0]:.2f}",
                               top=f"{top[0]:.2f}"))
        return reasons

    def unknown_words(self, query):
        """Words of the question the documentation never uses.

        The most useful thing to know before asking again: these are exactly
        the words to replace, because nothing in the base can match them.
        """
        self.build()
        length, stops, seen, out = (self.cfg.search.stem_length,
                                    self.cfg.stopwords, set(), [])
        for word in re.findall(r"[\w'’-]+", query, re.UNICODE):
            if word.lower() in stops or len(word) < 2:
                continue
            token = stem(word, length)
            if token not in self.idf and token not in seen:
                seen.add(token)
                out.append(word)
        return out

    def closest_documents(self, hits, count=3):
        """Distinct documents in the order the search ranked them."""
        out = []
        for hit in hits:
            if hit[1] not in out:
                out.append(hit[1])
            if len(out) == count:
                break
        return out

    def headings(self, name):
        """The document's own sections.

        Down to the fourth level, because a wiki reserves the first two for
        the page title and its lead: a real page's every section was an h4,
        and cutting at the third hid the whole document. Generated matter at
        the foot of the file is not a section of it.
        """
        path = dict(self.files()).get(name)
        if not path:
            return []
        out = []
        with open(path, encoding="utf-8") as handle:
            for number, raw in outside_fences(handle):
                match = RE_HEADING.match(raw)
                if not match or len(match.group(1)) > 4:
                    continue
                heading = clean(match.group(2)).strip()
                if RE_GENERATED_TAIL.match(heading):
                    break
                if heading and len(heading) < 90:
                    out.append((number, heading))
        return out


def outline(index, wanted="", cap=0):
    """What the base holds -> lines, disclosed progressively.

    With a handful of documents the sections are the useful part. With five
    hundred they are noise, so the map names the documents and waits to be
    asked about one of them.
    """
    files = index.files()
    if not files:
        return [say("The base is empty.")]

    needle = wanted.strip().lower()
    if needle:
        files = [(name, path) for name, path in files
                 if needle in name.lower() or needle in title_of(path).lower()]
        if not files:
            return [say("No document matches {wanted}. Ask for the map without "
                        "a name to see them all.", wanted=wanted)]

    detailed = bool(needle) or len(files) <= MAP_DOCUMENT_LIMIT
    shown = files[:cap] if cap and len(files) > cap else files

    out = []
    for name, path in shown:
        title = title_of(path)
        if not detailed:
            out.append(f"{name} — {title}")
            continue
        out.append(f"\n## {name} — {title}")
        headings = index.headings(name)
        for line_no, heading in headings[:MAP_SECTION_LIMIT]:
            out.append(f"  {line_no:>5}  {heading}")
        if len(headings) > MAP_SECTION_LIMIT:
            out.append(say("        … {count} more sections",
                           count=len(headings) - MAP_SECTION_LIMIT))

    if len(shown) < len(files):
        out.append(say("… and {count} more [[count:document|documents]]",
                       count=len(files) - len(shown)))
    if not detailed:
        out.append(say("\n{count} [[count:document|documents]]. Name one to see "
                       "its sections.", count=len(files)))
    return out


def shown_fragments(settings, hits):
    """The fragments exactly as `find` prints them, cut the way it cuts them.

    One function for the command and for the self-test: a test that read the
    fragments whole would pass on answers the agent is never shown.
    """
    lines, budget = [], settings.total_chars
    for score, name, line_no, heading, body in hits:
        text = "\n".join(l for l in body.splitlines() if l.strip())
        text = text[:min(settings.per_hit_chars, budget)]
        lines += ["", f"=== {name}:{line_no}  [{score:.2f}]  "
                      f"{heading or say('(no heading)')}", text]
        budget -= len(text)
        if budget <= 0:
            lines += ["", say("(truncated — narrow the query)")]
            break
    return lines


#: How many sections of each close document to show when asking again. Enough
#: to see what the documentation calls the topic; not the whole outline.
DOUBT_HEADINGS = 6


def low_confidence_notice(index, query, hits):
    """-> lines telling the reader why to doubt the answer and how to ask
    again, or [] when the answer holds up.

    Written for whoever acts on it, person or agent: the reasons, the words
    that cannot match anything, and the vocabulary the documentation uses for
    the nearby topics — which is what a second question should be built from.
    """
    reasons = index.doubts(query, hits)
    if not reasons:
        return []
    lines = [say("LOW CONFIDENCE — the best match may be the wrong one:")]
    lines += [f"  - {reason}" for reason in reasons]

    unknown = index.unknown_words(query)
    if unknown:
        lines += ["", say("Words from the question the documentation never uses: "
                          "{words}", words=", ".join(unknown)),
                  say("  Replace these first — nothing in the base can match them.")]

    closest = index.closest_documents(hits)
    if closest:
        lines += ["", say("What the documentation calls the nearby topics:")]
        for name in closest:
            lines.append(f"  {name}")
            for line_no, heading in index.headings(name)[:DOUBT_HEADINGS]:
                lines.append(f"     {line_no:>5}  {heading}")

    lines += ["",
              say("Ask again in the documentation's words, taken from the "
                  "headings above. If two searches agree on a section, read it "
                  "whole. If they do not, the base may not cover this — say so, "
                  "or ask which of the nearby topics was meant.")]
    return lines

