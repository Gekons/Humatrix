#!/usr/bin/env bash
#
# Messenger-Installer — ein eigener Matrix-Server für den eigenen Kreis.
#
# Baut auf einem frischen Debian 13 in einem Durchgang:
#   Synapse auf PostgreSQL · TLS · .well-known-Delegation · coturn für Anrufe
#   Element Web · Verwaltungsoberfläche · Einladungsseite · Sicherung
#   Update-Prüfung (einspielen nur auf Klick) · Härtung
#
# Aufruf:   sudo ./install.sh --domain beispieldomain.de --mail du@example.de --verwalter anna
# Hilfe:    ./install.sh --hilfe        (deutsch: ./install.sh --language de --hilfe)
#
# Entwurfsgrundsätze:
#   - Es wird nichts geraten. Was nicht stimmt, bricht VOR der ersten Änderung ab.
#   - Jeder Abschnitt ist wiederholbar: nochmal laufen lassen schadet nicht.
#   - Geheimnisse landen in /root/zugangsdaten-messenger.txt, nie auf dem Bildschirm.
#   - Zwei Sprachen (--language en|de). Die Schalter, Abschnittsnamen, Dateinamen
#     und Hostnamen-Präfixe bleiben in beiden Sprachen gleich: sie stehen in
#     DNS-Anleitungen und in der Doku. Das Protokoll in $LOG bekommt dieselben
#     Zeilen wie der Bildschirm, also ebenfalls in der gewählten Sprache.
#
set -euo pipefail

# Ausdrückliche Grundmaske. Vorher erbte das Skript die der Sitzung, und ein
# 'umask 077' für eine Geheimnisdatei galt ab da für ALLES - mit der Folge,
# dass nginx seine eigene Konfiguration, die Delegation und Elements
# config.json nicht mehr lesen konnte. Jede enge Maske steht ab jetzt in
# einer Unterschale und endet mit ihr.
umask 022

# Vorgaben
DOMAIN=""; MAIL=""; VERWALTER="verwalter"
SICHERUNG_ZIEL=""            # z.B. u123456@u123456.your-storagebox.de:/messenger
BETREIBER=""                 # steht in der Kundenanleitung: "Name, mail@example.de"
NUR=""                       # einzelnen Abschnitt wiederholen
HINTER_PROXY=nein            # TLS macht ein vorgelagerter Reverse-Proxy
SPRACHE=en                   # Sprache von Installer, Dienst und Kundenanleitung: en|de
UMGEBUNG=/etc/systemd/system/einladung.service.d/umgebung.conf   # Umgebung des Einladungsdienstes
HILFE=nein
LOG=/var/log/messenger-installer.log
ZUGANG=/root/zugangsdaten-messenger.txt

# Sprachtabellen
# Jeder Schluessel steht in beiden Tabellen; %s sind printf-Platzhalter, die
# Reihenfolge der Werte ist in beiden Sprachen dieselbe.
declare -A T_de T_en

T_de[abbruch]="ABBRUCH"
T_en[abbruch]="ABORT"

