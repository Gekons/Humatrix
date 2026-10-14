**English** | [Deutsch](architektur.md)

# Architecture

What runs on the box after `install.sh`, and how the invitation part works without
reading the code.

```
                 Internet
                    |
          nginx (TLS via certbot, or plain HTTP behind your proxy)
   ---------------------------------------------------------------
   matrix.<d>   chat.<d>         invite.<d>        service.<d>
      |            |                 |                 |
   Synapse     Element Web      invitation service (one Python process,
   :8008       static files     127.0.0.1:8082, user "einladung")
      |                              |        |
  PostgreSQL                      SQLite   Synapse Admin API (localhost)
                                           - registration tokens
                                           - users, passwords, deactivate
                                           - room invites
   coturn :3478 (calls)   backup timer   update check timer   ufw / fail2ban
```

## The invitation part

Nothing is patched in Synapse. The invitation service is a separate process that
only talks to Synapse through the **Admin API on localhost** with an admin access
token (`/etc/einladung/admin-token`, readable by root and the service user).

1. **A user creates an invitation** on `invite.<domain>` (login = the normal Matrix
   login, checked against Synapse). The service asks Synapse for a **registration
   token** (`/_synapse/admin/v1/registration_tokens/new`) with `uses_allowed: 1` and
   an expiry of 14 days, stores code, token, inviter and optional room in its SQLite
   database, and hands out a link `https://invite.<domain>/e/<code>`.
2. **The invited person opens the link.** The page shows the server name and the
   token, and links to Element Web's registration page. Registration itself is plain
   Matrix: Synapse's `registration_requires_token` is on, the token is used once.
3. **Who brought in whom:** Matrix does not report which account used a token. A
   background check compares the token's `completed` counter with new accounts and
   their creation time; the invitation is assigned only when exactly one account is
   a candidate, otherwise it stays "redeemed, assignment unclear". Assigned accounts
   get a room invite (Admin API `join`) if the invitation carried a room.
4. **Quotas** are rows in SQLite (per user, default for new users, `-1` = unlimited),
   enforced by the service before it asks Synapse for a token.
5. **Revoke / block:** revoking deletes the token at Synapse and marks the row.
   Blocking a user calls the Admin API `deactivate` (without erase, so it can be
   undone) and revokes all of that user's open invitations first.

## The admin page

Same process, served under `service.<domain>`. Who is an admin on the Matrix server
(`admin: true` in Synapse) is an admin here; there is no second user database. Admins
get an optional second factor (TOTP via `pyotp`; the secret is stored encrypted with a
key in `/etc/einladung/datenschluessel`). Sessions are an HMAC-signed cookie; the key
lives in `/etc/einladung/sitzungsschluessel`, and rotating it ends all sessions.

## Updates from the admin page

The service may only create an empty job file under `/var/lib/messenger/auftraege/`.
A systemd path unit running as root notices the file and runs a fixed script
(`messenger-update-ausfuehren.sh`: `apt-get upgrade`, Synapse restart only if its
package changed, Element Web refresh). The result lands in `/var/lib/messenger/
updates.json` and is shown on the page. No parameters cross from the web process to
root; the file's existence is the whole interface.

## Dependencies of the service

Python 3 standard library plus three Debian packages: `python3-pyotp`,
`python3-qrcode`, `python3-cryptography`. No pip, no framework, no JavaScript build.
