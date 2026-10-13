**English** | [Deutsch](README.de.md)

# Your own encrypted chat for your own circle

An installer that sets up a complete, end-to-end encrypted chat on a fresh Debian
server in a single run: Matrix server, encryption, calls, web interface,
administration, invitations, backup, basic hardening.

```bash
sudo ./install.sh --domain deinedomain.de --mail du@example.de --verwalter dein-name
```

After that your IDs are `@anna:deinedomain.de` and you write to each other encrypted:
the server holds nothing but ciphertext; not even the operator can read message content.
Where the limits are — metadata, passwords, the browser client — is covered below under
**[What the operator can see — and what they can't](#what-the-operator-can-see--and-what-they-cant)**.

<p align="center">
  <img src="dateien/bilder/04-einladung.png" width="420" alt="Invitation page">
</p>

---

## Will it run for me?

A word up front, so nobody downloads it first and only then finds out it doesn't fit.

| | |
|---|---|
| **Operating system** | Debian 13 (recommended) or Debian 12. **Nothing else.** |
| **Architecture** | `amd64` (ordinary PC and server processors). **No ARM.** |
| **Memory** | 2 GB minimum, 4 GB for 20–50 people, 8 GB with headroom |
| **Disk space** | 20 GB minimum, 80 GB recommended — photos and videos add up |
| **Your own domain** | required, and it is baked into every user ID for good |
| **Public IPv4** | required. Does not work on DS-Lite connections. |
| **Access** | root (or `sudo`) on a server that does nothing else |

### What explicitly does NOT work

**Raspberry Pi and other ARM machines.** This is not laziness: the package source
`packages.matrix.org` ships Synapse for `amd64` and `i386` only — for `arm64` and
`armhf` there isn't even a package list there. Anyone who wants to run Matrix on a Pi
needs Synapse in a container or built from source; this installer can do neither, and
it doesn't pretend otherwise. It aborts right at the start with exactly this reason.

**Windows.** The installer is a Bash script and builds on `systemd`, `apt` and Unix
file permissions. Even under WSL that is no sensible basis for a server that is
supposed to be reachable day and night.

**Ubuntu — for now.** matrix.org does ship packages for Ubuntu (focal, jammy, noble),
and it would probably work. But *probably* isn't good enough here: it hasn't been
tested, so it isn't promised. The installer rejects Ubuntu with exactly this reason.
If you have tried it, let us know — then it will be listed here as tested next time.

**A server that already runs something else.** The installer wants ports, an `nginx`
and a PostgreSQL database. If a web server is already there, it aborts. Exception: if
a reverse proxy is already running in your house, use `--hinter-proxy` (see Installation).

### Do I meet this?

These three lines on the server answer everything:

```bash
. /etc/os-release && echo "$PRETTY_NAME"      # Debian 12 or 13?
dpkg --print-architecture                      # must say amd64
free -g; df -h /                               # memory and disk space
```

And the installer checks it again itself anyway **before the first change** — including
DNS and ports. Whatever is wrong aborts without anything having been touched (single
exception: a missing `curl` is installed beforehand, the check needs it).

---

## Why this exists

Matrix is an excellent protocol. But setting up a Matrix server today means: around
forty steps from the manuals of five different projects, and you still get the call
configuration wrong.

And even once it runs, the decisive piece is missing: **Matrix has no invitations by
normal users.** Only the server administrator may create registration tokens. So the
mother can't invite the father — everything has to go through the technician. That is
exactly where a family chat fails in everyday life.

This project closes both gaps.

## What it is

- **One command** instead of forty steps
- **Invitations for everyone**: every user gets a quota and sends out links
- **Invitations bring their room along**: when you create one, you choose where the
  person should go. They land right there — instead of in an empty app — and separate
  circles on the same server stay separate
- **Traceable invitation chain**: for every account that came in by invitation, you can
  see who brought it in. To be honest about it: Matrix does not reveal who used an
  invitation link. The service matches by sign-up time — and only when exactly one
  account is a candidate. Otherwise the invitation counts as **redeemed, assignment
  unclear**: the quota stays used, the link is burnt, the admin page shows it as
  "unklar" (unclear), and the newcomer gets no automatic room invitation — you invite
  them by hand in the chat. The matching is retried on every run; it succeeds later once
  the other candidates have been assigned in the meantime — but if two newcomers arrive
  in the same minute via two invitations, it stays "unclear" for good. That is by
  design: better an honest gap than a wrong entry. Accounts created by hand and the
  admin account have no chain entry. And: **Abmelden** (log out) on the invitation page ends all
  sessions of that account there, on other devices too
- **Revoking**: an open invitation can be revoked with the ✕ on its row — the code is
  deleted at the server, the link reports "zurückgezogen" (revoked), the quota is free again. For the
  invitation sent twice, not for the redeemed one.
- **Sensible defaults**: encryption on, registration by invitation only, no previews of
  external pages, cached media from other servers cleared after 90 days
- **German interface and documentation** — for people who don't want to read English.
  The installer and the web pages currently speak German; an English mode is in the works
- **A ready-made customer guide**, filled in with the real credentials during installation

## What it is not

So nobody is disappointed:

- **Not a WhatsApp replacement.** Your people have to switch deliberately. That only works within your own circle.
- **No multi-tenant operation.** One server, one community.
- **No new encryption.** Matrix does all of that; nothing is home-made here.
- **No app of its own.** You use ready-made ones from the stores — and that is intentional:
  this way the person who built the app is not the one who also runs the server.
- **Not a product with round-the-clock support.** A maintained side project.

## Requirements

The key figures are above under **[Will it run for me?](#will-it-run-for-me)**. In addition:

| | |
|---|---|
| Server | Debian 13 (or 12), amd64, **4 cores, 8 GB RAM, 80 GB** for 20–50 people |
| Domain | your own — it is baked into every user ID for good |
| DNS | six A records (see below) |
| At home? | Works — but many connections have no public IPv4 of their own (DS-Lite). Then it won't work. The installer tells you. |
| Already have a proxy? | Then use `--hinter-proxy` (see Installation). |

**The domain cannot be changed later** without recreating all accounts. Think about it first.

### DNS

```
deinedomain.de              A   <IP>
matrix.deinedomain.de       A   <IP>
chat.deinedomain.de         A   <IP>
einladung.deinedomain.de    A   <IP>
verwaltung.deinedomain.de   A   <IP>
turn.deinedomain.de         A   <IP>
```

## Installation

The fresh server has no `git`. So you fetch the repository on your own machine and
copy it over with `scp`:

```bash
git clone https://github.com/Gekons/Humatrix.git     # on your machine
scp -r Humatrix root@<IP>:/root/
ssh root@<IP>                                         # from here on: on the server
cd /root/Humatrix
./install.sh --domain deinedomain.de --mail du@example.de --verwalter dein-name
```

With backup to remote storage:

```bash
sudo ./install.sh --domain deinedomain.de --mail du@example.de --verwalter dein-name \
                  --sicherung user@storage.example.de:/messenger
```

Afterwards the key of the encrypted backup is in `/root/.sicherung_passwort`.
**Without it no backup can be restored** — put it in your password manager.

Before the first change the installer checks the system, memory, disk space, all six
DNS names and the ports — and aborts without having touched anything if something is
missing (single exception: a missing `curl` is installed beforehand, the check needs
it). At the end ten final checks run, eleven with `--sicherung`.

To repeat a single section: `--nur turn` (see `--hilfe`). With
`--betreiber "Name, mail@example.de"` the name and contact details of the person looking
after the server appear in the customer guide and in the notes of the invitation
service — otherwise it just says "dein Betreuer" (your contact person).

### Already have a reverse proxy in the house?

If you already run a Nginx Proxy Manager, Traefik or Caddy at home, ports 80 and 443 of
the public address already belong to it. Then:

```bash
sudo ./install.sh --domain deinedomain.de --verwalter dein-name --hinter-proxy
```

In this mode the installer fetches **no** certificate and serves everything as plain
text on port 80 — the existing proxy takes care of TLS. At the end it tells you which
five entries you need to create there. The DNS names may then point to the proxy
instead of this server; the installer accepts that and only reports it.

Limitation: encrypted TURN (port 5349) is not available this way, because no
certificate is on the machine. TURN runs unencrypted on 3478 — no harm done, because
the call content is end-to-end encrypted anyway; TURN only relays.

## What runs afterwards

| Address | for whom |
|---|---|
| `https://chat.deinedomain.de` | everyone — chat in the browser |
| `https://einladung.deinedomain.de` | everyone — send invitations |
| `https://verwaltung.deinedomain.de` | admins only — accounts and quotas |
| Phone app | FluffyChat or Element X, enter `deinedomain.de` as the server |

### Anyone can invite — as much as you allow

<p align="center">
  <img src="dateien/bilder/02-nutzer.png" width="380" alt="User view">
  <img src="dateien/bilder/03-erzeugt.png" width="380" alt="Created invitation">
</p>

One click, one link, done. The invitation is valid for 14 days and works exactly once.
The code deliberately avoids easily confused characters: `Y9RV-3GZW-JJ57`.

### Administration

<p align="center">
  <img src="dateien/bilder/05-verwaltung.png" width="420" alt="Administration">
</p>

Quota per person freely editable — **0** means "may not invite anyone", plus a checkbox
for unlimited. Create accounts, suspend them, reset passwords, appoint admins.
And: **who brought in whom** — as far as the assignment was unambiguous (see above).

At the top of the page it says whether **updates** are pending (Synapse, Element Web,
system packages — checked daily). Nothing is installed on its own: first the button, then
a confirmation that says what will happen and that the chat pauses briefly during it.

An admin is whoever is one on the Matrix server — no second permission system, no second password.

### Second factor

<p align="center">
  <img src="dateien/bilder/06-zweitfaktor.png" width="380" alt="Second factor">
</p>

TOTP, two-step login, secret stored encrypted, throttling after five failed attempts.
It only becomes active after a valid code — you can't lock yourself out while
setting it up.

Lost your phone? `python3 /opt/einladung/einladung.py --zweitfaktor-aus <name>` as root.

## The decisions the installer makes for you

| | why |
|---|---|
| Registration **by invitation only** | Every account needs an invitation, and the chain behind it is traceable as far as the assignment could be made unambiguously (see above). That is the brake on abuse — not the encryption. |
| Search only **within shared rooms** | Several circles on one server (family, club, friends) don't see each other. Can be changed if there is only one circle — in `/etc/matrix-synapse/conf.d/50-eigene.yaml` (not in `20-betrieb.yaml`, the installer rewrites that on every run): `user_directory: {enabled: true, search_all_users: true, prefer_local_users: true}`, then `systemctl restart matrix-synapse`. See [docs/operator.md](docs/operator.md). |
| Federation **open** | Your people can reach the worldwide Matrix network. Brings **no** outside accounts onto your server. |
| Encryption preset **everywhere** | Not as a special case, but as the normal case. |
| Link previews **off** | Otherwise your server fetches external pages and reveals what someone linked. |
| Only **security** updates automatic | Everything else deliberately — checked daily, installed only on a click in the admin page. |
| Media from other servers cleared after **90 days** | Only affects the cache for media from other servers. Your own photos and videos stay for good — with end-to-end encryption, deleting would mean losing data. So plan for 80 GB and keep an eye on `du -sh /var/lib/matrix-synapse/media` (see [docs/operator.md](docs/operator.md)). |

## What the operator can see — and what they can't

This belongs openly on the table, and it can be verified:

| | |
|---|---|
| Messages, photos, files | **no** — the server holds nothing but ciphertext |
| Who, when, with whom | **yes** (metadata) |
| Restore history | **no** — the keys are on the devices |
| Reset passwords | **yes** — that would allow taking over an account and reading along from then on; old messages stay locked to them, and the device verification in the app makes such an attempt visible (see [SECURITY.md](SECURITY.md)) |
| Web interface in the browser | the operator serves the client code themselves (`/var/www/element`) and could alter it — so in the browser the encryption does not protect against them. If you want to rule that out, use the phone app from the store |

To verify: `su - postgres -c "psql synapse"` and look into the events table.
It's gibberish.

## Operation

| What | How often |
|---|---|
| Security updates | automatic |
| Everything else (system packages, Synapse, Element Web) | updates are checked daily and shown in the admin page; installed only on a click, after confirmation. Synapse restarts in the process, the chat is briefly unreachable |
| Synapse by hand | `apt update && apt install matrix-synapse-py3` — or as root `messenger-update-ausfuehren.sh`, which installs everything the check reported |
| Element Web by hand | `./install.sh --domain … --mail … --nur element` — only fetches if a newer version is available. The browser client gets no Debian security updates |
| Check backup | monthly |
| **Practice a restore** | **yearly** — a backup that has never been restored is not a backup |
| Certificates | automatic (certbot; coturn gets its copies via a hook) |

In detail: **[docs/operator.md](docs/operator.md)**

## The guide your people get

At the end the installer produces a ready-made guide — filled in with the real
address, the real account name and the initial password, with pictures, to print out
or pass on as a PDF. This is what it looks like (with placeholders, in German):

**[docs/muster-kundenanleitung.html](docs/muster-kundenanleitung.html)**

## Tested

Function test 18 of 18, load test with 50 accounts and 15 writing at the same time,
installation test on a fresh Debian 13. With the complete list of defects — including
the seven bugs the installation test itself brought to light, and the limits of what
the tests prove.

**[docs/test-report.md](docs/test-report.md)**

| | |
|---|---|
| 150 messages from 15 people at the same time | 7.8 s, median 108 ms |
| Encrypted room with 50 members | keys to everyone in 0.51 s |
| Memory under load (of 4 GB) | 366 MB |

## Contributing

Bug reports and improvements are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Security vulnerabilities **not** as a public ticket, but as described in
[SECURITY.md](SECURITY.md).

If this project is worth something to you: donations keep it alive and the development independent.

## What's inside — and from whom

By far the largest part of what runs after installation does **not come from this
project**. That needs saying:

| Component | from | License |
|---|---|---|
| **Matrix** — the protocol | [The Matrix.org Foundation](https://matrix.org) | Apache-2.0 |
| **Synapse** — the server, including the encryption | [Element](https://github.com/element-hq/synapse) | AGPL-3.0 |
| **Element Web** — the browser client | [Element](https://github.com/element-hq/element-web) | AGPL-3.0 |
| **coturn** — voice and video calls | [coturn project](https://github.com/coturn/coturn) | BSD |
| **FluffyChat, Element X** — the phone apps | respective projects | AGPL-3.0 |
| **pyotp, qrcode, cryptography** — second factor of the invitation page and encryption of the TOTP secrets, as Debian packages | [pyotp](https://github.com/pyotp/pyotp), [python-qrcode](https://github.com/lincolnloop/python-qrcode), [pyca/cryptography](https://cryptography.io) | MIT, BSD, Apache-2.0/BSD |
| PostgreSQL, nginx, certbot, Debian | respective projects | — |

**What this project contributes:**

1. **The installer** — one command instead of forty steps, with pre-checks and final checks
2. **The invitation page** — quotas per user, traceable chain, room assignment,
   second factor. This is the only genuinely new software here; Matrix has no
   mechanism by which a **normal user** can invite someone.
3. **The defaults** — each one trivial on its own, together the difference between
   "works for a family" and "an administrator's tinkering project"
4. **Documentation in German first, English alongside**

Synapse and the other components are **not shipped** here; they are fetched from their
official sources during installation. This project is not affiliated with the
Matrix.org Foundation or Element and is not endorsed by them.

If you like the result: the thanks go first to the projects in the table above.
Donations to [matrix.org](https://matrix.org/donate/) benefit the foundation that
everything here stands on.

## Tip jar

Humatrix is a side project with no company behind it. If it is useful to you, you can buy
a coffee at [ko-fi.com/humatrix](https://ko-fi.com/humatrix) — nothing is required, and
nothing changes if you don't.

## License

[AGPL-3.0](LICENSE). Whoever runs and modifies this software passes the changes on.
That is the point: a tool for data sovereignty should not disappear into a closed
service.