T_de[hilfe]="Messenger-Installer

  --domain  <name>     Pflicht. Die nackte Domain, z.B. beispieldomain.de
                       Kennungen werden daraus: @name:beispieldomain.de
  --mail    <adresse>  Pflicht. Für die Zertifikate (Let's Encrypt benachrichtigt dort).
  --verwalter <name>   Name des ersten Kontos mit Verwaltungsrechten. Vorgabe: verwalter
  --sicherung <ziel>   Optional. SSH-Ziel für die tägliche Sicherung,
                       z.B. u123456@u123456.your-storagebox.de:/messenger
  --betreiber <text>   Optional. Wer den Server betreut - steht in der Kundenanleitung.
                       z.B. \"Vorname Nachname, hilfe@example.de\"
  --language en|de     Sprache von Installer, Verwaltungsseiten und Kundenanleitung.
                       Vorgabe: en. Die Verwaltung kann später umschalten.
  --hinter-proxy       Dieser Server steht hinter einem vorhandenen Reverse-Proxy
  --behind-proxy       (Nginx Proxy Manager, Traefik, Caddy ...). Dann holt der
                       Installer KEIN Zertifikat und liefert alles auf Port 80 aus -
                       um TLS kümmert sich der Proxy. Nötig, sobald die Ports 80
                       und 443 der öffentlichen Adresse jemand anderem gehören.
                       Beide Schreibweisen sind gleichwertig.
  --nur <abschnitt>    Nur einen Abschnitt wiederholen:
                       pakete datenbank synapse web turn element
                       einladung sicherung updates haertung
  --hilfe / --help     Diese Hilfe.

  Englische Schreibweisen --admin --backup --operator --only --behind-proxy
  gehen genauso; Abschnittsnamen sind in beiden Sprachen gleich.

Vorher einzurichten (der Installer prüft es und bricht sonst ab):
  A-Einträge auf die IP dieses Servers für:  (mit --hinter-proxy: auf den Proxy)
     <domain>  matrix.<domain>  chat.<domain>  invite.<domain>
     service.<domain>  turn.<domain>"
T_en[hilfe]="Messenger installer

  --domain  <name>     Required. The bare domain, e.g. example.com
                       User IDs are built from it: @name:example.com
  --mail    <address>  Required. For the certificates (Let's Encrypt notifies there).
  --admin   <name>     Name of the first account with admin rights. Default: verwalter
  --backup  <target>   Optional. SSH target for the daily backup,
                       e.g. u123456@u123456.your-storagebox.de:/messenger
  --operator <text>    Optional. Who looks after the server - appears in the customer guide.
                       e.g. \"First Last, help@example.com\"
  --language en|de     Language of installer, admin pages and customer guide.
                       Default: en. The admin page can switch later.
  --behind-proxy       This server sits behind an existing reverse proxy
  --hinter-proxy       (Nginx Proxy Manager, Traefik, Caddy ...). The installer then
                       fetches NO certificate and serves everything on port 80 -
                       the proxy takes care of TLS. Needed as soon as ports 80 and
                       443 of the public address belong to someone else.
                       Both spellings are equivalent.
  --only <section>     Repeat one section only:
                       pakete datenbank synapse web turn element
                       einladung sicherung updates haertung
  --help / --hilfe     This help.

  The German spellings --verwalter --sicherung --betreiber --nur --hinter-proxy
  work as well; section names are the same in both languages.

To set up beforehand (the installer checks it and aborts otherwise):
  A records to the IP of this server for:  (with --behind-proxy: to the proxy)
     <domain>  matrix.<domain>  chat.<domain>  invite.<domain>
     service.<domain>  turn.<domain>"

# Argumente
T_de[unbekannte_angabe]="Unbekannte Angabe: %s  (--hilfe zeigt die Möglichkeiten)"
T_en[unbekannte_angabe]="Unknown option: %s  (--help shows the options)"
T_de[sprache_unbekannt]="--language: nur en oder de, nicht '%s'"
T_en[sprache_unbekannt]="--language: only en or de, not '%s'"
T_de[sprache_uebernommen]="Sprache %s aus der vorhandenen Installation übernommen (umstellen: --language)."
T_en[sprache_uebernommen]="Language %s taken over from the existing installation (change it with --language)."
T_de[domain_fehlt]="--domain fehlt"
T_en[domain_fehlt]="--domain is missing"
T_de[mail_fehlt]="--mail fehlt"
T_en[mail_fehlt]="--mail is missing"
T_de[als_root]="Bitte als root ausführen (sudo)."
T_en[als_root]="Please run as root (sudo)."
T_de[abschnitt_unbekannt]="Unbekannter Abschnitt: %s  (möglich: pakete datenbank synapse web turn element einladung sicherung updates haertung)"
T_en[abschnitt_unbekannt]="Unknown section: %s  (possible: pakete datenbank synapse web turn element einladung sicherung updates haertung)"
T_de[verwalter_zeichen]="--verwalter darf nur Kleinbuchstaben, Ziffern und . _ = - / enthalten (nicht leer): '%s'"
T_en[verwalter_zeichen]="--admin may only contain lowercase letters, digits and . _ = - / (not empty): '%s'"

# Prüfen
T_de[s_pruefen]="Voraussetzungen prüfen"
T_en[s_pruefen]="Checking prerequisites"
T_de[kein_os_release]="Kein /etc/os-release — unbekanntes System."
T_en[kein_os_release]="No /etc/os-release — unknown system."
T_de[arch]="Architektur: %s."
T_en[arch]="Architecture: %s."
T_de[arch_i386]="Architektur: %s — unterstützt, aber ungewöhnlich für einen Server."
T_en[arch_i386]="Architecture: %s — supported, but unusual for a server."
T_de[arch_arm]="Architektur %s — dafür gibt es kein Synapse-Paket von matrix.org."
T_en[arch_arm]="Architecture %s — there is no Synapse package from matrix.org for it."
T_de[arch_arm_hinweis]="   Betroffen sind Raspberry Pi und andere ARM-Rechner. Die Paketquelle
   https://packages.matrix.org/debian/ führt ausschließlich amd64 und i386.

   Möglich bleibt Synapse im Container (Docker) oder aus dem Quelltext.
   Dieser Installer kann das nicht und tut auch nicht so, als könnte er es."
T_en[arch_arm_hinweis]="   This affects Raspberry Pi and other ARM machines. The package source
   https://packages.matrix.org/debian/ carries amd64 and i386 only.

   What remains is Synapse in a container (Docker) or built from source.
   This installer cannot do that and does not pretend it could."
T_de[arch_nicht_unterstuetzt]="Nicht unterstützte Architektur: %s"
T_en[arch_nicht_unterstuetzt]="Unsupported architecture: %s"
T_de[arch_unbekannt]="Unbekannte Architektur: %s. Nötig ist amd64."
T_en[arch_unbekannt]="Unknown architecture: %s. amd64 is required."
T_de[debian13]="Debian 13 erkannt."
T_en[debian13]="Debian 13 detected."
T_de[debian12]="Debian 12 erkannt — unterstützt, Debian 13 ist empfohlen."
T_en[debian12]="Debian 12 detected — supported, Debian 13 is recommended."
T_de[ubuntu]="Ubuntu erkannt (%s)."
T_en[ubuntu]="Ubuntu detected (%s)."
T_de[ubuntu_hinweis]="   matrix.org liefert zwar Pakete für Ubuntu, aber dieser Installer ist
   darauf nicht geprüft worden. Ungeprüfte Zusagen gibt es hier nicht.
   Nimm Debian 13 — oder melde dich, wenn du es auf Ubuntu probiert hast."
T_en[ubuntu_hinweis]="   matrix.org does ship packages for Ubuntu, but this installer has not
   been tested on it. There are no untested promises here.
   Use Debian 13 — or get in touch if you have tried it on Ubuntu."
T_de[ubuntu_ungeprueft]="Ungeprüftes System: %s"
T_en[ubuntu_ungeprueft]="Untested system: %s"
T_de[system_falsch]="Debian 12 oder 13 nötig, gefunden: %s"
T_en[system_falsch]="Debian 12 or 13 required, found: %s"
T_de[ram_zu_wenig]="Zu wenig Arbeitsspeicher: %s MB. Mindestens 2 GB, empfohlen 8 GB."
T_en[ram_zu_wenig]="Not enough memory: %s MB. At least 2 GB, 8 GB recommended."
T_de[ram_knapp]="WARNUNG: nur %s MB RAM — für wenige Nutzer ok, wird bei Wachstum eng."
T_en[ram_knapp]="WARNING: only %s MB RAM — fine for a few users, gets tight with growth."
T_de[platz_zu_wenig]="Zu wenig Platz: %s GB frei. Mindestens 20 GB einplanen."
T_en[platz_zu_wenig]="Not enough disk space: %s GB free. Plan for at least 20 GB."
T_de[ip_unbekannt]="Die eigene öffentliche Adresse ist nicht ermittelbar — keine Internetverbindung?"
T_en[ip_unbekannt]="The server's public address cannot be determined — no internet connection?"
T_de[ip_oeffentlich]="Öffentliche Adresse: %s"
T_en[ip_oeffentlich]="Public address: %s"
T_de[ip_lokal]="Lokale Adresse: %s (der Server steht hinter einem Router)"
T_en[ip_lokal]="Local address: %s (the server is behind a router)"
T_de[direkt_am_netz]="Der Server hängt direkt am Internet."
T_en[direkt_am_netz]="The server is directly on the internet."
T_de[dns_loest_nicht]="löst nicht auf"
T_en[dns_loest_nicht]="does not resolve"
T_de[dns_erwartet]="erwartet %s"
T_en[dns_erwartet]="expected %s"
T_de[dns_woanders_proxy]="Hinweis: diese Namen zeigen woanders hin (hinter einem Proxy erwartet):"
T_en[dns_woanders_proxy]="Note: these names point elsewhere (expected behind a proxy):"
T_de[dns_nicht_aufloesbar]="Diese Namen lösen nicht auf:"
T_en[dns_nicht_aufloesbar]="These names do not resolve:"
T_de[dns_zeigen_nicht_hierher]="Diese Namen zeigen nicht auf diesen Server:"
T_en[dns_zeigen_nicht_hierher]="These names do not point to this server:"
T_de[dns_hinweis]="   Lege die A-Einträge an und warte, bis sie sichtbar sind (meist wenige Minuten).
   Ohne sie können keine Zertifikate ausgestellt werden - auch hinter einem
   Proxy müssen die Namen öffentlich vorhanden sein."
T_en[dns_hinweis]="   Create the A records and wait until they are visible (usually a few minutes).
   Without them no certificates can be issued - even behind a proxy the
   names must exist publicly."
T_de[dns_nicht_so_weit]="DNS ist noch nicht so weit."
T_en[dns_nicht_so_weit]="DNS is not ready yet."
T_de[dns_ok_proxy]="Alle sechs Namen lösen auf. TLS übernimmt der vorgelagerte Proxy."
T_en[dns_ok_proxy]="All six names resolve. TLS is handled by the upstream proxy."
T_de[dns_ok]="Alle sechs Namen zeigen hierher."
T_en[dns_ok]="All six names point here."
T_de[port_belegt]="Port %s ist schon belegt. Läuft hier bereits ein Webserver?"
T_en[port_belegt]="Port %s is already in use. Is a web server already running here?"
T_de[ports_eigener_nginx]="Port(e)%s hält der nginx aus einem früheren Lauf — in Ordnung."
T_en[ports_eigener_nginx]="Port(s)%s are held by the nginx from an earlier run — fine."
T_de[ports_frei]="Port(e) %s sind frei."
T_en[ports_frei]="Port(s) %s are free."
T_de[voraussetzungen_ok]="   Voraussetzungen erfüllt."
T_en[voraussetzungen_ok]="   Prerequisites met."

# Pakete
T_de[s_pakete]="Pakete installieren"
T_en[s_pakete]="Installing packages"
T_de[pakete_fehlgeschlagen]="Paketinstallation fehlgeschlagen: %s Ganzes Protokoll: %s"
T_en[pakete_fehlgeschlagen]="Package installation failed: %s Full log: %s"
T_de[apt_grund_unbekannt]="Grund nicht erkannt."
T_en[apt_grund_unbekannt]="Reason not recognised."
T_de[apt_gesperrt]="apt ist noch durch automatische Updates gesperrt - kurz warten, dann erneut starten."
T_en[apt_gesperrt]="apt is still locked by automatic updates - wait a moment, then start again."
T_de[grundpakete]="Grundpakete installiert."
T_en[grundpakete]="Base packages installed."
T_de[paketquelle]="Paketquelle matrix.org eingerichtet (%s)."
T_en[paketquelle]="Package source matrix.org set up (%s)."
T_de[pakete_fertig]="   Pakete fertig."
T_en[pakete_fertig]="   Packages done."

# Datenbank
T_de[s_datenbank]="Datenbank einrichten"
T_en[s_datenbank]="Setting up the database"
T_de[db_existiert]="Datenbank 'synapse' existiert bereits — unverändert."
T_en[db_existiert]="Database 'synapse' already exists — unchanged."
T_de[db_angelegt]="Datenbank angelegt (UTF8, Sortierung C)."
T_en[db_angelegt]="Database created (UTF8, collation C)."
T_de[db_sortierung]="Die Datenbank hat die Sortierung '%s' statt 'C'. Synapse braucht C."
T_en[db_sortierung]="The database has collation '%s' instead of 'C'. Synapse needs C."
T_de[db_bereit]="   Datenbank bereit."
T_en[db_bereit]="   Database ready."

# Synapse
T_de[s_synapse]="Synapse einrichten"
T_en[s_synapse]="Setting up Synapse"
T_de[synapse_pakete_fehlgeschlagen]="Paketinstallation von Synapse fehlgeschlagen: %s Ganzes Protokoll: %s"
T_en[synapse_pakete_fehlgeschlagen]="Package installation of Synapse failed: %s Full log: %s"
T_de[synapse_installiert]="Synapse installiert."
T_en[synapse_installiert]="Synapse installed."
T_de[synapse_schon_da]="Synapse ist schon da."
T_en[synapse_schon_da]="Synapse is already installed."
T_de[geheimnisse_erzeugt]="Geheimnisse erzeugt."
T_en[geheimnisse_erzeugt]="Secrets generated."
T_de[dbpw_nicht_in_yaml]="Datenbankpasswort nicht auffindbar: /root/.synapse_db_pw fehlt, %s enthält keins."
T_en[dbpw_nicht_in_yaml]="Database password not found: /root/.synapse_db_pw is missing, %s contains none."
T_de[dbpw_uebernommen]="Datenbankpasswort aus %s übernommen."
T_en[dbpw_uebernommen]="Database password taken from %s."
T_de[dbpw_fehlt]="Datenbankpasswort nicht auffindbar: /root/.synapse_db_pw fehlt, obwohl die Datenbank 'synapse' existiert. Neu setzen: su - postgres -c \"psql -qc \\\"ALTER USER synapse_user WITH PASSWORD '<neu>';\\\"\" und das Passwort in /root/.synapse_db_pw ablegen."
T_en[dbpw_fehlt]="Database password not found: /root/.synapse_db_pw is missing although the database 'synapse' exists. Set a new one: su - postgres -c \"psql -qc \\\"ALTER USER synapse_user WITH PASSWORD '<new>';\\\"\" and store the password in /root/.synapse_db_pw."
T_de[synapse_startet_nicht]="Synapse startet nicht. Siehe: journalctl -u matrix-synapse -n 50"
T_en[synapse_startet_nicht]="Synapse does not start. See: journalctl -u matrix-synapse -n 50"
T_de[synapse_laeuft]="Synapse läuft (Servername %s)."
T_en[synapse_laeuft]="Synapse is running (server name %s)."
T_de[synapse_bereit]="   Synapse bereit."
T_en[synapse_bereit]="   Synapse ready."

# Web
T_de[s_web]="Webserver, Zertifikate und Delegation"
T_en[s_web]="Web server, certificates and delegation"
T_de[nginx_fehlerhaft]="nginx-Konfiguration fehlerhaft."
T_en[nginx_fehlerhaft]="nginx configuration is faulty."
T_de[zertifikat_fehlgeschlagen]="Zertifikat konnte nicht ausgestellt werden: %s Ganzes Protokoll: %s"
T_en[zertifikat_fehlgeschlagen]="Certificate could not be issued: %s Full log: %s"
T_de[le_limit]="Let's Encrypt stellt für dieselben Namen höchstens 5 Zertifikate pro Woche aus (z. B. nach mehreren Neuinstallationen). Wieder möglich ab %s UTC."
T_en[le_limit]="Let's Encrypt issues at most 5 certificates per week for the same names (e.g. after several reinstalls). Possible again from %s UTC."
T_de[le_grund_unbekannt]="Grund nicht erkannt."
T_en[le_grund_unbekannt]="Reason not recognised."
T_de[zertifikat_ausgestellt]="Zertifikat für sechs Namen ausgestellt."
T_en[zertifikat_ausgestellt]="Certificate issued for six names."
T_de[zertifikat_existiert]="Zertifikat existiert bereits."
T_en[zertifikat_existiert]="Certificate already exists."
T_de[vhosts_aktiv]="Vhosts aktiv."
T_en[vhosts_aktiv]="Vhosts active."
T_de[web_bereit]="   Web bereit."
T_en[web_bereit]="   Web ready."

# TURN
T_de[s_turn]="coturn für Sprach- und Videoanrufe"
T_en[s_turn]="coturn for voice and video calls"
T_de[turn_unverschluesselt]="unverschlüsselt auf 3478 (kein Zertifikat auf diesem Rechner)"
T_en[turn_unverschluesselt]="unencrypted on 3478 (no certificate on this machine)"
T_de[turn_ports]="Ports 3478 und 5349"
T_en[turn_ports]="ports 3478 and 5349"
T_de[coturn_startet_nicht]="coturn startet nicht. Siehe: journalctl -u coturn -n 30"
T_en[coturn_startet_nicht]="coturn does not start. See: journalctl -u coturn -n 30"
T_de[coturn_laeuft]="coturn läuft (%s, Weiterleitung 49160-49200)."
T_en[coturn_laeuft]="coturn is running (%s, relay 49160-49200)."
T_de[relais_adresse]="Relais-Adresse: %s"
T_en[relais_adresse]="Relay address: %s"
T_de[router_handarbeit]="   Noch eine Handarbeit am Router:"
T_en[router_handarbeit]="   One more manual step on the router:"
T_de[router_hinweis]="   Diese Ports müssen auf %s weitergeleitet werden,
   sonst kommen Anrufe zwischen verschiedenen Netzen nicht zustande:
     3478        TCP und UDP
     5349        TCP und UDP
     49160-49200 UDP"
T_en[router_hinweis]="   These ports must be forwarded to %s,
   otherwise calls between different networks will not connect:
     3478        TCP and UDP
     5349        TCP and UDP
     49160-49200 UDP"
T_de[anrufe_vorbereitet]="   Anrufe vorbereitet."
T_en[anrufe_vorbereitet]="   Calls prepared."

# Element
T_de[s_element]="Element Web"
T_en[s_element]="Element Web"
T_de[datei_fehlt]="dateien/%s fehlt neben dem Installer."
T_en[datei_fehlt]="dateien/%s is missing next to the installer."
T_de[element_fehlgeschlagen]="Element Web konnte nicht erneuert werden. Grund steht in %s"
T_en[element_fehlgeschlagen]="Element Web could not be refreshed. The reason is in %s"
T_de[element_bereit]="   Element bereit."
T_en[element_bereit]="   Element ready."

# Einladung
T_de[s_einladung]="Einladungsseite"
T_en[s_einladung]="Invitation page"
T_de[synapse_nicht_eingerichtet]="Synapse ist noch nicht eingerichtet (/etc/matrix-synapse/conf.d/05-secrets.yaml fehlt oder enthält kein registration_shared_secret). Erst den vollständigen Installer ohne --nur laufen lassen."
T_en[synapse_nicht_eingerichtet]="Synapse is not set up yet (/etc/matrix-synapse/conf.d/05-secrets.yaml is missing or contains no registration_shared_secret). Run the full installer without --nur first."
T_de[dienstkonto_fehlgeschlagen]="Dienstkonto @einladungsdienst konnte nicht angelegt werden. Grund steht in %s"
T_en[dienstkonto_fehlgeschlagen]="Service account @einladungsdienst could not be created. The reason is in %s"
T_de[dienstkonto_angelegt]="Dienstkonto @einladungsdienst angelegt."
T_en[dienstkonto_angelegt]="Service account @einladungsdienst created."
T_de[einladung_startet_nicht]="Einladungsdienst startet nicht. Siehe: journalctl -u einladung -n 30"
T_en[einladung_startet_nicht]="Invitation service does not start. See: journalctl -u einladung -n 30"
T_de[einladung_unter]="Einladungsseite unter https://%s (Sprache: %s)"
T_en[einladung_unter]="Invitation page at https://%s (language: %s)"
T_de[einladungen_bereit]="   Einladungen bereit."
T_en[einladungen_bereit]="   Invitations ready."

# Sicherung
T_de[s_sicherung]="Sicherung"
T_en[s_sicherung]="Backup"
T_de[sicherung_erreichbar]="Sicherungsziel erreichbar, Verzeichnis angelegt."
T_en[sicherung_erreichbar]="Backup target reachable, directory created."
T_de[sicherung_taeglich_nach]="Täglich 03:30 nach %s"
T_en[sicherung_taeglich_nach]="Daily at 03:30 to %s"
T_de[sicherung_eingerichtet]="   Sicherung eingerichtet."
T_en[sicherung_eingerichtet]="   Backup set up."
T_de[sicherung_nicht_erreichbar]="   Sicherungsziel %s ist nicht erreichbar - der Timer wird NICHT gestartet."
T_en[sicherung_nicht_erreichbar]="   Backup target %s is not reachable - the timer is NOT started."
T_de[sicherung_schluessel_hinweis]="   Diesen öffentlichen Schlüssel beim Ziel hinterlegen (authorized_keys):
     %s
   Lauscht das Ziel nicht auf Port 22 (Hetzner Storage Box: 23), in /root/.ssh/config eintragen:
     Host %s
       Port 23
   Danach erneut:  ./install.sh ... --nur sicherung      Einzelheiten: %s"
T_en[sicherung_schluessel_hinweis]="   Register this public key at the target (authorized_keys):
     %s
   If the target does not listen on port 22 (Hetzner Storage Box: 23), put into /root/.ssh/config:
     Host %s
       Port 23
   Then again:  ./install.sh ... --nur sicherung      Details: %s"
T_de[sicherung_oertlich]="Täglich 03:30 nach /var/backups/messenger (14 Tage). Auswärts: --sicherung angeben."
T_en[sicherung_oertlich]="Daily at 03:30 to /var/backups/messenger (14 days). Off-site: pass --sicherung."

# Aktualisierungen
T_de[s_updates]="Aktualisierungen: täglich prüfen, einspielen nur auf Klick"
T_en[s_updates]="Updates: check daily, install only on a click"
T_de[benutzer_einladung_fehlt]="Benutzer 'einladung' fehlt - erst den Abschnitt einladung laufen lassen."
T_en[benutzer_einladung_fehlt]="User 'einladung' is missing - run the einladung section first."
T_de[service_nachgezogen]="einladung.service nachgezogen (Schreibrecht auf den Auftragsordner)."
T_en[service_nachgezogen]="einladung.service updated (write access to the job folder)."
T_de[updates_timer]="Tägliche Prüfung ab 06:00, Einspielen nur auf Klick in der Verwaltung."
T_en[updates_timer]="Daily check from 06:00, installing only on a click in the admin page."
T_de[erste_pruefung_fehlgeschlagen]="WARNUNG: erste Prüfung fehlgeschlagen - der Timer versucht es täglich erneut. Siehe %s"
T_en[erste_pruefung_fehlgeschlagen]="WARNING: first check failed - the timer retries daily. See %s"
T_de[updates_eingerichtet]="   Aktualisierungen eingerichtet."
T_en[updates_eingerichtet]="   Updates set up."

# Härtung
T_de[s_haertung]="Härtung"
T_en[s_haertung]="Hardening"
T_de[firewall]="Firewall: offen sind SSH (Port %s), Web und Anrufe."
T_en[firewall]="Firewall: SSH (port %s), web and calls are open."
T_de[fail2ban]="fail2ban aktiv für SSH (Port %s)."
T_en[fail2ban]="fail2ban active for SSH (port %s)."
T_de[sicherheitsupdates]="Sicherheitsaktualisierungen automatisch, der Rest bewusst."
T_en[sicherheitsupdates]="Security updates install automatically; everything else only when you choose."
T_de[haertung_fertig]="   Härtung fertig."
T_en[haertung_fertig]="   Hardening done."

# Abschluss
T_de[s_abschluss]="Verwalterkonto und Zugangsdaten"
T_en[s_abschluss]="Admin account and credentials"
T_de[verwalter_fehlgeschlagen]="Verwalterkonto @%s:%s konnte nicht angelegt werden. Grund steht in %s"
T_en[verwalter_fehlgeschlagen]="Admin account @%s:%s could not be created. The reason is in %s"
T_de[verwalter_angelegt]="Verwalterkonto @%s:%s angelegt."
T_en[verwalter_angelegt]="Admin account @%s:%s created."
T_de[erstanmeldung_eingerichtet]="Konto im Chat gesperrt bis zur Erstanmeldung in der Verwaltung (eigenes Passwort, zweiter Faktor)."
T_en[erstanmeldung_eingerichtet]="Account locked in the chat until the first sign-in on the admin page (own password, second factor)."
T_de[erstanmeldung_fehlgeschlagen]="Erstanmeldung konnte nicht eingerichtet werden. Grund steht in %s"
T_en[erstanmeldung_fehlgeschlagen]="Could not set up the first sign-in. The reason is in %s"
T_de[verwalter_pw_uebernommen]="Verwalterkonto gab es schon — Passwort aus der vorigen Datei übernommen."
T_en[verwalter_pw_uebernommen]="Admin account already existed — password taken from the previous file."
T_de[pw_unbekannt]="(unbekannt — neu setzen, siehe PASSWORT NEU SETZEN unten)"
T_en[pw_unbekannt]="(unknown — set a new one, see RESET PASSWORD below)"
T_de[verwalter_pw_unbekannt]="Verwalterkonto gab es schon, das Passwort ist hier nicht bekannt."
T_en[verwalter_pw_unbekannt]="Admin account already existed, the password is not known here."
T_de[zugangsdaten_in]="Zugangsdaten in %s"
T_en[zugangsdaten_in]="Credentials in %s"
T_de[kundenanleitung]="Kundenanleitung: %s"
T_en[kundenanleitung]="Customer guide: %s"
T_de[verwaltungsanleitung]="Verwaltungsanleitung: %s (auch in der Verwaltung zum Herunterladen)."
T_en[verwaltungsanleitung]="Operator guide: %s (also downloadable from the admin page)."
T_de[zugang_steht]="   Zugang steht."
T_en[zugang_steht]="   Access ready."

# Nachkontrolle
T_de[s_nachkontrolle]="Nachkontrolle"
T_en[s_nachkontrolle]="Final checks"
T_de[ok]="OK"
T_en[ok]="OK"
T_de[fehlt]="FEHLT"
T_en[fehlt]="FAIL"
T_de[geprueft_oertlich]="Geprüft wird örtlich — der Weg von außen hängt am Proxy."
T_en[geprueft_oertlich]="Checked locally — the path from outside depends on the proxy."
T_de[nk_synapse]="Synapse antwortet"
T_en[nk_synapse]="Synapse responds"
T_de[nk_delegation]="Delegation ausgeliefert"
T_en[nk_delegation]="Delegation served"
T_de[nk_matrix]="Matrix erreichbar"
T_en[nk_matrix]="Matrix reachable"
T_de[nk_servername]="Servername ist %s"
T_en[nk_servername]="Server name is %s"
T_de[nk_element]="Element Web"
T_en[nk_element]="Element Web"
T_de[nk_einladung]="Einladungsseite"
T_en[nk_einladung]="Invitation page"
T_de[nk_verwaltung]="Verwaltung antwortet"
T_en[nk_verwaltung]="Admin page responds"
T_de[nk_coturn]="coturn läuft"
T_en[nk_coturn]="coturn running"
T_de[nk_dienst]="Einladungsdienst läuft"
T_en[nk_dienst]="Invitation service running"
T_de[nk_firewall]="Firewall aktiv"
T_en[nk_firewall]="Firewall active"
T_de[nk_sicherung]="Sicherungsziel erreichbar"
T_en[nk_sicherung]="Backup target reachable"
T_de[pruefungen_fehlgeschlagen]="%s Prüfung(en) fehlgeschlagen. Der Server steht trotzdem,"
T_en[pruefungen_fehlgeschlagen]="%s check(s) failed. The server is up anyway,"
T_de[zugangsdaten_liegen]="die Zugangsdaten liegen in %s. Protokoll: %s"
T_en[zugangsdaten_liegen]="the credentials are in %s. Log: %s"
T_de[fertig]="Fertig. Alle Prüfungen bestanden."
T_en[fertig]="Done. All checks passed."
T_de[schluss_adressen]="   Chat:        https://%s
   Einladungen: https://%s
   Kennungen:   @name:%s"
T_en[schluss_adressen]="   Chat:        https://%s
   Invitations: https://%s
   User IDs:    @name:%s"
T_de[proxy_hinweis]="   Dieser Server liefert Klartext auf Port 80. Im vorgelagerten Proxy
   für diese fünf Namen je einen Eintrag auf  http://%s:80  anlegen:
     %s  %s  %s  %s  %s
   Das Zertifikat gehört dorthin, nicht hierher."
T_en[proxy_hinweis]="   This server serves plain HTTP (unencrypted) on port 80. In the upstream proxy,
   create one entry per name for these five names, pointing to  http://%s:80
     %s  %s  %s  %s  %s
   The certificate belongs there, not here."
T_de[schluss_zugang]="   Zugangsdaten stehen in %s — lies sie, ändere das Passwort, lösche die Datei."
T_en[schluss_zugang]="   Credentials are in %s — read them, change the password, delete the file."

# txt <schluessel> [werte...]: Text in der gewählten Sprache, Platzhalter per printf.
# Fehlt ein Schluessel in der gewählten Tabelle, kommt der englische; fehlt er
# auch dort, erscheint der Schluessel selbst - das fällt auf, statt still zu schweigen.
txt() {
  local k="$1" v; shift
  if [ "$SPRACHE" = de ]; then v="${T_de[$k]-}"; else v="${T_en[$k]-}"; fi
  [ -n "$v" ] || v="${T_en[$k]-$k}"
  # printf wertet Backslashes im Format aus - ein \" im Text käme als " an.
  # Darum verdoppeln; so bleibt z.B. das psql-Kommando in dbpw_fehlt wortgetreu.
  v="${v//\\/\\\\}"
  # shellcheck disable=SC2059
  printf -- "$v" "$@"
}
sag() { printf '%s\n' "$(txt "$@")"; }       # eine Zeile (oder mehrere) ohne Farbe

# Ausgabe
rot()  { printf '\033[31m%s\033[0m\n' "$*"; }
gruen(){ printf '\033[32m%s\033[0m\n' "$*"; }
grau() { printf '\033[90m%s\033[0m\n' "$*"; }
schritt(){ printf '\n\033[1m== %s\033[0m\n' "$*"; echo "[$(date '+%F %T')] == $*" >>"$LOG"; }
melde(){ printf '   %s\n' "$*"; echo "[$(date '+%F %T')]    $*" >>"$LOG"; }
ende()  { rot "$(txt abbruch): $*"; echo "[$(date '+%F %T')] $(txt abbruch): $*" >>"$LOG"; exit 1; }

hilfe() { sag hilfe; }

# Grund einer gescheiterten Zertifikatsanfrage in einer Zeile. Das Wochenlimit
# von Let's Encrypt bekommt einen eigenen Satz mit dem Zeitpunkt, ab dem es
# wieder geht; sonst die Fehlerzeile von certbot.
le_grund() {
  local z ab
  z=$(tail -n 60 "$LOG")
  ab=$(printf '%s' "$z" | grep -oP 'retry after \K[0-9-]+ [0-9:]+' | tail -n 1)
  if [ -n "$ab" ]; then
    txt le_limit "$ab"
  else
    z=$(printf '%s\n' "$z" | grep -E '^(Detail|Error|An unexpected error)' -A1 | grep -v '^--' | tail -n 2 | tr '\n' ' ')
    if [ -n "$z" ]; then printf '%s' "$z"; else txt le_grund_unbekannt; fi
  fi
}

# Grund eines gescheiterten apt-Laufs in einer Zeile, damit er auf dem
# Bildschirm und auf der Einrichtungsseite steht, nicht nur im Protokoll.
# Nur das Ende des Protokolls: weiter oben stehen Fehler früherer Läufe.
apt_grund() {
  local z
  # dpkg schreibt den eigentlichen Grund eingerückt in die Zeile danach;
  # "E: Sub-process ... returned an error code" sagt nichts Neues.
  z=$(tail -n 80 "$LOG" | grep -E -A1 '^(E: |dpkg: )' | grep -vE '^(--|E: Sub-process)' \
      | tail -n 3 | sed 's/^ *//' | tr '\n' ' ')
  if printf '%s' "$z" | grep -qiE 'could not get lock|lock-frontend|Sperre'; then
    txt apt_gesperrt
  elif [ -n "$z" ]; then
    printf '%s' "$z"
  else
    txt apt_grund_unbekannt
  fi
}

# Die Sprache zuerst, damit schon die Hilfe und ein Abbruch beim Lesen der
# übrigen Angaben in der gewünschten Sprache erscheinen.
# Sprache (Anfang)
_argumente=("$@"); _sprache_angegeben=nein
for ((_i = 0; _i < ${#_argumente[@]}; _i++)); do
  if [ "${_argumente[$_i]}" = --language ]; then
    SPRACHE="${_argumente[$((_i + 1))]:-}"; _sprache_angegeben=ja
  fi
done
# Wiederholungslauf ohne --language: die Sprache der vorhandenen Installation
# behalten. "en" ist nur die Vorgabe für eine frische Anlage - sonst stellte
# ein zweiter Lauf (etwa --nur turn) eine deutsche Anlage still auf Englisch um.
SPRACHE_UEBERNOMMEN=nein
if [ "$_sprache_angegeben" = nein ] && [ -r "$UMGEBUNG" ]; then
  _alt="$(sed -n 's/^Environment=MESSENGER_LANGUAGE=//p' "$UMGEBUNG" | tail -1)"
  case "$_alt" in en|de) SPRACHE="$_alt"; SPRACHE_UEBERNOMMEN=ja ;; esac
fi
case "$SPRACHE" in
  en|de) ;;
  *) _falsch="$SPRACHE"; SPRACHE=en; ende "$(txt sprache_unbekannt "$_falsch")" ;;
esac
# Sprache (Ende)

# Argumente (Anfang)
while [ $# -gt 0 ]; do
  case "$1" in
    --domain)    DOMAIN="${2:-}"; shift 2 ;;
    --mail)      MAIL="${2:-}"; shift 2 ;;
    --verwalter|--admin)    VERWALTER="${2:-}"; shift 2 ;;
    --sicherung|--backup)   SICHERUNG_ZIEL="${2:-}"; shift 2 ;;
    --betreiber|--operator) BETREIBER="${2:-}"; shift 2 ;;
    --language)  shift 2 ;;                            # oben schon gelesen
    --hinter-proxy|--behind-proxy) HINTER_PROXY=ja; shift ;;
    --nur|--only)           NUR="${2:-}"; shift 2 ;;
    --hilfe|--help|-h) HILFE=ja; shift ;;
    *) ende "$(txt unbekannte_angabe "$1")" ;;
  esac
