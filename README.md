# docbase

Turn exported documentation into a knowledge base an AI agent can actually use.

You export a page from your wiki, drop the file in a folder, and ask questions
in plain language. The agent answers from your documentation — quoting it,
not guessing — and says so when the answer is not there.

---

## The problem it solves

Handing a PDF export to an assistant mostly does not work, for three reasons
that are easy to miss:

**Converters throw away the links.** A wiki PDF keeps every hyperlink as a PDF
link annotation. Generic converters drop them, so "as described *here*" becomes
a dead end. docbase reads the annotations back and restores the links — and
where the target is another imported document, rewrites it to a local path.

**Converters scramble multi-column pages.** Two columns get interleaved
sentence by sentence into unreadable soup. docbase rebuilds reading order with
a recursive XY-cut over word coordinates.

**Whole documents do not fit.** One page is routinely 20k tokens; a real corpus
is millions. docbase retrieves fragments, not files — around 1k tokens per
question, with the full document as a fallback nobody normally pays for.

## Install

```bash
pip install -e .
```

Python 3.9 or newer. No services, no API keys, no data leaving the machine.

## Use

```bash
docbase init --internal-host wiki.example.com   # once, in your base folder
# drop an exported page into that folder
docbase sync
docbase find "how late can I submit a claim"
```

Accepted exports: `.pdf`, `.html`, `.htm`, `.doc` (Word export, MHTML inside),
`.mhtml`. PDF works everywhere; HTML and Word are better for table-heavy pages,
because a row stays a row and macro tabs that PDF flattens survive.

### Commands

| | |
|---|---|
| `docbase init` | create a config in this folder |
| `docbase sync` | import new files, refresh what changed |
| `docbase find` | search, return ranked fragments |
| `docbase map` | documents and their sections |
| `docbase status` | what is stale, changed or missing |
| `docbase eval` | measure retrieval quality |
| `docbase doctor` | check the environment |

## How it is laid out

```
kb/originals/   the files you dropped in
kb/text/        converted markdown          (generated)
kb/assets/      extracted images + descriptions
kb/cards/       task-sized extracts         (written by the agent)
kb/history/     previous versions and diffs (generated)
kb/graph.md     what exists, what is referenced but missing
```

`kb/` is your content and is git-ignored by default.

## Keeping it current

Re-import a changed page and docbase does not silently rebuild everything.
It stores the previous version, writes a unified diff to
`kb/history/<document>/`, and marks any derived extract stale. The agent then
updates the affected parts of the extract from the diff instead of re-reading
the whole document.

```bash
docbase status
```

shows documents and their dates, extracts awaiting rebuild, recent changes,
files that could not be read, and pages referenced but never imported.

## Retrieval

Fragments are scored by IDF-weighted coverage of the query, so words that occur
in every document stop drowning out the ones that pick out a topic. Headings
count double; a short focused fragment beats a long diffuse one.

The score reads as a confidence. Below the threshold, `find` says so and prints
a section map instead of pretending — which is what lets an agent take a second,
better-worded look rather than answering from the wrong fragment.

Measure it on your own corpus:

```bash
docbase eval --generate   # build cases from the corpus itself
docbase eval
```

Cases are `{question, marker, file}` in `kb/eval.json`. Generated cases are a
floor: they prove a passage is retrievable given good keywords. Rewrite the
questions into natural phrasing — by hand or with an agent — and the same
runner scores those.

## Images

Screenshots often carry rules that appear nowhere in the text — a marked-up
"wrong vs right" example, a form with its required fields. docbase extracts
them, filters out icons and repeated decoration by size and content hash, and
leaves a marker in place.

Images are not read automatically: one costs about 1500 tokens. An agent opens
one only when the text nearby does not answer the question, then writes what it
shows into `kb/assets/<doc>/descriptions.md`. Descriptions are indexed, so the
next question finds it as text and nobody pays for the image twice.

> Extracted screenshots can contain personal data. `kb/` is git-ignored for
> that reason; think before copying it anywhere.

## Agent integration

The tool is a CLI, so anything that can run a command can use it. Shipped with
Claude Code integration:

- `.claude/skills/docs-search` — answer from the base
- `.claude/skills/docs-import` — add documents, refresh stale extracts
- `.claude/skills/docs-tailor` — read the corpus and generate skills and
  extracts specific to it

`docs-tailor` is the one that makes this general. The toolkit ships knowing
nothing about your domain; the documentation decides what the useful helpers
are. A support handbook wants "what to ask the customer", a runbook wants
"pre-flight checks", a compliance manual wants "what must be recorded".

`.claude/settings.json` runs `docbase sync --quiet` before each turn, which
costs about 0.1 s and prints nothing when nothing changed.

## Tests

```bash
python -m unittest discover tests
```

Twenty regression tests, each one a failure that actually happened: a document
silently overwritten by another with a similar title, one broken file aborting
the whole import, an asset folder deleted along with hand-written notes, a
half-written manifest from two concurrent runs.

## Limits

- **Retrieval is lexical.** Vocabulary mismatches ("wake me up" vs "alarm
  service") need a second search. The low-confidence path exists for this.
- **Stemming is a prefix.** Crude, works well for inflected languages, and
  occasionally conflates unrelated words.
- **The PDF importer is tuned for wiki exports.** Other layouts may need
  different `MIN_GUTTER` / `MIN_ROW_GAP` thresholds.
- **Hidden macro tabs are absent from PDF.** Only the active tab renders; use
  an HTML or Word export if that content matters.

## License

MIT
