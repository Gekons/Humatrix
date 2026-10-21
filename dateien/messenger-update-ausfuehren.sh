#!/bin/bash
#
# Spielt Aktualisierungen ein - NUR auf Auftrag.
#
# Aufgerufen von messenger-update.service, sobald ein Verwalter in der
# Verwaltung "Jetzt aktualisieren" bestätigt hat (Auftragsdatei unter
# /var/lib/messenger/auftraege/, ihr Inhalt wird nie gelesen), oder von Hand
# als root. Nie vom Timer: der prüft nur.
#
# Was passiert:
#   1. apt-get upgrade (alles, was die Prüfung gemeldet hat - auch Synapse)
#   2. Synapse-Neustart, aber nur, wenn das Paket wirklich erneuert wurde
#   3. Element Web über messenger-element-erneuern.sh
#   4. Ergebnis nach updates.json unter "letzter_lauf", dann Prüfung erneut
#
# Kein 'set -e': jeder Schritt wird einzeln bewertet und gemeldet, ein
# gescheitertes Element-Update soll das Ergebnis von apt nicht verschlucken.
set -uo pipefail
umask 022

DATEI="${MESSENGER_UPDATES_DATEI:-/var/lib/messenger/updates.json}"   # ueberschreibbar nur fuer Tests ohne root
PROTOKOLL="${MESSENGER_UPDATE_PROTOKOLL:-/var/log/messenger-update.log}"
SPERRE="${MESSENGER_UPDATE_SPERRE:-/run/lock/messenger-update.lock}"
HIER="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"   # Geschwisterskripte
export DEBIAN_FRONTEND=noninteractive

# Nur ein Lauf gleichzeitig: Auftrag aus der Verwaltung und Handaufruf
# könnten sich sonst in apt verkeilen. Der zweite geht leer aus und sagt es.
exec 9>"$SPERRE"
if ! flock -n 9; then
  echo "Eine Aktualisierung läuft bereits - dieser Aufruf tut nichts." >&2
  exit 0
fi

# Hilfen
# updates.json nie halb geschrieben hinterlassen: Temp im selben Ordner, dann mv.
json_setzen() {            # json_setzen <jq-Ausdruck> [jq-Argumente...]
  local ausdruck="$1"; shift
  local tmp; tmp=$(mktemp "$(dirname "$DATEI")/.updates.XXXXXX")
  if [ -s "$DATEI" ]; then
    jq "$@" "$ausdruck" "$DATEI" > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  else
    jq -n "$@" "$ausdruck" > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  fi
  chmod 644 "$tmp"; mv -f "$tmp" "$DATEI"
}

LAUFPROTOKOLL=$(mktemp)
BEGONNEN=$(date +%s)
REGULAER_BEENDET=false
# Beim Verlassen "läuft" immer zurücknehmen: bleibt es nach einem Abbruch
# (systemctl stop, Neustart, Stromausfall mitten in apt) stehen, nimmt die
# Verwaltung nie wieder einen Auftrag an. Nur der reguläre Weg unten schreibt
# ein Ergebnis - jeder andere Ausgang zählt als 'abgebrochen'.
aufraeumen() {
  if [ "$REGULAER_BEENDET" != true ]; then
    { echo "=== Lauf $(date -d "@$BEGONNEN" '+%F %T') ABGEBROCHEN ==="; cat "$LAUFPROTOKOLL"; } >> "$PROTOKOLL" 2>/dev/null || true
    local zeilen; zeilen=$(tail -n 40 "$LAUFPROTOKOLL" 2>/dev/null | jq -R -s -c 'split("\n") | map(select(length > 0))' 2>/dev/null || echo '[]')
    json_setzen '.letzter_lauf = {laeuft: false, status: "abgebrochen", begonnen: $b, beendet: $e,
                                  fehlgeschlagen: "abgebrochen", synapse_neu_gestartet: false, protokoll: $z}' \
      --argjson b "$BEGONNEN" --argjson e "$(date +%s)" --argjson z "$zeilen" 2>/dev/null || true
  fi
  rm -f "$LAUFPROTOKOLL"
}
trap aufraeumen EXIT
# Signale in einen normalen exit verwandeln, damit der EXIT-Trap sicher läuft
# (systemctl stop schickt SIGTERM an den ganzen Lauf, auch an apt).
trap 'echo "Abgebrochen (Signal)." >&2; exit 143' TERM INT HUP
sag() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LAUFPROTOKOLL"; }
lauf() {                   # Befehl ausfuehren, Ausgabe ins Laufprotokoll, Status zurueck
  "$@" 2>&1 | tee -a "$LAUFPROTOKOLL"
  return "${PIPESTATUS[0]}"
}

