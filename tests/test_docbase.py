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
