"""How search does on questions worded the way people ask.

    python benchmarks/human-worded/run.py <folder built by fetch.sh>

`docbase eval` builds its questions out of the documents' own words, so it
scores near 100% on anything and cannot tell a better search from a worse
one. These 64 are phrased the way a person asks — "how do I make my program
crash on purpose", where the manual says "panic" — and each names the
documents that answer it.

Two numbers come out. The first search alone. Then the whole loop the
docs-search skill prescribes: when the answer is doubted, search again in the
documentation's words, at most twice; when the doubt remains, stop and ask
rather than answer. The second is the one that matters — it counts the answers
a person would actually have been given, and how many of them were wrong.

The questions and the rephrasings were written by the same author, who knew
which documents answer them. A real agent may rephrase better or worse.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from docbase import config                     # noqa: E402
from docbase.search import Index               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main(root):
    questions = json.load(open(os.path.join(HERE, "questions.json"), encoding="utf-8"))
    rephrased = json.load(open(os.path.join(HERE, "rephrasings.json"), encoding="utf-8"))
    first = {"right": 0, "n": 0}
    loop = {"right": 0, "wrong": 0, "asked": 0, "searches": 0}

    for base, cases in questions.items():
        index = Index(config.load(os.path.join(root, base))).build()
        for question, targets in cases:
            def right(name):
                return any(name.startswith(t) for t in targets)

            hits = index.search(question)
            loop["searches"] += 1
            first["n"] += 1
            first["right"] += bool(hits) and right(hits[0][1])

            settled = None if index.doubts(question, hits) else hits[0][1]
            for again in rephrased.get(question, []) if settled is None else []:
                more = index.search(again)
                loop["searches"] += 1
                if more and not index.doubts(again, more):
                    settled = more[0][1]
                    break
            if settled is None:
                loop["asked"] += 1
            else:
                loop["right" if right(settled) else "wrong"] += 1

    n = first["n"]
    print(f"first search alone     right first {first['right']}/{n}")
    print(f"with the re-ask loop   right {loop['right']}/{n}   wrong {loop['wrong']}   "
          f"stopped to ask {loop['asked']}   searches {loop['searches']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bench-bases")