done
# Argumente (Ende)
[ "$HILFE" = nein ] || { hilfe; exit 0; }

[ -n "$DOMAIN" ] || { hilfe; ende "$(txt domain_fehlt)"; }
# Ohne eigenes Zertifikat braucht Let's Encrypt auch keine Adresse.
[ -n "$MAIL" ] || [ "$HINTER_PROXY" = ja ] || { hilfe; ende "$(txt mail_fehlt)"; }
[ "$(id -u)" = 0 ] || ende "$(txt als_root)"
# Ein Tippfehler bei --nur liess vorher ALLE Abschnitte aus und meldete am Ende
# trotzdem "Fertig" - der Nutzer glaubte, sein Abschnitt sei erneuert worden.
case "$NUR" in
  ""|pakete|datenbank|synapse|web|turn|element|einladung|sicherung|updates|haertung) ;;
  *) ende "$(txt abschnitt_unbekannt "$NUR")" ;;
esac
# Der Verwaltername wird zur Matrix-Kennung @name:domain und steht ungeschützt
# in einer SQL-Abfrage. Synapse laesst nur Kleinbuchstaben, Ziffern und . _ = - /
# zu; alles andere scheiterte erst spät bei register_new_matrix_user.
# LC_ALL=C, weil [a-z] in einer deutschen Spracheinstellung auch Umlaute
# durchlaesst (Vergleich nach Sortierreihenfolge, nicht nach Zeichen).
printf '%s' "$VERWALTER" | LC_ALL=C grep -qE '^[a-z0-9._=/-]+$' \
  || ende "$(txt verwalter_zeichen "$VERWALTER")"

