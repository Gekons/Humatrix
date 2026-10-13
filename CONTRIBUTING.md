**English** | [Deutsch](CONTRIBUTING.de.md)

# Contributing

Thanks for taking a look.

## Reporting bugs

A ticket with: what you did, what happened, what you expected. Plus the Debian
version and the matching part of `/var/log/messenger-installer.log`.

**Security vulnerabilities not here**, but as described in [SECURITY.md](SECURITY.md).

## Proposing changes

Gladly — small steps are easier to review than big ones. If it is something larger,
open a ticket first; that saves you work that might not fit.

## What the code here looks like

So that contributions fit in:

- **German in the code.** Comments and identifiers stay German. User-facing texts live
  in text tables in both languages (`TEXTE` in `einladung.py`, `T_de`/`T_en` in
  `install.sh`, `VORLAGEN` in `kundenanleitung.py`); a new text needs a key in both, and
  the tests check that. Documentation exists in English and German side by side.
- **No third-party packages without good reason.** The invitation page gets by with
  what Python and Debian bring along. Every dependency is something somebody has to maintain.
- **Comments explain the why, not the what.** That a line generates a token is obvious.
  Why without the characters O, 0, I and l — that isn't.
- **Don't encrypt anything yourself.** Crypto comes from vetted libraries.
- **The installer stays repeatable.** Running it twice must not do harm. That also
  applies to the firewall: `ufw` is only reset while it is not yet active; after that
  the hardening only sets its own rules and leaves others in place. The SSH port
  comes from the running `sshd` configuration, not hard-coded in the script.
- **Check before changing.** Whatever is wrong aborts before anything is touched.

## What won't go into the project

- Multi-tenant operation (one server, one community)
- A chat app of our own (good ones exist, and they shouldn't be ours)
- Our own cryptography
- Features that only work with additional services running in the background
