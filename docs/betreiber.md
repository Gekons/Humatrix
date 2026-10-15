[English](operator.md) | **Deutsch**

# Betreiber-Anleitung: einen Kundenserver aufsetzen

Von der Bestellung bis zur Übergabe. Rechne mit 30 Minuten, davon etwa 10 Wartezeit.

---

## Vorab: ein Server je Kunde

Dieser Installer baut einen eigenen Server pro Kunde. Jede Familie bekommt ihre eigene
Maschine, ihre eigene Domain und ihre eigenen Daten und kann jederzeit ausziehen.

Die Zahlen für einen eigenen Server:

| Posten | je Kunde |
|---|---|
| Hetzner Cloud CX32 (4 Kerne, 8 GB, 80 GB) | ~8 €/Monat |
| Domain | ~1 €/Monat |
| **Deine Kosten** | **~9 €/Monat** |
| Sinnvoller Preis | **15–19 €/Monat** |

Die Marge ist schmaler als bei einem geteilten Server, dafür bleibt die Haftung
getrennt und „ihr könnt jederzeit umziehen“ stimmt. Bei vielen kleinen Kunden lohnt ein
Mehrmandanten-Aufbau; der braucht einen anderen Installer.

Deine eigentliche Kostenposition ist die Supportzeit, nicht die Hardware.

---

## Schritt 1: Domain

Der Kunde entscheidet den Namen. Er wird Teil jeder Kennung (`@anna:familie-mueller.de`)
und lässt sich später nicht ändern, ohne alle Konten neu anzulegen. Das dem Kunden
vorher sagen.

Wer die Domain kauft, ist eine Vertrauensfrage. Mein Rat: der Kunde kauft sie selbst.
Dann gehört sie ihm, und er kann wirklich jederzeit umziehen. Du brauchst nur Zugriff
auf die DNS-Einträge.

## Schritt 2: Server bestellen

Bei Hetzner Cloud:

| | |
|---|---|
| Standort | Falkenstein oder Nürnberg |
| Abbild | **Debian 13** |
| Typ | **CX32** (4 Kerne, 8 GB, 80 GB) |
| SSH-Schlüssel | deinen hinterlegen; kein Passwort-Login |
| Netz | IPv4 + IPv6 |

Kleiner als 8 GB nicht. Synapse wächst mit den Räumen, und Nachrüsten im Betrieb ist
mehr Arbeit als einmal richtig bestellen.

## Schritt 3: DNS setzen

Sechs A-Einträge auf die IP des neuen Servers:

```
<domain>              A   <IP>
matrix.<domain>       A   <IP>
chat.<domain>         A   <IP>
invite.<domain>       A   <IP>
service.<domain>      A   <IP>
turn.<domain>         A   <IP>
```

Dann warten, bis sie sichtbar sind, meist ein bis zehn Minuten. Prüfen mit:

```bash
for n in "" matrix. chat. invite. service. turn.; do
  echo "$n<domain> -> $(dig +short @1.1.1.1 $n<domain> A)"
done
```

Der Installer prüft das ebenfalls und bricht ab, wenn etwas fehlt.

## Schritt 4: Installer hochladen und starten

Zwei Wege. Der kurze ist die Einrichtungsseite: auf dem Server

```bash
curl -fsSLo setup.sh https://raw.githubusercontent.com/Gekons/Humatrix/main/setup.sh
sudo bash setup.sh
```

ausführen und die ausgegebene Adresse mit dem Einmalcode im Browser öffnen. Die Seite
fragt Domain, Proxy oder nicht, E-Mail, den Benutzernamen des Kunden und deinen Namen als
Betreiber ab, prüft die DNS-Namen, lässt den Installer laufen und zeigt das Ergebnis. Ein
Feld für `--sicherung` hat sie nicht; für eine entfernte Sicherung danach aus
`/opt/humatrix` heraus `./install.sh … --nur sicherung` mit den Schaltern unten
aufrufen. Die README beschreibt die Seite.

Der lange Weg, mit allen Schaltern in einer Zeile:

Auf einem frischen Server fehlt `git`, also auf deinem Rechner klonen und den Ordner
hinüberkopieren:

```bash
git clone https://github.com/Gekons/Humatrix.git
scp -r Humatrix root@<IP>:/root/
ssh root@<IP>
cd /root/Humatrix
./install.sh --domain <domain> --mail <deine-mail> --verwalter <kundenname> \
             --betreiber "<dein Name>, <deine-mail>" \
             --sicherung u123456@u123456.your-storagebox.de:/messenger/<domain>
```

