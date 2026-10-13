**English** | [Deutsch](betreiber.md)

# Operator guide — setting up a customer server

For you. From ordering to handover. Allow **30 minutes**, about 10 of them
waiting time.

---

## Up front: one server per customer

This installer builds **a separate server for each customer**. That is deliberate:
every family has its own machine, its own domain, its own data — and can move out at
any time. That is precisely the selling point.

So the maths differs from a shared server:

| Item | per customer |
|---|---|
| Hetzner Cloud CX32 (4 cores, 8 GB, 80 GB) | ~8 €/month |
| Domain | ~1 €/month |
| **Your costs** | **~9 €/month** |
| Sensible price | **15–19 €/month** |

The margin is thinner than with a shared server — in return the promise is true and
liability is cleanly separated. If you later have many small customers, a multi-tenant
setup pays off; but that needs a different installer.

**Don't forget:** your real cost item is support time, not hardware.

---

## Step 1 — Domain

The customer decides the name. **It is baked into every user ID for good**
(`@anna:familie-mueller.de`) and can't be changed later without recreating all
accounts. Tell the customer that beforehand.

Who buys the domain — you or the customer — is a question of trust. My advice:
**the customer buys it themselves**. Then it belongs to them, and "you can move at
any time" is not an empty phrase. You only need access to the DNS records.

## Step 2 — Order the server

At Hetzner Cloud:

| | |
|---|---|
| Location | Falkenstein or Nuremberg |
| Image | **Debian 13** |
| Type | **CX32** (4 cores, 8 GB, 80 GB) |
| SSH key | add yours — **no** password login |
| Network | IPv4 + IPv6 |

Not smaller than 8 GB. Synapse grows with the rooms, and upgrading in production is
more of a nuisance than ordering right the first time.

## Step 3 — Set DNS

Six A records pointing to the IP of the new server:

```
<domain>              A   <IP>
matrix.<domain>       A   <IP>
chat.<domain>         A   <IP>
einladung.<domain>    A   <IP>
verwaltung.<domain>   A   <IP>
turn.<domain>         A   <IP>
```

Then **wait until they are visible** — usually one to ten minutes. Check with:

```bash
for n in "" matrix. chat. einladung. verwaltung. turn.; do
  echo "$n<domain> -> $(dig +short @1.1.1.1 $n<domain> A)"
done
```

The installer checks this anyway and aborts if something is missing — but better to
look first than to wait for an abort.

## Step 4 — Upload and start the installer

Fetch the repository on your machine and copy it to the server (the fresh server has
no `git`, hence the detour via `scp`):

```bash
git clone https://github.com/Gekons/Humatrix.git
scp -r Humatrix root@<IP>:/root/
ssh root@<IP>
cd /root/Humatrix
./install.sh --domain <domain> --mail <deine-mail> --verwalter <kundenname> \
             --betreiber "<dein Name>, <deine-mail>" \
             --sicherung u123456@u123456.your-storagebox.de:/messenger/<domain>
```

- `--verwalter` is the **customer's username**, lowercase and without spaces
- `--mail` is **your** address — that is where Let's Encrypt warns you if a certificate
  wasn't renewed. You want to know that, not the customer.
- `--betreiber` is optional, but sensible when serving customers: name and contact
  details then appear in the customer guide ("Betreut von …", "Melde dich bei …" —
  "looked after by", "get in touch with") and in the notes of the invitation service.
  Without it, it just says "your contact person" / "the operator" (or the German
  equivalent).
- `--language en|de` is the language of the installer's output, the web pages
  (invitation and admin page) and the customer guide. Default `en` — for a German
  customer pass `--language de`. A repeat run keeps the language of the existing
  installation; `--language` sets it anew. The customer can switch the web pages later in the
  admin page (card "Sprache / Language"); each invitation also carries its own language,
  chosen when it is created, and an invitation without one follows the browser of the
  person who opens it. The switches themselves, the section names for `--nur` and the
  host name prefixes stay the same in both languages.
- `--behind-proxy` (same as `--hinter-proxy`) if a reverse proxy in front already owns
  ports 80 and 443 — see the README for what changes then.
