**English** | [Deutsch](testbericht.md)

# Test report

As of: 13 October 2026

This report records what was actually tested, with what result, and where the limits
of the statement lie. It is deliberately precise also where something did not work —
a test report without a list of defects is not one.

## What was tested

| Test | Scope | Result |
|---|---|---|
| Function test | 18 individual checks: login, rooms, messages, media, members, logout, permissions | **18 of 18** |
| Load test | 50 accounts, 15 writing at the same time, real end-to-end encryption | passed, figures below |
| Installation test | installation script on a fresh Debian 13, repeated several times | **7 runs needed**, 7 bugs found and fixed |

All tests run against a real server with real encryption — nothing is simulated.
The test clients are `matrix-nio` with `python3-olm`, i.e. the same Olm/Megolm library
the phone apps use.

## Test environment

A container on a Proxmox machine, deliberately kept small so that the figures apply
to a rented server at eight to ten euros a month:

- 4 cores, 4 GB memory, 40 GB disk
- Debian 13, Synapse 1.162 on PostgreSQL (collation C)
- Network: separate, isolated segment behind the firewall

## Function test: 18 of 18

What is tested is what a normal person does in a chat — not what is easy to test.

**Login and rooms**
- Three accounts created and logged in
- Encrypted group room created
- Second participant joined

**Messages**
- Text message arrives decrypted
- Redacted message disappears (server reports it as redacted)
- Reply to a message
- Reaction set

**Media**
- Image is on the server only as `m.room.encrypted` — the content is not readable
  there
- Image downloaded and decrypted, checksum matches
- File uploaded and verifiable in the media store

**Members**
- Third member added
- New member reads new messages
- New member does **not** read old messages — as intended
- Removed member receives nothing more

**Logging out and coming back**
- Readable again after re-login with the same device store
- New device without keys does **not** see the old history — as intended,
  and exactly why key backup is needed

**Permissions**
- Room renamed
- An ordinary member may not kick the founder

The two "as intended" points are the most important of the whole report: they
prove that the encryption really takes effect. Whoever joins later can't read what
was said before — not even the server operator.

## Load test: 50 accounts, 15 at the same time

All 50 accounts log in with a real device and upload their keys. Only this way does
the real load of Matrix arise: the group key has to be distributed to **every** device
individually.

### Creating accounts

| | |
|---|---|
| 50 accounts created and logged in | 33 s |
| per account | 0.65 s |

### One encrypted room with 50 members

| | |
|---|---|
| Inviting and joining, 49 people | 170 s |
| Members according to the database | 50 |
| Room encrypted | yes |

The 170 seconds are mostly the test setup, not the server: the test clients process
the joins one after the other. The server was largely idle during this.

### Messages

| | |
|---|---|
| First message (distribute key to all 50 devices) | 0.51 s |
| Second message (key already present) | 0.05 s |
| Sample: read decrypted | 5 of 5 |

### 15 people writing at the same time

| | |
|---|---|
| Messages sent | **150 of 150** |
| Duration | 7.8 s |
| Throughput | 19.1 messages/s |
| Response time median | **108 ms** |
| Response time mean | 535 ms |
| 95 percent below | 4,440 ms |
| slowest | 4,527 ms |

The median is a tenth of a second. The long tail of four and a half seconds is the
**first** message of each sender: that is where the group key is delivered to 50
devices. After that it is fast. For everyday use this means: it feels snappy, the
first sentence after a long pause can take a moment.

### Media

| Size | Duration | Throughput |
|---|---|---|
| 50 KB | 0.01 s | 4.9 MB/s |
| 500 KB | 0.03 s | 18.4 MB/s |
| 2 MB | 0.08 s | 24.9 MB/s |
| 5 MB | 0.24 s | 21.0 MB/s |

### Rooms and isolation

| | |
|---|---|
| 10 rooms created | 2.2 s |
| Outsider does not see someone else's room | confirmed |
| Member sees their room | confirmed |

### What it cost the server

| | before | after 50 accounts | after the load |
|---|---|---|---|
| Memory total | 293 MB | 313 MB | 366 MB |
| of which Synapse | 140 MB | 141 MB | 146 MB |
| Database | 33 MB | 33 MB | 42 MB |
| Events | 570 | 570 | 828 |

The server has 4 GB. Under the test run 366 MB were used — just under a tenth.
Synapse itself grew by 6 MB. For the intended scale (about 50 people, rarely more
than 15 at the same time) a small rented server is amply sized.

## Installation test: seven attempts, seven bugs

The installation script was run on a **fresh** Debian 13, the way a stranger would
use it. It took seven runs until all ten final checks passed. Every failure was a
real bug:

**1. No way for servers behind an existing reverse proxy.**
The script fetched certificates via port 80 on the server itself. Anyone already
running a proxy at home can't do that. Fixed: new switch `--hinter-proxy` (today also `--behind-proxy`).
The server then serves plain text on port 80, the proxy takes care of TLS.

**2. The name check aborted with split internal and external DNS.**
At home the names point to the proxy on the inside and to the connection on the
outside. The script took that for an error. Fixed: in proxy mode it is only reported.

**3. `external-ip` for coturn was missing entirely.**
Behind a router coturn otherwise tells the call participants a private address as
the relay — calls fail silently. Fixed: the script detects the router itself and
enters public and private address.