- `--verwalter` ist der Benutzername des Kunden, klein und ohne Leerzeichen.
- `--mail` ist deine Adresse. Dorthin warnt Let's Encrypt, wenn ein Zertifikat nicht
  erneuert wurde, und das willst du wissen, nicht der Kunde.
- `--betreiber` ist optional, im Kundenbetrieb aber sinnvoll: Name und Erreichbarkeit
  stehen dann in der Kundenanleitung („Betreut von …“, „Melde dich bei …“) und in den
  Hinweisen des Einladungsdienstes. Ohne ihn steht dort „dein Betreuer“ bzw. „der
  Betreiber“.
- `--language en|de` ist die Sprache der Installer-Ausgabe, der Webseiten (Einladungs-
  und Verwaltungsseite) und der Kundenanleitung. Vorgabe `en`; für einen deutschen
  Kunden `--language de` angeben. Ein Wiederholungslauf behält die Sprache der
  vorhandenen Installation, solange `--language` nicht erneut angegeben wird. Der Kunde
  kann die Webseiten später in der Verwaltung umschalten (Karte „Sprache / Language“).
  Jede Einladung trägt außerdem ihre eigene Sprache, gewählt beim Erzeugen; eine ohne
  folgt dem Browser dessen, der sie öffnet. Schalter, Abschnittsnamen und
  Hostnamen-Präfixe sind in beiden Sprachen gleich.
- `--hinter-proxy`, wenn ein vorgelagerter Reverse-Proxy die Ports 80 und 443 schon
  belegt. Was sich dann ändert, steht im README.
- `--sicherung` ist optional. Ohne sie sichert der Server lokal nach
  `/var/backups/messenger` (14 Tage), besser als nichts, aber keine echte Sicherung.
  Mit ihr muss der SSH-Schlüssel des Servers beim Ziel hinterlegt sein: Der Installer
  erzeugt bei Bedarf `/root/.ssh/id_ed25519`, nimmt den Hostschlüssel des Ziels auf und
  prüft den Zugang mit einer Probe-Übertragung per `rsync` (sie legt auch das
  Zielverzeichnis an). Klappt das nicht, startet er den Sicherungs-Timer nicht, zeigt
  den öffentlichen Schlüssel an, und du trägst ihn beim Ziel ein. Bei der Hetzner
  Storage Box geht das in der Hetzner-Konsole unter der Box; sie lauscht auf Port 23,
  das gehört dann in `/root/.ssh/config`, der Installer sagt es dir. Danach
  `./install.sh … --nur sicherung`. Beim ersten Server für ein neues Ziel ist dieser
  zweite Anlauf normal.

Vor der ersten Änderung prüft der Installer System, Speicher, Platz, alle sechs Namen
und die Ports. Stimmt etwas nicht, bricht er ab, ohne etwas angefasst zu haben (einzige
Ausnahme: ein fehlendes `curl` wird vorher installiert, die Prüfung braucht es).

Am Ende laufen zehn Nachkontrollen, mit `--sicherung` elf; die letzte prüft, ob das
Sicherungsziel erreichbar ist. Sind alle grün, ist der Server fertig.

## Schritt 5: Was dabei herauskommt

| Datei auf dem Server | Inhalt |
|---|---|
| `/root/zugangsdaten-messenger.txt` | alle Zugänge und der Verweis auf den Sicherungsschlüssel; für dich |
| `/root/.sicherung_passwort` | Schlüssel der verschlüsselten Sicherung; ins Passwortdepot |
| `/root/kundenanleitung-<domain>.html` | fertig befüllte Anleitung; für den Kunden |
| `/var/log/messenger-installer.log` | Protokoll, falls etwas schiefging |
| `/var/lib/messenger/updates.json` | Befund der täglichen Update-Prüfung (zeigt die Verwaltung an) |
| `/var/log/messenger-update.log` | Protokoll jedes eingespielten Updates |

## Schritt 6: Übergabe

Die Anleitung holen:

```bash
scp root@<IP>:/root/kundenanleitung-<domain>.html .
```

Im Browser öffnen, Strg+P, „Als PDF sichern“. Auf dem Server gibt es absichtlich
keinen PDF-Erzeuger; der zöge rund hundert Pakete nach, die ein Messenger nicht braucht.

