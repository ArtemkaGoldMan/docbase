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
        mcp._list_documents = lambda cfg: (_ for _ in ()).throw(RuntimeError("boom"))
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
