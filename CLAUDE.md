# docbase

A local documentation base: exported wiki pages converted to markdown, searched
by fragment rather than read whole. This file is the contract for working with
it; the CLI does the mechanical part.

## Rules that always apply

**Never read `kb/text/*.md` whole** — a single document is 20k+ tokens. Search:

```bash
python -m docbase find "keywords"
```

**Never invent documentation.** If the base does not cover something, say so
and name the document that would need importing. For anything a person acts on,
a confident wrong answer is worse than an honest gap.

**Never edit `kb/text/`** — it is regenerated from the originals.

**Numbers come from the source.** Limits, deadlines and thresholds are quoted
verbatim, never reconstructed from memory.

**One hop between documents.** Following a chain of references burns context
faster than it earns anything.

## Commands

| | |
|---|---|
| `python -m docbase sync` | import and refresh |
| `python -m docbase find "..."` | ranked fragments with `file:line` |
| `python -m docbase map [name]` | documents, and one document's sections |
| `python -m docbase status` | stale, changed, missing |
| `python -m docbase eval` | retrieval quality |
| `python -m docbase verify` | extracts still match their source |
| `python -m docbase doctor` | environment check |
| `python -m docbase serve` | MCP server on stdio, for other agents |

## When `find` says LOW CONFIDENCE

The query's words did not match the documentation's words — normal, not a dead
end. Read the section map it prints, search again using the wording the
documentation would use, then read the section directly with
`sed -n '<line>,+40p'`. Only after that conclude it is missing.

## Cards and history

`kb/cards/<topic>/` holds extracts distilled from a source; read those before
searching. After writing or editing one, run `python -m docbase verify` — it
catches a number or quotation that is not in the document. A `_stale` marker means the source changed — the diff in
`kb/history/<document>/` shows exactly what, so update the card surgically
rather than rebuilding it blind, then delete the marker.

## Images

`![image from page N](kb/assets/...)` marks a screenshot whose content is not
in the text. Do not open by default (~1500 tokens). Having opened one, record
what it shows in `kb/assets/<doc>/descriptions.md` — descriptions are indexed,
so it is paid for once.

`kb/assets/` may contain personal data from screenshots. Do not copy it into
answers or out of the base.

## Layout

```
kb/originals/  dropped files        kb/cards/    agent-written extracts
kb/text/       converted markdown   kb/history/  previous versions + diffs
kb/assets/     images + descriptions
```

Skills: `docs-search`, `docs-import`, `docs-tailor`.
