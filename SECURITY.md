# Security policy

Screend sees your screen and can read and write your clipboard, so security reports are taken seriously.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately instead:

1. Go to the repository's **Security** tab → **Report a vulnerability** ([direct link](https://github.com/jksnetwork/screend/security/advisories/new)).
2. Describe the problem, how to reproduce it, and what an attacker could do with it.

Only the maintainers can see these reports. You'll get a reply within a week. Once a fix is released, the advisory is published, with credit to you if you'd like.

## Supported versions

Fixes go into the latest release. Please check that the problem still happens with it before reporting.

## What's in scope

- Anything that lets another program or user see captures, recordings or clipboard contents they shouldn't
- The GNOME Shell extension's D-Bus interface (`io.github.jksnetwork.Screend.Shell`), which runs with the shell's privileges
- The installer and packages (for example, writing files or running code somewhere unexpected)

Out of scope: problems that need an attacker who already controls your user account, and bugs in GNOME, KDE, or other projects Screend uses (please report those upstream).
