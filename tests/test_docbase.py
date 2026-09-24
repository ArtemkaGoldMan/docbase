"""Regression tests for the machinery.

Every case here corresponds to a failure that actually happened during
development, most of them silent: a document overwritten by another, a broken
file stopping the whole import, an asset folder deleted along with notes that
existed nowhere else. Retrieval quality is measured separately by
``docbase eval``; this file is about the base not losing data.

Run with: python -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docbase import config as config_module      # noqa: E402
from docbase import sync as sync_module          # noqa: E402
from docbase.search import Index                 # noqa: E402

PAGE = """<!DOCTYPE html><html><head>
<meta name="ajs-page-id" content="{page_id}">
<title>{title}</title></head><body>
<div id="main-content">
<h1>{title}</h1>
<p>{body}</p>
{extra}
</div></body></html>
"""


def page(page_id, title, body, extra=""):
    return PAGE.format(page_id=page_id, title=title, body=body, extra=extra)


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="docbase-test-")
        config_module.write_default(self.root, language="en",
                                    internal_hosts=["wiki.example.com"])
        self.cfg = config_module.load(self.root)
        self.cfg.layout.ensure("originals", "text")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def drop(self, name, content):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def sync(self, **kwargs):
        # The report is meant for a human; tests assert on the returned data.
        with contextlib.redirect_stdout(io.StringIO()):
            return sync_module.run(self.cfg, **kwargs)

    def text_files(self):
        text_dir = self.cfg.layout.path("text")
        return sorted(f for f in os.listdir(text_dir) if f.endswith(".md"))


class TestImport(BaseCase):
    def test_empty_base_is_not_an_error(self):
        report = self.sync(quiet=True)
        self.assertEqual(report["documents"], 0)
        self.assertEqual(report["failed"], [])

    def test_document_is_imported_and_named_after_its_title(self):
        self.drop("whatever.html", page(1, "Expense reports", "Receipts must be PDF."))
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["expense-reports.md"])

    def test_dropped_file_moves_into_originals(self):
        self.drop("whatever.html", page(1, "Expense reports", "Receipts must be PDF."))
        self.sync(quiet=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, "whatever.html")))
        self.assertTrue(os.listdir(self.cfg.layout.path("originals")))

    def test_same_content_under_another_name_is_recognised(self):
        body = page(1, "Expense reports", "Receipts must be PDF.")
        self.drop("first.html", body)
        self.sync(quiet=True)
        self.drop("second-copy.html", body)
        report = self.sync(quiet=True)
        self.assertEqual(len(self.text_files()), 1)
        self.assertTrue(any("already in the base" in line for line in report["log"]))

    def test_broken_file_does_not_stop_the_others(self):
        self.drop("good.html", page(1, "Expense reports", "Receipts must be PDF."))
        with open(os.path.join(self.root, "broken.pdf"), "wb") as handle:
            handle.write(b"this is not a pdf")
        report = self.sync(quiet=True)
        self.assertEqual(len(report["failed"]), 1)
        self.assertIn("expense-reports.md", self.text_files())

    def test_broken_file_is_reported_only_once(self):
        with open(os.path.join(self.root, "broken.pdf"), "wb") as handle:
            handle.write(b"this is not a pdf")
        first = self.sync(quiet=True)
        second = self.sync(quiet=True)
        self.assertEqual(len(first["failed"]), 1)
        self.assertEqual(second["failed"], [])

    def test_sync_is_idempotent(self):
        self.drop("a.html", page(1, "Expense reports", "Receipts must be PDF."))
        self.sync(quiet=True)
        report = self.sync(quiet=True)
        self.assertEqual(report["converted"], 0)
        self.assertEqual(report["log"], [])


class TestNaming(BaseCase):
    def test_different_pages_with_similar_titles_do_not_overwrite(self):
        self.drop("one.html", page(11, "Expense reports", "First document body."))
        self.sync(quiet=True)
        self.drop("two.html", page(22, "Expense Reports!", "Second document body."))
        self.sync(quiet=True)

        files = self.text_files()
        self.assertEqual(len(files), 2, f"a document was overwritten: {files}")
        bodies = [open(os.path.join(self.cfg.layout.path("text"), f),
                       encoding="utf-8").read() for f in files]
        self.assertTrue(any("First document body" in b for b in bodies))
        self.assertTrue(any("Second document body" in b for b in bodies))

    def test_same_page_reimported_reuses_its_file(self):
        self.drop("one.html", page(11, "Expense reports", "Original text."))
        self.sync(quiet=True)
        self.drop("one-v2.html", page(11, "Expense reports", "Revised text."))
        self.sync(quiet=True)
        self.assertEqual(len(self.text_files()), 1)


class TestHistory(BaseCase):
    def test_change_is_recorded_as_a_diff(self):
        self.drop("a.html", page(1, "Travel booking", "Domestic trips need five days."))
        self.sync(quiet=True)
        self.drop("b.html", page(1, "Travel booking", "Domestic trips need ten days."))
        self.sync(quiet=True)

        history = self.cfg.layout.path("history")
        folders = os.listdir(history)
        self.assertEqual(folders, ["travel-booking"])
        files = os.listdir(os.path.join(history, folders[0]))
        self.assertTrue(any(f.endswith(".diff") for f in files))
        self.assertIn("previous.md", files)

        diff = [f for f in files if f.endswith(".diff")][0]
        content = open(os.path.join(history, folders[0], diff), encoding="utf-8").read()
        self.assertIn("ten days", content)
        self.assertIn("five days", content)


class TestCards(BaseCase):
    def _make_card(self, topic, source, page_id, extra=""):
        folder = os.path.join(self.cfg.layout.path("cards"), topic)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "ask.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"---\nsource: kb/text/{source}\n"
                         f"source_page_id: \"{page_id}\"\n---\n\n# Card\n{extra}\n")
        return folder

    def test_card_is_marked_stale_when_its_source_changes(self):
        self.drop("a.html", page(1, "Travel booking", "Domestic trips need five days."))
        self.sync(quiet=True)
        folder = self._make_card("travel", "travel-booking.md", "1")

        self.drop("b.html", page(1, "Travel booking", "Domestic trips need ten days."))
        self.sync(quiet=True)
        self.assertTrue(os.path.isfile(os.path.join(folder, "_stale")))

    def test_card_source_is_repaired_when_the_file_is_renamed(self):
        self.drop("a.html", page(1, "Travel booking", "Domestic trips need five days."))
        self.sync(quiet=True)
        folder = self._make_card("travel", "old-name.md", "1")

        self.sync(quiet=True)
        card = open(os.path.join(folder, "ask.md"), encoding="utf-8").read()
        self.assertIn("travel-booking.md", card)

    def test_asset_folder_merge_keeps_handwritten_descriptions(self):
        assets = self.cfg.layout.path("assets")
        old = os.path.join(assets, "from-file-name")
        new = os.path.join(assets, "from-title")
        os.makedirs(old), os.makedirs(new)
        with open(os.path.join(old, "p01-1.png"), "wb") as handle:
            handle.write(b"image")
        with open(os.path.join(new, "descriptions.md"), "w", encoding="utf-8") as handle:
            handle.write("knowledge that exists nowhere else")

        sync_module._align_assets(self.cfg, "from-file-name", "from-title.md", "")
        survivors = sorted(os.listdir(new))
        self.assertEqual(survivors, ["descriptions.md", "p01-1.png"])
        self.assertFalse(os.path.exists(old))


class TestManifest(BaseCase):
    def test_write_is_atomic_and_leaves_no_temp_file(self):
        path = self.cfg.layout.path("manifest")
        sync_module.write_manifest(path, {"a.pdf": {"sha1": "x"}})
        self.assertFalse(os.path.exists(path + ".tmp"))
        self.assertEqual(json.load(open(path, encoding="utf-8"))["a.pdf"]["sha1"], "x")

    def test_corrupt_manifest_is_survivable(self):
        path = self.cfg.layout.path("manifest")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(sync_module.read_manifest(path), {})


class TestLinking(BaseCase):
    def test_cross_document_link_becomes_local(self):
        link = '<p>See <a href="https://wiki.example.com/pages/viewpage.action?pageId=2">Travel</a>.</p>'
        self.drop("a.html", page(1, "Expense reports", "Receipts must be PDF.", link))
        self.drop("b.html", page(2, "Travel booking", "Domestic trips need five days."))
        self.sync(quiet=True)

        text = open(os.path.join(self.cfg.layout.path("text"), "expense-reports.md"),
                    encoding="utf-8").read()
        # The registry at the foot of the document keeps original URLs on
        # purpose, so only the body is checked here.
        body = text.split("## Links found on this page")[0]
        self.assertIn("(travel-booking.md)", body)
        self.assertNotIn("wiki.example.com/pages/viewpage.action?pageId=2", body)

    def test_link_to_missing_page_stays_absolute(self):
        link = '<p>See <a href="https://wiki.example.com/pages/viewpage.action?pageId=99">Other</a>.</p>'
        self.drop("a.html", page(1, "Expense reports", "Receipts must be PDF.", link))
        self.sync(quiet=True)
        text = open(os.path.join(self.cfg.layout.path("text"), "expense-reports.md"),
                    encoding="utf-8").read()
        self.assertIn("pageId=99", text)


class TestSearch(BaseCase):
    def _load(self):
        self.drop("a.html", page(
            1, "Expense reports",
            "Receipts must be attached as PDF. Photographs of receipts are rejected "
            "because the finance portal cannot archive them reliably."))
        self.drop("b.html", page(
            2, "Travel booking",
            "Refundable fares are the default. A non-refundable fare may be chosen "
            "only when it is cheaper by more than thirty percent."))
        self.sync(quiet=True)
        return Index(self.cfg).build()

    def test_finds_the_right_document(self):
        index = self._load()
        hits = index.search("refundable fare cheaper")
        self.assertTrue(hits)
        self.assertEqual(hits[0][1], "travel-booking.md")

    def test_absent_topic_scores_below_the_threshold(self):
        index = self._load()
        hits = index.search("parental leave entitlement calculation")
        best = hits[0][0] if hits else 0.0
        self.assertLess(best, self.cfg.search.low_confidence)

    def test_heading_without_body_is_still_indexed(self):
        self._load()
        path = os.path.join(self.cfg.layout.path("text"), "headings-only.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('---\nconfluence_page_id: "9"\n---\n\n# Sabbatical eligibility\n')
        index = Index(self.cfg).build()
        self.assertTrue(index.search("sabbatical eligibility"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestScaling(BaseCase):
    """Guards against the failure mode that only shows up on a big corpus.

    Both regressions here were real: an idle run once checksummed every
    original (70 seconds on 30 documents), and the index was rebuilt from
    scratch on every single search.
    """

    def _load_many(self, count=12):
        for i in range(count):
            self.drop(f"doc-{i}.html", page(
                100 + i, f"Handbook {i}",
                f"Section {i} covers approval thresholds and escalation paths. "
                f"Rule {i} applies whenever the quarterly budget is exceeded."))
        self.sync(quiet=True)

    def test_idle_run_does_not_checksum_the_originals(self):
        self._load_many()
        calls = []
        original = sync_module.fingerprint

        def counting(path):
            calls.append(path)
            return original(path)

        sync_module.fingerprint = counting
        try:
            self.sync(quiet=True)
        finally:
            sync_module.fingerprint = original
        self.assertEqual(calls, [],
                         "an idle run read file contents; this is what made "
                         "the pre-turn hook take a minute on a real corpus")

    def test_idle_run_does_not_rewrite_documents(self):
        self._load_many()
        text_dir = self.cfg.layout.path("text")
        before = {f: os.path.getmtime(os.path.join(text_dir, f))
                  for f in os.listdir(text_dir)}
        self.sync(quiet=True)
        after = {f: os.path.getmtime(os.path.join(text_dir, f))
                 for f in os.listdir(text_dir)}
        self.assertEqual(before, after,
                         "documents were rewritten with no change, which also "
                         "invalidates the search index every turn")

    def test_index_is_served_from_cache_on_the_next_process(self):
        self._load_many()
        first = Index(self.cfg).build()
        self.assertFalse(first.loaded_from_cache)
        second = Index(self.cfg).build()          # a fresh object, as a new CLI call
        self.assertTrue(second.loaded_from_cache)
        self.assertEqual(len(first.chunks), len(second.chunks))
        self.assertEqual([h[1:3] for h in first.search("approval escalation")],
                         [h[1:3] for h in second.search("approval escalation")])

    def test_cache_is_invalidated_when_a_document_changes(self):
        self._load_many()
        Index(self.cfg).build()
        self.drop("doc-0-v2.html", page(100, "Handbook 0", "Entirely new wording."))
        self.sync(quiet=True)
        rebuilt = Index(self.cfg).build()
        self.assertFalse(rebuilt.loaded_from_cache)

    def test_damaged_cache_falls_back_to_rebuilding(self):
        self._load_many()
        index = Index(self.cfg).build()
        with open(index.cache_path(), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        recovered = Index(self.cfg).build()
        self.assertFalse(recovered.loaded_from_cache)
        self.assertTrue(recovered.search("approval escalation"))


class TestEvaluation(BaseCase):
    """The evaluation must not quietly stop existing.

    Case generation once produced zero cases on documents whose paragraphs are
    a single long sentence — which is most regulations — and then reported
    success. The tests were gone and nothing said so.
    """

    def _load_long_sentences(self):
        for i in range(6):
            sentence = (f"Section {i} states that reimbursement of travel "
                        f"expenses incurred during quarterly audits requires "
                        f"written approval from the department head before the "
                        f"invoice number {i} is submitted to the finance portal "
                        f"together with supporting documentation and receipts")
            self.drop(f"doc-{i}.html", page(300 + i, f"Audit policy {i}", sentence))
        self.sync(quiet=True)

    def test_cases_are_generated_from_long_sentence_documents(self):
        from docbase import evaluate
        self._load_long_sentences()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = evaluate.generate(self.cfg, count=5)
        self.assertEqual(code, 0, f"generation failed: {output.getvalue()}")

        cases = json.load(open(os.path.join(self.root, "kb", "eval.json"),
                               encoding="utf-8"))
        self.assertTrue(cases, "no cases were produced")
        for case in cases:
            self.assertTrue(case["marker"].strip())
            self.assertTrue(case["question"].strip())

    def test_generated_markers_really_occur_in_their_file(self):
        from docbase import evaluate
        self._load_long_sentences()
        with contextlib.redirect_stdout(io.StringIO()):
            evaluate.generate(self.cfg, count=5)
        cases = json.load(open(os.path.join(self.root, "kb", "eval.json"),
                               encoding="utf-8"))
        for case in cases:
            self.assertTrue(
                evaluate._marker_present(self.cfg, case["marker"], case["file"]),
                f"marker not found in {case['file']}: {case['marker'][:60]}")

    def test_empty_corpus_reports_failure_instead_of_success(self):
        from docbase import evaluate
        with contextlib.redirect_stdout(io.StringIO()):
            code = evaluate.generate(self.cfg, count=5)
        self.assertEqual(code, 1)


class TestLanguages(BaseCase):
    """Names and stopwords must work outside English and Ukrainian.

    Before this was data-driven, `Zgłoszenie wydatków` became
    `zg-oszenie-wydatk-w` and every Greek, Japanese or Arabic title collapsed
    to the same placeholder, so unrelated documents overwrote each other.
    """

    def test_accented_latin_survives(self):
        from docbase import languages
        cases = {
            "Note de frais — procédure": "note-de-frais-procedure",
            "Zgłoszenie wydatków": "zgloszenie-wydatkow",
            "Šablona výdajů": "sablona-vydaju",
            "Reisekostenabrechnung": "reisekostenabrechnung",
        }
        for title, expected in cases.items():
            self.assertEqual(languages.slugify(title), expected)

    def test_other_scripts_transliterate(self):
        from docbase import languages
        self.assertEqual(languages.slugify("Витрати та звіти"), "vytraty-ta-zvity")
        self.assertEqual(languages.slugify("Πολιτική εξόδων"), "politiki-exodon")

    def test_untransliterable_titles_stay_distinct(self):
        from docbase import languages
        slugs = [languages.slugify(t) for t in ("経費精算", "出張規程",
                                                "سياسة النفقات", "נהלי נסיעות")]
        self.assertEqual(len(set(slugs)), len(slugs),
                         "titles in unsupported scripts collided")
        self.assertEqual(slugs, [languages.slugify(t) for t in ("経費精算", "出張規程",
                                                                "سياسة النفقات", "נהלי נסיעות")],
                         "slugs must be stable across runs")

    def test_several_languages_can_be_active_at_once(self):
        config_module.write_default(self.root)
        path = os.path.join(self.root, config_module.CONFIG_NAME)
        data = json.load(open(path, encoding="utf-8"))
        data["language"] = ["en", "de"]
        json.dump(data, open(path, "w", encoding="utf-8"))

        cfg = config_module.load(self.root)
        self.assertEqual(cfg.languages, ("en", "de"))
        self.assertIn("the", cfg.stopwords)
        self.assertIn("welche", cfg.stopwords)

    def test_a_user_can_add_a_language_without_touching_the_code(self):
        folder = os.path.join(self.root, "kb", "languages")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "xx.json"), "w", encoding="utf-8") as handle:
            json.dump({"stopwords": ["zzq", "wwq"],
                       "translit": {"ǅ": "dz"}}, handle)

        path = os.path.join(self.root, config_module.CONFIG_NAME)
        data = json.load(open(path, encoding="utf-8"))
        data["language"] = ["xx"]
        json.dump(data, open(path, "w", encoding="utf-8"))

        cfg = config_module.load(self.root)
        self.assertIn("zzq", cfg.stopwords)
        self.assertEqual(cfg.slug("ǅungla"), "dzungla")

    def test_a_damaged_language_file_is_ignored(self):
        folder = os.path.join(self.root, "kb", "languages")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "yy.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        cfg = config_module.load(self.root)
        self.assertTrue(cfg.stopwords)          # still usable

    def test_a_language_file_can_override_decomposition(self):
        """Danish "å" must become "aa", not the "a" that stripping accents gives.

        Decomposition used to run first, which silently discarded every custom
        mapping for an accented Latin letter.
        """
        from docbase import languages
        self.assertEqual(
            languages.slugify("Rejseafregning på årsbasis", {"å": "aa", "ø": "oe"}),
            "rejseafregning-paa-aarsbasis")
        # …while unmapped accents still fall back to decomposition.
        self.assertEqual(languages.slugify("procédure", {"å": "aa"}), "procedure")


class TestTextAndArchives(BaseCase):
    """Documentation that is already text, and exports that arrive zipped.

    A tool that converts *into* markdown could not accept markdown, which left
    out the most common case there is: docs-as-code repositories, Obsidian
    vaults, Notion and GitBook exports.
    """

    def test_markdown_is_imported_as_is(self):
        self.drop("onboarding.md",
                  "# Onboarding\n\nNew hires get a laptop within three days.\n")
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["onboarding.md"])
        body = open(os.path.join(self.cfg.layout.path("text"), "onboarding.md"),
                    encoding="utf-8").read()
        self.assertIn("New hires get a laptop within three days.", body)
        self.assertIn("source_id:", body)

    def test_plain_text_gets_a_title_from_its_file_name(self):
        self.drop("expense_rules.txt", "Anything above 500 EUR needs approval.\n")
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["expense-rules.md"])
        body = open(os.path.join(self.cfg.layout.path("text"), "expense-rules.md"),
                    encoding="utf-8").read()
        self.assertIn("# Expense rules", body)

    def test_the_same_markdown_under_another_name_is_not_forked(self):
        text = "# Onboarding\n\nNew hires get a laptop within three days.\n"
        self.drop("a.md", text)
        self.sync(quiet=True)
        self.drop("b.md", text)
        self.sync(quiet=True)
        self.assertEqual(len(self.text_files()), 1)

    def test_existing_frontmatter_identity_is_respected(self):
        self.drop("policy.md",
                  '---\nsource_id: "kept-identity"\n---\n\n# Policy\n\nBody text here.\n')
        self.sync(quiet=True)
        body = open(os.path.join(self.cfg.layout.path("text"), "policy.md"),
                    encoding="utf-8").read()
        self.assertIn('source_id: "kept-identity"', body)

    def _zip(self, name, members):
        import zipfile
        path = os.path.join(self.root, name)
        with zipfile.ZipFile(path, "w") as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def test_an_archive_is_unpacked_and_imported(self):
        self._zip("space-export.zip", {
            "export/security.md": "# Security policy\n\nPasswords rotate every 90 days.\n",
            "export/incidents.md": "# Incident response\n\nSeverity one pages on-call.\n",
            "export/logo.png": "not a document",
        })
        report = self.sync(quiet=True)
        self.assertIn("security-policy.md", self.text_files())
        self.assertIn("incident-response.md", self.text_files())
        self.assertTrue(any("unpacked" in line for line in report["log"]))
        self.assertFalse(os.path.exists(os.path.join(self.root, "space-export.zip")))

    def test_archive_members_cannot_escape_the_base(self):
        self._zip("evil.zip", {"../../escaped.md": "# Escaped\n\nShould stay inside.\n"})
        self.sync(quiet=True)
        outside = os.path.abspath(os.path.join(self.root, "..", "..", "escaped.md"))
        self.assertFalse(os.path.exists(outside), "an archive member escaped the base")

    def test_a_damaged_archive_is_reported_not_fatal(self):
        with open(os.path.join(self.root, "broken.zip"), "wb") as handle:
            handle.write(b"definitely not a zip")
        self.drop("good.md", "# Good\n\nThis document must still import.\n")
        report = self.sync(quiet=True)
        self.assertIn("good.md", self.text_files())
        self.assertTrue(any("could not open" in line for line in report["log"]))


class TestVerify(BaseCase):
    """Extracts must keep saying what their source says.

    Staleness only notices that a document changed. It cannot tell whether the
    card was ever right — and a card is written by a model, from fragments,
    which is exactly where an invented number comes from.
    """

    SOURCE = ("# Travel booking\n\nDomestic trips must be requested at least five "
              "working days in advance, international trips at least fifteen. A "
              "non-refundable fare may be chosen only when it is cheaper by more "
              "than 30 percent.\n\nCancellation after booking incurs a 150 EUR fee.\n")

    def _card(self, body, source="travel-booking.md"):
        folder = os.path.join(self.cfg.layout.path("cards"), "travel")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "ask.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"---\nsource: kb/text/{source}\n---\n\n{body}")
        return path

    def _load(self):
        self.drop("travel.md", self.SOURCE)
        self.sync(quiet=True)

    def _run(self):
        from docbase import verify
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = verify.report(self.cfg)
        return code, out.getvalue()

    def test_a_faithful_card_passes(self):
        self._load()
        self._card("# Card\n\n- Domestic: 5 working days\n"
                   "- International: 15 working days\n"
                   "- Cancellation fee is 150 EUR\n")
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("present in its source", output)

    def test_an_invented_number_is_caught(self):
        self._load()
        self._card("# Card\n\n- Cancellation fee is 250 EUR\n")
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("250", output)

    def test_an_invented_quotation_is_caught(self):
        self._load()
        self._card("# Card\n\n- Approval from the `regional finance controller`\n")
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("regional finance controller", output)

    def test_a_real_quotation_passes(self):
        self._load()
        self._card("# Card\n\n- Allowed when `cheaper by more than 30 percent`\n")
        code, output = self._run()
        self.assertEqual(code, 0, output)

    def test_a_card_pointing_at_a_missing_document_is_reported(self):
        self._load()
        self._card("# Card\n\n- Anything\n", source="does-not-exist.md")
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("source is missing", output)

    def test_no_cards_is_not_a_failure(self):
        self._load()
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("No cards to verify", output)

    def test_list_numbering_is_not_treated_as_a_claim(self):
        self._load()
        self._card("# Card\n\n1. First step\n2. Second step\n3. Third step\n")
        code, output = self._run()
        self.assertEqual(code, 0, f"list markers were read as claims:\n{output}")


class TestChunkOverlap(BaseCase):
    """A rule and its exception must be able to land in one fragment.

    With disjoint fragments they fall on opposite sides of a boundary and
    neither half answers the question: the fragment naming a penalty no longer
    says what triggers it.
    """

    def test_a_rule_and_its_exception_survive_a_boundary(self):
        from docbase.search import split_chunks
        prefix = " ".join(f"Routine sentence {i} about the process." for i in range(1, 6))
        trailing = " ".join(f"Trailing sentence {i} about retention." for i in range(1, 6))
        text = (f"{prefix} A request above 750 EUR requires written approval. "
                f"This threshold is waived inside an approved annual plan. {trailing}")

        without = split_chunks(text, 260, 0)
        with_overlap = split_chunks(text, 260, 120)

        self.assertEqual(
            sum(1 for p in without if "750" in p and "annual plan" in p), 0,
            "the fixture no longer straddles a boundary; the test proves nothing")
        self.assertGreaterEqual(
            sum(1 for p in with_overlap if "750" in p and "annual plan" in p), 1,
            "overlap failed to keep the rule and its exception together")

    def test_adjacent_fragments_share_a_sentence(self):
        from docbase.search import split_chunks
        text = " ".join(f"Sentence {i} carries fact number {i}." for i in range(1, 13))
        parts = split_chunks(text, 150, 100)
        shared = sum(1 for a, b in zip(parts, parts[1:])
                     if (set(a.split(".")) & set(b.split("."))) - {""})
        self.assertGreater(shared, 0)

    def test_overlap_is_whole_sentences(self):
        from docbase.search import split_chunks
        text = " ".join(f"Sentence {i} carries fact number {i}." for i in range(1, 13))
        for part in split_chunks(text, 150, 100):
            self.assertFalse(part.startswith("carries"),
                             "a fragment began mid-sentence")

    def test_changing_chunk_settings_invalidates_the_cache(self):
        """Otherwise a config change appears to do nothing at all."""
        from dataclasses import replace as dc_replace
        for i in range(6):
            self.drop(f"doc-{i}.html", page(
                700 + i, f"Handbook {i}",
                " ".join(f"Sentence {n} about approval thresholds and limits."
                         for n in range(1, 20))))
        self.sync(quiet=True)

        first = Index(self.cfg).build()
        self.assertFalse(first.loaded_from_cache)

        wider = config_module.Config(
            layout=self.cfg.layout, importer=self.cfg.importer,
            languages=self.cfg.languages,
            search=dc_replace(self.cfg.search, chunk_chars=120))
        second = Index(wider).build()
        self.assertFalse(second.loaded_from_cache,
                         "the cache was reused after the chunk settings changed")


class TestDocx(BaseCase):
    """Word .docx, parsed with the standard library.

    A .docx is a zip holding XML, so no third-party package is needed — which
    matters, because every added dependency is another thing that can fail to
    install on the machine of someone who did not want to install anything.
    """

    NS_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

    def _docx(self, name="vendor.docx"):
        import zipfile
        document = f'''<?xml version="1.0"?>
<w:document {self.NS_W} {self.NS_R}><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Vendor onboarding</w:t></w:r></w:p>
<w:p><w:r><w:t>Screened before the first </w:t></w:r>
     <w:r><w:rPr><w:b/></w:rPr><w:t>purchase order</w:t></w:r>
     <w:r><w:t> is raised.</w:t></w:r></w:p>
<w:tbl>
 <w:tr><w:tc><w:p><w:r><w:t>Spend</w:t></w:r></w:p></w:tc>
       <w:tc><w:p><w:r><w:t>Approver</w:t></w:r></w:p></w:tc></w:tr>
 <w:tr><w:tc><w:p><w:r><w:t>above 5000 EUR</w:t></w:r></w:p></w:tc>
       <w:tc><w:p><w:r><w:t>Finance director</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Check sanctions lists</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Against the consolidated list</w:t></w:r></w:p>
<w:p><w:hyperlink r:id="rId1"><w:r><w:t>Expense reports</w:t></w:r></w:hyperlink></w:p>
</w:body></w:document>'''
        rels = '''<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
 Target="https://wiki.example.com/pages/viewpage.action?pageId=1001" TargetMode="External"/>
</Relationships>'''
        path = os.path.join(self.root, name)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)
            archive.writestr("word/_rels/document.xml.rels", rels)
        return path

    def _imported(self):
        self._docx()
        self.sync(quiet=True)
        return open(os.path.join(self.cfg.layout.path("text"), self.text_files()[0]),
                    encoding="utf-8").read()

    def test_headings_come_from_styles(self):
        self.assertIn("# Vendor onboarding", self._imported())

    def test_emphasis_survives(self):
        self.assertIn("**purchase order**", self._imported())

    def test_a_table_stays_a_table(self):
        body = self._imported()
        self.assertIn("| Spend | Approver |", body)
        # Rows separated by blank lines are not a table any more.
        self.assertIn("| Spend | Approver |\n|---|---|", body)
        self.assertIn("| above 5000 EUR | Finance director |", body)

    def test_list_items_stay_together(self):
        self.assertIn("- Check sanctions lists", self._imported())

    def test_hyperlinks_are_resolved_through_the_relationship_file(self):
        body = self._imported()
        self.assertIn("[Expense reports](https://wiki.example.com", body)

    def test_a_table_cell_is_searchable(self):
        self._docx()
        self.sync(quiet=True)
        hits = Index(self.cfg).build().search("who approves spend above 5000")
        self.assertTrue(hits)
        self.assertIn("Finance director", hits[0][4])


class TestMcpServer(BaseCase):
    """The MCP surface, exercised as a client would drive it.

    Implemented against the protocol directly rather than through an SDK, so
    the handshake and the framing are ours to get right — and worth testing
    rather than assuming.
    """

    SOURCE = ("# Travel booking\n\nDomestic trips need five working days of notice, "
              "international fifteen. A non-refundable fare is allowed only when "
              "cheaper by more than 30 percent.\n")

    def _load(self):
        self.drop("travel.md", self.SOURCE)
        self.sync(quiet=True)

    def _ask(self, message):
        from docbase import mcp
        return mcp.handle(message, self.cfg)

    def _call(self, name, arguments=None):
        reply = self._ask({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments or {}}})
        return reply["result"]["content"][0]["text"]

    def test_initialize_echoes_a_supported_protocol(self):
        reply = self._ask({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(reply["result"]["serverInfo"]["name"], "docbase")

    def test_an_unknown_protocol_falls_back_rather_than_failing(self):
        reply = self._ask({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "1999-01-01"}})
        self.assertIn("result", reply)
        self.assertIn(reply["result"]["protocolVersion"], ("2024-11-05",))

    def test_a_notification_gets_no_reply(self):
        self.assertIsNone(self._ask({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}))

    def test_tools_are_advertised_with_schemas(self):
        reply = self._ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"]: t for t in reply["result"]["tools"]}
        self.assertIn("search_documentation", tools)
        self.assertEqual(tools["search_documentation"]["inputSchema"]["required"],
                         ["query"])

    def test_search_returns_the_fragment(self):
        self._load()
        text = self._call("search_documentation",
                          {"query": "non-refundable fare cheaper percent"})
        self.assertIn("30 percent", text)

    def test_read_section_returns_lines(self):
        self._load()
        name = self.text_files()[0]
        text = self._call("read_section", {"file": name, "line": 1, "lines": 5})
        self.assertTrue(text.strip())

    def test_read_section_names_the_problem_when_the_file_is_wrong(self):
        self._load()
        self.assertIn("No document", self._call("read_section", {"file": "nope.md"}))

    def test_an_unknown_tool_is_a_protocol_error(self):
        reply = self._ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "does_not_exist"}})
        self.assertEqual(reply["error"]["code"], -32602)

    def test_a_failing_tool_is_reported_not_fatal(self):
        """A broken tool must come back as a result the model can react to."""
        from docbase import mcp
        original = mcp._list_documents
        mcp._list_documents = lambda cfg, arguments=None: (
            (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            reply = self._ask({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "list_documents"}})
        finally:
            mcp._list_documents = original
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("boom", reply["result"]["content"][0]["text"])

    def test_the_stdio_loop_survives_malformed_json(self):
        from docbase import mcp
        out = io.StringIO()
        mcp.serve(self.cfg, stdin=io.StringIO(
            'not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), stdout=out)
        replies = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 1)


class TestAuditFindings(BaseCase):
    """Problems found by reviewing the whole project rather than a diff."""

    def test_language_files_are_read_once_not_per_fragment(self):
        """Resolving them on every access meant a directory check per indexed
        fragment, and an actual file read for anyone who used the feature —
        2.4x slower for exactly the people the feature exists for."""
        from docbase import languages

        folder = os.path.join(self.root, "kb", "languages")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "da.json"), "w", encoding="utf-8") as handle:
            json.dump({"stopwords": ["og", "eller"]}, handle)

        for i in range(4):
            self.drop(f"doc-{i}.html", page(800 + i, f"Handbook {i}",
                                            " ".join(f"Sentence {n} about limits."
                                                     for n in range(1, 30))))
        self.sync(quiet=True)

        calls = []
        original = languages.load_custom
        languages.load_custom = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        try:
            cfg = config_module.load(self.root)
            index = Index(cfg).build()
        finally:
            languages.load_custom = original

        self.assertTrue(index.chunks)
        self.assertLessEqual(len(calls), 1,
                             "language files were re-read while indexing")

    def test_history_does_not_grow_without_bound(self):
        from docbase import sync as sync_module
        for version in range(sync_module.HISTORY_LIMIT + 4):
            self.drop(f"v{version}.html", page(
                900, "Travel booking", f"Notice period is {version} working days."))
            self.sync(quiet=True)

        folder = os.path.join(self.cfg.layout.path("history"), "travel-booking")
        diffs = [f for f in os.listdir(folder) if f.endswith(".diff")]
        self.assertLessEqual(len(diffs), sync_module.HISTORY_LIMIT)
        self.assertTrue(diffs, "history was pruned away entirely")

    def test_verify_clears_a_stale_marker_when_the_card_still_holds(self):
        """Otherwise the marker stays up forever and people learn to ignore it."""
        from docbase import verify

        self.drop("travel.html", page(901, "Travel booking",
                                      "Cancellation incurs a 150 EUR fee."))
        self.sync(quiet=True)

        folder = os.path.join(self.cfg.layout.path("cards"), "travel")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "ask.md"), "w", encoding="utf-8") as handle:
            handle.write("---\nsource: kb/text/travel-booking.md\n---\n\n"
                         "# Card\n\n- Cancellation fee is 150 EUR\n")

        # The source changes, but the fee does not.
        self.drop("travel-v2.html", page(
            901, "Travel booking",
            "Cancellation incurs a 150 EUR fee. Requests go through the portal."))
        self.sync(quiet=True)
        self.assertTrue(os.path.isfile(os.path.join(folder, "_stale")))

        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = verify.report(self.cfg)
        self.assertEqual(code, 0, out.getvalue())
        self.assertFalse(os.path.isfile(os.path.join(folder, "_stale")))

    def test_verify_keeps_the_marker_when_a_claim_broke(self):
        from docbase import verify

        self.drop("travel.html", page(902, "Travel booking",
                                      "Cancellation incurs a 150 EUR fee."))
        self.sync(quiet=True)
        folder = os.path.join(self.cfg.layout.path("cards"), "travel")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "ask.md"), "w", encoding="utf-8") as handle:
            handle.write("---\nsource: kb/text/travel-booking.md\n---\n\n"
                         "# Card\n\n- Cancellation fee is 150 EUR\n")

        self.drop("travel-v2.html", page(902, "Travel booking",
                                         "Cancellation incurs a 400 EUR fee."))
        self.sync(quiet=True)

        with contextlib.redirect_stdout(io.StringIO()):
            code = verify.report(self.cfg)
        self.assertEqual(code, 1)
        self.assertTrue(os.path.isfile(os.path.join(folder, "_stale")))

    def test_linking_does_not_leak_state_between_calls(self):
        """Two bases with different wiki hosts in one process must not
        interfere; a long-running MCP server is exactly such a process."""
        from docbase import link

        first = link.page_pattern(("wiki.one.example",))
        second = link.page_pattern(("wiki.two.example",))
        url = "https://wiki.one.example/pages/viewpage.action?pageId=5"
        self.assertIsNotNone(link.page_id_of(url, first))
        self.assertIsNone(link.page_id_of(url, second))


class TestHostileInput(BaseCase):
    """Degenerate and hostile files, because a base is fed by whatever a
    person happened to export."""

    def _docx(self, name, document, rels=True):
        import zipfile
        path = os.path.join(self.root, name)
        with zipfile.ZipFile(path, "w") as archive:
            if document is not None:
                archive.writestr("word/document.xml", document)
            else:
                archive.writestr("readme.txt", "no document inside")
        return path

    def test_malformed_docx_xml_is_reported_not_fatal(self):
        self._docx("broken.docx", "<not closed")
        self.drop("good.md", "# Good\n\nThis must still import.\n")
        report = self.sync(quiet=True)
        self.assertEqual(len(report["failed"]), 1)
        self.assertIn("good.md", self.text_files())

    def test_docx_without_a_document_part_is_reported(self):
        self._docx("empty.docx", None)
        report = self.sync(quiet=True)
        self.assertEqual(len(report["failed"]), 1)

    def test_a_symlink_in_an_archive_does_not_become_a_symlink(self):
        import zipfile
        path = os.path.join(self.root, "evil.zip")
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("evil.md")
            info.external_attr = (0xA000 | 0o777) << 16      # symlink bit
            archive.writestr(info, "/etc/passwd")
        self.sync(quiet=True)
        for folder in (self.root, self.cfg.layout.path("originals")):
            candidate = os.path.join(folder, "evil.md")
            if os.path.exists(candidate):
                self.assertFalse(os.path.islink(candidate),
                                 "an archive entry was restored as a symlink")

    def test_hundreds_of_duplicates_do_not_flood_the_report(self):
        """The report is read by an agent that pays per line of it."""
        import zipfile
        path = os.path.join(self.root, "many.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for i in range(200):
                archive.writestr(f"doc-{i}.md", "# Doc\n\n" + "filler " * 100)
        report = self.sync(quiet=True)
        from docbase import sync as sync_module
        self.assertLessEqual(len(report["log"]), sync_module.LOG_LIMIT + 2)
        self.assertTrue(any("duplicate" in line for line in report["log"]))

    def test_force_does_not_rewrite_identical_output(self):
        """Rewriting would change the mtime and discard the search index."""
        self.drop("a.md", "# Policy\n\nThe rule is stable.\n")
        self.sync(quiet=True)
        target = os.path.join(self.cfg.layout.path("text"), self.text_files()[0])
        before = os.path.getmtime(target)
        self.sync(quiet=True, force=True)
        self.assertEqual(before, os.path.getmtime(target))

    def test_empty_files_do_not_crash_the_import(self):
        self.drop("empty.md", "")
        self.drop("empty.html", "")
        report = self.sync(quiet=True)
        self.assertEqual(report["failed"], [])


class TestRealDocumentShapes(BaseCase):
    """Behaviour learned from real published PDFs rather than fixtures.

    Every case here corresponds to something that only showed up when the tool
    met documents written by someone else: NIST special publications, whose
    body text is larger than the export these heuristics were first tuned on.
    """

    def test_heading_levels_are_relative_to_the_document(self):
        """Absolute size thresholds were tuned to a 9.9pt export. A document
        set in 12pt then had every line above the threshold, so almost every
        paragraph became a heading."""
        from docbase.importers.pdf import heading_prefix

        def word(size, bold=False):
            return {"size": size, "fontname": "Arial-BoldMT" if bold else "ArialMT",
                    "text": "Section"}

        # Body text must never be a heading, whatever its absolute size.
        self.assertEqual(heading_prefix([word(12.0)], body=12.0), "")
        self.assertEqual(heading_prefix([word(9.9)], body=9.9), "")
        # A title well above body size is.
        self.assertEqual(heading_prefix([word(20.0)], body=12.0), "# ")
        self.assertEqual(heading_prefix([word(20.0)], body=9.9), "# ")

    def test_bold_at_body_size_is_recognised_as_a_heading(self):
        """Technical reports and anything written in Word mark sections with
        bold at body size; size alone cannot see them."""
        from docbase.importers.pdf import heading_prefix

        bold_line = [{"size": 12.0, "fontname": "Arial-BoldMT", "text": t}
                     for t in ("2", "Zero", "Trust", "Basics")]
        self.assertEqual(heading_prefix(bold_line, body=12.0), "### ")

        # …but a bold sentence is emphasis, not a heading.
        sentence = [{"size": 12.0, "fontname": "Arial-BoldMT", "text": t}
                    for t in ("This", "is", "a", "whole", "bold", "sentence.")]
        self.assertEqual(heading_prefix(sentence, body=12.0), "")

    def test_a_declared_title_beats_the_first_heading(self):
        """A cover page may carry a withdrawal banner, or split the real title
        across three lines that each become a heading."""
        from docbase.sync import document_title

        markdown = ('---\nsource_id: "x"\ntitle: "Digital Identity Guidelines"\n---\n\n'
                    "# Withdrawn NIST Technical Series Publication\n")
        self.assertEqual(document_title(markdown, "file.pdf"),
                         "Digital Identity Guidelines")

        without = '---\nsource_id: "x"\n---\n\n# Actual Heading\n'
        self.assertEqual(document_title(without, "file.pdf"), "Actual Heading")

    def test_eval_markers_survive_markdown_emphasis(self):
        """Markers come from fragments already stripped of emphasis, so a
        literal search against the raw file silently dropped every passage
        containing bold or a link."""
        from docbase import evaluate

        self.drop("policy.md",
                  "# Policy\n\nThe **cancellation** fee is 150 EUR for a "
                  "[late](https://example.com/late) request.\n")
        self.sync(quiet=True)
        name = self.text_files()[0]
        self.assertTrue(evaluate._marker_present(
            self.cfg, "The cancellation fee is 150 EUR for a late request.", name))


class TestLinkResolution(BaseCase):
    """Cross-document linking beyond one wiki's URL shape.

    It used to understand exactly one thing — a page id — so a standards body
    linking to a landing page, a docs site linking to a file, or a handbook
    citing a document number all went unresolved, and the base knew nothing
    about how its own documents relate.
    """

    DOCS = {
        "zero-trust-architecture.md": {"source_file": "nist-sp-800-207.pdf",
                                       "title": "Zero Trust Architecture"},
        "digital-identity-guidelines.md": {"source_file": "nist-sp-800-63-3.pdf",
                                           "title": "Digital Identity Guidelines"},
        "billing.md": {"page_id": "4242",
                       "url": "https://wiki.example.com/pages/viewpage.action?pageId=4242"},
    }

    def _resolver(self, documents=None):
        from docbase.resolve import Resolver
        return Resolver(documents or self.DOCS)

    def _pattern(self):
        from docbase.link import page_pattern
        return page_pattern(("wiki.example.com",))

    def test_resolves_a_wiki_page_id(self):
        self.assertEqual(
            self._resolver().resolve(
                "https://wiki.example.com/pages/viewpage.action?pageId=4242",
                self._pattern()),
            "billing.md")

    def test_resolves_a_direct_file_link(self):
        self.assertEqual(
            self._resolver().resolve(
                "https://nvlpubs.nist.gov/nistpubs/sp/NIST.SP.800-207.pdf"),
            "zero-trust-architecture.md")

    def test_resolves_a_landing_page_by_document_number(self):
        self.assertEqual(
            self._resolver().resolve(
                "https://www.nist.gov/iam/nist-special-publication-800-63-digital-identity"),
            "digital-identity-guidelines.md")

    def test_a_revision_answers_a_reference_to_the_family(self):
        self.assertEqual(self._resolver().resolve("https://doi.org/10.6028/NIST.SP.800-63"),
                         "digital-identity-guidelines.md")

    def test_a_letter_suffix_is_a_different_document(self):
        """800-63A is another volume, not a revision. Treating it as the
        parent would send the reader to the wrong text."""
        self.assertIsNone(
            self._resolver().resolve("https://doi.org/10.6028/NIST.SP.800-63A"))

    def test_an_ambiguous_identifier_resolves_to_nothing(self):
        documents = {
            "one.md": {"source_file": "policy-12-3.pdf", "title": "One"},
            "two.md": {"source_file": "guide-12-3.pdf", "title": "Two"},
        }
        self.assertIsNone(self._resolver(documents).resolve("https://x.example/12-3"))

    def test_dates_and_doi_prefixes_are_not_identifiers(self):
        from docbase.resolve import identifiers
        self.assertEqual(identifiers("annual-report-2024"), set())
        self.assertNotIn("10-6028", identifiers("NIST.SP.800-207"))

    def test_a_document_never_links_to_itself(self):
        resolver = self._resolver()
        self.assertIsNone(resolver.resolve(
            "https://nvlpubs.nist.gov/NIST.SP.800-207.pdf",
            exclude="zero-trust-architecture.md"))

    def test_missing_is_only_tracked_for_internal_hosts(self):
        """Otherwise the list fills with DOI prefixes and dates, and stops
        being read."""
        link = ('<p>See <a href="https://doi.org/10.1109/MITP.2016.84">a paper</a> '
                'and <a href="https://wiki.example.com/pages/viewpage.action?pageId=77">'
                'a policy</a>.</p>')
        self.drop("a.html", page(1, "Handbook", "Body text.", link))
        report = self.sync(quiet=True)
        missing = report["link"]["missing"]
        self.assertIn("77", missing)
        self.assertNotIn("10-1109", missing)


class TestUnderlinedHeadings(BaseCase):
    """reStructuredText and plain text underline their headings.

    Passing such a file through untouched — which is what "supporting .rst"
    amounted to — loses its structure entirely: a 49 KB style guide arrived
    with three headings, so the map showed nothing and retrieval had no
    section titles to weigh.
    """

    RST = """PEP: 8
