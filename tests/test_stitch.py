# SPDX-License-Identifier: GPL-3.0-or-later
"""Scrolling-capture stitching against synthetic pages with sticky header/footer."""

import random
import unittest

from PIL import Image, ImageDraw

from screend.stitch import Stitcher


def page(width, height, seed=1):
    """A tall 'document': text-like random blocks on white."""
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    y = 0
    while y < height:
        line_h = rng.randint(10, 22)
        x = 8
        while x < width - 40:
            w = rng.randint(8, 60)
            shade = rng.randint(0, 120)
            d.rectangle((x, y + 3, x + w, y + line_h - 3), fill=(shade, shade, shade + 40))
            x += w + rng.randint(4, 12)
        y += line_h + rng.randint(2, 10)
    return img


def frames(doc, view_h, offsets, header=None, footer=None):
    """Viewport crops of doc at each scroll offset, with optional fixed header/footer."""
    out = []
    for off in offsets:
        f = doc.crop((0, off, doc.width, off + view_h)).copy()
        if header is not None:
            f.paste(header, (0, 0))
        if footer is not None:
            f.paste(footer, (0, view_h - footer.height))
        out.append(f)
    return out


class StitchTest(unittest.TestCase):
    def test_plain_scroll_reconstructs_page(self):
        doc = page(400, 1500)
        offs = [0, 120, 260, 400, 555, 700]
        st = Stitcher()
        for f in frames(doc, 400, offs):
            st.add(f)
        result = st.result()
        expected = doc.crop((0, 0, 400, 700 + 400))
        self.assertEqual(result.size, expected.size)
        self.assertEqual(result.tobytes(), expected.tobytes())

    def test_sticky_header_and_footer_kept_once(self):
        doc = page(400, 1500, seed=2)
        header = Image.new("RGB", (400, 40), (30, 90, 200))
        ImageDraw.Draw(header).rectangle((10, 10, 120, 30), fill="white")
        footer = Image.new("RGB", (400, 30), (200, 60, 40))
        ImageDraw.Draw(footer).rectangle((300, 8, 390, 22), fill="white")
        offs = [0, 150, 300, 450]
        st = Stitcher()
        for f in frames(doc, 420, offs, header, footer):
            st.add(f)
        result = st.result()
        # header + body rows 40..(450+390) of the page + footer
        self.assertEqual(result.height, 40 + (450 + 420 - 30 - 40) + 30)
        self.assertEqual(result.crop((0, 0, 400, 40)).tobytes(), header.tobytes())
        self.assertEqual(result.crop((0, result.height - 30, 400, result.height)).tobytes(), footer.tobytes())
        body = result.crop((0, 40, 400, result.height - 30))
        self.assertEqual(body.tobytes(), doc.crop((0, 40, 400, 40 + body.height)).tobytes())

    def test_unchanged_and_backwards_frames_are_ignored(self):
        doc = page(300, 1200, seed=3)
        st = Stitcher()
        f0, f1, f_back = frames(doc, 300, [200, 350, 100])
        self.assertTrue(st.add(f0))
        self.assertFalse(st.add(f0.copy()))
        self.assertTrue(st.add(f1))
        self.assertFalse(st.add(f_back))
        self.assertEqual(st.result().height, 300 + 150)


if __name__ == "__main__":
    unittest.main()
