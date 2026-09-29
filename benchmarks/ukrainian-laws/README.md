# Ukrainian law, asked in plain Ukrainian

Eight laws on consumer financial services — consumer protection, payment
services, consumer credit, deposit guarantees, personal data, banks, financial
services, citizens' appeals — from the parliament's public database, and 32
questions worded the way a client asks: *"чи можна скасувати переказ, який я
вже відправив"*, where the law says *"відкликання платіжної інструкції"*.

Scored by **article**, not by document. Eight long laws make "the right law"
too easy a target; a person needs the article.

```bash
sh benchmarks/ukrainian-laws/fetch.sh bench-bases
python benchmarks/ukrainian-laws/run.py bench-bases
```

| | right article | right law, another article | wrong law | stopped to ask |
|---|---|---|---|---|
| first search alone | 8 of 32 | 12 | 12 | — |
| with the ask-again loop | 23 | 6 | 1 | 2 |

What this set changed in docbase:

- The laws are published as plain paragraphs, with "Стаття 8." made bold by a
  stylesheet. They arrived as one section each, articles invisible. Numbered
  parts are now recognised as headings in every built-in language: right
  article first 4 → 8, and 15 → 20 through the loop.
- The site's `<title>` carries the date, the number and "(Текст для друку)",
  and cuts long names short with an ellipsis. The Open Graph title is now
  preferred: `pro-banky-i-bankivsku-diialnist.md`, not
  `pro-banky-i-bankivsku-diial-vid-07-12-2000-no-21.md`.
- In a base of a few long documents the fragments around a weak match nearly
  all come from the same law and "agree" with it. A very weak match is now
  doubted regardless.
- A word of the question the laws never use was left out of the score, so a
  question in a client's words matched the few legal words it shared as
  confidently as the law's own phrasing would. It now counts against the
  score like the rarest word in the base: such a question is doubted and
  asked again in the law's words. Right article through the loop 20 → 23,
  wrong law 3 → 1; the English set did not move.

And what it did not: cutting Ukrainian words at four, five, six or seven
letters made no difference beyond noise, so five stays.

The limits of the English set apply here too: one author wrote the questions
and the rephrasings, knowing the answers.