Title: Style Guide for Python Code
Author: Guido van Rossum

Introduction
============

This document gives coding conventions.

Code Lay-out
============

Indentation
-----------

Use four spaces per indentation level.

Maximum Line Length
-------------------

Limit all lines to a maximum of 79 characters.
"""

    def test_underlined_headings_become_markdown(self):
        from docbase.importers.text import underlined_headings
        converted = underlined_headings(self.RST)
        self.assertIn("# Introduction", converted)
        self.assertIn("## Indentation", converted)
        self.assertNotIn("=====", converted)

    def test_levels_follow_first_appearance(self):
        from docbase.importers.text import underlined_headings
        converted = underlined_headings(self.RST)
        self.assertIn("# Code Lay-out", converted)
        self.assertIn("## Maximum Line Length", converted)

    def test_a_declared_title_is_used(self):
        self.drop("pep-0008.rst", self.RST)
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["style-guide-for-python-code.md"])

    def test_sections_become_findable(self):
        self.drop("pep-0008.rst", self.RST)
        self.sync(quiet=True)
        hits = Index(self.cfg).build().search("maximum line length characters limit")
        self.assertTrue(hits)
        self.assertIn("Maximum Line Length", hits[0][3])

    def test_a_row_of_dashes_inside_prose_is_not_a_heading(self):
        from docbase.importers.text import underlined_headings
        text = "Some prose here.\n\n-------\n\nMore prose.\n"
        self.assertNotIn("# Some prose here.", underlined_headings(text))


class TestRealWordDocument(BaseCase):
    """Behaviour learned from a document Word actually produced.

    The hand-built fixture used earlier had idealised XML. A real file keeps
    list nesting in one part, list markers in another, and internal
    cross-references that carry no URL at all.
    """

    NS_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

    def _docx(self, name="lists.docx", numbering=True):
        import zipfile
        document = f'''<?xml version="1.0"?>
<w:document {self.NS_W} {self.NS_R}><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Procedure</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr></w:pPr>
     <w:r><w:t>Open the request</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="7"/></w:numPr></w:pPr>
     <w:r><w:t>Attach the receipt</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="2"/><w:numId w:val="7"/></w:numPr></w:pPr>
     <w:r><w:t>As PDF only</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="9"/></w:numPr></w:pPr>
     <w:r><w:t>A plain bullet</w:t></w:r></w:p>
<w:p><w:hyperlink w:anchor="_Toc123"><w:r><w:t>See above</w:t></w:r></w:hyperlink></w:p>
</w:body></w:document>'''
        numbering_xml = f'''<?xml version="1.0"?>
<w:numbering {self.NS_W}>
<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
<w:num w:numId="7"><w:abstractNumId w:val="1"/></w:num>
<w:num w:numId="9"><w:abstractNumId w:val="2"/></w:num>
</w:numbering>'''
        path = os.path.join(self.root, name)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)
            if numbering:
                archive.writestr("word/numbering.xml", numbering_xml)
        return path

    def _body(self, **kwargs):
        self._docx(**kwargs)
        self.sync(quiet=True)
        return open(os.path.join(self.cfg.layout.path("text"), self.text_files()[0]),
                    encoding="utf-8").read()

    def test_list_nesting_is_preserved(self):
        body = self._body()
        self.assertIn("1. Open the request", body)
        self.assertIn("  1. Attach the receipt", body)
        self.assertIn("    1. As PDF only", body)

    def test_numbered_and_bulleted_lists_are_distinguished(self):
        """In a procedure that is the difference between steps and options."""
        body = self._body()
        self.assertIn("1. Open the request", body)
        self.assertIn("- A plain bullet", body)

    def test_an_internal_anchor_becomes_plain_text(self):
        """A cross-reference inside the document has no URL to link to."""
        body = self._body()
        self.assertIn("See above", body)
        self.assertNotIn("](_Toc123)", body)

    def test_a_document_without_numbering_still_imports(self):
        body = self._body(numbering=False)
        self.assertIn("Open the request", body)


class TestRealWikiExport(BaseCase):
    """Behaviour learned from pages served by an actual Confluence.

    The fixture used until now was idealised in the two ways that mattered
    most: it linked with absolute URLs and had no separate page title.
    """

    PAGE = '''<!DOCTYPE html><html><head>
<meta name="ajs-page-id" content="{page_id}">
<meta name="ajs-page-title" content="{title}">
<title>{title} - Apache Kafka - Apache Software Foundation</title></head><body>
<div id="main-content">
<p>{body}</p>
{extra}
</div></body></html>'''

    def _wiki(self, name, page_id, title, body, extra=""):
        self.drop(name, self.PAGE.format(page_id=page_id, title=title,
                                         body=body, extra=extra))

    def test_the_page_title_is_used_not_the_browser_tab_title(self):
        """A tab title carries the site and space name too."""
        self._wiki("a.html", 900, "Contributing Code Changes",
                   "Open a pull request against trunk.")
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["contributing-code-changes.md"])

    def test_a_relative_link_resolves(self):
        """More than half the links on a real wiki page are relative."""
        link = ('<p>See <a href="/confluence/spaces/KAFKA/pages/901/System+Tools">'
                'System Tools</a>.</p>')
        self._wiki("a.html", 900, "Contributing Code Changes",
                   "Open a pull request.", link)
        self._wiki("b.html", 901, "System Tools", "The offset checker prints lag.")
        self.sync(quiet=True)

        body = open(os.path.join(self.cfg.layout.path("text"),
                                 "contributing-code-changes.md"),
                    encoding="utf-8").read().split("## Links found")[0]
        self.assertIn("(system-tools.md)", body)

    def test_a_relative_link_to_a_missing_page_is_named_readably(self):
        """A number scraped out of a title makes a list nobody can act on."""
        link = ('<p>See <a href="/confluence/display/KAFKA/KIP-500+Replace+ZooKeeper">'
                'KIP-500</a>.</p>')
        self._wiki("a.html", 900, "Contributing Code Changes", "Body.", link)
        report = self.sync(quiet=True)
        keys = list(report["link"]["missing"])
        self.assertTrue(any("kip-500" in k for k in keys), keys)

    def test_the_missing_list_in_the_map_is_capped(self):
        """A wiki index page can reference a thousand others."""
        from docbase import link as link_module
        links = "".join(
            f'<p><a href="/confluence/spaces/KAFKA/pages/{2000 + i}/Page+{i}">P{i}</a></p>'
            for i in range(link_module.GRAPH_MISSING_LIMIT + 25))
        self._wiki("index.html", 900, "Index", "Many links.", links)
        self.sync(quiet=True)

        graph = open(os.path.join(self.root, "kb", "graph.md"), encoding="utf-8").read()
        rows = [l for l in graph.splitlines() if l.startswith("| `")]
        self.assertLessEqual(len(rows), link_module.GRAPH_MISSING_LIMIT)
        self.assertIn("references in total", graph)


class TestRealTextArchive(BaseCase):
    """Behaviour learned from a published documentation archive.

    537 plain-text manuals, exported by the project itself. A third of them
    arrived without their own title, because the heading rules had been
    written against a fixture whose titles were plain words at the left
    margin — real ones quote an identifier, carry a chapter number, and sit
    a few lines above code that looks exactly like an underline.
    """

    def test_a_title_that_opens_with_a_quote_is_still_a_title(self):
        """`"csv" --- CSV File Reading and Writing` is how a module is named."""
        from docbase.importers.text import underlined_headings
        converted = underlined_headings('"csv" --- CSV File Reading\n'
                                        '**************************\n')
        self.assertIn('# "csv" --- CSV File Reading', converted)

    def test_a_numbered_chapter_title_is_still_a_title(self):
        from docbase.importers.text import underlined_headings
        converted = underlined_headings("1. Extending Python with C\n"
                                        "**************************\n")
        self.assertIn("# 1. Extending Python with C", converted)

    def test_an_indented_rule_is_code_not_an_underline(self):
        """A docstring's closing quotes under example output are not a heading.

        This is how the doctest manual came to be titled "120": the number
        was the output of a sample call, and the line below it closed the
        docstring with three quotation marks.
        """
        from docbase.importers.text import underlined_headings
        converted = underlined_headings('   >>> factorial(5)\n'
                                        '   120\n'
                                        '   """\n')
        self.assertNotIn("#", converted)

    def test_an_overline_does_not_survive_as_text(self):
        from docbase.importers.text import underlined_headings
        converted = underlined_headings("=========\nThe Title\n=========\n")
        self.assertIn("# The Title", converted)
        self.assertNotIn("=====", converted)

    def test_the_document_is_named_after_its_own_title(self):
        self.drop("cmdline-1.txt", '"csv" --- CSV File Reading\n'
                                   '**************************\n\n'
                                   'The module reads tabular data.\n\n'
                                   'Examples\n========\n\nRead a file.\n')
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["csv-csv-file-reading.md"])