- `--sicherung` is optional. Without it the server backs up locally to
  `/var/backups/messenger` (14 days) — better than nothing, but not a real backup.
  With it, the **server's SSH key must be registered at the target**: the installer
  creates `/root/.ssh/id_ed25519` if needed, records the target's host key and
  tests access with a trial transfer via `rsync` (which also creates the target
  directory). If that fails, it does **not** start the backup timer, shows the public
  key, and you register it at the target — for the Hetzner Storage Box in the Hetzner
  console under the box; it listens on port 23, which then goes into
  `/root/.ssh/config`, the installer tells you. After that
  `./install.sh … --nur sicherung`. For the first server on a new target this second
  attempt is the normal case, not an error.

Before the first change the installer checks the system, memory, disk space, all six
names and the ports. If something is wrong, it aborts without having touched anything
(single exception: a missing `curl` is installed beforehand, the check needs it).

At the end ten final checks run, eleven with `--sicherung` — the last one checks whether
the backup target is reachable. If all are green, the server is done.

## Step 5 — What comes out of it

| File on the server | Content |
|---|---|
| `/root/zugangsdaten-messenger.txt` | all credentials and the pointer to the backup key — **for you** |
| `/root/.sicherung_passwort` | key of the encrypted backup — **into Vaultwarden** |
| `/root/kundenanleitung-<domain>.html` | fully filled-in guide — **for the customer** |
| `/var/log/messenger-installer.log` | log, in case something went wrong |
| `/var/lib/messenger/updates.json` | result of the daily update check (shown by the admin page) |
| `/var/log/messenger-update.log` | log of every update that was installed |

## Step 6 — Handover

**Fetch the guide:**

```bash
scp root@<IP>:/root/kundenanleitung-<domain>.html .
```

