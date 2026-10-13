# Testbericht

Stand: 13. Oktober 2026

Dieser Bericht hält fest, was tatsächlich geprüft wurde, mit welchem Ergebnis und
wo die Grenzen der Aussage liegen. Er ist bewusst auch dort genau, wo etwas
nicht funktioniert hat — ein Testbericht ohne Fehlerliste ist keiner.

## Was geprüft wurde

| Prüfung | Umfang | Ergebnis |
|---|---|---|
| Funktionstest | 18 Einzelprüfungen: Anmelden, Räume, Nachrichten, Medien, Mitglieder, Abmelden, Rechte | **18 von 18** |
| Lasttest | 50 Konten, 15 gleichzeitig schreibend, echte Ende-zu-Ende-Verschlüsselung | bestanden, Zahlen unten |
| Installationstest | Installationsskript auf frischem Debian 13, mehrfach wiederholt | **7 Durchläufe nötig**, 7 Fehler gefunden und behoben |

Alle Tests laufen gegen einen echten Server mit echter Verschlüsselung — es wird
nichts nachgestellt. Die Testclients sind `matrix-nio` mit `python3-olm`, also
dieselbe Olm/Megolm-Bibliothek, die auch die Telefon-Apps benutzen.

## Prüfumgebung

Ein Container auf einem Proxmox-Rechner, bewusst klein gehalten, damit die
Zahlen für einen gemieteten Server von acht bis zehn Euro im Monat gelten:

- 4 Kerne, 4 GB Arbeitsspeicher, 40 GB Platte
- Debian 13, Synapse 1.162 auf PostgreSQL (Sortierung C)
- Netz: eigenes, abgeschottetes Segment hinter der Firewall

## Funktionstest: 18 von 18

Geprüft wird das, was ein normaler Mensch im Chat tut — nicht, was sich leicht
prüfen lässt.

**Anmelden und Räume**
- Drei Konten angelegt und angemeldet
- Verschlüsselten Gruppenraum angelegt
- Zweiter Teilnehmer beigetreten

**Nachrichten**
- Textnachricht kommt entschlüsselt an
- Zurückgezogene Nachricht verschwindet (Server meldet sie als zurückgezogen)
- Antwort auf eine Nachricht
- Reaktion gesetzt

**Medien**
- Bild liegt auf dem Server nur als `m.room.encrypted` — der Inhalt ist dort
  nicht lesbar
- Bild heruntergeladen und entschlüsselt, Prüfsumme stimmt
- Datei hochgeladen und im Medienspeicher nachweisbar

**Mitglieder**
- Drittes Mitglied hinzugefügt
- Neues Mitglied liest neue Nachrichten
- Neues Mitglied liest **alte** Nachrichten nicht — so gewollt
- Entferntes Mitglied bekommt nichts mehr

**Abmelden und wiederkommen**
- Nach Neuanmeldung mit demselben Gerätespeicher wieder lesbar
- Neues Gerät ohne Schlüssel sieht den alten Verlauf **nicht** — so gewollt,
  und genau deshalb braucht es die Schlüsselsicherung

**Rechte**
- Raum umbenannt
- Ein normales Mitglied darf den Gründer nicht hinauswerfen

Die beiden „so gewollt"-Punkte sind die wichtigsten des ganzen Berichts: Sie
belegen, dass die Verschlüsselung wirklich greift. Wer neu dazukommt, kann das
Gesagte von vorher nicht lesen — auch der Serverbetreiber nicht.

## Lasttest: 50 Konten, 15 gleichzeitig

Alle 50 Konten melden sich mit einem echten Gerät an und laden ihre Schlüssel
hoch. Nur so entsteht die eigentliche Last von Matrix: Der Gruppenschlüssel muss
an **jedes** Gerät einzeln verteilt werden.

### Konten anlegen

| | |
|---|---|
| 50 Konten angelegt und angemeldet | 33 s |
| je Konto | 0,65 s |