class TestSuspectConversions(BaseCase):
    """A file that converts to nothing must not enter the base.

    Exporting a page you are not logged in for returns the login page, and it
    imports exactly as cleanly as the real thing. The base then answers from a
    document that says nothing — the one failure this design exists to stop.
    """

    def test_a_large_file_that_converts_to_nothing_is_refused(self):
        self.drop("export.html",
                  "<html><head><title>Log in</title></head><body>"
                  "<div id=\"sidebar-container\"></div>"
                  + "<!-- %s -->" % ("padding " * 2000) +
                  "</body></html>")
        report = self.sync(quiet=True)
        self.assertEqual(self.text_files(), [])
        self.assertEqual(len(report["failed"]), 1)
        self.assertIn("almost no text", report["failed"][0][1])

    def test_a_genuinely_short_document_still_imports(self):
        self.drop("note.md", "# Office hours\n\nWe answer between 9 and 17.\n")
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["office-hours.md"])

    def test_the_refusal_is_remembered_rather_than_retried(self):
        self.drop("export.html", "<html><body><div></div>"
                  + "<!-- %s -->" % ("padding " * 2000) + "</body></html>")
        self.sync(quiet=True)
        report = self.sync(quiet=True)
        self.assertEqual(report["skipped"], 1)


class TestNestedHtmlLists(BaseCase):
    """A wiki's table of contents is a list inside a list.

    Rendered as part of its parent item it collapses into one bullet: a real
    page's contents arrived as a single line carrying eight links.
    """

    PAGE = ('<html><body><div id="main-content"><ul>'
            '<li><a href="#a">How to set up a mirror</a></li>'
            '<li><a href="#b">Configuration</a>'
            '<ul><li><a href="#c">Whitelist</a></li>'
            '<li><a href="#d">Producer timeout</a></li></ul></li>'
            '</ul><p>Mirroring keeps a replica of a cluster.</p>'
            '</div></body></html>')

    def test_a_nested_list_is_indented_not_flattened(self):
        self.drop("mirror.html", self.PAGE)
        self.sync(quiet=True)
        text = open(os.path.join(self.cfg.layout.path("text"),
                                 self.text_files()[0]), encoding="utf-8").read()
        self.assertIn("- [Configuration](#b)", text)
        self.assertIn("  - [Whitelist](#c)", text)
        self.assertIn("  - [Producer timeout](#d)", text)

    def test_the_parent_item_keeps_only_its_own_text(self):
        self.drop("mirror.html", self.PAGE)
        self.sync(quiet=True)
        text = open(os.path.join(self.cfg.layout.path("text"),
                                 self.text_files()[0]), encoding="utf-8").read()
        for line in text.splitlines():
            self.assertLessEqual(line.count("]("), 1,
                                 "a sub-list was folded into its parent item")


