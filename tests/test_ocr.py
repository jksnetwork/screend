# SPDX-License-Identifier: GPL-3.0-or-later
"""OCR helpers that don't need Tesseract installed."""

import unittest
from unittest import mock

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from screend import ocr  # noqa: E402


class OcrTest(unittest.TestCase):
    def test_clean_trims_form_feed_and_blank_runs(self):
        raw = "Hello world  \n\n\n\nSecond   para\n\f"
        self.assertEqual(ocr.clean(raw), "Hello world\n\nSecond   para")

    def test_missing_tesseract_reports_install_hint(self):
        results = []
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 10, 10)
        with mock.patch.object(ocr, "available", return_value=False):
            ocr.recognize(pb, "eng", lambda text, err: results.append((text, err)))
        self.assertEqual(results, [(None, ocr.INSTALL_HINT)])


if __name__ == "__main__":
    unittest.main()
