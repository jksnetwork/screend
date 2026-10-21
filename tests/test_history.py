# SPDX-License-Identifier: GPL-3.0-or-later
"""Recent-captures history: written for every shot, pruned by count."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GdkPixbuf  # noqa: E402

from screend import shot as shot_mod  # noqa: E402
from screend.config import Config  # noqa: E402


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = Path(self.tmp.name) / "history"
        patcher = mock.patch.object(shot_mod, "HISTORY_DIR", self.history)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = Config(save_dir=str(Path(self.tmp.name) / "saved"), history_limit=3)

    def tearDown(self):
        self.tmp.cleanup()

    def make_shot(self, created):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
        s = shot_mod.Shot(pb, self.config)
        s.created = created
        return s

    def test_prune_keeps_newest(self):
        self.history.mkdir(parents=True)
        for i in range(5):
            f = self.history / f"{i}.png"
            f.write_bytes(b"x")
            os.utime(f, (1000 + i, 1000 + i))
        shot_mod.prune_history(3)
        self.assertEqual(sorted(p.name for p in self.history.iterdir()), ["2.png", "3.png", "4.png"])

    def test_capture_goes_to_history_not_save_folder(self):
        s = self.make_shot(time.time())
        s.keep_in_history_async()
        for _ in range(50):
            if s.history_path.exists():
                break
            time.sleep(0.05)
        self.assertTrue(s.history_path.exists())
        self.assertFalse((Path(self.tmp.name) / "saved").exists())
        saved = s.save()
        self.assertEqual(saved.parent, Path(self.tmp.name) / "saved")

    def test_zero_limit_keeps_nothing(self):
        self.config.history_limit = 0
        s = self.make_shot(time.time())
        s.keep_in_history_async()
        self.assertIsNone(s.history_path)


if __name__ == "__main__":
    unittest.main()