class TestIndexStoresPositionsNotProse(BaseCase):
    """The cache was twice the size of the base it indexed.

    Keeping every fragment's text alongside the words it matches duplicates
    the whole corpus on disk for no gain: only the handful of fragments
    actually shown need their prose, and one document re-splits in
    milliseconds.
    """

    def _base(self):
        self.drop("guide.md", "# Refunds\n\n"
                  + "A refund is issued within 14 days of the request. " * 12
                  + "\n\n## Exceptions\n\nGift cards are never refunded.\n")
        self.sync(quiet=True)
        return Index(self.cfg)

    def test_the_cache_does_not_hold_the_documents_text(self):
        index = self._base().build()
        self.assertTrue(index.chunks)
        raw = open(index.cache_path(), encoding="utf-8").read()
        self.assertNotIn("Gift cards are never refunded", raw)

    def test_a_result_read_from_the_cache_still_carries_its_text(self):
        self._base().build()                      # writes the cache
        fresh = Index(self.cfg)
        hits = fresh.search("gift cards refunded")
        self.assertTrue(fresh.loaded_from_cache)
        self.assertTrue(hits)
        self.assertIn("Gift cards are never refunded", hits[0][4])

    def test_the_same_query_answers_the_same_either_way(self):
        built = Index(self.cfg)
        self._base()
        built.build()
        from_cache = Index(self.cfg)
        self.assertEqual([h[1:] for h in built.search("refund within days")],
                         [h[1:] for h in from_cache.search("refund within days")])


