# Questions worded the way people ask

`docbase eval` writes its questions out of the documents' own words, so it
scores close to 100% on any base and cannot tell a better search from a worse
one. This benchmark asks 64 questions the way a person would — "how do I make
my program crash on purpose", where the manual says "panic" — against two
public manuals: the Rust book (112 documents) and the Python library
documentation (537).

```bash
sh benchmarks/human-worded/fetch.sh bench-bases
python benchmarks/human-worded/run.py bench-bases
```

It reports the first search on its own, and then the whole loop the
`docs-search` skill prescribes: when the answer is doubted, ask again in the
documentation's words, at most twice, and if the doubt remains, stop and ask
the person instead of answering.

| | right | wrong | stopped to ask |
|---|---|---|---|
| first search alone | 29 of 64 | — | — |
| loop, doubting on score alone (before) | 43 | 21 | 0 |
| loop, doubting on score, title and a close race (now) | 57 | 2 | 5 |

In all five cases where it stopped to ask, the right document was among the
options it named.

Two limits worth knowing. The questions and the rephrasings were written by
the same person, who knew which documents answer them, and a real agent may
rephrase better or worse. And the doubt rule was tuned on these 64: it was
checked on each half separately, but no question here was kept back from it.