MATRIX_HOST="matrix.$DOMAIN"
CHAT_HOST="chat.$DOMAIN"
EINLADUNG_HOST="invite.$DOMAIN"
VERWALTUNG_HOST="service.$DOMAIN"
TURN_HOST="turn.$DOMAIN"
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

lauf() { [ -z "$NUR" ] || [ "$NUR" = "$1" ]; }

# PRUEFEN
schritt "$(txt s_pruefen)"
[ "$SPRACHE_UEBERNOMMEN" = nein ] || melde "$(txt sprache_uebernommen "$SPRACHE")"

. /etc/os-release 2>/dev/null || ende "$(txt kein_os_release)"

# Die Architektur zuerst. matrix.org liefert Synapse NUR für amd64 und i386 -
# für arm64 und armhf gibt es dort gar keine Paketliste. Auf einem Raspberry Pi
# scheitert sonst erst der Paket-Schritt, mitten in der Installation, mit einer
# Meldung, aus der niemand schliessen kann, woran es wirklich lag.
BOGEN=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$BOGEN" in
  amd64|x86_64) melde "$(txt arch "$BOGEN")" ;;
  i386|i686)    melde "$(txt arch_i386 "$BOGEN")" ;;
  arm64|aarch64|armhf|armv7l)
    rot "$(txt arch_arm "$BOGEN")"
    sag arch_arm_hinweis
    ende "$(txt arch_nicht_unterstuetzt "$BOGEN")"
    ;;
  *) ende "$(txt arch_unbekannt "$BOGEN")" ;;
esac

case "${ID:-}:${VERSION_ID:-}" in
  debian:13*) melde "$(txt debian13)" ;;
  debian:12*) melde "$(txt debian12)" ;;
  ubuntu:*)
    rot "$(txt ubuntu "${PRETTY_NAME:-Ubuntu}")"
    sag ubuntu_hinweis
    ende "$(txt ubuntu_ungeprueft "${PRETTY_NAME:-Ubuntu}")"
    ;;
  *) ende "$(txt system_falsch "${PRETTY_NAME:-?}")" ;;
esac

RAM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
[ "$RAM_MB" -ge 1800 ] || ende "$(txt ram_zu_wenig "$RAM_MB")"
[ "$RAM_MB" -ge 3600 ] || melde "$(txt ram_knapp "$RAM_MB")"

PLATZ_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
[ "$PLATZ_GB" -ge 15 ] || ende "$(txt platz_zu_wenig "$PLATZ_GB")"

