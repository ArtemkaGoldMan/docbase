---
name: docs-import
description: Add documentation to the local base or refresh it after the source changed — importing an exported wiki page, PDF, Word or HTML file, checking what is out of date, and updating the extracts built from it. Use when the user adds or replaces a document, asks what is stale or missing, or says the documentation has been updated.
---

# Import and refresh

## Adding a document

The user drops an exported file anywhere in the base folder. Then:

```bash
python -m docbase sync
```

That moves it into `kb/originals/`, converts it, restores its links, stitches
cross-document references, and marks affected extracts stale. Accepted:
`.pdf`, `.html`, `.htm`, `.docx`, `.doc`, `.mhtml`, `.md`, `.txt`, `.rst`, and
`.zip` archives, which are unpacked in place.

The same file dropped twice is recognised by content, even under a different
name. If a page exists as both PDF and HTML, the HTML wins: it keeps tables as
tables and preserves macro tabs that PDF flattens away.

## Which export format to suggest

PDF is fine and universal. HTML or Word are better for pages built on tables,
because a row stays a row and retrieval lands on the right cell. Word exports
are MHTML inside and import the same way.

## After a source changes

`sync` does not silently rebuild the extracts — it records what changed:

```bash
python -m docbase status
```

Shows documents and their dates, cards awaiting rebuild, recent changes, files
that could not be read, and pages referenced but never imported.

For a card marked `_stale`:

1. Read the diff in `kb/history/<document>/<timestamp>.diff`. It shows exactly
   which lines moved, so you do not have to re-read the document.
2. Update only the affected parts of the card.
3. Delete the `_stale` marker.

Rebuilding a card from scratch is the fallback, not the default — the diff is
there so the update is surgical.

## When something fails

A damaged or misnamed file is reported once and skipped; the rest of the base
still imports. Tell the user which file it was and ask for a fresh export.

Environment problems:

```bash
python -m docbase doctor
```

## After importing a batch

Two things are worth doing once:

- **Describe the images.** Extracted screenshots are not read automatically.
  Ask whether to walk the `![image from page N]` markers and write
  `kb/assets/<doc>/descriptions.md`. Rules that exist only inside a screenshot
  stay invisible to search until this is done.
- **Refresh the evaluation set** so retrieval quality is measured against the
  new corpus: `python -m docbase eval --generate`, then `python -m docbase eval`.
