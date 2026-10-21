# SPDX-License-Identifier: GPL-3.0-or-later
"""Window-geometry parsing for compositors we can't run in CI (Hyprland, Sway)."""

import unittest

from screend import platform


class HyprlandTest(unittest.TestCase):
    monitors = [{"activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]

    def test_only_visible_workspaces_floating_first(self):
        clients = [
            {"at": [0, 0], "size": [800, 600], "workspace": {"id": 1}, "floating": False, "focusHistoryID": 0},
            {"at": [100, 100], "size": [300, 200], "workspace": {"id": 1}, "floating": True, "focusHistoryID": 1},
            {"at": [0, 0], "size": [500, 500], "workspace": {"id": 2}, "floating": False, "focusHistoryID": 2},
            {"at": [5, 5], "size": [10, 10], "workspace": {"id": 1}, "hidden": True},
        ]
        self.assertEqual(platform.hyprland_windows(clients, self.monitors),
                         [(100, 100, 300, 200), (0, 0, 800, 600)])

    def test_missing_data(self):
        self.assertEqual(platform.hyprland_windows(None, None), [])


class SwayTest(unittest.TestCase):
    tree = {
        "type": "root", "nodes": [{
            "type": "output", "nodes": [{
                "type": "workspace",
                "nodes": [
                    {"type": "con", "visible": True, "rect": {"x": 0, "y": 0, "width": 960, "height": 1080},
                     "nodes": [], "floating_nodes": []},
                    {"type": "con", "visible": False, "focused": True,
                     "rect": {"x": 960, "y": 0, "width": 960, "height": 1080}, "nodes": [], "floating_nodes": []},
                ],
                "floating_nodes": [
                    {"type": "floating_con", "visible": True, "rect": {"x": 200, "y": 200, "width": 400, "height": 300},
                     "nodes": [], "floating_nodes": []},
                ],
            }],
            "floating_nodes": [],
        }],
        "floating_nodes": [],
    }

    def test_visible_leaves_floating_first(self):
        self.assertEqual(platform.sway_windows(self.tree), [(200, 200, 400, 300), (0, 0, 960, 1080)])

    def test_focused(self):
        node = platform._find_focused(self.tree)
        self.assertEqual(node["rect"]["x"], 960)


if __name__ == "__main__":
    unittest.main()
