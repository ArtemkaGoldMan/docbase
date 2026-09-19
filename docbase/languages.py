"""Language data: stopwords and transliteration.

Two jobs, both language-dependent and neither worth hardcoding:

* **Stopwords** keep scaffolding out of the ranking. A query is mostly
  articles, modals and question words; scoring those as content drags real
  answers below the confidence threshold.
* **Transliteration** turns a document title into a file name. Getting it
  wrong is not cosmetic: ``Zgłoszenie wydatków`` became ``zg-oszenie-wydatk-w``
  before this module existed, and every Greek or Japanese title collapsed to
  the same placeholder, so documents collided with each other.

Adding a language takes no code — see ``docs/LANGUAGES.md``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata

# ---------------------------------------------------------------- stopwords
# Closed-class words only: articles, pronouns, prepositions, auxiliaries,
# question words. Never domain vocabulary — that is what IDF is for.
STOPWORDS = {
    "en": """the a an and or of to in on for from is are was were be been at by
        with as it its that this these those there here how what when where
        which why who whom can could do does did should must may might will
        would shall if not no yes but than i me my we our you your they their
        have has had am any all some about""",
    "uk": """як що чи для при від або але це не на по до за у в з із та і й а
        чого кого коли де куди хто який яка яке які якщо щоб бо теж також ще
        вже так ні мені мене ми ви вам вони їх його її треба потрібно можна
        може бути буде був була зі про над під між""",
    "de": """der die das den dem des ein eine einer eines einem einen und oder
        aber wenn dass als wie wo wann warum wer was welche welcher ist sind
        war waren sein haben hat hatte werden wird von zu mit auf für in im
        an am bei nach aus über unter nicht kein keine man es sie er wir ihr
        ich du sich auch noch nur schon""",
    "fr": """le la les un une des du de au aux et ou mais si que qui quoi dont
        où quand comment pourquoi est sont était étaient être avoir a ont avait
        dans sur pour par avec sans sous vers chez ne pas plus très ce cet
        cette ces il elle ils elles nous vous je tu on se son sa ses leur""",
    "es": """el la los las un una unos unas de del al y o pero si que quien
        cual cuando donde como por qué es son era eran ser estar haber ha han
        en sobre para por con sin bajo no más muy este esta estos estas ese
        esa él ella ellos nosotros vosotros yo tú se su sus lo""",
    "it": """il lo la i gli le un uno una di del della al alla e o ma se che
        chi cosa quando dove come perché è sono era erano essere avere ha
        hanno in su per con senza sotto non più molto questo questa questi
        quello quella egli ella noi voi io tu si suo sua loro""",
    "pt": """o a os as um uma uns umas de do da dos das no na nos nas ao aos e
        ou mas se que quem qual quando onde como porque é são era eram ser
        estar ter tem têm em sobre para por com sem sob não mais muito este
        esta esse essa ele ela eles nós vós eu tu se seu sua seus""",
    "nl": """de het een en of maar als dat die dit deze wie wat waar wanneer
        hoe waarom is zijn was waren worden wordt heeft hebben had van tot met
        voor op in aan bij naar uit over onder niet geen meer zeer ik jij hij
        zij wij jullie ze er ook nog al""",
    "pl": """i oraz lub ale jeśli że który która które kto co gdzie kiedy jak
        dlaczego jest są był była były być mieć ma mają w na do od za po przez
        dla z ze bez pod nad nie tak bardzo ten ta to te tamten ja ty on ona
        my wy oni się jego jej ich""",
}

# ------------------------------------------------------------- script tables
# Letters that NFKD does not decompose, so they need naming explicitly.
LATIN_EXTRAS = {
    "ł": "l", "đ": "d", "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss",
    "þ": "th", "ð": "d", "ħ": "h", "ı": "i", "ŋ": "n", "ĸ": "k",
}

# Ukrainian Cyrillic, following the national romanisation standard.
# Other Cyrillic alphabets differ enough to deserve their own file rather than
# a merged table — see docs/LANGUAGES.md.
CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia",
}

GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
}

SCRIPTS = {}
SCRIPTS.update(CYRILLIC)
SCRIPTS.update(GREEK)
SCRIPTS.update(LATIN_EXTRAS)


def _words(blob):
    return frozenset(blob.split())


def stopwords_for(languages, extra=()):
    """Merge the stopword sets of every configured language.

    Mixed-language corpora are the norm, not the exception: Ukrainian policies
    quote English product names, German handbooks quote English job titles.
    """
    out = set(extra)
    for code in languages:
        blob = STOPWORDS.get(code)
        if blob:
            out |= _words(blob)
    return frozenset(out)


def transliterate(text, extra_map=None):
    """Any script -> ASCII, as faithfully as a lookup table can manage.

    Accented Latin is handled by decomposition, so French, Czech, Hungarian and
    the rest work without a table of their own.
    """
    table = dict(SCRIPTS)
    if extra_map:
        table.update(extra_map)

    # Decompose first, then map. An accented Greek or Cyrillic letter is not
    # in any table under its accented form, and mapping before decomposition
    # left the bare letter behind to be discarded later.
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))

    out = []
    for char in bare:
        lower = char.lower()
        if lower in table:
            replacement = table[lower]
            out.append(replacement.upper() if char.isupper() and replacement else replacement)
        else:
            out.append(char)
    return "".join(out)


def slugify(text, extra_map=None, fallback="document", seed=""):
    """Title -> file name.

    Scripts with no transliteration table — CJK, Arabic, Hebrew — would all
    reduce to the same placeholder and collide. A short digest of the original
    keeps them distinct and stable across runs.
    """
    latin = transliterate(text or "", extra_map).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:48].strip("-")
    if slug:
        return slug
    source = (text or seed).strip()
    if source:
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
        return f"{fallback}-{digest}"
    return fallback


def load_custom(root, layout_languages="kb/languages"):
    """Language files a user dropped into their own base.

    ``kb/languages/<code>.json`` with ``{"stopwords": [...], "translit": {...}}``
    either adds a language or overrides a built-in one, without touching the
    installed package.
    """
    folder = os.path.join(root, layout_languages)
    if not os.path.isdir(folder):
        return {}, {}
    stop_extra, translit_extra = {}, {}
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        code = os.path.splitext(name)[0]
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            continue                    # a broken file must not break the tool
        if isinstance(data.get("stopwords"), list):
            stop_extra[code] = " ".join(str(w) for w in data["stopwords"])
        if isinstance(data.get("translit"), dict):
            translit_extra.update({str(k): str(v) for k, v in data["translit"].items()})
    return stop_extra, translit_extra


def available():
    return sorted(STOPWORDS)
