---
name: docs-tailor
description: Build skills and extracts tailored to whatever documentation has been imported — turning a generic base into one that knows this domain's workflows, checklists and required fields. Use when the user asks to set up, tailor or customise the base for their documentation, wants task-specific helpers generated, or asks how to make the assistant follow their procedures.
---

# Tailor the base to this documentation

The point of this skill: the toolkit ships generic, and the documentation
decides what the useful helpers are. A support handbook wants "what to ask the
customer"; an infrastructure runbook wants "pre-flight checks"; a compliance
manual wants "what must be recorded". Do not guess — read what is there.

## 1. Survey before proposing

```bash
python -m docbase map
python -m docbase status
```

Then sample the shape of the content with a few targeted searches. You are
looking for recurring structures, not for topics:

- checklists of things to gather before acting
- required fields, forms, records to fill in
- fixed phrasings or templates meant to be reused verbatim
- hard prohibitions, thresholds, approval gates
- decision tables that route between procedures

## 2. Propose, then confirm

Tell the user what recurring structures you found and which helpers follow from
them. Two to four skills is usually right. Get agreement before writing files —
generated skills that miss the point are worse than none.

## 3. Write the skills

Into `.claude/skills/<name>/SKILL.md`, with frontmatter:

```yaml
---
name: <kebab-case>
description: <what it does, and the situations that should trigger it —
  phrased the way the user would actually describe them>
---
```

Rules for what you write:

- A skill holds a **procedure**, never the content itself. Content lives in the
  base and is retrieved at run time; copying it into a skill guarantees it goes
  stale silently.
- Name the concrete steps: what to read, in what order, what to output.
- Fix the output shape. Predictable structure is most of the value.
- State the boundaries: what must never be invented, what requires a human.
- Keep the body under about 150 lines. It loads on every trigger.

## 4. Build the extracts

For each recurring topic, create `kb/cards/<topic>/` holding short extracts,
split by task rather than by document. Every card starts with:

```yaml
---
topic: <name>
source: kb/text/<file>.md
source_page_id: "<id>"
built: <date>
---
```

`source_page_id` is what lets `docbase sync` re-bind and invalidate the card
when its document changes. Never omit it.

Extract, do not paraphrase: keep the documentation's own wording for anything
quoted back to a person, and keep numbers verbatim. If the source does not
cover something, write that the card is partial and say what is missing —
an honest gap is usable, a confident invention is not.

Write it so `verify` can check it:

- Put the document's own words in quotation marks or backticks. That is what
  marks a phrase as a quotation rather than your summary of one, and only
  quotations are checked.
- Keep every number next to what it counts — "72 hours", not "72" in one
  sentence and "hours" in the next. The unit is what tells 72 hours from 72
  days, and it is the only thing that can.

## 5. Verify

```bash
python -m docbase eval --generate
python -m docbase eval
python -m docbase verify
python -m docbase doctor
```

`verify` checks every number and quotation in the cards you wrote against the
document they came from. A card that drifts from its source is the one failure
mode this whole design exists to prevent, so treat a finding there as a bug in
the card, not as noise.

`doctor` checks the skills you wrote for the faults that stop one loading
without saying so: frontmatter it cannot read, a name that has drifted from
its folder, a missing description, a `kb/` path that is not there. A skill
that never loads leaves the base looking tailored and behaving exactly as it
did before, so treat a finding there the same way.

`verify` confirms that a claim is *somewhere* in the document, not that you
quoted the right occurrence of it. A long document that mentions "24 hours" about
something else will confirm a card that says 24 hours about this. Reading the
section is still the only way to be sure.