class TestHtmlCommentsAreNotContent(BaseCase):
    """A comment is markup, not prose.

    Emitted as text it becomes searchable: an analytics tag, a conditional
    comment or a commented-out draft answers a question it was never part of.
    """

    def test_a_comment_does_not_reach_the_document(self):
        self.drop("page.html",
                  '<html><body><div id="main-content">'
                  '<!-- internal note: this price is out of date -->'
                  '<p>A refund takes 14 days.</p></div></body></html>')
        self.sync(quiet=True)
        text = open(os.path.join(self.cfg.layout.path("text"),
                                 self.text_files()[0]), encoding="utf-8").read()
        self.assertIn("A refund takes 14 days.", text)
        self.assertNotIn("out of date", text)

    def test_a_comment_is_not_searchable(self):
        self.drop("page.html",
                  '<html><body><div id="main-content">'
                  '<!-- helicopter transfer costs 900 euro -->'
                  '<p>Ground transfers are arranged on request.</p>'
                  '</div></body></html>')
        self.sync(quiet=True)
        self.assertEqual(Index(self.cfg).search("helicopter"), [])


class TestMapIsProgressive(BaseCase):
    """The map is read by an agent that pays for every line of it.

    Five hundred documents with their sections came to fifty-seven thousand
    tokens through the MCP server — more than the base exists to save, and
    more than most context windows hold.
    """

    def _many(self, count):
        for n in range(count):
            self.drop(f"doc{n}.md",
                      f"# Document {n}\n\n## First part\n\nText about topic {n}.\n"
                      f"\n## Second part\n\nMore about topic {n}.\n")
        self.sync(quiet=True)
        return Index(self.cfg).build()

    def test_a_small_base_shows_its_sections(self):
        from docbase.search import outline
        lines = outline(self._many(3))
        self.assertTrue(any("First part" in line for line in lines))

    def test_a_large_base_names_documents_and_stops(self):
        from docbase.search import outline, MAP_DOCUMENT_LIMIT
        lines = outline(self._many(MAP_DOCUMENT_LIMIT + 5))
        self.assertFalse(any("First part" in line for line in lines))
        self.assertTrue(any("Name one to see its sections" in line for line in lines))

    def test_naming_a_document_opens_it(self):
        from docbase.search import outline, MAP_DOCUMENT_LIMIT
        index = self._many(MAP_DOCUMENT_LIMIT + 5)
        lines = outline(index, "document-7.md")
        self.assertTrue(any("First part" in line for line in lines))
        self.assertFalse(any("document-8.md" in line for line in lines))

    def test_a_name_that_matches_nothing_says_so(self):
        from docbase.search import outline
        lines = outline(self._many(3), "no-such-document")
        self.assertEqual(len(lines), 1)
        self.assertIn("No document matches", lines[0])

    def test_the_cap_is_honoured_and_declared(self):
        from docbase.search import outline
        lines = outline(self._many(10), cap=4)
        self.assertTrue(any("6 more documents" in line for line in lines))

    def test_a_document_with_many_sections_is_trimmed(self):
        from docbase.search import outline, MAP_SECTION_LIMIT
        body = "\n".join(f"## Section {n}\n\nText {n}.\n"
                         for n in range(MAP_SECTION_LIMIT + 10))
        self.drop("big.md", "# Big\n\n" + body)
        self.sync(quiet=True)
        lines = outline(Index(self.cfg).build())
        self.assertTrue(any("more sections" in line for line in lines))

    def test_status_does_not_name_every_document(self):
        from docbase import status
        self._many(60)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            status.report(self.cfg)
        printed = out.getvalue()
        self.assertIn("Documents: 60", printed)
        self.assertIn("more (docbase map lists them all)", printed)
        self.assertLess(len(printed.splitlines()), 50)

    def test_list_documents_over_mcp_takes_a_name(self):
        from docbase import mcp
        self._many(3)
        answer = mcp.call_tool(self.cfg, "list_documents",
                               {"document": "document-1.md"})
        self.assertIn("document-1.md", answer)
        self.assertNotIn("document-2.md", answer)


