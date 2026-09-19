<div align="center">

# docbase

**Turn exported documentation into a knowledge base an AI agent can actually use.**

Export a page from your wiki. Drop the file in a folder. Ask questions in plain
language — and get answers quoted from your documentation, with an honest
"not in the base" when it isn't there.

[![tests](https://github.com/ArtemkaGoldMan/docbase/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtemkaGoldMan/docbase/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.12-blue.svg)](pyproject.toml)
[![platforms](https://img.shields.io/badge/tested%20on-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg)](.github/workflows/ci.yml)

Local-only · no services · no API keys · nothing leaves the machine

</div>

---

```console
$ docbase sync
Base: 2 documents
  - expenses.html -> kb/originals/expenses.html
  - travel.html -> kb/originals/travel.html
  - added kb/text/expense-reports.md
  - added kb/text/travel-booking.md
  - linked 2 cross-document references

$ docbase find "photographs of receipts"

=== expense-reports.md:9  [1.07]  Expense reports
Every reimbursement request goes through the finance portal. Receipts must be
attached as **PDF**; photographs of receipts are rejected.
```

And when the answer genuinely isn't there, it says so instead of improvising:

```console
$ docbase find "parental leave entitlement"
LOW CONFIDENCE: nothing matched at all.

## expense-reports.md — Expense reports
      8  Expense reports
     10  Deadlines
     18  Currency
```

## Why this exists

Handing a PDF export to an assistant mostly does not work, for three reasons
that are easy to miss until you look at the converted text.

<table>
<tr><td width="33%" valign="top">

### Links vanish

A wiki PDF stores every hyperlink as a PDF link annotation. Generic converters
drop them, so *"as described **here**"* becomes a dead end.

**docbase** reads the annotations back, and rewrites links to already-imported
pages into local paths.

</td><td width="33%" valign="top">

### Columns scramble

Two-column pages get interleaved sentence by sentence into unreadable soup —
silently, so nobody notices until an answer is wrong.

**docbase** rebuilds reading order with a recursive XY-cut over word
coordinates.

</td><td width="33%" valign="top">

### Documents don't fit

One page is routinely 20k tokens. A real corpus is millions. "Just give the
agent the docs" is not a plan.

**docbase** retrieves fragments — about 1k tokens per question, with the full
document as a fallback nobody normally pays for.

</td></tr>
</table>

## Quick start

```bash
pip install -e .

docbase init --internal-host wiki.example.com   # once, in your base folder
#            ^ drop an exported page into that folder
docbase sync
docbase find "how late can I submit a claim"
```

Accepted exports: `.pdf`, `.html`, `.htm`, `.doc` (Word export — MHTML inside),
`.mhtml`.

PDF works everywhere. HTML and Word are better for table-heavy pages: a row
stays a row, and macro tabs that PDF flattens away survive.

## Commands

| Command | What it does |
|---|---|
| `docbase init` | create a config in this folder |
| `docbase sync` | import new files, refresh what changed |
| `docbase find` | search, return ranked fragments |
| `docbase map` | documents and their sections |
| `docbase status` | what is stale, changed or missing |
| `docbase eval` | measure retrieval quality on your own corpus |
| `docbase doctor` | check the environment |

## For AI agents

The tool is a CLI, so anything that can run a command can use it. Three
[Claude Code](https://claude.com/claude-code) skills ship in `.claude/skills/`:

| Skill | Triggers on | Does |
|---|---|---|
| **`docs-search`** | "what does the documentation say about…" | answers from the base, quotes the source, refuses to invent |
| **`docs-import`** | adding a document, "what's out of date?" | imports, reports staleness, updates extracts from diffs |
| **`docs-tailor`** | "set this up for my documentation" | reads the corpus and **generates skills specific to it** |

`docs-tailor` is what keeps the toolkit general. It ships knowing nothing about
your domain; your documentation decides what the useful helpers are. A support
handbook wants *"what to ask the customer"*. A runbook wants *"pre-flight
checks"*. A compliance manual wants *"what must be recorded"*. The skills a
domain needs are generated from the domain, not guessed in advance.

`.claude/settings.json` runs `docbase sync --quiet` before each turn: ~0.1 s,
and silent when nothing changed.

## How it works

```
  exported file                  you drop it anywhere in the base folder
        │
        ▼
  kb/originals/                  deduplicated by content hash, not by name
        │
        ├── .pdf   ─────────►  link annotations · XY-cut · font-size headings
        └── .html/.doc ─────►  tables stay tables · hidden macro tabs survive
        │
        ▼
  kb/text/*.md                   frontmatter carries the page id
        │
        ├──► cross-document links rewritten to local paths
        ├──► images extracted, icons and repeated decoration filtered out
        └──► changed? previous version + unified diff into kb/history/
        │
        ▼
  search                         fragments, IDF-weighted, cached on disk
```

## Keeping it current

Re-import a changed page and docbase does not quietly rebuild everything.
It keeps the previous version, writes a unified diff to `kb/history/<doc>/`,
and marks derived extracts stale.

That matters: "this card is stale" alone forces an agent to re-read the whole
document. A diff tells it *which sections moved*, so the update is surgical.

```console
$ docbase sync
Base: 2 documents, 1 unchanged
  - travel-booking.md changed: +2/-2 lines (diff in kb/history/travel-booking)
  - cards for 'travel' marked stale

$ docbase status
Documents: 2
  expense-reports.md      1 KB   updated 2026-09-19
  travel-booking.md       1 KB   updated 2026-09-19

Cards waiting to be rebuilt (their source changed):
  - travel
```

## Retrieval

Fragments are scored by IDF-weighted coverage of the query, so words that occur
in every document stop drowning out the ones that pick out a topic. Headings
count double; a short focused fragment beats a long diffuse one.

The score reads as a confidence, measured against the best that query could
score — so the threshold means the same thing on a corpus of five documents and
one of five hundred. Below it, `find` says so and prints a section map instead
of pretending, which is what lets an agent take a second, better-worded look
rather than answering from the wrong fragment.

Measure it on your own corpus — not on someone else's benchmark:

```bash
docbase eval --generate   # build cases from the corpus itself
docbase eval
```

```console
Cases: 40
  top-1              40/40 (100%)
  top-3              40/40 (100%)
  in returned 8      40/40 (100%)   <- what the agent actually sees
  right document #1  40/40 (100%)   <- guards against confident wrong answers
  search time        5 ms average
  context returned   ~794 tokens per query
```

Cases live in `kb/eval.json` as `{question, marker, file}`. Generated ones are a
floor: they prove a passage is retrievable *given good keywords*. Rewrite the
questions into natural phrasing — by hand, or have an agent do it — and the same
runner scores those.

## Images

Screenshots often carry rules that appear nowhere in the text: a marked-up
wrong-vs-right example, a form with its required fields. docbase extracts them,
filters out icons and repeated decoration by size and content hash, and leaves a
marker where they belonged.

They are not read automatically — one image costs about 1500 tokens. An agent
opens one only when the surrounding text does not answer the question, then
writes what it shows into `kb/assets/<doc>/descriptions.md`. Descriptions are
indexed, so the next question finds it as text and nobody pays for the image
twice.

> [!WARNING]
> Extracted screenshots can contain personal data. `kb/` is git-ignored for
> exactly this reason. Think before copying it anywhere.

## Layout

```
kb/originals/   the files you dropped in
kb/text/        converted markdown              (generated)
kb/assets/      extracted images + descriptions
kb/cards/       task-sized extracts             (written by the agent)
kb/history/     previous versions and diffs     (generated)
kb/graph.md     what exists, what is referenced but missing
```

`kb/` is your content, and is git-ignored by default.

## Tests

```bash
python -m unittest discover tests
```

Twenty-eight regression tests. Every one of them is a failure that actually
happened, most of them silent: a document overwritten by another with a similar
title, one broken file aborting the whole import, an asset folder deleted along
with hand-written notes, a half-written manifest from two concurrent runs.

Five guard behaviour that only breaks at scale — an idle run must not checksum
every original, must not rewrite unchanged documents, and must serve the index
from cache. Each was invisible at ten documents and crippling at two hundred.
Three more guard the evaluation itself, which once produced zero cases on
long-sentence documents and reported success.

### Measured on 200 documents · 1.2 MB of markdown · 3600 fragments

| | |
|---|---|
| first import | 1.1 s |
| idle run (the pre-turn hook) | 0.14 s |
| index build, cold | 165 ms |
| index load, cached | 14 ms |
| search | 5 ms |
| memory | 43 MB |

The index is cached on disk because every CLI call is a new process; without it,
each search re-parsed the entire corpus.

## Limits

Stated plainly, because knowing where a tool stops is part of using it.

- **Retrieval is lexical.** A vocabulary mismatch — *"wake me up"* against
  *"alarm service"* — needs a second search. The low-confidence path exists
  precisely for this.
- **Stemming is a fixed-length prefix.** Crude; works well for inflected
  languages, and occasionally conflates unrelated words.
- **The PDF importer is tuned for wiki exports.** Other layouts may need
  different `MIN_GUTTER` / `MIN_ROW_GAP` thresholds.
- **Hidden macro tabs are absent from PDF.** Only the active tab renders. Use an
  HTML or Word export when that content matters.
- **Search is a linear scan.** 5 ms at 200 documents, ~50 ms at 2000. Beyond
  that it wants an inverted index.

## License

MIT
