[English](SECURITY.md) | **Deutsch**

# Sicherheit

## Was dieses Projekt ist

Ein **gepflegtes Nebenprojekt**, kein Produkt mit Bereitschaftsdienst. Das steht hier, damit
du weißt, worauf du dich einlässt — bei Software, die Chats absichert, ist eine ehrliche
Erwartung mehr wert als ein großes Versprechen.

## Lücken melden

**Bitte nicht als öffentliches Ticket.** Stattdessen:

- **E-Mail:** `humatrix@gekons.de`
- oder über die private Meldefunktion von GitHub: „Report a vulnerability" im Reiter
  „Security" dieses Repositories. Fehlt der Knopf: bitte die E-Mail, kein Ticket.

Hilfreich ist: was betroffen ist, wie es sich nachstellen lässt, welche Fassung du benutzt.

## Unterstützte Fassungen

Nur der aktuelle Stand von `main` und das neueste Release. Es gibt keine Pflegezweige
für ältere Fassungen — stattdessen aktualisieren.

## Was du erwarten kannst

| | |
|---|---|
| Erste Rückmeldung | in der Regel **binnen 7 Tagen** |
| Einschätzung | binnen 14 Tagen |
| Behebung | je nach Schwere — kritische Fehler haben Vorrang vor allem anderen |
| Nennung | auf Wunsch, gern mit Namen |

Wenn ich im Urlaub bin, dauert es länger. Das ist die ehrliche Antwort.

## Was dieses Projekt nicht absichert

Der Installer richtet Matrix, Synapse, coturn und nginx ein. **Lücken in diesen Programmen
sind dort zu melden**, nicht hier:

- Synapse und Matrix: https://matrix.org/security-disclosure-policy/
- Debian: https://www.debian.org/security/

Hier gehören hin: Fehler im Installer, in der Einladungsseite, in den Voreinstellungen —
also alles, was dieses Projekt selbst entscheidet oder schreibt.

## Bekannte Grenzen

Kein Fehler, sondern Entwurf — damit niemand etwas annimmt, das nicht stimmt:

- **Nutzer haben kein 2FA.** Matrix kann das nicht; nur die Verwaltungsseite hat einen
  zweiten Faktor. Für 2FA bei allen bräuchte es einen Umbau auf den
  Matrix Authentication Service.
- **Metadaten sind sichtbar.** Wer wann mit wem schreibt, steht auf dem Server.
  Inhalte nicht. Siehe README.
- **Föderation ist offen voreingestellt.** Eure Leute erreichen das weltweite Netz; andere
  Server erfahren dadurch Raum-Metadaten. Abschalten: in
  `/etc/matrix-synapse/conf.d/50-eigene.yaml` die Zeile `federation_domain_whitelist: []`
  eintragen (leere Liste = kein fremder Server) und `systemctl restart matrix-synapse`.
  Diese Datei fasst der Installer nie an; `20-betrieb.yaml` dagegen schreibt er bei
  jedem Lauf neu, Änderungen dort gehen verloren (siehe docs/betreiber.md, „Betrieb").
- **Der Betreiber ist mächtig.** Er kann Konten anlegen, sperren und Passwörter setzen —
  und damit theoretisch ein Konto übernehmen. Lesen kann er alte Nachrichten trotzdem nicht,
  die Schlüssel liegen auf den Geräten. Die Geräteprüfung per Emoji würde so einen Versuch
  sichtbar machen.
- **Automatisch kommen nur die Debian-Sicherheitsupdates.** Synapse, Element Web und die
  übrigen Pakete werden täglich geprüft und in der Verwaltung angezeigt, eingespielt aber
  erst, wenn der Verwalter klickt (oder `messenger-update-ausfuehren.sh` aufruft). Klickt
  niemand, wird nichts aktualisiert — siehe docs/betreiber.md.
