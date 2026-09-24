---
name: docs-search
description: Answer a question from the local documentation base — internal policies, procedures, regulations, handbooks, runbooks, anything imported with docbase. Use whenever the user asks what the documentation says, what the rule or limit is, which procedure applies, or asks you to look something up in their docs. Quotes the source instead of relying on general knowledge, and says so plainly when the answer is not in the base.
---

# Answer from the documentation base

## Never read a document whole

A single imported page routinely costs 20k+ tokens. Search instead:

```bash
python -m docbase find "keywords"
```

The command returns ranked fragments with `file:line`, roughly 1k tokens in
total. Read those. Open the full document only if a fragment points at
something it does not contain.

Don't know what is in the base at all:

```bash
python -m docbase map
```

On a large base that names the documents without their sections. Name one to
open it:

```bash
python -m docbase map "invoicing"
```

## When the output says LOW CONFIDENCE

This is not a signal to give up. It means the words of the question did not
match the words of the documentation — which is normal, because people and
manuals rarely use the same vocabulary.

Do this, in order:

1. Read the section map that `find` prints alongside the warning. Pick where
   the topic would plausibly live.
2. Search again **using the wording the documentation would use**, not the
   wording of the question. Put yourself in the author's position.
3. Still nothing: read the section directly.
   ```bash
   sed -n '<line>,+40p' kb/text/<file>.md
   ```
4. Only after those three steps say the base does not cover it.

An extra search costs about 1k tokens. A wrong answer costs more.

## Cards

If `kb/cards/<topic>/` exists, it holds an extract someone already distilled
from the source — read that first, it is cheaper than searching. If the folder
contains a `_stale` marker the source has changed since: check
`kb/history/<document>/` for the diff, update the card, and remove the marker.

## Images

Markers like `![image from page 2](kb/assets/<doc>/p02-1.png)` mean a
screenshot carried content the text does not.

- Do not open images by default — roughly 1500 tokens each.
- Open one only when the answer is missing from the text and the marker sits
  in a relevant section.
- Having opened it, write what it shows into
  `kb/assets/<doc>/descriptions.md`. Descriptions are indexed, so the next
  question finds it as text and no one pays for the image again.

## Answering

- Quote or closely paraphrase the source, and name the file and line.
- Numbers, limits and deadlines: take them from the document verbatim. Never
  reconstruct them from memory.
- Not in the base: say that plainly, and say which document would need to be
  imported. **Do not invent policy.** For anything a person will act on, a
  confident wrong answer is worse than an honest gap.
- A link like `https://<wiki host>/...` still in the text means that page has
  not been imported yet.
