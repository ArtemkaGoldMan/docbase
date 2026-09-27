"""How search does on Ukrainian law, asked in plain Ukrainian.

    python benchmarks/ukrainian-laws/run.py <folder built by fetch.sh>

32 questions worded the way a client asks — "чи можна скасувати переказ,
який я вже відправив" — each naming the article that answers it. Scored by
article, not by document: eight long laws make "the right law" too easy.

Reports the first search alone, and the whole loop the docs-search skill
prescribes: when the answer is doubted, ask again in the law's own words, at
most twice, and when the doubt remains, stop and ask rather than answer.

The questions and the rephrasings were written by the same author, who knew
which articles answer them.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from docbase import config                     # noqa: E402
from docbase.search import Index               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RE_ARTICLE = re.compile(r"^(?:#+\s+)?Стаття\s+(\d+(?:-\d+)?)\.")


def article_spans(index):
    """document -> [(article, first line, line after it)]."""
    out = {}
    for name, path in index.files():
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        starts = [(n, m.group(1)) for n, line in enumerate(lines, 1)
                  if (m := RE_ARTICLE.match(line))]
        out[name] = [(article, start,
                      starts[k + 1][0] if k + 1 < len(starts) else len(lines) + 1)
                     for k, (start, article) in enumerate(starts)]
    return out


def main(root):
    index = Index(config.load(os.path.join(root, "ukrainian-laws"))).build()
    spans = article_spans(index)

    def article(hit):
        return next((a for a, first, after in spans.get(hit[1], [])
                     if first <= hit[2] < after), None)

    questions = json.load(open(os.path.join(HERE, "questions.json"), encoding="utf-8"))
    rephrased = json.load(open(os.path.join(HERE, "rephrasings.json"), encoding="utf-8"))
    first = {"article": 0, "law": 0}
    loop = {"article": 0, "law": 0, "wrong": 0, "asked": 0, "searches": 0}

    for question, law, wanted in questions:
        hits = index.search(question)
        loop["searches"] += 1
        if hits:
            first["law"] += hits[0][1].startswith(law)
            first["article"] += hits[0][1].startswith(law) and article(hits[0]) == wanted

        settled = None if index.doubts(question, hits) else hits[0]
        for again in rephrased.get(question, []) if settled is None else []:
            more = index.search(again)
            loop["searches"] += 1
            if more and not index.doubts(again, more):
                settled = more[0]
                break
        if settled is None:
            loop["asked"] += 1
        elif not settled[1].startswith(law):
            loop["wrong"] += 1
        elif article(settled) == wanted:
            loop["article"] += 1
        else:
            loop["law"] += 1

    n = len(questions)
    print(f"first search alone     right article first {first['article']}/{n}   "
          f"right law first {first['law']}/{n}")
    print(f"with the re-ask loop   right article {loop['article']}/{n}   right law, "
          f"another article {loop['law']}   wrong law {loop['wrong']}   "
          f"stopped to ask {loop['asked']}   searches {loop['searches']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bench-bases")