class TestVerifyOnRealCards(BaseCase):
    """What the check was worth when a real card was put through it.

    Against a 280 KB wiki page, three deliberate corruptions — a changed
    number, an altered quotation and an invented rule — all passed. The
    number check compared against the document's digits with the separators
    taken out, which in that page is a string of nineteen thousand digits:
    "96" is inside it whether or not the document ever says ninety-six.
    """

    SOURCE = ("# Proposals\n\nThe criteria for acceptance is lazy majority. "
              "The vote should remain open for at least 72 hours.\n\n"
              "1. Take the next available number.\n"
              "2. Fill in the sections described above.\n"
              "3. Start a discussion thread.\n\n"
              "A page id of 27846330 identifies this page.\n")

    def _card(self, body):
        folder = os.path.join(self.cfg.layout.path("cards"), "proposals")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "raising.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("---\nsource: kb/text/proposals.md\n---\n\n" + body)
        return path

    def _run(self, body):
        from docbase import verify
        self.drop("proposals.md", self.SOURCE)
        self.sync(quiet=True)
        self._card(body)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = verify.report(self.cfg)
        return code, out.getvalue()

    def test_the_right_number_passes(self):
        code, output = self._run("The vote stays open for at least 72 hours.\n")
        self.assertEqual(code, 0, output)

    def test_a_number_the_source_never_states_is_caught(self):
        code, output = self._run("The vote stays open for at least 96 hours.\n")
        self.assertEqual(code, 1)
        self.assertIn("96", output)

    def test_the_same_number_counting_something_else_is_caught(self):
        """72 hours and 72 days are the same digits and a different rule."""
        code, output = self._run("The vote stays open for at least 72 days.\n")
        self.assertEqual(code, 1)
        self.assertIn("72 days", output)

    def test_a_digit_hidden_inside_a_longer_number_does_not_confirm(self):
        """2 is inside 27846330; that is not the document saying two."""
        code, output = self._run("Two approvals are needed: 2 reviewers.\n")
        self.assertEqual(code, 1)
        self.assertIn("2 reviewers", output)

    def test_a_list_marker_in_the_source_is_not_evidence(self):
        """The source's own "3." is structure, exactly as a card's is."""
        code, output = self._run("Start 3 threads.\n")
        self.assertEqual(code, 1)

    def test_a_number_the_source_spells_out_still_matches(self):
        self.drop("hours.md", "# Leave\n\nRequests need at least fifteen "
                              "working days of notice.\n")
        self.sync(quiet=True)
        folder = os.path.join(self.cfg.layout.path("cards"), "leave")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "notice.md"), "w", encoding="utf-8") as h:
            h.write("---\nsource: kb/text/leave.md\n---\n\n"
                    "Give 15 working days of notice.\n")
        from docbase import verify
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = verify.report(self.cfg)
        self.assertEqual(code, 0, out.getvalue())