FEHLER=""
mkdir -p "$(dirname "$DATEI")"
# "läuft" sofort setzen: die Verwaltung nimmt solange keinen zweiten Auftrag an.
json_setzen '.letzter_lauf = {laeuft: true, begonnen: $b, status: "laeuft"}' --argjson b "$BEGONNEN" \
  || echo "Warnung: $DATEI nicht beschreibbar." >&2
sag "Aktualisierung beginnt."

# 1. Pakete
synapse_vorher=$(dpkg-query -W -f='${Version}' matrix-synapse-py3 2>/dev/null || true)
lauf apt-get update -qq || sag "apt-get update meldete Fehler - es wird mit dem bekannten Stand weitergemacht."
# confdef/confold: bei geänderten Konfigurationsdateien die vorhandene behalten,
# nie nachfragen - hier sitzt niemand am Terminal.
if lauf apt-get -y -q \
     -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
     upgrade; then
  sag "Pakete eingespielt."
else
  FEHLER="$FEHLER Pakete"
  sag "apt-get upgrade ist fehlgeschlagen."
fi

# 2. Synapse
synapse_nachher=$(dpkg-query -W -f='${Version}' matrix-synapse-py3 2>/dev/null || true)
SYNAPSE_NEU_GESTARTET=false
if [ -n "$synapse_nachher" ] && [ "$synapse_vorher" != "$synapse_nachher" ]; then
  sag "Synapse ${synapse_vorher:-neu} -> $synapse_nachher, Neustart."
  if lauf systemctl restart matrix-synapse; then
    SYNAPSE_NEU_GESTARTET=true
    for _ in $(seq 1 60); do
      curl -sf --max-time 3 http://127.0.0.1:8008/health >/dev/null 2>&1 && break
      sleep 1
    done
    if curl -sf --max-time 3 http://127.0.0.1:8008/health >/dev/null 2>&1; then
      sag "Synapse antwortet wieder."
    else
      FEHLER="$FEHLER Synapse-Neustart"
      sag "Synapse antwortet nach dem Neustart nicht. Siehe: journalctl -u matrix-synapse -n 50"
    fi
  else
    FEHLER="$FEHLER Synapse-Neustart"
  fi
else
  sag "Synapse unverändert (${synapse_nachher:-nicht installiert}), kein Neustart."
fi

# 3. Element
if [ -x "$HIER/messenger-element-erneuern.sh" ]; then
  if ! lauf "$HIER/messenger-element-erneuern.sh"; then
    FEHLER="$FEHLER Element"
    sag "Element Web konnte nicht erneuert werden."
  fi
else
  sag "messenger-element-erneuern.sh fehlt - Element Web bleibt, wie es ist."
fi

# 4. Ergebnis
BEENDET=$(date +%s)
if [ -z "$FEHLER" ]; then STATUS=ok; else STATUS=fehler; fi
sag "Aktualisierung beendet: $STATUS${FEHLER:+ (fehlgeschlagen:$FEHLER)}."
{ echo "=== Lauf $(date -d "@$BEGONNEN" '+%F %T') ==="; cat "$LAUFPROTOKOLL"; } >> "$PROTOKOLL" 2>/dev/null || true

# Die letzten 40 Zeilen für die Verwaltung - das Ganze steht im Protokoll.
zeilen=$(tail -n 40 "$LAUFPROTOKOLL" | jq -R -s -c 'split("\n") | map(select(length > 0))')
json_setzen '.letzter_lauf = {laeuft: false, status: $status, begonnen: $b, beendet: $e,
                              fehlgeschlagen: $f, synapse_neu_gestartet: $sn, protokoll: $z}' \
  --arg status "$STATUS" --argjson b "$BEGONNEN" --argjson e "$BEENDET" \
  --arg f "${FEHLER# }" --argjson sn "$SYNAPSE_NEU_GESTARTET" --argjson z "$zeilen" \
  || echo "Warnung: $DATEI nicht beschreibbar." >&2
REGULAER_BEENDET=true       # ab hier schreibt der EXIT-Trap nichts mehr

# Zum Schluss den Befund erneuern, damit die Verwaltung nicht weiter
# "3 Pakete ausstehend" zeigt, obwohl sie gerade eingespielt wurden.
if [ -x "$HIER/messenger-update-pruefen.sh" ]; then
  "$HIER/messenger-update-pruefen.sh" || echo "Warnung: erneute Prüfung fehlgeschlagen." >&2
fi
[ "$STATUS" = ok ]
