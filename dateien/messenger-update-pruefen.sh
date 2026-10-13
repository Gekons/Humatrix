#!/bin/bash
#
# Prueft, ob Aktualisierungen vorliegen - und tut sonst NICHTS.
#
# Grundsatz: nichts wird blind eingespielt. Dieses Skript laeuft taeglich
# (messenger-update-pruefen.timer), schreibt sein Ergebnis nach updates.json,
# und die Verwaltungsseite zeigt es an. Eingespielt wird erst, wenn ein
# Verwalter dort klickt (messenger-update-ausfuehren.sh).
#
# Ergebnis (JSON, lesbar fuer den Einladungsdienst):
#   geprueft      Unixzeit der Pruefung
#   synapse       {ist, neu}    installierte Fassung und Kandidat aus apt
#   element       {ist, neu}    /var/www/element/.version und neueste GitHub-Fassung
#   pakete        {gesamt, sicherheit, liste[..30]}
#   letzter_lauf  bleibt aus dem vorigen Stand erhalten (schreibt das Ausfuehrskript)
#
set -euo pipefail
umask 022

AUSGABE="${MESSENGER_UPDATES_DATEI:-/var/lib/messenger/updates.json}"   # ueberschreibbar nur fuer Tests ohne root
ELEMENT_VERSION="${MESSENGER_ELEMENT_VERSION:-/var/www/element/.version}"
SPERRE="${MESSENGER_UPDATE_SPERRE:-/run/lock/messenger-update.lock}"   # haelt das Ausfuehrskript, solange es arbeitet
export DEBIAN_FRONTEND=noninteractive

# Ohne Netz zaehlt der zuletzt bekannte Paketstand - lieber ein alter Befund
# als gar keiner. Der Fehler steht im Journal.
apt-get update -qq 2>&1 | tail -3 >&2 || true

# --- Systempakete -------------------------------------------------------
# Zeilenform: nginx/stable-security 1.26.3-3+deb13u9 amd64 [upgradable from: ...]
# Sicherheitsupdate heisst: die Quelle hinter dem Schraegstrich enthaelt '-security'.
# 'Listing...' ist die Kopfzeile von apt, keine Paketzeile.
pakete_roh=$(apt list --upgradable 2>/dev/null | grep -v '^Listing' | grep -v '^$' || true)
pakete=$(printf '%s\n' "$pakete_roh" | jq -R -s -c '
  split("\n") | map(select(length > 0)) | map(
    (split(" ")) as $t
    | ($t[0] | split("/")) as $nq
    | { name: $nq[0],
        quelle: ($nq[1] // ""),
        neu: ($t[1] // ""),
        alt: ((capture("from: (?<a>[^\\]]+)") | .a) // ""),
        sicherheit: (($nq[1] // "") | test("-security")) })
  | { gesamt: length,
      sicherheit: (map(select(.sicherheit)) | length),
      liste: .[:30] }')

# --- Synapse ------------------------------------------------------------
synapse_ist=$(dpkg-query -W -f='${Version}' matrix-synapse-py3 2>/dev/null || true)
synapse_neu=$(apt-cache policy matrix-synapse-py3 2>/dev/null | awk '/Candidate:/{print $2; exit}' || true)
[ "$synapse_neu" != "(none)" ] || synapse_neu=""

# --- Element Web --------------------------------------------------------
# Der Browser-Client kommt nicht aus Debian; seine Fassung steht nur auf GitHub.
# Antwortet GitHub nicht, heisst es ehrlich "unbekannt" - kein Abbruch.
element_ist=$(cat "$ELEMENT_VERSION" 2>/dev/null || true)
element_neu=$(curl -sf --max-time 15 https://api.github.com/repos/element-hq/element-web/releases/latest 2>/dev/null \
              | jq -r '.tag_name // empty' 2>/dev/null || true)
[ -n "$element_neu" ] || element_neu="unbekannt"

# --- Ergebnis atomar ablegen ---------------------------------------------
# Den vorigen 'letzter_lauf' mitnehmen: die Verwaltung soll das Ergebnis des
# letzten Einspielens auch nach der naechsten Pruefung noch zeigen.
letzter_lauf=$(jq -c '.letzter_lauf // null' "$AUSGABE" 2>/dev/null || echo null)
[ -n "$letzter_lauf" ] || letzter_lauf=null
# Steht dort noch "laeuft", obwohl niemand die Sperre haelt, ist der Lauf tot
# (Neustart, Stromausfall, SIGKILL mitten in apt). Sonst bliebe die Karte fuer
# immer blockiert. Haelt jemand die Sperre, laeuft er wirklich - nichts anfassen.
if [ "$(jq -r 'if type == "object" then .laeuft == true else false end' <<<"$letzter_lauf")" = true ] \
   && flock -n "$SPERRE" true 2>/dev/null; then
  letzter_lauf=$(jq -c '. + {laeuft: false, status: "abgebrochen", fehlgeschlagen: "abgebrochen", beendet: $e}' \
                 --argjson e "$(date +%s)" <<<"$letzter_lauf")
  echo "Voriger Update-Lauf ist nicht zu Ende gekommen (Sperre frei) - als abgebrochen vermerkt." >&2
fi
mkdir -p "$(dirname "$AUSGABE")"
tmp=$(mktemp "$(dirname "$AUSGABE")/.updates.XXXXXX")
jq -n \
  --argjson geprueft "$(date +%s)" \
  --arg s_ist "$synapse_ist" --arg s_neu "$synapse_neu" \
  --arg e_ist "$element_ist" --arg e_neu "$element_neu" \
  --argjson pakete "$pakete" \
  --argjson letzter_lauf "$letzter_lauf" \
  '{ geprueft: $geprueft,
     synapse: { ist: $s_ist, neu: $s_neu },
     element: { ist: $e_ist, neu: $e_neu },
     pakete: $pakete,
     letzter_lauf: $letzter_lauf }' > "$tmp"
chmod 644 "$tmp"
mv -f "$tmp" "$AUSGABE"

echo "Geprueft: $(jq -r '.pakete.gesamt' "$AUSGABE") Paket(e) aktualisierbar, davon $(jq -r '.pakete.sicherheit' "$AUSGABE") Sicherheit; Synapse ${synapse_ist:-fehlt} -> ${synapse_neu:-?}; Element ${element_ist:-fehlt} -> $element_neu"