**4. A second run aborted.**
"Port 80 ist schon belegt" (port 80 already in use) — by its own web server from
the first run. The script explicitly promised that a second run breaks nothing.
Fixed.

**5. `umask 077` applied to the whole script.**
A tight mask for one secret file applied to everything from then on. Consequence:
the delegation under `/.well-known/matrix/server` and Element's `config.json` were
not readable for the web server — "403 Forbidden", without anything anywhere saying
why. Fixed: every tight mask is now in its own subshell, and the served files get
their permissions set explicitly.

**6. A repeat run destroyed the admin password.**
If the account already existed, the script wrote "(unverändert — stand schon in
dieser Datei)" (unchanged — was already in this file) — and in doing so overwrote
exactly the file it was pointing to. The password was gone. Fixed: the old file is
backed up first and the password taken over from it.

**7. The invitation service addressed Synapse via the public address.**
Every admin call thus went through router and proxy back to the same machine — and
stood still as long as the proxy didn't yet know the name. Fixed: the service
addresses Synapse locally.

Two further small things were fixed along the way: the help mentioned a section
`verwaltung` that doesn't exist at all, and the credentials file pointed to a path
that was only set afterwards — the line stayed empty.

### Final state

Installer output (this run was in German; since then the installer has `--language en|de`, default English):

```
== Nachkontrolle
   OK   Synapse antwortet
   OK   Delegation ausgeliefert
   OK   Matrix erreichbar
   OK   Servername ist <domain>
   OK   Element Web
   OK   Einladungsseite
   OK   Verwaltung antwortet
   OK   coturn laeuft
   OK   Einladungsdienst laeuft
   OK   Firewall aktiv

Fertig. Alle Pruefungen bestanden.
```

Afterwards the script was called once more with `--nur turn`, to redo only the call
part after an address change. That went through as well.

## Review run before publication: 66 raw findings, 41 confirmed, 41 fixed

Before publication the whole package was read by six independent reviewers, each
from one angle — installer correctness, security of the service, correctness of the
service, consistency of the documentation, privacy and traces, legal and promises.
Each of the 66 raw findings was then read by a further reviewer with the explicit
task of **refuting** it. 25 fell through, 41 stood. All 41 were fixed and the fix
verified; the most important ones:

- **Sessions could not be revoked.** Suspending, a new password or a changed second
  factor did not end a running invitation session — it kept running for up to twelve
  hours. Now every session carries its issue time, and a suspension invalidates
  everything older. Proven over HTTP: password reset via the admin page, old session
  rejected immediately. (The `curl` route from the operator guide only logs out
  Matrix devices.)
- **"Create account by hand" overwrote an existing account** including password and
  admin rights, because the call to Synapse creates *or* modifies. Now it is checked
  first; a taken name is rejected.
- **The invitation chain guessed.** Two redemptions in the same minute or an account
  created by hand in between ended up with the wrong inviter — and thus in the wrong
  room. Now assignment happens only when unambiguous, otherwise the invitation stays
  open and the journal reports it.
- **`ufw --force reset` with a hard-coded port 22** would have locked out anyone who
  moved SSH to another port, and deleted other rules on every run. Now the port comes
  from `sshd -T`, and the reset happens only the first time.
- **The service listened on all interfaces** instead of locally only — directly
  reachable for every machine in the same network segment, bypassing nginx.
- **Roughly every 17th login failed for no reason**, because the session signature
  stood as raw bytes behind a separator it could itself contain (measured: 5.8 % of
  20,000). Now hex; 60 of 60 real logins.
- **The Git history contained the unsanitised images** — with TOTP key, real domain
  and valid invitation code. That is why the history of this repository is
  deliberately short: it begins with the cleaned-up state.

The procedure is not infallible — it finds what you give it in terms of angles.
But in one pass it found more than two days of development and testing before it.
If you want to contribute further angles: `CONTRIBUTING.md`.

## What this report does NOT prove

Honesty about the limits is part of it:

- **No sustained load.** What was measured was a peak of 150 messages, not operation
  over weeks. What the database looks like after a year, this test doesn't say.
- **No real phones.** The test clients use the same encryption as the apps, but
  they are not apps. Handling errors, battery behaviour and notifications are left
  out.
- **No voice and video calls over TURN tested.** coturn runs and is configured
  correctly; a real call between two networks was not measured.
- **No federation tested.** The server is set up for exchange with other Matrix
  servers; only its own was tested.
- **Only one platform tested.** Tested was Debian 13 on `amd64`. Debian 12 is
  permitted by the installer but not tested through. Ubuntu, ARM (Raspberry Pi) and
  Windows are not the subject of this report — for ARM matrix.org has no Synapse
  package at all, the installer aborts there with that reason.
- **No attack test.** This is a function and load test, not a security audit.
  If you are looking for weaknesses: see `SECURITY.md`.
- **The check "members come and go"** from the load test was aborted early
  (handling error during the test run, not on the server). The same point is
  included in the function test and passed there.

## How to reproduce it

The two test scripts are not in the package because they require a test
environment and create real accounts. If you want them: ask via the issues. The
load test is around 360 lines of Python and needs `matrix-nio` with `python3-olm`.