### Ein verschlüsselter Raum mit 50 Mitgliedern

| | |
|---|---|
| Einladen und Beitreten, 49 Personen | 170 s |
| Mitglieder laut Datenbank | 50 |
| Raum verschlüsselt | ja |

Die 170 Sekunden sind überwiegend der Testaufbau, nicht der Server: Die
Testclients arbeiten die Beitritte nacheinander ab. Der Server war dabei
weitgehend unbeschäftigt.

### Nachrichten

| | |
|---|---|
| Erste Nachricht (Schlüssel an alle 50 Geräte verteilen) | 0,51 s |
| Zweite Nachricht (Schlüssel liegt schon) | 0,05 s |
| Stichprobe: entschlüsselt gelesen | 5 von 5 |

### 15 Leute schreiben gleichzeitig

| | |
|---|---|
| Nachrichten gesendet | **150 von 150** |
| Dauer | 7,8 s |
| Durchsatz | 19,1 Nachrichten/s |
| Antwortzeit Mitte (Median) | **108 ms** |
| Antwortzeit Mittelwert | 535 ms |
| 95 Prozent unter | 4 440 ms |
| langsamste | 4 527 ms |

Die Mitte liegt bei einer Zehntelsekunde. Der lange Schwanz von viereinhalb
Sekunden sind die jeweils **ersten** Nachrichten der Sender: Da wird der
Gruppenschlüssel an 50 Geräte ausgeliefert. Danach ist es schnell. Für
Alltagsgebrauch heißt das: Es fühlt sich flott an, der erste Satz nach langer
Pause kann einen Moment brauchen.

### Medien

| Größe | Dauer | Durchsatz |
|---|---|---|
| 50 KB | 0,01 s | 4,9 MB/s |
| 500 KB | 0,03 s | 18,4 MB/s |
| 2 MB | 0,08 s | 24,9 MB/s |
| 5 MB | 0,24 s | 21,0 MB/s |

### Räume und Abschottung

| | |
|---|---|
| 10 Räume angelegt | 2,2 s |
| Außenstehender sieht fremden Raum nicht | bestätigt |
| Mitglied sieht seinen Raum | bestätigt |

### Was es den Server gekostet hat

| | vorher | nach 50 Konten | nach der Last |
|---|---|---|---|
| Arbeitsspeicher gesamt | 293 MB | 313 MB | 366 MB |
| davon Synapse | 140 MB | 141 MB | 146 MB |
| Datenbank | 33 MB | 33 MB | 42 MB |
| Ereignisse | 570 | 570 | 828 |

Der Server hat 4 GB. Benutzt wurden unter dem Testlauf 366 MB — knapp ein
Zehntel. Synapse selbst wuchs um 6 MB. Für die anvisierte Größenordnung
(etwa 50 Personen, selten mehr als 15 gleichzeitig) ist ein kleiner gemieteter
Server reichlich dimensioniert.

## Installationstest: sieben Anläufe, sieben Fehler

Das Installationsskript wurde auf einem **frischen** Debian 13 ausgeführt, so wie
es ein Fremder benutzen würde. Es brauchte sieben Durchläufe, bis alle zehn
Schlussprüfungen bestanden. Jeder Fehlschlag war ein echter Fehler:

**1. Kein Weg für Server hinter einem vorhandenen Reverse-Proxy.**
Das Skript holte Zertifikate über Port 80 am Server selbst. Wer zu Hause schon
einen Proxy betreibt, kann das nicht. Behoben: neuer Schalter `--hinter-proxy`.
Dann liefert der Server Klartext auf Port 80, um TLS kümmert sich der Proxy.

**2. Die Namensprüfung brach bei getrenntem internen und externen DNS ab.**
Zu Hause zeigen die Namen innen auf den Proxy, außen auf den Anschluss. Das
Skript hielt das für einen Fehler. Behoben: im Proxy-Modus wird nur gemeldet.

