[English](architecture.md) | **Deutsch**

# Architektur

Was nach `install.sh` auf dem Rechner läuft, und wie der Einladungsteil
funktioniert, ohne den Code zu lesen.

```
                 Internet
                    |
          nginx (TLS per certbot, oder reines HTTP hinter deinem Proxy)
   ---------------------------------------------------------------
   matrix.<d>   chat.<d>         invite.<d>        service.<d>
      |            |                 |                 |
   Synapse     Element Web      Einladungsdienst (ein Python-Prozess,
   :8008       statische Dateien 127.0.0.1:8082, Nutzer „einladung“)
      |                              |        |
  PostgreSQL                      SQLite   Synapse-Admin-API (localhost)
                                           - Registrierungs-Token
                                           - Nutzer, Passwörter, Deaktivieren
                                           - Raumeinladungen
   coturn :3478 (Anrufe)   Sicherungs-Timer   Update-Prüfung   ufw / fail2ban
```

## Der Einladungsteil

An Synapse wird nichts verändert. Der Einladungsdienst ist ein eigener Prozess, der
mit Synapse nur über die **Admin-API auf localhost** spricht, mit einem
Admin-Zugangstoken (`/etc/einladung/admin-token`, lesbar für root und den Dienstnutzer).

1. **Ein Nutzer erzeugt eine Einladung** auf `invite.<domain>` (Anmeldung = das
   normale Matrix-Login, gegen Synapse geprüft). Der Dienst lässt sich von Synapse ein
   **Registrierungs-Token** geben (`/_synapse/admin/v1/registration_tokens/new`) mit
   `uses_allowed: 1` und 14 Tagen Gültigkeit, speichert Code, Token, Einlader und
   optionalen Raum in seiner SQLite-Datenbank und gibt den Link
   `https://invite.<domain>/e/<code>` heraus.
2. **Der Eingeladene öffnet den Link.** Die Seite zeigt Servernamen und Token und
   verweist auf die Registrierungsseite von Element Web. Die Registrierung selbst ist
   reines Matrix: Synapses `registration_requires_token` ist an, das Token geht einmal.
3. **Wer hat wen geholt:** Matrix meldet nicht, welches Konto ein Token benutzt hat.
   Eine Hintergrundprüfung vergleicht den `completed`-Zähler des Tokens mit neuen
   Konten und deren Anlagezeit; zugeordnet wird nur, wenn genau ein Konto in Frage
   kommt, sonst bleibt es „eingelöst, Zuordnung unklar“. Zugeordnete Konten bekommen
   die Raumeinladung (Admin-API `join`), wenn die Einladung einen Raum trug.
4. **Kontingente** sind Zeilen in SQLite (je Nutzer, Vorgabe für Neue, `-1` =
   unbegrenzt), vom Dienst geprüft, bevor er Synapse um ein Token bittet.
5. **Zurückziehen / Sperren:** Zurückziehen löscht das Token bei Synapse und markiert
   die Zeile. Sperren ruft die Admin-API `deactivate` (ohne erase, also umkehrbar)
   und zieht vorher alle offenen Einladungen des Nutzers zurück.

## Die Verwaltung

Derselbe Prozess, ausgeliefert unter `service.<domain>`. Verwalter ist, wer auf dem
Matrix-Server Admin ist (`admin: true` in Synapse); es gibt keine zweite
Nutzerdatenbank. Verwalter bekommen einen optionalen zweiten Faktor (TOTP über
`pyotp`; das Geheimnis liegt verschlüsselt mit einem Schlüssel aus
`/etc/einladung/datenschluessel`). Sitzungen sind ein HMAC-signiertes Cookie; der
Schlüssel liegt in `/etc/einladung/sitzungsschluessel`, ihn zu erneuern beendet alle
Sitzungen.

## Updates aus der Verwaltung

Der Dienst darf nur eine leere Auftragsdatei unter `/var/lib/messenger/auftraege/`
anlegen. Eine systemd-Path-Unit als root bemerkt die Datei und startet ein festes
Skript (`messenger-update-ausfuehren.sh`: `apt-get upgrade`, Synapse-Neustart nur bei
neuem Paket, Element Web nachziehen). Das Ergebnis landet in
`/var/lib/messenger/updates.json` und wird auf der Seite angezeigt. Vom Webprozess
gelangen keine Parameter zu root; die Existenz der Datei ist die ganze Schnittstelle.

## Abhängigkeiten des Dienstes

Python-3-Standardbibliothek plus drei Debian-Pakete: `python3-pyotp`,
`python3-qrcode`, `python3-cryptography`. Kein pip, kein Framework, kein
JavaScript-Build.
