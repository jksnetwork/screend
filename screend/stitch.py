# SPDX-License-Identifier: GPL-3.0-or-later
"""Stitch frames of a scrolling area into one tall image.

Each frame is reduced to per-row fingerprints (rows shrunk to a few dozen grey
levels). Rows that stay put at the top/bottom of consecutive frames are a
sticky header/footer and are kept once. In between, fingerprints that occur
once in the previous frame vote for how far the content moved; the new rows
at the bottom are appended.
"""

from PIL import Image

SIGNATURE_WIDTH = 64
MAX_HEIGHT = 40000
MIN_OVERLAP = 24     # rows that must overlap for a scroll to be trusted
MIN_INFORMATIVE = 8  # ...of which this many must have visible content
MAX_MISMATCH = 0.02  # fraction of overlapping rows allowed to differ (cursor, animations)


def signatures(frame):
    """One bytes object per row: the row shrunk to SIGNATURE_WIDTH grey samples, quantised."""
    w, h = frame.size
    grey = frame.convert("L").resize((min(SIGNATURE_WIDTH, w), h), Image.BOX)
    data = bytes(v >> 3 for v in grey.tobytes())
    sw = grey.size[0]
    return [data[i * sw:(i + 1) * sw] for i in range(h)]


def _informative(sig):
    return max(sig) - min(sig) > 1


def find_scroll(prev, cur):
    """How many rows the content moved up between two fingerprint lists, or None.

    Tries every offset, abandoning one as soon as too many rows disagree, and
    keeps the best. Overlaps made only of flat rows prove nothing and are skipped.
    """
    n = len(cur)
    best = None  # (mismatches / overlap, dy)
    for dy in range(1, n - MIN_OVERLAP + 1):
        overlap = n - dy
        allowed = int(overlap * MAX_MISMATCH)
        mismatches = informative = 0
        for j in range(overlap):
            a = cur[j]
            if a != prev[j + dy]:
                mismatches += 1
                if mismatches > allowed:
                    break
            elif _informative(a):
                informative += 1
        else:
            if informative >= MIN_INFORMATIVE:
                score = (mismatches / overlap, dy)
                if best is None or score < best:
                    best = score
                if mismatches == 0:
                    break  # the smallest exact match is the scroll we want
    return best[1] if best else None


class Stitcher:
    def __init__(self):
        self.parts = []       # body strips, top to bottom
        self.last = None      # previous frame
        self.last_sigs = None
        self.footer = None    # rows of sticky footer, fixed once detected
        self.footer_img = None
        self.height = 0

    def add(self, frame):
        """Feed the next frame; returns True if it added new content."""
        frame = frame.convert("RGB")
        sigs = signatures(frame)
        if self.last is None:
            self.last, self.last_sigs = frame, sigs
            self.parts = [frame]
            self.height = frame.height
            return True
        if frame.size != self.last.size or sigs == self.last_sigs or self.height >= MAX_HEIGHT:
            return False

        h = frame.height
        limit = h // 3
        header = 0
        while header < limit and sigs[header] == self.last_sigs[header]:
            header += 1
        footer = 0
        while footer < limit and sigs[h - 1 - footer] == self.last_sigs[h - 1 - footer]:
            footer += 1
        if self.footer is not None:
            footer = self.footer

        body_prev = self.last_sigs[header:h - footer]
        body_cur = sigs[header:h - footer]
        dy = find_scroll(body_prev, body_cur)
        if dy is None or dy <= 0 or dy >= len(body_cur):
            return False  # no usable overlap (or scrolled up): skip this frame

        if self.footer is None:
            # first real scroll: now we know the footer; trim it from what we have so far
            self.footer = footer
            first = self.parts[0]
            self.parts[0] = first.crop((0, 0, first.width, first.height - footer))
            self.height -= footer
        body_bottom = h - footer
        strip = frame.crop((0, body_bottom - dy, frame.width, body_bottom))
        self.parts.append(strip)
        self.height += dy
        self.footer_img = frame.crop((0, body_bottom, frame.width, h)) if footer else None
        self.last, self.last_sigs = frame, sigs
        return True

    def result(self):
        if not self.parts:
            return None
        width = self.parts[0].width
        total = sum(p.height for p in self.parts) + (self.footer_img.height if self.footer_img else 0)
        out = Image.new("RGB", (width, total))
        y = 0
        for part in self.parts + ([self.footer_img] if self.footer_img else []):
            out.paste(part, (0, y))
            y += part.height
        return out
