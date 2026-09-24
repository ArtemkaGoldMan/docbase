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

from . import languages

CONFIG_NAME = "docbase.json"

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
    #: Fraction of a fragment repeated into the next one.
    chunk_overlap: float = 0.25
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
    min_image_bytes: int = 8_000


@dataclass(frozen=True)
class Config:
    layout: Layout
    search: Search = field(default_factory=Search)
    importer: Importer = field(default_factory=Importer)
    #: One code, or several for a mixed-language corpus.
    languages: tuple = ("en",)
    #: Extra stopwords on top of the language defaults.
    extra_stopwords: frozenset = frozenset()

    @property
    def language(self):
        """The primary language, for callers that want a single code."""
        return self.languages[0] if self.languages else "en"

    #: Language files found in the base, resolved once at load time.
    #: Reading them on every access meant a directory check per indexed
    #: fragment — 7000 of them on a 200-document corpus, and an actual file
    #: read and JSON parse each time for anyone who used the feature.
    custom_stopwords: tuple = ()
    custom_translit: tuple = ()

    @property
    def stopwords(self):
        merged = dict(languages.STOPWORDS)
        merged.update(dict(self.custom_stopwords))
        words = set(self.extra_stopwords)
        for code in self.languages:
            if code in merged:
                words |= frozenset(merged[code].split())
        return frozenset(words)

    @property
    def translit(self):
        """Extra character mappings contributed by user language files."""
        return dict(self.custom_translit)

    def slug(self, text, fallback="document"):
        return languages.slugify(text, self.translit, fallback)

    def is_internal(self, url):
        return any(host in url for host in self.importer.internal_hosts)


DEFAULT_CONFIG = {
    "language": "en",
    "importer": {"internal_hosts": [], "min_image_bytes": 8000},
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
        min_image_bytes=int(imp_raw.get("min_image_bytes", 8_000)),
    )

    language = raw.get("language", "en")
    if isinstance(language, str):
        language = [language]

    custom_stop, custom_translit = languages.load_custom(layout.root)

    return Config(
        layout=layout,
        search=search,
        importer=imp,
        languages=tuple(language) or ("en",),
        extra_stopwords=frozenset(raw.get("extra_stopwords", ())),
        custom_stopwords=tuple(sorted(custom_stop.items())),
        custom_translit=tuple(sorted(custom_translit.items())),
    )


def write_default(root, language="en", internal_hosts=()):
    """Create a starter config. Returns the path written."""
    data = dict(DEFAULT_CONFIG)
    data["language"] = language
    data["importer"] = {"internal_hosts": list(internal_hosts),
                        "min_image_bytes": 8000}
    path = os.path.join(root, CONFIG_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path
