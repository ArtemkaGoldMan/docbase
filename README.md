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

=== expense-reports.md:8  [1.07]  Expense reports
Every reimbursement request goes through the finance portal. Receipts must be
attached as **PDF**; photographs of receipts are rejected.
```

And when the answer isn't there, it says so instead of improvising — and says
which words to change before concluding it:

```console
$ docbase find "parental leave entitlement"
LOW CONFIDENCE — nothing in the base matched the question.
Words the documentation never uses: parental, leave, entitlement

## expense-reports.md — Expense reports
      6  Expense reports
     11  Deadlines
     16  Currency
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
documents into local paths — matching by page id, by declared URL, by file
name, or by document number, so it works outside wikis too.

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

| Format | Notes |
|---|---|
| `.md` `.markdown` `.txt` `.rst` | already text; underlined headings and a declared title are picked up |
| `.html` `.htm` | best for table-heavy pages: a row stays a row |
| `.docx` | Word; headings from styles, nested and numbered lists, tables, links |

In HTML and Word, a numbered part at the start of a short paragraph —
*Стаття 8.*, *Article 5*, *Chapter IV*, *§ 3* — is read as a heading even when
only a stylesheet made it look like one. Laws, contracts and internal policies
are published that way, and without it a whole regulation is one section.
| `.doc` `.mhtml` | wiki "Export to Word" (MHTML inside); keeps macro tabs and the pictures inside it |
| `.pdf` | works everywhere; links, reading order and figures are recovered |
| `.zip` | a space export — pages and their attachments, imported in one step |

PDF is universal but lossy by nature. HTML and Word keep tables and macro tabs
that PDF flattens away. Markdown passes through untouched.

## Commands

| Command | What it does |
|---|---|
| `docbase init` | create a config in this folder |
| `docbase sync` | import new files, refresh what changed |
| `docbase find` | search, return ranked fragments |
| `docbase map [name]` | documents, and one document's sections |
| `docbase status` | what is stale, changed or missing |
| `docbase eval` | measure retrieval quality on your own corpus |
| `docbase verify` | check extracts still match their source |
| `docbase serve` | expose the base to any MCP-capable agent |
| `docbase doctor` | check the environment and the generated skills |

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

`.claude/settings.json` runs `docbase sync --quiet` before each turn: about
40 ms when nothing changed, and silent.

### Any other agent

`docbase serve` speaks MCP over stdio, so search becomes a native tool call
rather than something the model has to shell out for and parse.

```json
{
  "mcpServers": {
    "docbase": { "command": "docbase", "args": ["serve"], "cwd": "/path/to/your/base" }
  }
}
```

Five tools: `search_documentation`, `list_documents`, `read_section`,
`base_status`, `sync_base`. The working directory decides which base is served,
so several bases mean several entries.

Implemented against the protocol directly rather than through an SDK — MCP over
stdio is JSON-RPC on stdin and stdout, and a dependency here would undo the
thing that makes the rest of this easy to install.

## How it works

```
  exported file or .zip          you drop it anywhere in the base folder
        │
        ▼
  kb/originals/                  archives unpacked, duplicates found by content
        │
        ├── .pdf   ─────────►  link annotations · XY-cut · font-size headings
        ├── .html/.doc ─────►  tables stay tables · hidden macro tabs survive
        ├── .docx  ─────────►  styles · tables · relationship-resolved links
        └── .md/.txt/.rst ──►  underlined headings · declared title
        │
        ▼
  kb/text/*.md                   frontmatter carries the page id
        │
        ├──► cross-document links resolved and rewritten to local paths
        ├──► images extracted, icons and repeated decoration filtered out
        └──► changed? previous version + unified diff into kb/history/
        │
        ▼
  search                         fragments, IDF-weighted, cached on disk
```

## Cross-document links

Documents cite each other, and a citation that goes nowhere is a dead end for
whoever follows it. Four strategies decide what a link points at, in order of
how much they prove:

| | |
|---|---|
| page id | a wiki URL carrying the target's identifier, absolute or relative |
| declared URL | the document said where it came from |
| file name | the URL ends in the file that was imported |
| document number | URL and document share `800-207`, `RFC 2119`, `POL-042` |