class TestHistoryDiffIsReadable(BaseCase):
    """A wiki page puts a paragraph on one line.

    Compared line by line, one changed number reported seven hundred
    characters as removed and seven hundred as added — two lines that look
    identical to whoever has to update the card from them.
    """

    def _page(self, hours):
        filler = ("These proposals are more serious than code changes and "
                  "more serious even than release votes. ") * 4
        return (f"# Proposals\n\nCall a vote to have the proposal adopted. "
                f"{filler}The vote should remain open for at least {hours} "
                f"hours. Report the result to the mailing list.\n")

    def test_only_the_sentence_that_changed_is_shown(self):
        self.drop("proposals.md", self._page(72))
        self.sync(quiet=True)
        self.drop("proposals.md", self._page(96))
        self.sync(quiet=True)

        folder = os.path.join(self.cfg.layout.path("history"), "proposals")
        diffs = sorted(f for f in os.listdir(folder) if f.endswith(".diff"))
        body = open(os.path.join(folder, diffs[-1]), encoding="utf-8").read()
        changed = [line for line in body.splitlines()
                   if line[:1] in "+-" and not line.startswith(("---", "+++"))]
        self.assertEqual(len(changed), 2, body)
        for line in changed:
            self.assertLess(len(line), 120, "the whole paragraph was reported")
        self.assertIn("72", changed[0])
        self.assertIn("96", changed[1])