Dann getrennt verschicken:

| Was | Wie |
|---|---|
| Die Anleitung (PDF) | Mail ist in Ordnung |
| Das erste Passwort | nicht in derselben Mail. Telefon, Signal, persönlich. |

Das Passwort steht in der Anleitung. Also entweder die Anleitung sicher schicken (nicht
per einfacher Mail) oder die Passwortzeile vor dem Versand löschen und das Passwort am
Telefon sagen.

Sicherungsschlüssel sichern: `cat /root/.sicherung_passwort` ist der Schlüssel der
Sicherung (die Zugangsdatei verweist nur darauf). Ohne ihn ist keine Sicherung
wiederherstellbar. Ins Passwortdepot, bevor du den Server das erste Mal vergisst.

## Schritt 7: Erstes Gespräch mit dem Kunden

Zeig ihm drei Dinge:

1. Anmelden und Passwort ändern.
2. Zweiten Faktor einrichten. Sein Konto kann Leute löschen.
3. Eine Einladung verschicken, am besten an dich, damit er sieht, wie sie ankommt.

Sag ihm außerdem, was du sehen kannst: dass jemand geschrieben hat, wann und mit wem,
aber nicht was. Steht so auch in der Anleitung.

---

## Betrieb

| Was | Wie oft | Wie |
|---|---|---|
| Sicherheitsupdates | automatisch | nur Debian-Security |
| Alles andere: Systempakete, Synapse, Element Web | täglich geprüft, eingespielt auf Klick | Ein Timer (`messenger-update-pruefen.timer`, ab 06:00) prüft und legt den Befund in `/var/lib/messenger/updates.json` ab; die Verwaltung zeigt ihn oben in der Karte „Aktualisierungen“. Klickt der Kunde dort „Jetzt aktualisieren“ und bestätigt, legt der Dienst eine Auftragsdatei unter `/var/lib/messenger/auftraege/` ab; `messenger-update.path` sieht sie und startet den Lauf als root (apt-get upgrade, Synapse-Neustart nur bei neuem Paket, Element Web). Das Ergebnis steht danach in der Verwaltung und in `/var/log/messenger-update.log`. |
| Dasselbe von Hand | bei Bedarf | als root `messenger-update-ausfuehren.sh`: gleicher Lauf, gleiches Protokoll. `messenger-update-pruefen.sh` erneuert nur den Befund. |
| Synapse allein | bei Bedarf | `apt update && apt install matrix-synapse-py3`, danach `systemctl status matrix-synapse` |
| Element Web allein | z. B. monatlich | `./install.sh --domain … --mail … --nur element` (ruft `messenger-element-erneuern.sh` auf); verglichen wird mit `/var/www/element/.version`, nachgezogen nur bei neuerer Fassung. Der Browser-Client bekommt keine Debian-Sicherheitsupdates. |
| Sicherung prüfen | monatlich | `systemctl list-timers messenger-sicherung*` und nachsehen, ob Dateien ankommen |
| Wiederherstellung üben | jährlich | Eine Sicherung, die nie zurückgespielt wurde, ist keine. |
| Zertifikate | automatisch | certbot erneuert; coturn bekommt seine Kopien über einen Haken |
| Platz | bei Bedarf | Medien wachsen am schnellsten. Eigene Fotos und Videos werden nie geräumt, nur der Zwischenspeicher fremder Server (90 Tage). `du -sh /var/lib/matrix-synapse/media` |

Eigene Synapse-Einstellungen (Föderation aus, serverweite Suche, …) gehören nach
`/etc/matrix-synapse/conf.d/50-eigene.yaml`. Diese Datei fasst der Installer nie an.
`20-betrieb.yaml` schreibt er bei jedem Lauf neu; Änderungen dort sind nach dem
nächsten Lauf weg. Synapse liest alle `conf.d/*.yaml` in Namensreihenfolge; ein
oberster Schlüssel aus einer späteren Datei ersetzt den aus der früheren vollständig,
also immer den ganzen Block übernehmen (z. B. `user_directory:` mit allen drei Zeilen).
Die Datei wie die anderen anlegen (`chown root:matrix-synapse`, `chmod 640`), danach
`systemctl restart matrix-synapse`.

## Wenn etwas klemmt

