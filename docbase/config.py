"""Configuration and directory layout.

Everything that used to be hardcoded — the wiki host, the language of the
corpus, retrieval thresholds — lives here and can be overridden by a
``docbase.json`` at the root of the knowledge base.

JSON rather than TOML on purpose: ``tomllib`` only ships with Python 3.11+,
and this tool is meant to run on whatever interpreter a non-technical user
already has.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace

CONFIG_NAME = "docbase.json"

# Ukrainian Cyrillic -> Latin, used to build safe file names.
CYRILLIC_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia",
}

# Question words and modals matter here as much as articles: a query like
# "how long can I wait" is mostly scaffolding, and scoring it as content
# drags the confidence of correct answers below the threshold.
STOPWORDS = {
    "en": {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "from",
        "is", "are", "was", "were", "be", "been", "at", "by", "with", "as",
        "it", "its", "that", "this", "these", "those", "there", "here",
        "how", "what", "when", "where", "which", "why", "who", "whom",
        "can", "could", "do", "does", "did", "should", "must", "may", "might",
        "will", "would", "shall", "if", "not", "no", "yes", "but", "than",
        "i", "me", "my", "we", "our", "you", "your", "they", "their",
        "have", "has", "had", "am", "any", "all", "some", "about",
    },
    "uk": {
        "як", "що", "чи", "для", "при", "від", "або", "але", "це", "не",
        "на", "по", "до", "за", "у", "в", "з", "із", "та", "і", "й", "а",
        "чого", "кого", "коли", "де", "куди", "хто", "який", "яка", "яке",
        "які", "якщо", "щоб", "бо", "теж", "також", "ще", "вже", "так",
        "ні", "мені", "мене", "ми", "ви", "вам", "вони", "їх", "його", "її",
        "треба", "потрібно", "можна", "може", "бути", "буде", "був", "була",
    },
}


@dataclass(frozen=True)
class Layout:
    """Where things live, relative to the knowledge-base root."""

    root: str
    originals: str = "kb/originals"   # files the user drops in
    text: str = "kb/text"             # converted markdown (generated)
    assets: str = "kb/assets"         # extracted images (generated)
    cards: str = "kb/cards"           # agent-written summaries
    history: str = "kb/history"       # previous versions and diffs
    manifest: str = "kb/.manifest.json"

    def path(self, which):
        return os.path.join(self.root, getattr(self, which))

    def ensure(self, *which):
        for name in which or ("originals", "text"):
            os.makedirs(self.path(name), exist_ok=True)


@dataclass(frozen=True)
class Search:
    stem_length: int = 5
    chunk_chars: int = 420
    default_hits: int = 8
    per_hit_chars: int = 620
    total_chars: int = 6000
    low_confidence: float = 0.9
    heading_weight: float = 2.0
    density_weight: float = 0.4


@dataclass(frozen=True)
class Importer:
    #: Hosts treated as internal — links to them can resolve to local files.
    internal_hosts: tuple = ()
    min_image_bytes: int = 20_000


@dataclass(frozen=True)
class Config:
    layout: Layout
    search: Search = field(default_factory=Search)
    importer: Importer = field(default_factory=Importer)
    language: str = "en"
    #: Extra stopwords on top of the language defaults.
    extra_stopwords: frozenset = frozenset()

    @property
    def stopwords(self):
        return frozenset(STOPWORDS.get(self.language, set())) | self.extra_stopwords

    @property
    def translit(self):
        return CYRILLIC_TRANSLIT

    def is_internal(self, url):
        return any(host in url for host in self.importer.internal_hosts)


DEFAULT_CONFIG = {
    "language": "en",
    "importer": {"internal_hosts": [], "min_image_bytes": 20000},
    "search": {},
    "layout": {},
}


def find_root(start=None):
    """Nearest ancestor holding a config file, else the starting directory."""
    current = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(current, CONFIG_NAME)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(start or os.getcwd())
        current = parent


def _section(raw, name):
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def load(root=None):
    root = find_root(root)
    path = os.path.join(root, CONFIG_NAME)
    raw = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (json.JSONDecodeError, OSError):
            raw = {}        # a broken config must not stop the whole tool

    layout = Layout(root=root)
    for key, value in _section(raw, "layout").items():
        if hasattr(layout, key) and key != "root":
            layout = replace(layout, **{key: value})

    search = Search()
    for key, value in _section(raw, "search").items():
        if hasattr(search, key):
            search = replace(search, **{key: value})

    imp_raw = _section(raw, "importer")
    imp = Importer(
        internal_hosts=tuple(imp_raw.get("internal_hosts", ())),
        min_image_bytes=int(imp_raw.get("min_image_bytes", 20_000)),
    )

    return Config(
        layout=layout,
        search=search,
        importer=imp,
        language=raw.get("language", "en"),
        extra_stopwords=frozenset(raw.get("extra_stopwords", ())),
    )


def write_default(root, language="en", internal_hosts=()):
    """Create a starter config. Returns the path written."""
    data = dict(DEFAULT_CONFIG)
    data["language"] = language
    data["importer"] = {"internal_hosts": list(internal_hosts),
                        "min_image_bytes": 20000}
    path = os.path.join(root, CONFIG_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path