class TestReExportUpdatesTheDocument(BaseCase):
    """Dropping a file that is already here is a new version of it.

    Every importer had settled this differently. A wiki export was keyed on
    its file name and updated; a markdown file was keyed on its content and a
    Word file on its paragraph count, so each edit became a second document.
    A handbook synced from a repository grew a stale twin every time a
    sentence changed, and the base then answered from whichever copy the
    search happened to prefer.
    """

    def _version(self, hours):
        return (f"# Escalation\n\nRespond to a priority ticket within {hours} "
                f"hours. Record the outcome in the tracker.\n")

    def test_a_second_drop_replaces_the_first(self):
        self.drop("escalation.md", self._version(4))
        self.sync(quiet=True)
        self.drop("escalation.md", self._version(2))
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["escalation.md"])
        self.assertEqual(len(os.listdir(self.cfg.layout.path("originals"))), 1)

    def test_the_document_carries_the_new_text(self):
        self.drop("escalation.md", self._version(4))
        self.sync(quiet=True)
        self.drop("escalation.md", self._version(2))
        self.sync(quiet=True)
        text = open(os.path.join(self.cfg.layout.path("text"), "escalation.md"),
                    encoding="utf-8").read()
        self.assertIn("within 2 hours", text)
        self.assertNotIn("within 4 hours", text)

    def test_the_change_is_recorded_in_history(self):
        self.drop("escalation.md", self._version(4))
        self.sync(quiet=True)
        self.drop("escalation.md", self._version(2))
        self.sync(quiet=True)
        folder = os.path.join(self.cfg.layout.path("history"), "escalation")
        self.assertTrue(os.path.isdir(folder))
        self.assertTrue(any(f.endswith(".diff") for f in os.listdir(folder)))

    def test_a_declared_id_survives_a_rename(self):
        """The documented way to keep one identity across file names."""
        body = ('---\nsource_id: "kept-across-renames"\n---\n\n'
                "# Escalation\n\nRespond within 4 hours.\n")
        self.drop("escalation.md", body)
        self.sync(quiet=True)
        self.drop("escalation-v2.md", body.replace("4 hours", "2 hours"))
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["escalation.md"])

    def test_an_edit_of_the_same_length_is_noticed(self):
        """Same size, same second: the cheap check had to look closer."""
        self.drop("escalation.md", self._version(4))
        self.sync(quiet=True)
        self.drop("escalation.md", self._version(2))
        report = self.sync(quiet=True)
        self.assertEqual(report["skipped"], 0)


class TestArchiveMemberNames(BaseCase):
    """An export holds a dozen files called index.html.

    Kept by basename alone, which document ended up as index-3 depended on the
    order the archive happened to list its members in — so re-importing the
    same space could quietly rebind a name to a different page.
    """

    def _archive(self, name, members):
        import zipfile
        path = os.path.join(self.root, name)
        with zipfile.ZipFile(path, "w") as archive:
            for member, body in members.items():
                archive.writestr(member, body)
        return path

    def test_the_folder_is_kept_in_the_name(self):
        self._archive("space.zip", {
            "export/library/index.md": "# Library\n\nModules are documented here.\n",
            "export/tutorial/index.md": "# Tutorial\n\nStart with the basics.\n",
        })
        self.sync(quiet=True)
        self.assertEqual(sorted(os.listdir(self.cfg.layout.path("originals"))),
                         ["library-index.md", "tutorial-index.md"])

    def test_both_documents_survive(self):
        self._archive("space.zip", {
            "export/library/index.md": "# Library\n\nModules are documented here.\n",
            "export/tutorial/index.md": "# Tutorial\n\nStart with the basics.\n",
        })
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["library.md", "tutorial.md"])

    def test_re_importing_the_same_export_changes_nothing(self):
        members = {
            "export/library/index.md": "# Library\n\nModules are documented here.\n",
            "export/tutorial/index.md": "# Tutorial\n\nStart with the basics.\n",
        }
        self._archive("space.zip", members)
        self.sync(quiet=True)
        self._archive("space.zip", members)
        report = self.sync(quiet=True)
        self.assertEqual(report["documents"], 2)
        self.assertEqual(len(os.listdir(self.cfg.layout.path("originals"))), 2)

    def test_a_newer_export_updates_the_document(self):
        self._archive("space.zip", {
            "export/library/index.md": "# Library\n\nModules are documented here.\n"})
        self.sync(quiet=True)
        self._archive("space.zip", {
            "export/library/index.md": "# Library\n\nModules are listed here.\n"})
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["library.md"])
        text = open(os.path.join(self.cfg.layout.path("text"), "library.md"),
                    encoding="utf-8").read()
        self.assertIn("listed here", text)

    def test_a_traversing_member_cannot_escape_the_base(self):
        from docbase.sync import flatten_member
        self.assertEqual(flatten_member("../../etc/passwd.md"), "etc-passwd.md")
        self.assertEqual(flatten_member("/absolute/path.md"), "absolute-path.md")


class TestDocsAsCode(BaseCase):
    """A documentation repository refers to itself with relative paths.

    Tested last of the formats it accepts, and the one a technical user is
    likeliest to have: a docs/ folder, an Obsidian vault, a GitBook or Notion
    export. Nothing in it was stitched together, because a link that did not
    start with a scheme or a slash was assumed to already be a local file —
    while the base is flat, so those paths point at nothing.
    """

    def _repo(self):
        self.drop("install.md",
                  "# Installing the agent\n\n"
                  "Read the [configuration guide](../reference/configuration.md).\n"
                  "See also [troubleshooting](./troubleshooting.md) "
                  "and the [FAQ](faq.md).\n\n"
                  "```bash\n# this is a shell comment, not a heading\n"
                  "apt install thing\n```\n")
        self.drop("configuration.md",
                  "# Configuration reference\n\nSet the timeout to 30 seconds.\n")
        self.drop("troubleshooting.md",
                  "# Troubleshooting\n\nRestart it and try again.\n")
        self.sync(quiet=True)

    def _text(self, name):
        return open(os.path.join(self.cfg.layout.path("text"), name),
                    encoding="utf-8").read()

    def test_a_relative_link_reaches_the_imported_document(self):
        self._repo()
        body = self._text("installing-the-agent.md")
        self.assertIn("](configuration-reference.md)", body)
        self.assertIn("](troubleshooting.md)", body)

    def test_a_relative_link_to_a_missing_document_is_named_readably(self):
        self._repo()
        path = os.path.join(self.cfg.layout.root, "kb", "linkmap.json")
        missing = json.load(open(path, encoding="utf-8"))["missing"]
        self.assertIn("faq", missing)

    def test_a_shell_comment_is_not_a_section(self):
        self._repo()
        headings = [h for _line, h in Index(self.cfg).build()
                    .headings("installing-the-agent.md")]
        self.assertEqual(headings, ["Installing the agent"])

    def test_a_title_is_not_taken_from_a_code_sample(self):
        self.drop("guide.md", "```\n# not the title\n```\n\n"
                              "# The real title\n\nProse follows.\n")
        self.sync(quiet=True)
        self.assertEqual(self.text_files(), ["the-real-title.md"])

    def test_an_underline_inside_a_code_sample_is_not_a_heading(self):
        from docbase.importers.text import underlined_headings
        converted = underlined_headings("```\nprint(total)\n=====\n```\n")
        self.assertNotIn("#", converted)

    def test_an_image_reference_is_left_alone(self):
        self.drop("page.md", "# Architecture\n\n"
                             "![the diagram](images/architecture.png)\n")
        self.sync(quiet=True)
        self.assertIn("![the diagram](images/architecture.png)",
                      self._text("architecture.md"))

    def test_a_mail_or_phone_link_is_not_a_document(self):
        self.drop("page.md", "# Support\n\n"
                             "Write to [us](mailto:help@example.com) "
                             "or call [the desk](tel:+123456).\n")
        self.sync(quiet=True)
        body = self._text("support.md")
        self.assertIn("(mailto:help@example.com)", body)
        self.assertIn("(tel:+123456)", body)

    def test_an_anchor_stays_an_anchor(self):
        self.drop("page.md", "# Handbook\n\nJump to [details](#details).\n\n"
                             "## Details\n\nHere.\n")
        self.sync(quiet=True)
        self.assertIn("](#details)", self._text("handbook.md"))

    def test_a_relinked_base_is_stable_on_a_second_run(self):
        """The rewritten link must not be read as a new reference."""
        self._repo()
        first = self._text("installing-the-agent.md")
        self.sync(quiet=True, force=True)
        self.assertEqual(first, self._text("installing-the-agent.md"))

    def test_code_that_reads_like_a_link_is_left_alone(self):
        """`[T](Sized)` is a type hint and `[.](?!bat$)` a regular expression.

        Both appear in published reference manuals, and markdown cannot tell
        either from a link. Six of them reached the list of documents worth
        importing next, which is the one list that has to stay actionable.
        """
        self.drop("typing.md", "# Typing\n\n"
                               "A bound method on list[T](Sized) returns it.\n"
                               "The pattern [.](?!bat$) excludes the suffix.\n")
        self.sync(quiet=True)
        path = os.path.join(self.cfg.layout.root, "kb", "linkmap.json")
        self.assertEqual(json.load(open(path, encoding="utf-8"))["missing"], {})
        self.assertIn("list[T](Sized)", self._text("typing.md"))