**3. `external-ip` bei coturn fehlte ganz.**
Hinter einem Router nennt coturn den Gesprächspartnern sonst eine private
Adresse als Relais — Anrufe scheitern lautlos. Behoben: Das Skript erkennt den
Router selbst und trägt öffentliche und private Adresse ein.

**4. Ein zweiter Durchlauf brach ab.**
„Port 80 ist schon belegt" — von seinem eigenen Webserver aus dem ersten Lauf.
Das Skript versprach ausdrücklich, dass ein zweiter Lauf nichts kaputt macht.
Behoben.

**5. `umask 077` wirkte auf das ganze Skript.**
Eine enge Maske für eine Geheimnisdatei galt ab da für alles. Folge: Die
Delegation unter `/.well-known/matrix/server` und Elements `config.json` waren
für den Webserver nicht lesbar — „403 Forbidden", ohne dass irgendwo stand,
warum. Behoben: Jede enge Maske steht jetzt in einer eigenen Unterschale, und
die ausgelieferten Dateien bekommen ihre Rechte ausdrücklich gesetzt.

**6. Ein Wiederholungslauf vernichtete das Verwalterpasswort.**
Bestand das Konto schon, schrieb das Skript „(unverändert — stand schon in
dieser Datei)" — und überschrieb dabei genau die Datei, auf die es verwies.
Das Passwort war weg. Behoben: Die alte Datei wird vorher gesichert und das
Passwort daraus übernommen.

**7. Der Einladungsdienst sprach Synapse über die öffentliche Adresse an.**
Jeder Verwaltungsaufruf lief damit durch Router und Proxy zurück zum selben
Rechner — und stand still, solange der Proxy den Namen noch nicht kannte.
Behoben: Der Dienst spricht Synapse örtlich an.

Zwei weitere Kleinigkeiten wurden mitbehoben: Die Hilfe nannte einen Abschnitt
`verwaltung`, den es gar nicht gibt, und die Zugangsdatei verwies auf einen
Pfad, der erst danach gesetzt wurde — die Zeile blieb leer.

### Endstand

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

Danach wurde das Skript ein weiteres Mal mit `--nur turn` aufgerufen, um nach
einem Adresswechsel allein den Anruf-Teil nachzuziehen. Auch das lief durch.

## Prüflauf vor der Veröffentlichung: 66 Rohbefunde, 41 bestätigt, 41 behoben

Vor der Veröffentlichung wurde das gesamte Paket von sechs unabhängigen Prüfern
aus je einem Blickwinkel gelesen — Installer-Korrektheit, Sicherheit des Dienstes,
Korrektheit des Dienstes, Stimmigkeit der Doku, Datenschutz und Spuren, Recht und
Versprechen. Jeder der 66 Rohbefunde wurde danach von einem weiteren Prüfer mit
dem ausdrücklichen Auftrag gelesen, ihn zu **widerlegen**. 25 fielen dabei durch,
41 blieben bestehen. Alle 41 wurden behoben und die Behebung nachgeprüft; die
wichtigsten:

- **Sitzungen ließen sich nicht widerrufen.** Sperren, neues Passwort oder ein
  geänderter zweiter Faktor beendeten eine laufende Einladungs-Sitzung nicht —
  sie lief bis zu zwölf Stunden weiter. Jetzt trägt jede Sitzung ihren
  Ausgabezeitpunkt, und eine Sperre macht alles Ältere ungültig. Über HTTP
  nachgewiesen: Passwort über die Verwaltungsseite neu gesetzt, alte Sitzung sofort
  abgewiesen. (Der `curl`-Weg aus der Betreiberanleitung meldet nur Matrix-Geräte ab.)
- **„Konto von Hand anlegen" überschrieb ein bestehendes Konto** samt Passwort
  und Verwalterrecht, weil der Aufruf an Synapse anlegt *oder* ändert. Jetzt wird
  vorher geprüft; ein vergebener Name wird abgewiesen.
