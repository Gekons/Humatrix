#!/bin/bash
#
# Element Web auf die neueste Fassung bringen.
#
# Wird vom Installer (Abschnitt 'element', auch mit --nur element) UND vom
# Update-Lauf (messenger-update-ausfuehren.sh) benutzt - deshalb eine eigene
# Datei statt Installer-Code: der Installer ist aus dem Update-Lauf heraus
# nicht aufrufbar, er braucht --domain und --mail und prueft DNS.
#
# Aufruf:  messenger-element-erneuern.sh [domain]
# Ohne Angabe kommt die Domain aus der Synapse-Konfiguration (server_name.yaml),
# die der Installer schreibt - so ist nichts doppelt gepflegt.
#
set -euo pipefail
umask 022

# Beide Pfade ueberschreibbar nur fuer Tests ohne root.
VERZ="${MESSENGER_ELEMENT_VERZ:-/var/www/element}"
SERVERNAME_DATEI="${MESSENGER_SERVERNAME_DATEI:-/etc/matrix-synapse/conf.d/server_name.yaml}"

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  DOMAIN=$(sed -n 's/^server_name: *//p' "$SERVERNAME_DATEI" 2>/dev/null | tr -d '"'"'"' ' || true)
fi
[ -n "$DOMAIN" ] || { echo "Domain unbekannt: weder als Angabe noch in $SERVERNAME_DATEI." >&2; exit 1; }
MATRIX_HOST="matrix.$DOMAIN"

VER=$(curl -sL --max-time 20 https://api.github.com/repos/element-hq/element-web/releases/latest \
      | jq -r .tag_name 2>/dev/null || true)
[ -n "$VER" ] && [ "$VER" != "null" ] || { echo "Element-Fassung nicht ermittelbar (GitHub nicht erreichbar?)." >&2; exit 1; }

if [ "$(cat "$VERZ/.version" 2>/dev/null)" != "$VER" ]; then
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  curl -fsSL --max-time 300 \
    "https://github.com/element-hq/element-web/releases/download/$VER/element-$VER.tar.gz" \
    -o "$TMP/p.tgz"
  tar xzf "$TMP/p.tgz" -C "$TMP"
  # Das Archiv hat einen Ordner element-vX.Y.Z obenauf; die index.html mit der
  # geringsten Tiefe zeigt, wo die eigentliche Anwendung liegt.
  QUELLE=$(find "$TMP" -name index.html -printf '%d %h\n' | sort -n | head -1 | cut -d' ' -f2-)
  [ -n "$QUELLE" ] || { echo "Archiv ohne index.html - Element hat sein Format geaendert?" >&2; exit 1; }
  mkdir -p "$VERZ"
  rm -rf "${VERZ:?}"/*
  cp -a "$QUELLE"/. "$VERZ"/
  printf '%s\n' "$VER" > "$VERZ/.version"
  echo "Element Web $VER ausgeliefert."
else
  echo "Element Web ist schon auf $VER."
fi

# config.json nach JEDEM Durchlauf: das Entpacken oben raeumt den Ordner leer.
cat > "$VERZ/config.json" <<EOF
{
  "default_server_config": {
    "m.homeserver": { "base_url": "https://$MATRIX_HOST", "server_name": "$DOMAIN" }
  },
  "disable_custom_urls": true,
  "disable_guests": true,
  "default_country_code": "DE",
  "setting_defaults": { "language": "de" },
  "room_directory": { "servers": ["$DOMAIN"] }
}
EOF
chmod 644 "$VERZ/config.json"
