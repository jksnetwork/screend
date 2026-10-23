## What this changes

<!-- What does this do, and why? Link the issue it fixes, e.g. "Fixes #12". -->

## How I tested it

<!-- Desktop and distro, and what you tried. Screenshots or a short recording help for UI changes. -->

## Checklist

- [ ] `python3 -m unittest discover tests` passes
- [ ] New logic that doesn't need a display has a test
- [ ] User-facing changes are reflected in `README.md` / `docs/user-guide.md`
- [ ] If the UI changed: `tools/nested-shell.sh python3 tools/docs_screenshots.py` regenerated the screenshots
- [ ] If the GNOME extension's D-Bus API changed: bumped `API_VERSION` and updated CONTRIBUTING.md
