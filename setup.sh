#!/usr/bin/env bash
#
# Humatrix setup: one command on the server, everything else in the browser.
#
#   curl -fsSLo setup.sh https://raw.githubusercontent.com/Gekons/Humatrix/main/setup.sh
#   sudo bash setup.sh
#
# What this does, and nothing more:
#   1. checks that this is Debian 12 or 13 on amd64, run as root
#   2. installs python3 and curl (the setup page needs them)
#   3. puts the installer tree into /opt/humatrix (downloaded from GitHub, or
#      copied from the folder this script lies in when install.sh is next to it)
#   4. starts the setup page on port 8088 and prints its address and a one-time code
#
# The real work (Synapse, nginx, certificate, coturn ...) happens when you press
# "Set up" in the browser; it is install.sh, driven by the page instead of switches.
# The page shuts itself down when you finish.
#
# Messages below are in English and German, the page lets you pick the language.
set -euo pipefail
umask 022

PORT="${HUMATRIX_SETUP_PORT:-8088}"
ZIEL=/opt/humatrix
ZUSTAND=/var/lib/humatrix-einrichtung
QUELLE_URL="${HUMATRIX_QUELLE:-https://github.com/Gekons/Humatrix/archive/refs/heads/main.tar.gz}"
HIER="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

rot()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
ende() { rot "ABORT / ABBRUCH: $*"; exit 1; }

[ "$(id -u)" = 0 ] || ende "Please run as root (sudo). / Bitte als root ausfuehren (sudo)."

. /etc/os-release 2>/dev/null || ende "No /etc/os-release, unknown system. / Kein /etc/os-release, unbekanntes System."
case "${ID:-}:${VERSION_ID:-}" in
  debian:13*|debian:12*) ;;
  *) ende "This needs Debian 12 or 13, not ${PRETTY_NAME:-?}. / Das braucht Debian 12 oder 13, nicht ${PRETTY_NAME:-?}." ;;
esac
BOGEN=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$BOGEN" in
  amd64|x86_64|i386|i686) ;;
  *) ende "matrix.org ships Synapse for amd64/i386 only, not $BOGEN. / matrix.org liefert Synapse nur fuer amd64/i386, nicht $BOGEN." ;;
esac

export DEBIAN_FRONTEND=noninteractive
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null || ! command -v ss >/dev/null; then
  echo "Installing python3 and curl ... / Installiere python3 und curl ..."
  apt-get update -qq
  apt-get install -y -qq python3 curl ca-certificates iproute2 >/dev/null
fi

# Never replace the tree underneath a running install.sh.
if [ -f "$ZUSTAND/lauf.json" ] && pgrep -f "bash $ZIEL/install.sh" >/dev/null 2>&1; then
  ende "install.sh is still running from the setup page; wait for it. / install.sh laeuft noch (Einrichtungsseite); erst abwarten."
fi

# The installer tree. Next to this script (a clone or an unpacked archive) it is
# copied; otherwise the current main branch is downloaded.
mkdir -p "$ZIEL"
if [ -f "$HIER/install.sh" ] && [ -f "$HIER/dateien/einrichtung.py" ]; then
  if [ "$HIER" != "$ZIEL" ]; then
    echo "Copying installer from $HIER to $ZIEL ... / Kopiere den Installer von $HIER nach $ZIEL ..."
    rm -rf "$ZIEL".neu; mkdir -p "$ZIEL".neu
    cp -a "$HIER"/. "$ZIEL".neu/
    rm -rf "$ZIEL".neu/.git
    rm -rf "$ZIEL"; mv "$ZIEL".neu "$ZIEL"
  fi
else
  echo "Downloading installer ... / Lade den Installer ..."
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  curl -fsSL --max-time 120 -o "$TMP/humatrix.tgz" "$QUELLE_URL" \
    || ende "Download failed: $QUELLE_URL / Download fehlgeschlagen."
  mkdir -p "$TMP/baum"
  tar -xzf "$TMP/humatrix.tgz" -C "$TMP/baum" --strip-components=1
  [ -f "$TMP/baum/install.sh" ] && [ -f "$TMP/baum/dateien/einrichtung.py" ] \
    || ende "Archive has no install.sh. / Im Archiv fehlt install.sh."
  rm -rf "$ZIEL"; mv "$TMP/baum" "$ZIEL"
fi
chmod 755 "$ZIEL/install.sh" "$ZIEL/setup.sh" 2>/dev/null || true
chown -R root:root "$ZIEL"

# One-time code for the page. Kept across restarts of the page service; a new
# one is written when the page locks itself after too many wrong attempts.
mkdir -p "$ZUSTAND"; chmod 700 "$ZUSTAND"
if [ ! -s "$ZUSTAND/code" ]; then
  # python3, nicht 'tr </dev/urandom | head': das endet unter pipefail mit SIGPIPE (141).
  ( umask 077; python3 -c "import secrets; print(''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8)))" > "$ZUSTAND/code" )
fi
CODE=$(cat "$ZUSTAND/code")

install -m 644 "$ZIEL/dateien/humatrix-einrichtung.service" /etc/systemd/system/humatrix-einrichtung.service
mkdir -p /etc/systemd/system/humatrix-einrichtung.service.d
cat > /etc/systemd/system/humatrix-einrichtung.service.d/port.conf <<EOU
[Service]
Environment=HUMATRIX_SETUP_PORT=$PORT
Environment=HUMATRIX_QUELLE=$ZIEL
EOU
systemctl daemon-reload
# Auf einem schon eingerichteten Server steht die Firewall: Port fuer die Seite
# oeffnen, "Abschliessen" auf der Seite schliesst ihn wieder.
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
  ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
fi
systemctl enable humatrix-einrichtung.service >/dev/null 2>&1 \
  || ende "systemctl enable failed. / systemctl enable fehlgeschlagen."
systemctl restart humatrix-einrichtung.service \
  || ende "The setup page did not start. See: journalctl -u humatrix-einrichtung -n 30 / Die Einrichtungsseite startet nicht."

# Wait for the port, then say where to go.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then break; fi
  sleep 1
done
if ! systemctl is-active --quiet humatrix-einrichtung.service \
   || ! ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
  ende "The setup page did not start (port $PORT busy?). See: journalctl -u humatrix-einrichtung -n 30 / Die Einrichtungsseite startet nicht (Port $PORT belegt?)."
fi

IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1 || true)
[ -n "$IP" ] || IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
printf '\033[1m%s\033[0m\n' "Open in your browser / Im Browser oeffnen:"
printf '   \033[32mhttp://%s:%s/\033[0m\n' "$IP" "$PORT"
echo
printf '\033[1m%s\033[0m\n' "One-time code / Einmalcode:"
printf '   \033[32m%s-%s\033[0m\n' "${CODE:0:4}" "${CODE:4:4}"
echo
echo "The page asks for domain, language and the first account, then installs everything."
echo "Die Seite fragt Domain, Sprache und das erste Konto ab und richtet dann alles ein."
echo
echo "Show the code again / Code nochmal anzeigen:   cat $ZUSTAND/code"
echo "Stop the page / Seite abschalten:               systemctl disable --now humatrix-einrichtung"