The last is loose, so it is guarded: an identifier matching more than one
document resolves to none of them, and a letter suffix is a different document
rather than a revision — `800-63A` is not `800-63-3`. A missing link costs a
lookup; a wrong one sends the reader to the wrong document.

What stays unresolved on a configured internal host is reported as missing, so
`kb/graph.md` doubles as the list of what to import next:

```
| identifier | Referred to as |
| `800-30`   | http://csrc.nist.gov/publications/nistpubs/800-30/sp800-30.pdf |
| `800-53`   | http://csrc.nist.gov/publications/nistpubs/800-53-Rev2/... |
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

## Verifying extracts

Cards are written by a model, from fragments, and then sit there while the
document moves underneath them. Staleness only notices that a file changed; it
cannot tell whether the card was ever right.

```console
$ docbase verify
Cards checked: 1   claims confirmed: 4

Claims not found in the source:

  travel/ask.md  (against travel-booking.md)
    number  250 EUR   <- - Cancellation fee is 250 EUR
    quote   regional finance controller
```

Two kinds of claim are checked, both verifiable and both expensive to get
wrong: **numbers** — limits, deadlines, penalties — and **quoted text**, which
claims to be the document's own words. Prose is deliberately left alone; a card
is supposed to summarise, and flagging every reworded sentence would bury the
findings that matter.

Exit code 1 when something is unverified, so it works as a gate in CI.

It also closes the loop on staleness: when a source changes, its extracts are
marked stale, and `verify` clears that mark if every claim still holds against
the new text. A marker nobody can clear is a signal people learn to ignore.

## Retrieval

Documents are split by heading, then into overlapping fragments at sentence
boundaries. The overlap matters more than it looks: without it a rule and its
exception land on opposite sides of a boundary, and the fragment naming a
penalty no longer says what triggers it.

Fragments are scored by IDF-weighted coverage of the query, so words that occur
in every document stop drowning out the ones that pick out a topic. Headings
count double; a short focused fragment beats a long diffuse one.

### When to doubt an answer

A lexical search misses when the question and the documentation use different
words — someone asks how to make a program "crash on purpose", the manual says
"panic". That cannot be fixed by ranking; it is fixed by asking again in the
documentation's words. What the search owes the agent is knowing *when* to.

`find` marks an answer LOW CONFIDENCE for three reasons, and says which:

- the best match sits in a document whose title shares no word with the
  question, while other titles do;
- the match is weak and nothing near it agrees;
- another document is almost as likely — "install" landing on *Installing
  Binaries with cargo install*, one place above *Installation*.

With the doubt comes what a second search needs: the words of the question the
documentation never uses, and what it calls the nearby topics. The
`docs-search` skill spells out the rest — ask again in those words at most
twice, read a section two searches agree on, and otherwise ask the person which
topic they meant, or say the base does not cover it.

### How well it works

Measured on 64 questions worded the way people ask, against two public manuals
— the Rust book and the Python library documentation. Reproduce it with
[`benchmarks/human-worded/`](benchmarks/human-worded/).

| | right | wrong | stopped to ask |
|---|---|---|---|
| first search alone | 29 of 64 | — | — |
| with the ask-again loop, doubting on score alone (before) | 43 | 21 | 0 |
| with the ask-again loop, as it is now | **57** | **2** | 5 |

The first search alone is right less than half the time. The loop is what
makes it work, and it only works because the doubt fires on the answers that
are wrong: in every case where it stopped to ask, the right document was among
the options it named.

The same in Ukrainian — eight laws on consumer financial services, 32 questions
in plain Ukrainian, scored by the **article** that answers them
([`benchmarks/ukrainian-laws/`](benchmarks/ukrainian-laws/)):

| | right article | right law, another article | wrong law | stopped to ask |
|---|---|---|---|---|
| first search alone | 8 of 32 | 12 | 12 | — |
| with the ask-again loop | **20** | 7 | **3** | 2 |

Legal Ukrainian is further from how people talk than a programming manual is
from how programmers do, and it shows: the first search finds the right
article one time in four. Through the loop it is nearly two in three, and the
answers from the wrong law drop from twelve to three.

### `docbase eval`

```bash
docbase eval --generate   # build cases from the corpus itself
docbase eval
```

Generated cases take their questions from the documents' own words, so they
score close to 100% on almost any base. That makes them a regression check —
a passage that was findable is still findable after you change something — and
not a measure of how well the search understands people. For that, write
questions the way your users ask them into `kb/eval.json` as
`{question, marker, file}`; the same runner scores those.

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

## Languages

Two things depend on the language of your corpus, and both are data rather than
code: **stopwords**, which keep question scaffolding out of the ranking, and
**transliteration**, which turns a document title into a file name.

Built in: `de` `en` `es` `fr` `it` `nl` `pl` `pt` `uk`

Accented Latin needs no table of its own — text is decomposed before mapping,
so Czech, Hungarian, Romanian and Turkish already work. Ukrainian Cyrillic and Greek have
an explicit table. Scripts without one (CJK, Arabic, Hebrew) keep a digest of the
title as the file name, so documents stay distinct rather than colliding.

```json
{ "language": ["uk", "en"] }
```

Several at once, because mixed corpora are the norm: Ukrainian policies quote
English product names, German handbooks quote English job titles.

Adding one takes no code — drop `kb/languages/<code>.json` into your own base:

```json
{ "stopwords": ["og", "eller", "men", "hvis"], "translit": { "å": "aa", "ø": "oe" } }
```

Full guide: [docs/LANGUAGES.md](docs/LANGUAGES.md).

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

Over two hundred regression tests. Every one of them is a failure that actually
happened, most of them silent: a document overwritten by another with a similar
title, one broken file aborting the whole import, an asset folder deleted along
with hand-written notes, a half-written manifest from two concurrent runs.

Five guard behaviour that only breaks at scale — an idle run must not checksum
every original, must not rewrite unchanged documents, and must serve the index
from cache. Each was invisible at ten documents and crippling at two hundred.
Three more guard the evaluation itself, which once produced zero cases on
long-sentence documents and reported success.

### Measured on two public manuals

The same bases the search benchmark uses, so these can be reproduced.

| | Rust book | Python library docs |
|---|---|---|
| documents · text · fragments | 112 · 1.2 MB · 3 900 | 538 · 14.7 MB · 44 300 |
| first import | 0.4 s | 5.1 s |
| idle run (the pre-turn hook) | 37 ms | 44 ms |
| index build, cold | 0.24 s | 3.2 s |
| index load, cached | 16 ms | 0.40 s |
| search, average · worst | 7 ms · 11 ms | 60 ms · 263 ms |
| one `docbase find`, whole process | 60 ms | 0.41 s |
| memory | 39 MB | 265 MB |

A base of a few hundred wiki pages sits near the left column. The right column
is where the design starts to show its limits; see below.

## Limits

Stated plainly, because knowing where a tool stops is part of using it.

- **Retrieval is lexical.** A vocabulary mismatch — *"crash on purpose"*
  against *"panic"* — needs a second search. On questions worded the way people
  ask, the first search is right less than half the time; the ask-again loop
  brings that to 57 of 64. An agent that answers from the first result without
  reading the doubt will be wrong often.
- **Stemming is a fixed-length prefix** — six letters for English, five for
  every other language. Crude, and it occasionally conflates unrelated words:
  *гарантія* (a warranty) and *гарантування* (a deposit guarantee) are the
  same word to it. English and Ukrainian were measured; for Ukrainian no cut
  between four and seven letters did better than another, and the other
  languages keep five without having been checked.
- **Legal language is hard for it.** On Ukrainian law the first search finds
  the right article one time in four. The ask-again loop brings that to 20 of
  32; an agent that skips it will mostly answer from the wrong article.
- **Column detection is tuned for wiki exports.** Other page layouts may need
  different `MIN_GUTTER` / `MIN_ROW_GAP` thresholds. Heading detection is not:
  it measures against each document's own body text.
- **Hidden macro tabs are absent from PDF.** Only the active tab renders. Use an
  HTML or Word export when that content matters.
- **It does not scale past a few hundred documents gracefully.** Each command
  is a new process that loads the whole index: at 538 documents that is 0.4 s
  and 265 MB for a single search. It wants an inverted index and a leaner cache
  format before it holds thousands.

## License

MIT
