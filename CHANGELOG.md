# Changelog

## Unreleased

- Blocking an account revokes its open invitations.
- The invitation page recommends FluffyChat; English screenshots use example.com.

## 1.0.0 — 2026-10-14

First public release.

- Installer for Debian 13 (and 12), amd64: Synapse, Element Web, coturn, nginx,
  Let's Encrypt, PostgreSQL, backup, basic hardening. Re-runnable; aborts before the
  first change if DNS, ports or the system are not ready. `--behind-proxy` for setups
  with an existing reverse proxy.
- Invitation service: one-time links with a quota per user, revoke with one click,
  room assignment, traceable invitation chain, TOTP second factor for admins, no open
  registration.
- Admin page: accounts, quotas, passwords, admins; daily update check (Synapse,
  Element Web, system packages) with install on a click; server language.
- Customer guide generated per installation.
- Interface and documentation in English and German.
- Test report: functional test with real Olm/Megolm sessions, load test, hardening checks.
