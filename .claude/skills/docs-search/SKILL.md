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

The best match may be the wrong one, and the output says why. People and
manuals rarely use the same words — someone asks how to make a program
"crash on purpose", the manual calls it "panic" — so this is the normal case,
not a dead end. Measured on questions worded the way people ask, about half
of first searches miss; a second search in the documentation's own words is
how they get answered.

Never answer from a LOW CONFIDENCE result as it stands. Instead:

1. **Ask again in the documentation's words.** The output lists the words of
   the question the documentation never uses — replace those first. Take the
   replacements from the headings it shows for the nearby topics, and from
   the fragments themselves: a fragment that says "instead of allowing the
   program to panic" has just told you what the manual calls it.
2. **Two more searches at most.** Each costs about 1k tokens; a wrong answer
   costs more, but a hunt through the whole base costs more than either.
3. **When two searches agree on a section, read it whole** and answer from
   it, quoting what a person will act on:
   ```bash
   sed -n '<line>,+40p' kb/text/<file>.md
   ```
4. **When they still do not agree, look at what is competing.**
   - The documents are about the same thing — three guides to logging, say —
     read the first; either answers the question.
   - They are about different things — "Installation" and "Installing
     Binaries with cargo install" — ask the person which one they meant, and
     name the options. On questions measured this way, the right document was
     among the options every time.
   - It matches none of them — say the documentation does not seem to cover
     it, what you searched for, and which documents came closest.

A result without LOW CONFIDENCE can still be wrong, only less often. If the
fragment does not actually answer the question, treat it the same way.

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
