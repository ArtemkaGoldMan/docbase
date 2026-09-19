# Languages

docbase needs two language-dependent things, and both are data, not code.

**Stopwords** keep scaffolding out of the ranking. A question is mostly
articles, modals and question words; scoring those as content drags real
answers below the confidence threshold. This is not cosmetic — before the
English list covered question words, a correct hit scored 0.43 and was
reported as low confidence.

**Transliteration** turns a document title into a file name. Getting it wrong
is not cosmetic either: `Zgłoszenie wydatków` once became `zg-oszenie-wydatk-w`,
and every Greek or Japanese title collapsed to the same placeholder, so
unrelated documents overwrote each other.

## Built in

`de` `en` `es` `fr` `it` `nl` `pl` `pt` `uk`

Accented Latin needs no table of its own: text is decomposed before mapping,
so Czech, Hungarian, Romanian, Turkish and the rest transliterate correctly
already. Ukrainian Cyrillic and Greek have explicit tables; another Cyrillic
alphabet is a language file away.

Scripts with no table — CJK, Arabic, Hebrew — keep a short digest of the title
as their file name, so documents stay distinct and stable instead of colliding.

## Choosing one

```json
{ "language": "de" }
```

Mixed corpora are the norm rather than the exception — Ukrainian policies quote
English product names, German handbooks quote English job titles — so several
are allowed:

```json
{ "language": ["uk", "en"] }
```

The first entry is the primary one. Stopword sets are merged.

## Adding a word or two

For corpus-specific noise that is not really a stopword of the language:

```json
{ "language": "en", "extra_stopwords": ["appendix", "revision", "confidential"] }
```

A good candidate appears in nearly every document and never distinguishes one
from another. IDF already discounts such words, so reach for this only when a
word is actively getting in the way.

## Adding a language

No code, no reinstall. Drop a file into your own base:

```
kb/languages/<code>.json
```

```json
{
  "stopwords": ["og", "eller", "men", "hvis", "som", "det", "den", "er", "var"],
  "translit": { "å": "aa", "ø": "oe", "æ": "ae" }
}
```

Then select it:

```json
{ "language": "da" }
```

Both keys are optional. `stopwords` adds a language; `translit` adds character
mappings, and may also override a built-in mapping — useful when a
romanisation standard differs from the default, for instance Ukrainian `г`
as `g` rather than `h`.

Check it landed:

```bash
docbase doctor
```

A file with broken JSON is skipped rather than fatal — a typo in a language
file should not take the whole base offline.

## What makes a good stopword list

Closed-class words only: articles, pronouns, prepositions, conjunctions,
auxiliaries, question words. Forty to a hundred entries is plenty.

Do **not** add domain vocabulary. If "invoice" appears in every document of an
invoicing handbook, IDF already gives it almost no weight — that is the
mechanism's job, and removing the word outright would make it unsearchable
when someone genuinely asks about invoices.

Verify against your own corpus rather than by eye:

```bash
docbase eval --generate
docbase eval
```