Open it in the browser, **Ctrl+P → "Save as PDF"**. Done. (Deliberately no PDF
generator on the server — that would pull in around a hundred packages a messenger
doesn't need.)

**Then send separately:**

| What | How |
|---|---|
| The guide (PDF) | mail is fine |
| The **initial password** | **Not** in the same mail. Phone, Signal, in person. |

The password is in the guide, admittedly — so: either you send the guide securely
(not by plain mail), **or** you delete the password line before sending and say it
over the phone. This is the only point where a clean setup can get sloppy.

**Secure the backup key:** `cat /root/.sicherung_passwort` — that is the key of the
backup (the credentials file only points to it). **Without it no backup can be
restored.** It belongs in your Vaultwarden before you forget about the server for the
first time.

## Step 7 — First conversation with the customer

Show them three things, no more:

1. **Log in and change the password.**
2. **Set up the second factor** — their account can delete people. Don't skip this.
3. **Send an invitation.** Preferably to you, so they see how it arrives at the
   recipient's end.

Also tell them openly **what you can see**: that someone wrote, when and with whom —
not what. The guide says the same. This openness is part of the product.

---

## Operation

| What | How often | How |
|---|---|---|
| Security updates | automatic | Debian security only, everything else deliberately |
| Everything else: system packages, Synapse, Element Web | checked daily, installed only on a click | A timer (`messenger-update-pruefen.timer`, from 06:00) checks and stores the result in `/var/lib/messenger/updates.json`; the admin page shows it at the top in the "Aktualisierungen" (updates) card. If the customer clicks "Jetzt aktualisieren" (update now) there and confirms the prompt, the service places a job file under `/var/lib/messenger/auftraege/`; `messenger-update.path` sees it and starts the run as root (apt-get upgrade, Synapse restart only on a new package, Element Web). The result then appears in the admin page and in `/var/log/messenger-update.log`. |
| The same by hand | as needed | as root `messenger-update-ausfuehren.sh` — same run, same log; `messenger-update-pruefen.sh` only refreshes the result |
| Synapse alone | deliberately | `apt update && apt install matrix-synapse-py3`, then `systemctl status matrix-synapse` |
| Element Web alone | deliberately, e.g. monthly | `./install.sh --domain … --mail … --nur element` (calls `messenger-element-erneuern.sh`); compared against `/var/www/element/.version`, updated only if a newer version exists. The browser client gets no Debian security updates. |
| Check backup | monthly | `systemctl list-timers messenger-sicherung*` and check whether files are arriving |
| **Practice a restore** | **yearly** | A backup that has never been restored is not a backup. |
| Certificates | automatic | certbot renews; coturn gets its copies via a hook |
| Disk space | as needed | Media grows fastest — your own photos and videos are never cleared, only the cache of other servers (90 days). `du -sh /var/lib/matrix-synapse/media` |

**Your own Synapse settings** (federation off, server-wide search, …) belong in
`/etc/matrix-synapse/conf.d/50-eigene.yaml`. The installer never touches this file —
`20-betrieb.yaml`, on the other hand, is rewritten on every run; changes there are gone
after the next run. Synapse reads all `conf.d/*.yaml` in name order; a top-level key
from a later file replaces the one from the earlier file completely, so always copy the
whole block (e.g. `user_directory:` with all three lines). Create the file like the
others (`chown root:matrix-synapse`, `chmod 640`), then
`systemctl restart matrix-synapse`.

## When something gets stuck

| Symptom | Cause | Remedy |
|---|---|---|
| Installer aborts at DNS | records not yet visible | wait, start again — it is repeatable |
| Certificate fails | port 80 in use or DNS wrong | `ss -lnt`, check DNS, then `--nur web` |
| Calls don't connect | UDP blocked | check `ufw status`; at Hetzner also the cloud firewall |
| "Too many attempts" when logging in | Synapse throttles after a few attempts | wait a minute. The password is usually right. |
| Customer has lost their phone (2FA) | — | `python3 /opt/einladung/einladung.py --zweitfaktor-aus <name>` as root on the server; the username is enough, the full ID works too |
| Customer has forgotten their password | — | reset via the admin page ("Passwort neu" — with confirmation; the new password appears **exactly once**, write it down immediately and pass it on). If it concerns the admin account itself, as root via the admin API, see below. `register_new_matrix_user` is **not** suitable for this — it only creates new accounts and aborts if the ID is taken |
| Repeat a single section | — | `./install.sh --domain … --mail … --nur turn` |
| Admin page shows "Noch nicht geprüft" / "Not checked yet" | timer not running or the check fails | `systemctl status messenger-update-pruefen.timer`, then `messenger-update-pruefen.sh` by hand — the error appears on screen. Existing server without this card: run the installer again **in full** (it is repeatable) — that also brings the invitation service up to date |
| "Jetzt aktualisieren" / "Update now" does nothing | path unit not active or the service may not write to the job folder | `systemctl status messenger-update.path`, `ls -ld /var/lib/messenger/auftraege` (owned by `einladung`, 750), `journalctl -u messenger-update -n 50` |
| Your own firewall rules (WireGuard, restricted SSH) | — | are kept on repeat runs: `ufw` is only reset while it is not yet active; after that the hardening only sets its own rules and takes the SSH port from the running `sshd` configuration |

**Reset a password as root** — for any account, including the admin account. The
better way is the admin page ("Passwort neu"): it sets the password, logs out the
Matrix devices **and** ends a running session of the account on
`einladung.`/`verwaltung.<domain>`. The call below (it is also in
`/root/zugangsdaten-messenger.txt` under "PASSWORT NEU SETZEN") is the emergency exit
when nobody can get into the admin page any more. It affects **Matrix only**: a session
on the invitation or admin page keeps running for up to twelve hours afterwards.

```bash
curl -s -X POST -H "Authorization: Bearer $(cat /etc/einladung/admin-token)" \
     -H 'Content-Type: application/json' \
     -d '{"new_password":"NEUES-PASSWORT","logout_devices":true}' \
     http://127.0.0.1:8008/_synapse/admin/v1/reset_password/@<name>:<domain>
```

If the admin account may have been taken over, then end all invitation and admin
sessions of **all** accounts afterwards — the service re-reads the key on every check,
no restart needed; the file's permissions are preserved when overwriting:

```bash
openssl rand -base64 48 > /etc/einladung/sitzungsschluessel
```

## What this installer doesn't do

- **No monitoring.** You won't find out on your own when a customer server is down.
  For more than two or three customers you need an availability check.
- **No automatic updates for Synapse and Element Web.** Deliberately — checked daily,
  triggered only on a click in the admin page or by you via script (see Operation).
- **No multi-tenant operation.** One server, one customer.