| Symptom | Ursache | Abhilfe |
|---|---|---|
| Installer bricht bei DNS ab | Einträge noch nicht sichtbar | warten, erneut starten; er ist wiederholbar |
| Zertifikat schlägt fehl | Port 80 belegt oder DNS falsch | `ss -lnt`, DNS prüfen, dann `--nur web` |
| Anrufe kommen nicht zustande | UDP blockiert | `ufw status` prüfen; bei Hetzner auch die Cloud-Firewall |
| „Zu viele Versuche“ beim Anmelden | Synapse drosselt nach wenigen Versuchen | eine Minute warten. Das Passwort ist meist richtig. |
| Kunde hat sein Telefon verloren (2FA) | | `python3 /opt/einladung/einladung.py --zweitfaktor-aus <name>` als root auf dem Server; der Benutzername reicht, die volle Kennung geht auch |
| Kunde hat sein Passwort vergessen | | über die Verwaltung zurücksetzen („Passwort neu“, mit Rückfrage; das neue Passwort erscheint genau einmal, sofort notieren und weitergeben). Betrifft es das Verwalterkonto selbst, als root über die Admin-Schnittstelle, siehe unten. `register_new_matrix_user` taugt dafür nicht; es legt nur neue Konten an und bricht bei vergebener Kennung ab. |
| Einzelnen Abschnitt wiederholen | | `./install.sh --domain … --mail … --nur turn` |
| Verwaltung zeigt „Noch nicht geprüft“ | Timer läuft nicht oder die Prüfung scheitert | `systemctl status messenger-update-pruefen.timer`, dann `messenger-update-pruefen.sh` von Hand; der Fehler steht auf dem Bildschirm. Bestehender Server ohne diese Karte: den Installer komplett erneut laufen lassen (er ist wiederholbar), so bekommt auch der Einladungsdienst die neue Fassung. |
| „Jetzt aktualisieren“ tut nichts | Path-Unit nicht aktiv oder der Dienst darf nicht in den Auftragsordner | `systemctl status messenger-update.path`, `ls -ld /var/lib/messenger/auftraege` (gehört `einladung`, 750), `journalctl -u messenger-update -n 50` |
| Eigene Firewall-Regeln (WireGuard, eingeschränktes SSH) | | bleiben bei Wiederholungsläufen erhalten: `ufw` wird nur zurückgesetzt, solange es noch nicht aktiv ist; danach setzt die Härtung nur ihre eigenen Regeln und nimmt den SSH-Port aus der laufenden `sshd`-Konfiguration |

**Passwort als root neu setzen**, für jedes Konto, auch das Verwalterkonto. Der bessere
Weg ist die Verwaltungsseite („Passwort neu“): Sie setzt das Passwort, meldet die
Matrix-Geräte ab und beendet eine laufende Sitzung des Kontos auf
`invite.`/`service.<domain>`. Der Aufruf unten (er steht auch in
`/root/zugangsdaten-messenger.txt` unter „PASSWORT NEU SETZEN“) ist der Notausgang,
wenn niemand mehr in die Verwaltung kommt. Er trifft nur Matrix: Eine Sitzung auf der
Einladungs- oder Verwaltungsseite läuft danach bis zu zwölf Stunden weiter.

```bash
curl -s -X POST -H "Authorization: Bearer $(cat /etc/einladung/admin-token)" \
     -H 'Content-Type: application/json' \
     -d '{"new_password":"NEUES-PASSWORT","logout_devices":true}' \
     http://127.0.0.1:8008/_synapse/admin/v1/reset_password/@<name>:<domain>
```

Ist das Verwalterkonto womöglich übernommen, danach alle Einladungs- und
Verwaltungssitzungen aller Konten beenden. Der Dienst liest den Schlüssel bei jeder
Prüfung neu, ein Neustart ist nicht nötig; die Rechte der Datei bleiben beim
Überschreiben erhalten:

```bash
openssl rand -base64 48 > /etc/einladung/sitzungsschluessel
```

## Was dieser Installer nicht macht

- **Keine Überwachung.** Du erfährst nicht von allein, wenn ein Kundenserver steht.
  Für mehr als zwei, drei Kunden brauchst du eine Erreichbarkeitsprüfung.
- **Keine automatischen Updates für Synapse und Element Web.** Sie werden täglich
  geprüft und auf Klick in der Verwaltung eingespielt, oder von dir per Skript (siehe
  Betrieb).
- **Kein Mehrmandanten-Betrieb.** Ein Server, ein Kunde.