- **Die Einladungskette riet.** Zwei Einlösungen in derselben Minute oder ein
  zwischendurch von Hand angelegtes Konto landeten beim falschen Einladenden —
  und damit im falschen Raum. Jetzt wird nur bei Eindeutigkeit zugeordnet, sonst
  bleibt die Einladung offen und das Journal meldet es.
- **`ufw --force reset` mit festem Port 22** hätte jeden ausgesperrt, der SSH
  auf einen anderen Port gelegt hat, und löschte bei jedem Lauf fremde Regeln.
  Jetzt kommt der Port aus `sshd -T`, zurückgesetzt wird nur beim ersten Mal.
- **Der Dienst lauschte auf allen Schnittstellen** statt nur örtlich — für
  jeden Rechner im selben Netzsegment direkt erreichbar, am nginx vorbei.
- **Rund jede 17. Anmeldung scheiterte grundlos**, weil die Sitzungsunterschrift
  als Rohbytes hinter einem Trenner stand, den sie selbst enthalten konnte
  (gemessen: 5,8 % von 20 000). Jetzt Hex; 60 von 60 echte Anmeldungen.
- **Die Git-Historie enthielt die ungesäuberten Bilder** — mit TOTP-Schlüssel,
  echter Domain und gültigem Einladungscode. Deshalb ist die Historie dieses
  Repositories bewusst kurz: Sie beginnt mit dem bereinigten Stand.

Das Verfahren ist nicht unfehlbar — es findet, was man ihm an Blickwinkeln gibt.
Aber es hat in einem Durchgang mehr gefunden als zwei Tage Entwicklung und Test
davor. Wer weitere Blickwinkel beisteuern will: `CONTRIBUTING.md`.

## Was dieser Bericht NICHT belegt

Ehrlichkeit über die Grenzen gehört dazu:

- **Keine Dauerlast.** Gemessen wurde eine Spitze von 150 Nachrichten, nicht
  ein Betrieb über Wochen. Wie die Datenbank nach einem Jahr aussieht, sagt
  dieser Test nicht.
- **Keine echten Telefone.** Die Testclients benutzen dieselbe Verschlüsselung
  wie die Apps, sind aber keine Apps. Bedienfehler, Akkuverhalten und
  Benachrichtigungen bleiben außen vor.
- **Keine Sprach- und Videoanrufe über TURN geprüft.** coturn läuft und ist
  richtig eingestellt; ein echtes Gespräch zwischen zwei Netzen wurde nicht
  gemessen.
- **Keine Föderation geprüft.** Der Server ist auf Austausch mit anderen
  Matrix-Servern eingestellt, getestet wurde nur der eigene.
- **Nur eine Plattform geprüft.** Getestet wurde Debian 13 auf `amd64`. Debian 12
  ist vom Installer zugelassen, aber nicht durchgetestet. Ubuntu, ARM (Raspberry
  Pi) und Windows sind nicht Gegenstand dieses Berichts — für ARM gibt es von
  matrix.org gar kein Synapse-Paket, der Installer bricht dort begründet ab.
- **Kein Angriffstest.** Dies ist ein Funktions- und Lasttest, keine
  Sicherheitsprüfung. Wer Schwächen sucht: siehe `SECURITY.md`.
- **Die Prüfung „Mitglieder kommen und gehen"** aus dem Lasttest wurde vorzeitig
  abgebrochen (Bedienfehler beim Testlauf, nicht am Server). Derselbe Punkt ist
  im Funktionstest enthalten und dort bestanden.

## Wie man es nachvollzieht

Die beiden Testskripte liegen nicht im Paket, weil sie eine Testumgebung
voraussetzen und echte Konten anlegen. Wer sie haben möchte: Anfrage über die
Issues. Der Lasttest ist rund 360 Zeilen Python und braucht `matrix-nio`
mit `python3-olm`.