command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl; }
EIGENE_IP=$(curl -sf --max-time 10 https://api4.ipify.org || true)
[ -n "$EIGENE_IP" ] || ende "$(txt ip_unbekannt)"
melde "$(txt ip_oeffentlich "$EIGENE_IP")"

# Die lokale Adresse auf dem Weg nach draussen. Weicht sie von der öffentlichen
# ab, sitzt der Server hinter einem Router. Das muss coturn wissen: sonst nennt
# es Gesprächspartnern eine private Adresse als Relais, und Anrufe bleiben
# stumm, obwohl alles zu laufen scheint.
LOKALE_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
[ -n "$LOKALE_IP" ] || LOKALE_IP="$EIGENE_IP"
if [ "$LOKALE_IP" != "$EIGENE_IP" ]; then
  HINTER_ROUTER=ja
  melde "$(txt ip_lokal "$LOKALE_IP")"
else
  HINTER_ROUTER=nein
  melde "$(txt direkt_am_netz)"
fi

# Zwei getrennte Fälle: "löst nicht auf" und "zeigt woanders hin". Nur der
# zweite ist hinter einem Proxy erwartet. Ein Name, der gar nicht auflöst,
# bricht IMMER ab - sonst liefe ein Tippfehler in --domain bis zum Ende durch
# und würde zum Synapse-Servernamen, der nachträglich nicht änderbar ist.
fehlende=""
nicht_aufloesbar=""
for n in "$DOMAIN" "$MATRIX_HOST" "$CHAT_HOST" "$EINLADUNG_HOST" "$VERWALTUNG_HOST" "$TURN_HOST"; do
  # '|| true': getent endet bei unbekanntem Namen mit Status 2, und unter
  # set -eo pipefail beendete das die Zuweisung wortlos - der Abbruch unten
  # mit seiner Meldung wurde nie erreicht.
  ziel=$(getent ahostsv4 "$n" 2>/dev/null | awk '{print $1; exit}' || true)
  if [ -z "$ziel" ]; then
    nicht_aufloesbar="$nicht_aufloesbar\n     $n  -> $(txt dns_loest_nicht)"
  elif [ "$ziel" != "$EIGENE_IP" ]; then
    fehlende="$fehlende\n     $n  -> $ziel  ($(txt dns_erwartet "$EIGENE_IP"))"
  fi
done
if [ -n "$fehlende" ] && [ "$HINTER_PROXY" = ja ]; then
  # Hinter einem Proxy ist genau das richtig: die Namen zeigen auf den Proxy,
  # nicht hierher. Nur melden, nicht abbrechen.
  melde "$(txt dns_woanders_proxy)"
  printf "$fehlende\n"
  fehlende=""
fi
if [ -n "$nicht_aufloesbar" ] || [ -n "$fehlende" ]; then
  if [ -n "$nicht_aufloesbar" ]; then
    rot "$(txt dns_nicht_aufloesbar)"
    printf "$nicht_aufloesbar\n"
  fi
  if [ -n "$fehlende" ]; then
    rot "$(txt dns_zeigen_nicht_hierher)"
    printf "$fehlende\n"
  fi
  echo
  sag dns_hinweis
  ende "$(txt dns_nicht_so_weit)"
fi
if [ "$HINTER_PROXY" = ja ]; then
  melde "$(txt dns_ok_proxy)"
else
  melde "$(txt dns_ok)"
fi

# Der eigene nginx aus einem früheren Lauf darf die Ports halten. Sonst
# widerspräche der Installer seiner eigenen Zusage, dass ein zweiter Lauf
# nichts kaputt macht - und genau daran ist er beim Nachtesten gescheitert.
EIGENER_NGINX=nein
# Die Vorlage in sites-available, nicht der Verweis in sites-enabled: beim
# allerersten Lauf wird erst später verlinkt, der Port ist aber schon belegt.
# Dazu die Marke aus dem Paket-Abschnitt: Debian startet nginx sofort nach der
# Installation mit seiner Standardseite auf Port 80. Brach ein Lauf zwischen
# Paketen und Web-Abschnitt ab, gab es die Vorlage noch nicht, und der zweite
# Lauf scheiterte an "Port 80 belegt" - durch den eigenen nginx.
if { [ -f /etc/nginx/sites-available/messenger ] || [ -f /var/lib/messenger/nginx-ist-unser ]; } \
   && systemctl is-active --quiet nginx 2>/dev/null; then
  EIGENER_NGINX=ja
fi
# Hinter einem Proxy benutzen wir 443 nie - dann ist er auch kein Hindernis.
if [ "$HINTER_PROXY" = ja ]; then PORTE="80"; else PORTE="80 443"; fi
belegt=""
for p in $PORTE; do
  if ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$p\$"; then
    [ "$EIGENER_NGINX" = ja ] || ende "$(txt port_belegt "$p")"
    belegt="$belegt $p"
  fi
done
if [ -n "$belegt" ]; then
  melde "$(txt ports_eigener_nginx "$belegt")"
else
  melde "$(txt ports_frei "$PORTE")"
fi

mkdir -p "$(dirname "$LOG")"; touch "$LOG"
gruen "$(txt voraussetzungen_ok)"

# PAKETE
if lauf pakete; then
schritt "$(txt s_pakete)"
# Schon VOR dem apt-Lauf: die Vorprüfung hat Port 80 frei vorgefunden, also ist
# jeder nginx, den ein späterer Lauf dort antrifft, der eigene (s. PRUEFEN) -
# auch wenn apt mittendrin abbricht (Strg+C, Verbindungsabbruch, fehlschlagendes
# postinst), denn Debian startet nginx bereits während der Installation. Die
# Marke allein genügt nicht, die Vorprüfung verlangt zusätzlich einen
# aktiven nginx; scheitert apt vor dessen Installation, bleibt sie wirkungslos.
mkdir -p /var/lib/messenger; : > /var/lib/messenger/nginx-ist-unser
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Nicht wortlos sterben: der Grund stand sonst nur im Protokoll, auf dem
# Bildschirm kam nach der Überschrift gleich wieder der Prompt.
apt-get install -y -qq \
  ca-certificates gnupg lsb-release \
  postgresql postgresql-contrib \
  nginx certbot python3-certbot-nginx \
  coturn \
  python3 python3-venv python3-pyotp python3-qrcode python3-cryptography \
  ufw fail2ban unattended-upgrades \
  rsync jq >>"$LOG" 2>&1 \
  || ende "$(txt pakete_fehlgeschlagen "$(apt_grund)" "$LOG")"
melde "$(txt grundpakete)"

if [ ! -f /usr/share/keyrings/matrix-org-archive-keyring.gpg ]; then
  curl -fsSL https://packages.matrix.org/debian/matrix-org-archive-keyring.gpg \
    -o /usr/share/keyrings/matrix-org-archive-keyring.gpg
fi
SUITE=$(lsb_release -cs)
curl -sf -o /dev/null "https://packages.matrix.org/debian/dists/$SUITE/Release" \
  || SUITE=bookworm     # matrix.org hinkt manchmal eine Fassung hinterher
echo "deb [signed-by=/usr/share/keyrings/matrix-org-archive-keyring.gpg] https://packages.matrix.org/debian/ $SUITE main" \
  > /etc/apt/sources.list.d/matrix-org.list
apt-get update -qq
melde "$(txt paketquelle "$SUITE")"
gruen "$(txt pakete_fertig)"
fi

# DATENBANK
if lauf datenbank; then
schritt "$(txt s_datenbank)"
if su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='synapse'\"" | grep -q 1; then
  melde "$(txt db_existiert)"
else
  DBPW=$(openssl rand -base64 24)
  ( umask 077; printf '%s\n' "$DBPW" > /root/.synapse_db_pw )
  # Die Rolle kann aus einem abgebrochenen Lauf schon da sein (Datenbank fehlt,
  # Rolle nicht). CREATE USER bricht dann ab, und zwar bei jedem weiteren Lauf.
  # Deshalb: vorhandene Rolle bekommt das neue Passwort, fehlende wird angelegt.
  if su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='synapse_user'\"" | grep -q 1; then
    su - postgres -c "psql -qc \"ALTER USER synapse_user WITH PASSWORD '$DBPW';\""
  else
    su - postgres -c "psql -qc \"CREATE USER synapse_user WITH PASSWORD '$DBPW';\""
  fi
  # Sortierreihenfolge C ist für Synapse zwingend - mit anderer bricht es später.
  su - postgres -c "createdb --encoding=UTF8 --locale=C --template=template0 --owner=synapse_user synapse"
  melde "$(txt db_angelegt)"
fi
sortierung=$(su - postgres -c "psql -tAc \"SELECT datcollate FROM pg_database WHERE datname='synapse'\"")
[ "$sortierung" = "C" ] || ende "$(txt db_sortierung "$sortierung")"
gruen "$(txt db_bereit)"
fi

# SYNAPSE
if lauf synapse; then
schritt "$(txt s_synapse)"
if ! dpkg -l matrix-synapse-py3 2>/dev/null | grep -q '^ii'; then
  echo "matrix-synapse-py3 matrix-synapse/server-name string $DOMAIN" | debconf-set-selections
  echo "matrix-synapse-py3 matrix-synapse/report-stats boolean false" | debconf-set-selections
  apt-get install -y -qq matrix-synapse-py3 >>"$LOG" 2>&1 \
    || ende "$(txt synapse_pakete_fehlgeschlagen "$(apt_grund)" "$LOG")"
  melde "$(txt synapse_installiert)"
else
  melde "$(txt synapse_schon_da)"
fi

CD=/etc/matrix-synapse/conf.d
printf 'server_name: %s\n' "$DOMAIN" > "$CD/server_name.yaml"

# Feste Geheimnisse: ohne sie erfindet Synapse bei JEDEM Neustart neue und
# wirft alle angemeldeten Geräte raus. Die Debian-Pakete setzen sie nicht.
if [ ! -f "$CD/05-secrets.yaml" ]; then
  ( umask 077
  cat > "$CD/05-secrets.yaml" <<EOF
macaroon_secret_key: "$(openssl rand -base64 36)"
form_secret: "$(openssl rand -base64 36)"
registration_shared_secret: "$(openssl rand -base64 32)"
suppress_key_server_warning: true
EOF
  )
  melde "$(txt geheimnisse_erzeugt)"
fi

# Das Passwort entsteht nur beim Anlegen der Datenbank. Fehlt die Datei in /root
# (aufgeraeumt, aus einer Sicherung ohne Punktdateien zurückgeholt), steht es
# noch in der vorhandenen Synapse-Konfiguration - erst dann ist Schluss.
if [ -f /root/.synapse_db_pw ]; then
  DBPW=$(cat /root/.synapse_db_pw)
elif [ -f "$CD/10-database.yaml" ]; then
  DBPW=$(sed -n 's/^ *password: "\(.*\)"$/\1/p' "$CD/10-database.yaml")
  [ -n "$DBPW" ] || ende "$(txt dbpw_nicht_in_yaml "$CD/10-database.yaml")"
  ( umask 077; printf '%s\n' "$DBPW" > /root/.synapse_db_pw )
  melde "$(txt dbpw_uebernommen "$CD/10-database.yaml")"
else
  ende "$(txt dbpw_fehlt)"
fi
cat > "$CD/10-database.yaml" <<EOF
database:
  name: psycopg2
  args:
    user: synapse_user
    password: "$DBPW"
    dbname: synapse
    host: localhost
    cp_min: 5
    cp_max: 10
EOF

cat > "$CD/15-listener.yaml" <<'EOF'
# Nur lokal - nach aussen geht es ausschliesslich ueber nginx.
listeners:
  - port: 8008
    tls: false
    type: http
    x_forwarded: true
    bind_addresses: ['127.0.0.1']
    resources:
      - names: [client, federation]
        compress: false
EOF

TURN_GEHEIM=$(cat /etc/matrix-synapse/.turn_secret 2>/dev/null || openssl rand -hex 32)
( umask 077; printf '%s\n' "$TURN_GEHEIM" > /etc/matrix-synapse/.turn_secret )

cat > "$CD/20-betrieb.yaml" <<EOF
# DIESE DATEI SCHREIBT DER INSTALLER BEI JEDEM LAUF NEU. Eigene Aenderungen
# gehoeren in /etc/matrix-synapse/conf.d/50-eigene.yaml - die fasst er nie an,
# und Synapse liest alle conf.d/*.yaml (spaetere Dateien ueberschreiben fruehere).
public_baseurl: "https://$MATRIX_HOST/"

# --- Registrierung: NUR mit Einladungscode ---
# Das ist die eigentliche Missbrauchsbremse. Jedes Konto geht auf eine
# nachvollziehbare Einladungskette zurueck.
enable_registration: true
registration_requires_token: true
enable_registration_without_verification: true

# --- Verschluesselung ist der Normalfall ---
encryption_enabled_by_default_for_room_type: all

# --- Foederation offen: die Leute hier erreichen das ganze Matrix-Netz.
#     Das bringt KEINE fremden Konten auf diesen Server - dafuer sorgt die
#     geschlossene Registrierung oben. Einzelne Stoerenfriede spaeter so sperren:
#     federation_denylist: ["beispiel.invalid"]
# ---

# --- Suche: nur finden, wer schon einen Raum teilt ---
# Serverweites Finden waere bequemer, wuerde aber getrennte Kreise auf demselben
# Server wieder zusammenwerfen (Familie sieht Freundeskreis). Das ist unnoetig,
# seit Einladungen ihren Zielraum mitbringen: Wer hereinkommt, landet sofort im
# richtigen Raum und findet dort alle, die ihn etwas angehen.
# Fuer EINEN Kreis, in dem sich alle finden sollen: 'search_all_users: true' in
# 50-eigene.yaml setzen (nicht hier - diese Datei wird ueberschrieben).
user_directory:
  enabled: true
  search_all_users: false
  prefer_local_users: true

# --- Anrufe ---
turn_uris: ["turn:$TURN_HOST?transport=udp", "turn:$TURN_HOST?transport=tcp", "turns:$TURN_HOST?transport=tcp"]
turn_shared_secret: "$TURN_GEHEIM"
turn_user_lifetime: 86400000
turn_allow_guests: false

max_upload_size: 100M
url_preview_enabled: false

# --- Nur FREMDE Medien raeumen (von anderen Servern zwischengespeichert). Eigene
#     Fotos und Videos bleiben - bei Ende-zu-Ende-Verschluesselung waeren geraeumte
#     Anhaenge unwiederbringlich. Platz fuer eigene Medien muss der Betreiber einplanen. ---
media_retention:
  remote_media_lifetime: 90d
EOF
chown root:matrix-synapse "$CD"/*.yaml; chmod 640 "$CD"/*.yaml
systemctl restart matrix-synapse
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8008/health >/dev/null 2>&1 && break; sleep 1; done
curl -sf http://127.0.0.1:8008/health >/dev/null || ende "$(txt synapse_startet_nicht)"
melde "$(txt synapse_laeuft "$DOMAIN")"
gruen "$(txt synapse_bereit)"
fi

# WEB + TLS
if lauf web; then
schritt "$(txt s_web)"
mkdir -p /var/www/acme /var/www/wellknown/.well-known/matrix

# Delegation: DAMIT lauten die Kennungen @name:DOMAIN statt @name:matrix.DOMAIN.
cat > /var/www/wellknown/.well-known/matrix/server <<EOF
{ "m.server": "$MATRIX_HOST:443" }
EOF
cat > /var/www/wellknown/.well-known/matrix/client <<EOF
{
  "m.homeserver": { "base_url": "https://$MATRIX_HOST" },
  "org.matrix.msc3575.proxy": { "url": "https://$MATRIX_HOST" }
}
EOF

if [ "$HINTER_PROXY" = nein ]; then
# Schritt 1: nur HTTP, damit Let's Encrypt die Prüfdateien abholen kann
cat > /etc/nginx/sites-available/messenger <<EOF
server {
  listen 80 default_server; listen [::]:80 default_server;
  server_name $DOMAIN $MATRIX_HOST $CHAT_HOST $EINLADUNG_HOST $VERWALTUNG_HOST $TURN_HOST;
  location /.well-known/acme-challenge/ { root /var/www/acme; }
  location / { return 404; }
}
EOF
ln -sf /etc/nginx/sites-available/messenger /etc/nginx/sites-enabled/messenger
rm -f /etc/nginx/sites-enabled/default
nginx -t >>"$LOG" 2>&1 || ende "$(txt nginx_fehlerhaft)"
systemctl reload nginx

if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  certbot certonly --webroot -w /var/www/acme --non-interactive --agree-tos \
    -m "$MAIL" --cert-name "$DOMAIN" \
    -d "$DOMAIN" -d "$MATRIX_HOST" -d "$CHAT_HOST" \
    -d "$EINLADUNG_HOST" -d "$VERWALTUNG_HOST" -d "$TURN_HOST" >>"$LOG" 2>&1 \
    || ende "$(txt zertifikat_fehlgeschlagen "$(le_grund)" "$LOG")"
  melde "$(txt zertifikat_ausgestellt)"
else
  melde "$(txt zertifikat_existiert)"
fi
fi   # Ende: eigenes Zertifikat nur ohne vorgelagerten Proxy
Z="/etc/letsencrypt/live/$DOMAIN"

# Wie die Vhosts lauschen und was sie an Sicherheitsköpfen mitgeben.
# Hinter einem Proxy endet TLS dort - hier läuft nur noch Klartext auf Port 80.
# X-Forwarded-For: Synapse vertraut dem Kopf (x_forwarded: true). Hinter einem
# Proxy ist remote_addr immer der Proxy - dann muss dessen Kette ergänzt
# werden, sonst sieht Synapse alle Nutzer unter einer Adresse und die
# adressbezogenen Bremsen treffen alle gemeinsam. OHNE Proxy dagegen nur
# remote_addr: sonst könnte jeder Client sich per eigenem Kopf eine
# beliebige Adresse geben und damit Bremsen und fail2ban umgehen.
if [ "$HINTER_PROXY" = ja ]; then
  XFF='$proxy_add_x_forwarded_for'
  LAUSCH="listen 80; listen [::]:80;"
  gemeinsam="
  add_header X-Content-Type-Options nosniff always;
  add_header Referrer-Policy no-referrer always;
"
  VORSCHALT="server {
  listen 80 default_server; listen [::]:80 default_server;
  server_name _;
  location / { return 404; }
}"
else
  XFF='$remote_addr'
  LAUSCH="listen 443 ssl; listen [::]:443 ssl; http2 on;"
  gemeinsam="
  ssl_certificate $Z/fullchain.pem;
  ssl_certificate_key $Z/privkey.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_prefer_server_ciphers off;
  add_header Strict-Transport-Security \"max-age=31536000\" always;
  add_header X-Content-Type-Options nosniff always;
  add_header Referrer-Policy no-referrer always;
"
  VORSCHALT="server {
  listen 80 default_server; listen [::]:80 default_server;
  server_name _;
  location /.well-known/acme-challenge/ { root /var/www/acme; }
  location / { return 301 https://\$host\$request_uri; }
}"
fi

cat > /etc/nginx/sites-available/messenger <<EOF
$VORSCHALT

# --- nackte Domain: traegt die Delegation, sonst nichts ---
server {
  $LAUSCH
  server_name $DOMAIN;
$gemeinsam
  root /var/www/wellknown;
  location /.well-known/matrix/ { default_type application/json; add_header Access-Control-Allow-Origin *; }
  location / { return 302 https://$CHAT_HOST; }
}

# --- Matrix selbst ---
server {
  $LAUSCH
  server_name $MATRIX_HOST;
$gemeinsam
  client_max_body_size 100M;
  location ~ ^(/_matrix|/_synapse/client) {
    proxy_pass http://127.0.0.1:8008;
    proxy_set_header X-Forwarded-For $XFF;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header Host \$host;
    proxy_read_timeout 600s;
  }
  location / { return 302 https://$CHAT_HOST; }
}

# --- Element Web ---
server {
  $LAUSCH
  server_name $CHAT_HOST;
$gemeinsam
  root /var/www/element; index index.html;
  location = /config.json { add_header Cache-Control "no-cache"; }
  location / { try_files \$uri \$uri/ /index.html; }
}

# --- Einladungsseite (oeffentlich: Eingeladene klicken von unterwegs) ---
server {
  $LAUSCH
  server_name $EINLADUNG_HOST;
$gemeinsam
  location / {
    proxy_pass http://127.0.0.1:8082;
    proxy_set_header X-Forwarded-For $XFF;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header Host \$host;
  }
}

# --- Verwaltung: dieselbe Anwendung wie die Einladungsseite, anderes Gesicht.
#     Hinein kommt nur, wer am Matrix-Server Verwalter ist. Bewusst KEIN zweites
#     Passwort davor - der Betreiber soll sich nicht zweimal anmelden muessen. ---
server {
  $LAUSCH
  server_name $VERWALTUNG_HOST;
$gemeinsam
  location / {
    proxy_pass http://127.0.0.1:8082;
    proxy_set_header X-Forwarded-For $XFF;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header Host \$host;
  }
}
EOF
# Lesbar für den Webserver. Ausdrücklich, damit eine zu enge Maske nicht
# wieder unbemerkt zu "403 Forbidden" auf der Delegation führt.
chmod 644 /etc/nginx/sites-available/messenger
chmod 755 /var/www/wellknown /var/www/wellknown/.well-known /var/www/wellknown/.well-known/matrix
chmod 644 /var/www/wellknown/.well-known/matrix/server /var/www/wellknown/.well-known/matrix/client
# Einschalten gehört hierher, nicht in den Zertifikats-Vorlauf: sonst bleibt
# die Konfiguration im Proxy-Modus geschrieben, aber unbenutzt - der Server
# antwortet dann auf gar nichts und niemand sieht, warum.
ln -sf /etc/nginx/sites-available/messenger /etc/nginx/sites-enabled/messenger
rm -f /etc/nginx/sites-enabled/default
nginx -t >>"$LOG" 2>&1 || ende "$(txt nginx_fehlerhaft)"
systemctl reload nginx
melde "$(txt vhosts_aktiv)"
gruen "$(txt web_bereit)"
fi

# TURN
if lauf turn; then
schritt "$(txt s_turn)"
TURN_GEHEIM=$(cat /etc/matrix-synapse/.turn_secret)
Z="/etc/letsencrypt/live/$DOMAIN"
mkdir -p /etc/coturn/certs

# Hinter einem Router braucht coturn beide Adressen: aussen die öffentliche,
# innen die eigene. Hängt der Server direkt am Netz, genügt die eine.
if [ "$HINTER_ROUTER" = ja ]; then
  TURN_AUSSEN="$EIGENE_IP/$LOKALE_IP"
else
  TURN_AUSSEN="$EIGENE_IP"
fi

if [ "$HINTER_PROXY" = nein ]; then
# coturn läuft nicht als root und kommt an /etc/letsencrypt nicht heran.
# Deshalb Kopien, die bei jeder Erneuerung aufgefrischt werden.
cat > /etc/letsencrypt/renewal-hooks/deploy/coturn.sh <<EOF
#!/bin/sh
install -o turnserver -g turnserver -m 600 "$Z/fullchain.pem" /etc/coturn/certs/fullchain.pem
install -o turnserver -g turnserver -m 600 "$Z/privkey.pem"  /etc/coturn/certs/privkey.pem
systemctl reload coturn 2>/dev/null || systemctl restart coturn
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/coturn.sh
/etc/letsencrypt/renewal-hooks/deploy/coturn.sh
fi

# Verschluesseltes TURN gibt es nur mit eigenem Zertifikat. Hinter einem Proxy
# liegt keines auf diesem Rechner - TURN läuft dann unverschluesselt auf 3478.
# Das ist kein Schaden: die Gesprächsinhalte sind ohnehin Ende zu Ende
# verschluesselt, TURN leitet nur weiter.
if [ "$HINTER_PROXY" = ja ]; then
  TLS_ZEILEN=""
  TURN_HINWEIS="$(txt turn_unverschluesselt)"
else
  TLS_ZEILEN="tls-listening-port=5349
cert=/etc/coturn/certs/fullchain.pem
pkey=/etc/coturn/certs/privkey.pem"
  TURN_HINWEIS="$(txt turn_ports)"
fi

cat > /etc/turnserver.conf <<EOF
use-auth-secret
static-auth-secret=$TURN_GEHEIM
realm=$DOMAIN

# Welche Adresse coturn den Teilnehmern als Relais nennt.
# Ohne diese Zeile scheitern Anrufe hinter einem Router lautlos.
external-ip=$TURN_AUSSEN

listening-port=3478
min-port=49160
max-port=49200
$TLS_ZEILEN

# Nur weiterleiten, nicht ins eigene Netz hineinlassen
no-tcp-relay
no-multicast-peers
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
allowed-peer-ip=$EIGENE_IP

user-quota=12
total-quota=1200
no-cli
syslog
EOF
sed -i 's/^#*TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn 2>/dev/null || true
systemctl enable --now coturn >>"$LOG" 2>&1
systemctl restart coturn
sleep 2
systemctl is-active --quiet coturn || ende "$(txt coturn_startet_nicht)"
melde "$(txt coturn_laeuft "$TURN_HINWEIS")"
melde "$(txt relais_adresse "$TURN_AUSSEN")"
if [ "$HINTER_ROUTER" = ja ]; then
  echo
  rot "$(txt router_handarbeit)"
  sag router_hinweis "$LOKALE_IP"
  echo
fi
gruen "$(txt anrufe_vorbereitet)"
fi

# ELEMENT WEB
if lauf element; then
schritt "$(txt s_element)"
# Holen, Entpacken und config.json stehen in einem eigenen Skript: der
# Update-Lauf aus der Verwaltung (Abschnitt 'updates') erneuert Element
# damit ebenfalls, ohne den Installer aufrufen zu muessen.
[ -f "$HIER/dateien/messenger-element-erneuern.sh" ] || ende "$(txt datei_fehlt messenger-element-erneuern.sh)"
install -m 755 "$HIER/dateien/messenger-element-erneuern.sh" /usr/local/sbin/messenger-element-erneuern.sh
ausgabe=$(/usr/local/sbin/messenger-element-erneuern.sh "$DOMAIN" 2>>"$LOG") \
  || ende "$(txt element_fehlgeschlagen "$LOG")"
while IFS= read -r zeile; do [ -z "$zeile" ] || melde "$zeile"; done <<< "$ausgabe"
gruen "$(txt element_bereit)"
fi

# EINLADUNG
if lauf einladung; then
schritt "$(txt s_einladung)"
[ -f "$HIER/dateien/einladung.py" ] || ende "$(txt datei_fehlt einladung.py)"
id einladung >/dev/null 2>&1 || useradd --system --home /opt/einladung --shell /usr/sbin/nologin einladung
# WICHTIG: nicht mit umask 077 anlegen, sonst kommt der Dienstbenutzer nicht hinein.
mkdir -p /opt/einladung /etc/einladung /var/lib/einladung
chmod 755 /opt/einladung; chmod 750 /etc/einladung /var/lib/einladung
chgrp einladung /etc/einladung; chown einladung:einladung /var/lib/einladung
# Der Auftragsordner für "Jetzt aktualisieren" (Abschnitt 'updates') muss
# VOR dem Dienststart da sein: ReadWritePaths bindet nur ein, was beim Start
# existiert - später angelegt bliebe er für den laufenden Dienst unsichtbar.
mkdir -p /var/lib/messenger/auftraege
chmod 755 /var/lib/messenger
chown einladung:einladung /var/lib/messenger/auftraege; chmod 750 /var/lib/messenger/auftraege
install -m 755 "$HIER/dateien/einladung.py" /opt/einladung/einladung.py

GEHEIM=$(grep -oP 'registration_shared_secret: "\K[^"]+' /etc/matrix-synapse/conf.d/05-secrets.yaml 2>/dev/null) \
  || ende "$(txt synapse_nicht_eingerichtet)"
if ! su - postgres -c "psql -tAc \"SELECT 1 FROM users WHERE name='@einladungsdienst:$DOMAIN'\" synapse" | grep -q 1; then
  DPW=$(openssl rand -base64 24)
  ( umask 077; printf '%s\n' "$DPW" > /root/.einladungsdienst_pw )
  register_new_matrix_user -u einladungsdienst -p "$DPW" -a -k "$GEHEIM" http://127.0.0.1:8008 >>"$LOG" 2>&1 \
    || ende "$(txt dienstkonto_fehlgeschlagen "$LOG")"
  melde "$(txt dienstkonto_angelegt)"
fi
if [ ! -s /etc/einladung/admin-token ]; then
  DPW=$(cat /root/.einladungsdienst_pw)
  curl -s -X POST http://127.0.0.1:8008/_matrix/client/v3/login -H 'Content-Type: application/json' \
    -d "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"einladungsdienst\"},\"password\":\"$DPW\",\"initial_device_display_name\":\"Einladungsdienst\"}" \
    | jq -r .access_token > /etc/einladung/admin-token
fi
[ -s /etc/einladung/sitzungsschluessel ] || openssl rand -base64 48 > /etc/einladung/sitzungsschluessel
# Verschluesselt die TOTP-Geheimnisse in der Ablage (Fernet).
[ -s /etc/einladung/datenschluessel ] || \
  python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
  > /etc/einladung/datenschluessel
chown root:einladung /etc/einladung/admin-token /etc/einladung/sitzungsschluessel /etc/einladung/datenschluessel
chmod 640 /etc/einladung/admin-token /etc/einladung/sitzungsschluessel /etc/einladung/datenschluessel

install -m 644 "$HIER/dateien/einladung.service" /etc/systemd/system/einladung.service
mkdir -p /etc/systemd/system/einladung.service.d
cat > "$UMGEBUNG" <<EOF
[Service]
# Oertlich, nicht ueber den oeffentlichen Namen: sonst laeuft jeder
# Verwaltungsaufruf durch Router und Proxy wieder zurueck zum selben Rechner -
# und steht still, solange der Proxy den Namen noch nicht kennt.
Environment=MESSENGER_HEIMSERVER=http://127.0.0.1:8008
Environment=MESSENGER_SERVERNAME=$DOMAIN
Environment=MESSENGER_ELEMENT=https://$CHAT_HOST
Environment=MESSENGER_EIGENE_URL=https://$EINLADUNG_HOST
Environment=MESSENGER_VERWALTUNG_HOST=$VERWALTUNG_HOST
# Sprache der Seiten, bis ein Verwalter in der Verwaltung umschaltet (en|de).
Environment=MESSENGER_LANGUAGE=$SPRACHE
# Anrede des Betreibers in Endnutzerhinweisen ("oder sag ... Bescheid"), also
# --betreiber, nicht der Verwaltername. In Anfuehrungszeichen, sonst bricht
# systemd den Wert am ersten Leerzeichen ab. Leer heisst: der Dienst nimmt die
# Vorgabe seiner Sprache ("dem Betreiber" / "the operator").
Environment="MESSENGER_BETREIBER=${BETREIBER:-}"
EOF
systemctl daemon-reload
systemctl enable --now einladung >>"$LOG" 2>&1
systemctl restart einladung; sleep 2
systemctl is-active --quiet einladung || ende "$(txt einladung_startet_nicht)"
melde "$(txt einladung_unter "$EINLADUNG_HOST" "$SPRACHE")"
gruen "$(txt einladungen_bereit)"
fi

# SICHERUNG
# Probe mit demselben Werkzeug, das die Sicherung nachts benutzt: ein leeres
# Verzeichnis hinschieben legt das Ziel samt Zwischenstufen an und beweist
# Schluessel, Hostschluessel und Schreibrecht in einem Schritt. ssh mit mkdir
# taugt dafür nicht - eine Hetzner Storage Box etwa hat keine Shell.
# Steht ausserhalb des Abschnitts, weil auch die Nachkontrolle sie braucht.
sicherung_erreichbar() {
  local leer rc=0; leer=$(mktemp -d)
  rsync -a --mkpath -e "ssh -o BatchMode=yes -o ConnectTimeout=15" "$leer/" "$SICHERUNG_ZIEL/" >>"$LOG" 2>&1 || rc=$?
  rmdir "$leer"; return $rc
}

if lauf sicherung; then
schritt "$(txt s_sicherung)"
cat > /usr/local/sbin/messenger-sicherung.sh <<EOF
#!/bin/bash
# Taegliche Sicherung. Verschluesselt, bevor sie das Haus verlaesst.
set -euo pipefail
ZIEL="\${1:-${SICHERUNG_ZIEL:-}}"
TMP=\$(mktemp -d); trap 'rm -rf "\$TMP"' EXIT
STAND=\$(date +%F)
su - postgres -c "pg_dump synapse" | gzip > "\$TMP/synapse-\$STAND.sql.gz"
tar czf "\$TMP/konfig-\$STAND.tgz" /etc/matrix-synapse /etc/einladung /etc/turnserver.conf 2>/dev/null
tar czf "\$TMP/medien-\$STAND.tgz" -C /var/lib/matrix-synapse media 2>/dev/null || true
if [ -f /root/.sicherung_passwort ]; then
  for d in "\$TMP"/*; do
    gpg --batch --yes --symmetric --cipher-algo AES256 \\
        --passphrase-file /root/.sicherung_passwort "\$d" && rm -f "\$d"
  done
fi
# --mkpath legt fehlende Zwischenverzeichnisse an (rsync allein nur das letzte),
# BatchMode verhindert stilles Haengen an einer Passwortabfrage. Scheitert der
# Weg nach draussen, bleibt der Stand oertlich und der Dienst meldet Fehler -
# vorher raeumte der trap ihn weg, und es gab gar keine Sicherung.
AUSWAERTS=ja
if [ -n "\$ZIEL" ]; then
  rsync -a --mkpath -e "ssh -o BatchMode=yes" --remove-source-files "\$TMP"/ "\$ZIEL/\$STAND/" || AUSWAERTS=nein
fi
if [ -z "\$ZIEL" ] || [ "\$AUSWAERTS" = nein ]; then
  mkdir -p /var/backups/messenger/\$STAND
  [ -z "\$(ls -A "\$TMP")" ] || mv "\$TMP"/* /var/backups/messenger/\$STAND/
  find /var/backups/messenger -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true
fi
if [ "\$AUSWAERTS" = nein ]; then
  echo "Sicherungsziel \$ZIEL nicht erreichbar - Stand liegt örtlich in /var/backups/messenger/\$STAND" >&2
  exit 1
fi
EOF
chmod 755 /usr/local/sbin/messenger-sicherung.sh
[ -f /root/.sicherung_passwort ] || { openssl rand -base64 32 > /root/.sicherung_passwort; chmod 600 /root/.sicherung_passwort; }
cat > /etc/systemd/system/messenger-sicherung.service <<EOF
[Unit]
Description=Sicherung des Messengers
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/messenger-sicherung.sh ${SICHERUNG_ZIEL:-}
EOF
cat > /etc/systemd/system/messenger-sicherung.timer <<'EOF'
[Unit]
Description=Tägliche Sicherung des Messengers
[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=900
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
if [ -n "${SICHERUNG_ZIEL:-}" ]; then
  # Zugang zum Ziel herstellen und prüfen, BEVOR der Timer startet. Vorher
  # lief der Timer ab 03:30 ins Leere (kein Schluessel, kein Hostschluessel,
  # kein Zielverzeichnis), und der Fehler stand allein im Journal.
  mkdir -p /root/.ssh; chmod 700 /root/.ssh
  [ -f /root/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/id_ed25519 -C "messenger-sicherung@$DOMAIN"
  ZIEL_RECHNER="${SICHERUNG_ZIEL%%:*}"; ZIEL_RECHNER="${ZIEL_RECHNER#*@}"
  # Den Port so nehmen, wie ssh ihn auflöst (auch aus /root/.ssh/config) -
  # Hostschluessel anderer Ports stehen in known_hosts als [rechner]:port.
  ZIEL_PORT=$(ssh -G "$ZIEL_RECHNER" 2>/dev/null | awk '/^port /{print $2}' || true)
  [ -n "$ZIEL_PORT" ] || ZIEL_PORT=22
  if [ "$ZIEL_PORT" = 22 ]; then ZIEL_MERKMAL="$ZIEL_RECHNER"; else ZIEL_MERKMAL="[$ZIEL_RECHNER]:$ZIEL_PORT"; fi
  ssh-keygen -F "$ZIEL_MERKMAL" -f /root/.ssh/known_hosts >/dev/null 2>&1 \
    || ssh-keyscan -T 10 -p "$ZIEL_PORT" "$ZIEL_RECHNER" >> /root/.ssh/known_hosts 2>/dev/null || true
  if sicherung_erreichbar; then
    systemctl enable --now messenger-sicherung.timer >>"$LOG" 2>&1
    melde "$(txt sicherung_erreichbar)"
    melde "$(txt sicherung_taeglich_nach "$SICHERUNG_ZIEL")"
    gruen "$(txt sicherung_eingerichtet)"
  else
    rot "$(txt sicherung_nicht_erreichbar "$SICHERUNG_ZIEL")"
    sag sicherung_schluessel_hinweis "$(cat /root/.ssh/id_ed25519.pub)" "$ZIEL_RECHNER" "$LOG"
  fi
else
  systemctl enable --now messenger-sicherung.timer >>"$LOG" 2>&1
  melde "$(txt sicherung_oertlich)"
  gruen "$(txt sicherung_eingerichtet)"
fi
fi

# AKTUALISIERUNGEN
# Grundsatz: nichts wird blind aktualisiert. Ein Timer PRUEFT täglich und
# legt den Befund in updates.json ab; die Verwaltung zeigt ihn an. EINGESPIELT
# wird erst, wenn ein Verwalter dort klickt: der Dienst legt dann eine
# Auftragsdatei ab, eine Path-Unit sieht sie und startet den Update-Lauf als
# root. Die Datei ist nur ein Klingelknopf - ihr Inhalt wird nie ausgewertet.
if lauf updates; then
schritt "$(txt s_updates)"
id einladung >/dev/null 2>&1 || ende "$(txt benutzer_einladung_fehlt)"
for d in messenger-update-pruefen.sh messenger-update-ausfuehren.sh messenger-element-erneuern.sh; do
  [ -f "$HIER/dateien/$d" ] || ende "$(txt datei_fehlt "$d")"
  install -m 755 "$HIER/dateien/$d" "/usr/local/sbin/$d"
done

# Befund lesbar für den Dienst (644 in 755), Auftragsordner gehört ihm (750).
mkdir -p /var/lib/messenger/auftraege
chmod 755 /var/lib/messenger
chown einladung:einladung /var/lib/messenger/auftraege; chmod 750 /var/lib/messenger/auftraege

# Der Dienst darf nur dort schreiben (ProtectSystem=strict). Die Freigabe steht
# in dateien/einladung.service; auf einem BESTEHENDEN Server mit '--nur updates'
# käme sie sonst nie an - deshalb die Unit hier nachziehen, aber nur neu
# starten, wenn sie sich wirklich geändert hat.
if [ -f "$HIER/dateien/einladung.service" ] \
   && ! cmp -s "$HIER/dateien/einladung.service" /etc/systemd/system/einladung.service; then
  install -m 644 "$HIER/dateien/einladung.service" /etc/systemd/system/einladung.service
  systemctl daemon-reload
  systemctl try-restart einladung >>"$LOG" 2>&1 || true
  melde "$(txt service_nachgezogen)"
fi

cat > /etc/systemd/system/messenger-update-pruefen.service <<'EOF'
[Unit]
Description=Prüfen, ob Aktualisierungen für den Messenger vorliegen
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/messenger-update-pruefen.sh
EOF
cat > /etc/systemd/system/messenger-update-pruefen.timer <<'EOF'
[Unit]
Description=Tägliche Prüfung auf Aktualisierungen (nur prüfen, nie einspielen)
[Timer]
OnCalendar=*-*-* 06:00:00
RandomizedDelaySec=3600
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/messenger-update.path <<'EOF'
[Unit]
Description=Wartet auf einen Update-Auftrag aus der Verwaltung
[Path]
PathExists=/var/lib/messenger/auftraege/update
Unit=messenger-update.service
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/messenger-update.service <<'EOF'
[Unit]
Description=Aktualisierung des Messengers auf Auftrag aus der Verwaltung
# Kein Startlimit: faellt der Dienst mehrmals kurz hintereinander, ginge sonst
# auch die Path-Unit dauerhaft in 'failed' und kein Klick kaeme mehr an. Eine
# Schleife droht nicht: die Auftragsdatei wird unten entfernt, und das Skript
# selbst laesst nur einen Lauf zu (Sperre).
StartLimitIntervalSec=0
[Service]
Type=oneshot
# Erst den Auftrag entfernen - sonst zuendet die Path-Unit sofort wieder.
# Sein Inhalt wird nie gelesen: er ist ein Klingelknopf, kein Befehl. '-rf',
# falls der Dienstbenutzer statt einer Datei einen Ordner abgelegt hat, und
# '-' davor: scheitert das Entfernen, laeuft der Dienst trotzdem.
ExecStartPre=-/bin/rm -rf /var/lib/messenger/auftraege/update
ExecStart=/usr/local/sbin/messenger-update-ausfuehren.sh
EOF
systemctl daemon-reload
systemctl enable --now messenger-update-pruefen.timer >>"$LOG" 2>&1
systemctl enable --now messenger-update.path >>"$LOG" 2>&1
melde "$(txt updates_timer)"

# Erstlauf sofort, damit die Verwaltung nicht bis morgen "noch nicht geprüft" zeigt.
if befund=$(/usr/local/sbin/messenger-update-pruefen.sh 2>>"$LOG"); then
  melde "$befund"
else
  melde "$(txt erste_pruefung_fehlgeschlagen "$LOG")"
fi
gruen "$(txt updates_eingerichtet)"
fi

# HAERTUNG
if lauf haertung; then
schritt "$(txt s_haertung)"
# Den SSH-Port aus der laufenden sshd-Konfiguration nehmen, nicht fest 22:
# wer sshd auf einen anderen Port gelegt hat, sperrte sich sonst mit der
# nächsten Anmeldung aus (die laufende Sitzung überlebt, das täuscht).
SSH_PORTS=$(sshd -T 2>/dev/null | awk '/^port /{print $2}' | sort -u | xargs || true)
[ -n "$SSH_PORTS" ] || SSH_PORTS=22
# Zurücksetzen nur, solange ufw noch nicht aktiv ist. Sonst verschwänden bei
# jedem Lauf Regeln, die der Betreiber von Hand angelegt hat (WireGuard,
# eingeschränkte Quelladressen) - entgegen der Zusage, dass Wiederholen nicht
# schadet. Die eigenen allow-Regeln sind ohnehin wiederholbar.
ufw status 2>/dev/null | grep -q '^Status: active' || ufw --force reset >>"$LOG" 2>&1
ufw default deny incoming >>"$LOG" 2>&1
ufw default allow outgoing >>"$LOG" 2>&1
for p in $SSH_PORTS; do ufw allow "$p"/tcp >>"$LOG" 2>&1; done
ufw allow 80,443/tcp >>"$LOG" 2>&1
ufw allow 3478,5349/tcp >>"$LOG" 2>&1
ufw allow 3478,5349/udp >>"$LOG" 2>&1
ufw allow 49160:49200/udp >>"$LOG" 2>&1
# Läuft die Einrichtung im Browser (setup.sh), hält deren Seite einen eigenen
# Port. Der muss offen bleiben, sonst bricht genau hier die Verbindung ab, und
# der Nutzer sieht das Ende der Einrichtung nie. "Abschliessen" auf der Seite
# schliesst den Port wieder.
if [ -n "${HUMATRIX_SETUP_PORT:-}" ]; then
  ufw allow "$HUMATRIX_SETUP_PORT"/tcp >>"$LOG" 2>&1
  ufw allow "$((HUMATRIX_SETUP_PORT + 1))"/tcp >>"$LOG" 2>&1   # verschluesselte Fassung der Seite nach "Fertig"
fi
ufw --force enable >>"$LOG" 2>&1
melde "$(txt firewall "$SSH_PORTS")"

# Den ermittelten SSH-Port auch der Jail mitgeben: ohne 'port =' erbt sie
# 'ssh' (22) aus Debians jail.conf, und auf einem verlegten Port liefe der
# Bann ins Leere. Mehrere Ports als Kommaliste.
cat > /etc/fail2ban/jail.d/messenger.conf <<EOF
[sshd]
enabled = true
port = ${SSH_PORTS// /,}
maxretry = 4
bantime = 1h
EOF
systemctl enable --now fail2ban >>"$LOG" 2>&1; systemctl restart fail2ban
melde "$(txt fail2ban "$SSH_PORTS")"

# Nur Sicherheitsaktualisierungen automatisch - alles andere bewusst von Hand.
cat > /etc/apt/apt.conf.d/51messenger-unattended <<'EOF'
Unattended-Upgrade::Origins-Pattern { "origin=Debian,codename=${distro_codename}-security"; };
Unattended-Upgrade::Automatic-Reboot "false";
EOF
systemctl enable --now unattended-upgrades >>"$LOG" 2>&1 || true
melde "$(txt sicherheitsupdates)"
gruen "$(txt haertung_fertig)"
fi

# ABSCHLUSS
# Erst Zugang schaffen, dann prüfen. Vorher brach der Installer bei einer
# fehlgeschlagenen Prüfung ab - und hinterliess einen Server OHNE Verwalterkonto
# und ohne Zugangsdatei. Das ist genau falsch herum.
schritt "$(txt s_abschluss)"
# Läuft bei jedem --nur-Aufruf, auch auf einem System ohne Synapse: dann sonst
# nur ein nackter grep-Fehler. Fester Pfad, $CD gibt es nur im Synapse-Block.
GEHEIM=$(grep -oP 'registration_shared_secret: "\K[^"]+' /etc/matrix-synapse/conf.d/05-secrets.yaml 2>/dev/null) \
  || ende "$(txt synapse_nicht_eingerichtet)"

# Die alte Zugangsdatei NICHT einfach überschreiben. Beim zweiten Lauf stand
# sonst "(unveraendert - stand schon in dieser Datei)" genau dort, wo vorher das
# Passwort stand: der Verweis zeigte auf sich selbst, das Passwort war weg.
# Die Passwortzeile heisst je nach Sprache der vorigen Datei "Passwort" oder
# "Password" - beide gleich lang, dieselbe Spalte.
ALTES_PW=""
if [ -f "$ZUGANG" ]; then
  cp -p "$ZUGANG" "$ZUGANG.vorher-$(date +%F-%H%M)"
  ALTES_PW=$(awk '/^  (Passwort|Password)   /{sub(/^  (Passwort|Password)   /,""); print; exit}' "$ZUGANG")
fi

if ! su - postgres -c "psql -tAc \"SELECT 1 FROM users WHERE name='@$VERWALTER:$DOMAIN'\" synapse" | grep -q 1; then
  APW=$(openssl rand -base64 15)
  # Nicht wortlos sterben: vorher endete das Skript hier still mit Status 1
  # (Grund nur im Protokoll) - fertiger Server, kein Konto, keine Zugangsdatei.
  register_new_matrix_user -u "$VERWALTER" -p "$APW" -a -k "$GEHEIM" http://127.0.0.1:8008 >>"$LOG" 2>&1 \
    || ende "$(txt verwalter_fehlgeschlagen "$VERWALTER" "$DOMAIN" "$LOG")"
  melde "$(txt verwalter_angelegt "$VERWALTER" "$DOMAIN")"
  # Erstanmeldung: Das Startpasswort gilt nur in der Verwaltung, im Chat ist das
  # Konto gesperrt, bis dort ein eigenes Passwort steht. Erst den Eintrag, dann
  # die Sperre - umgekehrt bliebe bei einem Fehler ein Konto ohne Zugang.
  printf '%s\n' "$APW" | python3 /opt/einladung/einladung.py --erstanmeldung "@$VERWALTER:$DOMAIN" >/dev/null 2>>"$LOG" \
    && curl -fsS -o /dev/null -X PUT -H "Authorization: Bearer $(cat /etc/einladung/admin-token)" \
         -H 'Content-Type: application/json' -d '{"locked":true}' \
         "http://127.0.0.1:8008/_synapse/admin/v2/users/@$VERWALTER:$DOMAIN" >>"$LOG" 2>&1 \
    || ende "$(txt erstanmeldung_fehlgeschlagen "$LOG")"
  melde "$(txt erstanmeldung_eingerichtet)"
elif [ -n "$ALTES_PW" ] && [ "${ALTES_PW#(}" = "$ALTES_PW" ]; then
  APW="$ALTES_PW"
  melde "$(txt verwalter_pw_uebernommen)"
else
  # register_new_matrix_user taugt hier NICHT: es legt nur neue Konten an und
  # bricht bei vergebener Kennung ab. Der Weg für bestehende Konten steht
  # unten in der Zugangsdatei (Admin-Schnittstelle, wie die Verwaltung selbst).
  APW="$(txt pw_unbekannt)"
  melde "$(txt verwalter_pw_unbekannt)"
fi

# Pfad VOR der Zugangsdatei festlegen - sonst steht dort eine leere Zeile.
ANLEITUNG=/root/kundenanleitung-$DOMAIN.html

# Die Zugangsdatei je Sprache als eigene Vorlage: zu viele Platzhalter für
# printf, und sie soll am Stück lesbar bleiben. Zeilenaufbau identisch.
zugangsdatei_schreiben() {
if [ "$SPRACHE" = de ]; then
cat > "$ZUGANG" <<EOF
Messenger $DOMAIN — Zugangsdaten
Eingerichtet $(date '+%d.%m.%Y %H:%M')

ADRESSEN
  Chat im Browser   https://$CHAT_HOST
  Einladungen       https://$EINLADUNG_HOST
  Verwaltung        https://$VERWALTUNG_HOST   (gleiche Anmeldung, nur für Verwalter)
  Für die App       Server: $DOMAIN

DEIN KONTO
  Benutzer   $VERWALTER
  Kennung    @$VERWALTER:$DOMAIN
  Passwort   $APW

SICHERUNG
  Schlüssel liegt in /root/.sicherung_passwort
  OHNE IHN IST KEINE SICHERUNG WIEDERHERSTELLBAR. Weg damit ins Passwortdepot.

ERSTE SCHRITTE
  1. Erstanmeldung auf https://$VERWALTUNG_HOST mit diesem Passwort: eigenes
     Passwort festlegen, zweiten Faktor einrichten. Bis dahin ist das Konto im
     Chat gesperrt. Danach gilt dieses Passwort nicht mehr - Datei löschen.
  2. Auf https://$EINLADUNG_HOST anmelden und Einladungen verschicken.
  3. App: FluffyChat oder Element X, Server "$DOMAIN" eintragen.
  3b. Kundenanleitung liegt in $ANLEITUNG - im Browser öffnen, Strg+P, als PDF sichern.
  4. ZWEITEN FAKTOR EINRICHTEN: https://$VERWALTUNG_HOST -> "Zweiter Faktor".
     Diese Seite kann Konten löschen - mit nur einem Passwort davor ist das dünn.
     Notausgang, falls das Telefon weg ist (als root auf diesem Server):
       python3 /opt/einladung/einladung.py --zweitfaktor-aus $VERWALTER
  5. Sprache der Seiten: $SPRACHE - in der Verwaltung umschaltbar (Karte "Sprache / Language").

PASSWORT NEU SETZEN (als root auf diesem Server; geht so für jedes Konto)
  register_new_matrix_user taugt dafür NICHT - es legt nur neue Konten an.
  Dieser Aufruf meldet nur die Matrix-Geräte ab. Eine laufende Sitzung auf der
  Einladungs-/Verwaltungsseite endet damit NICHT - dafür dort "Passwort neu" benutzen.
  curl -s -X POST -H "Authorization: Bearer \$(cat /etc/einladung/admin-token)" \\
       -H 'Content-Type: application/json' \\
       -d '{"new_password":"NEUES-PASSWORT","logout_devices":true}' \\
       http://127.0.0.1:8008/_synapse/admin/v1/reset_password/@$VERWALTER:$DOMAIN
EOF
else
cat > "$ZUGANG" <<EOF
Messenger $DOMAIN — credentials
Set up $(date '+%Y-%m-%d %H:%M')

ADDRESSES
  Chat in browser   https://$CHAT_HOST
  Invitations       https://$EINLADUNG_HOST
  Admin page        https://$VERWALTUNG_HOST   (same login, admins only)
  For the app       Server: $DOMAIN

YOUR ACCOUNT
  Username   $VERWALTER
  User ID    @$VERWALTER:$DOMAIN
  Password   $APW

BACKUP
  The key is in /root/.sicherung_passwort
  WITHOUT IT NO BACKUP CAN BE RESTORED. Put it into your password manager now.

FIRST STEPS
  1. First sign-in at https://$VERWALTUNG_HOST with this password: choose your
     own password, set up the second factor. Until then the account is locked
     in the chat. Afterwards this password no longer works - delete this file.
  2. Log in at https://$EINLADUNG_HOST and send invitations.
  3. App: FluffyChat or Element X, enter server "$DOMAIN".
  3b. The customer guide is in $ANLEITUNG - open in a browser, Ctrl+P, save as PDF.
  4. SET UP THE SECOND FACTOR: https://$VERWALTUNG_HOST -> "Second factor".
     This page can delete accounts - a single password in front of that is not enough.
     Emergency exit if the phone is gone (as root on this server):
       python3 /opt/einladung/einladung.py --zweitfaktor-aus $VERWALTER
  5. Language of the pages: $SPRACHE - switchable in the admin page (card "Sprache / Language").

RESET PASSWORD (as root on this server; works like this for any account)
  register_new_matrix_user is NOT suitable - it only creates new accounts.
  This call only logs out the Matrix devices. A running session on the
  invitation/admin page does NOT end with it - use "New password" there for that.
  curl -s -X POST -H "Authorization: Bearer \$(cat /etc/einladung/admin-token)" \\
       -H 'Content-Type: application/json' \\
       -d '{"new_password":"NEW-PASSWORD","logout_devices":true}' \\
       http://127.0.0.1:8008/_synapse/admin/v1/reset_password/@$VERWALTER:$DOMAIN
EOF
fi
}
zugangsdatei_schreiben
chmod 600 "$ZUGANG"
melde "$(txt zugangsdaten_in "$ZUGANG")"

# Bildschirmfotos in der Sprache der Anleitung: dateien/bilder (de) bzw. dateien/bilder/en.
BILDER="$HIER/dateien/bilder"
[ "$SPRACHE" = en ] && [ -d "$BILDER/en" ] && BILDER="$BILDER/en"
if [ -f "$HIER/dateien/kundenanleitung.py" ]; then
  python3 "$HIER/dateien/kundenanleitung.py" \
    --domain "$DOMAIN" --verwalter "$VERWALTER" \
    --betreiber "${BETREIBER:-}" --bilder "$BILDER" --language "$SPRACHE" \
    --ausgabe "$ANLEITUNG" >/dev/null && chmod 600 "$ANLEITUNG"
  # Ohne Passwort, also darf die Verwaltung sie zum Herunterladen anbieten.
  mkdir -p /var/lib/messenger; chmod 755 /var/lib/messenger
  install -m 640 -o root -g einladung "$ANLEITUNG" /var/lib/messenger/kundenanleitung.html 2>/dev/null \
    || install -m 600 "$ANLEITUNG" /var/lib/messenger/kundenanleitung.html
  melde "$(txt kundenanleitung "$ANLEITUNG")"
fi

# Verwaltungsanleitung: alles über DIESEN Server für den Betreiber, ohne
# Passwort. Liegt bei root und, lesbar für den Dienst, unter /var/lib/messenger,
# wo die Verwaltung sie zum Herunterladen anbietet.
if [ -f "$HIER/dateien/verwaltungsanleitung.py" ]; then
  if [ "$HINTER_PROXY" = ja ]; then SITUATION=proxy
  elif [ "$HINTER_ROUTER" = ja ]; then SITUATION=zuhause
  else SITUATION=mietserver; fi
  VANLEITUNG=/root/verwaltungsanleitung-$DOMAIN.html
  python3 "$HIER/dateien/verwaltungsanleitung.py" \
    --domain "$DOMAIN" --verwalter "$VERWALTER" --situation "$SITUATION" \
    --lokale-ip "$LOKALE_IP" --oeffentliche-ip "$EIGENE_IP" --mail "${MAIL:-}" \
    --betreiber "${BETREIBER:-}" --sicherung-ziel "${SICHERUNG_ZIEL:-}" --language "$SPRACHE" \
    --ausgabe "$VANLEITUNG" >/dev/null && chmod 600 "$VANLEITUNG"
  mkdir -p /var/lib/messenger; chmod 755 /var/lib/messenger
  install -m 640 -o root -g einladung "$VANLEITUNG" /var/lib/messenger/verwaltungsanleitung.html 2>/dev/null \
    || install -m 600 "$VANLEITUNG" /var/lib/messenger/verwaltungsanleitung.html
  melde "$(txt verwaltungsanleitung "$VANLEITUNG")"
fi
gruen "$(txt zugang_steht)"

# NACHKONTROLLE
schritt "$(txt s_nachkontrolle)"
fehler=0
pruefe() {
  if eval "$2" >/dev/null 2>&1; then printf '   \033[32m%-5s\033[0m %s\n' "$(txt ok)" "$1"
  else printf '   \033[31m%-5s\033[0m %s\n' "$(txt fehlt)" "$1"; fehler=$((fehler+1)); fi
}

# Hinter einem Proxy sind die öffentlichen Namen von hier aus NICHT erreichbar -
# sie zeigen auf den Proxy, und der kennt diesen Server vielleicht noch gar nicht.
# Geprüft wird deshalb örtlich, mit gesetztem Host-Kopf: das prüft genau das,
# wofür dieser Server zuständig ist, und nichts, was ihm nicht gehört.
if [ "$HINTER_PROXY" = ja ]; then
  hole() { curl -sf --max-time 10 -H "Host: $1" "http://127.0.0.1$2"; }
  melde "$(txt geprueft_oertlich)"
else
  hole() { curl -sf --max-time 10 "https://$1$2"; }
fi

# Die Anmeldeseite wird am Formularziel erkannt, nicht am Knopftext - der ist
# je nach Sprache "Anmelden" oder "Log in".
pruefe "$(txt nk_synapse)"       'curl -sf --max-time 10 http://127.0.0.1:8008/health'
pruefe "$(txt nk_delegation)"    'hole "$DOMAIN" "/.well-known/matrix/server" | grep -q "$MATRIX_HOST"'
pruefe "$(txt nk_matrix)"        'hole "$MATRIX_HOST" "/_matrix/client/versions"'
pruefe "$(txt nk_servername "$DOMAIN")" 'hole "$MATRIX_HOST" "/_matrix/key/v2/server" | grep -q "\"server_name\":\"$DOMAIN\""'
pruefe "$(txt nk_element)"       'hole "$CHAT_HOST" "/" >/dev/null'
pruefe "$(txt nk_einladung)"     'hole "$EINLADUNG_HOST" "/" | grep -q "action=\"/anmelden\""'
pruefe "$(txt nk_verwaltung)"    'hole "$VERWALTUNG_HOST" "/" | grep -q "action=\"/anmelden\""'
pruefe "$(txt nk_coturn)"        'systemctl is-active --quiet coturn'
pruefe "$(txt nk_dienst)"        'systemctl is-active --quiet einladung'
pruefe "$(txt nk_firewall)"      'ufw status | grep -q "Status: active"'
# Nur mit auswärtigem Ziel: dann ist "Fertig" nur ehrlich, wenn es erreichbar ist.
[ -z "${SICHERUNG_ZIEL:-}" ] || pruefe "$(txt nk_sicherung)" 'sicherung_erreichbar'

echo
if [ "$fehler" -gt 0 ]; then
  rot "$(txt pruefungen_fehlgeschlagen "$fehler")"
  rot "$(txt zugangsdaten_liegen "$ZUGANG" "$LOG")"
else
  gruen "$(txt fertig)"
fi
echo
sag schluss_adressen "$CHAT_HOST" "$EINLADUNG_HOST" "$DOMAIN"
echo
if [ "$HINTER_PROXY" = ja ]; then
  sag proxy_hinweis "$LOKALE_IP" "$DOMAIN" "$MATRIX_HOST" "$CHAT_HOST" "$EINLADUNG_HOST" "$VERWALTUNG_HOST"
  echo
fi
grau "$(txt schluss_zugang "$ZUGANG")"
[ "$fehler" -gt 0 ] && exit 1
exit 0
