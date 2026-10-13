[English](CONTRIBUTING.md) | **Deutsch**

# Mitmachen

Danke, dass du dir das ansiehst.

## Fehler melden

Ein Ticket mit: was du gemacht hast, was passiert ist, was du erwartet hast. Dazu
Debian-Fassung und der Teil aus `/var/log/messenger-installer.log`, der dazu passt.

**Sicherheitslücken nicht hier**, sondern wie in [SECURITY.de.md](SECURITY.de.md) beschrieben.

## Änderungen vorschlagen

Gern — kleine Schritte sind leichter zu prüfen als große. Vorher kurz ein Ticket aufmachen,
wenn es etwas Größeres ist; dann spart ihr euch Arbeit, die vielleicht nicht passt.

## Wie der Code hier aussieht

Damit Beiträge sich einfügen:

- **Deutsch im Code.** Kommentare und Bezeichner bleiben deutsch. Nutzertexte stehen in
  Sprachtabellen in beiden Sprachen (`TEXTE` in `einladung.py`, `T_de`/`T_en` in
  `install.sh`, `VORLAGEN` in `kundenanleitung.py`); ein neuer Text braucht einen
  Schlüssel in beiden, die Tests prüfen das. Dokumentation gibt es auf Englisch und
  Deutsch nebeneinander.
- **Keine Fremdpakete ohne guten Grund.** Die Einladungsseite kommt mit dem aus, was Python
  und Debian mitbringen. Jede Abhängigkeit ist etwas, das jemand pflegen muss.
- **Kommentare erklären das Warum, nicht das Was.** Dass eine Zeile ein Token erzeugt, sieht
  man. Warum ohne die Zeichen O, 0, I und l — das nicht.
- **Nichts selbst verschlüsseln.** Krypto kommt aus geprüften Bibliotheken.
- **Der Installer bleibt wiederholbar.** Zweimal laufen lassen darf nicht schaden. Das gilt
  auch für die Firewall: `ufw` wird nur zurückgesetzt, solange es noch nicht aktiv ist;
  danach setzt die Härtung nur ihre eigenen Regeln und lässt fremde stehen. Der SSH-Port
  kommt aus der laufenden `sshd`-Konfiguration, nicht fest aus dem Skript.
- **Prüfen vor Ändern.** Was nicht stimmt, bricht ab, bevor etwas angefasst wurde.

## Was nicht ins Projekt kommt

- Mehrmandanten-Betrieb (ein Server, eine Gemeinschaft)
- Eine eigene Chat-App (es gibt gute, und sie sollen nicht von uns sein)
- Eigene Kryptografie
- Funktionen, die nur mit zusätzlichen Diensten im Hintergrund laufen
