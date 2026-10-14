# Changelog

## Unreleased

- Setup in the browser: `setup.sh` installs the bare minimum, starts a setup page on
  port 8088 and prints its address with a one-time code. The page asks for domain,
  proxy mode, e-mail, first account and operator, checks the DNS names, runs
  `install.sh` with the answers, shows progress and log, and ends with the credentials
  and the customer guide. A preparation step lists, per situation (rented server, home
  behind a router, behind a reverse proxy), what has to be done outside the server:
  fixed address, port forwards, DynDNS, DNS records, proxy entries. On a rented server
  the password is shown only on an encrypted copy of the page (port 8089, with the new
  certificate). "Finish setup" turns it off; it also turns itself off after a day. The
  installer keeps the page's ports open in the firewall while it runs
  (`HUMATRIX_SETUP_PORT`).
- Operator guide: `install.sh` writes `/root/verwaltungsanleitung-<domain>.html` with
  addresses, network requirements, backup, update and troubleshooting commands for this
  server, without passwords. The admin page offers it under "Operator guide".
- Blocking an account revokes its open invitations.
- Invitation page: the account is created in the browser first, then the app (FluffyChat or Element X) is used to log in; FluffyChat and Element X cannot register on this server. English screenshots use example.com.

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
