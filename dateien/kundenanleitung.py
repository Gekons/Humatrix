#!/usr/bin/env python3
"""Erzeugt die Kundenanleitung, befuellt mit den echten Zugangsdaten.

Ausgabe ist eine einzelne HTML-Datei ohne Fremdbestandteile - sie laesst sich
weitergeben, oeffnen und im Browser als PDF sichern. Bewusst kein PDF-Erzeuger
auf dem Server: der zieht rund hundert Pakete nach, die ein Messenger nicht braucht.

Aufruf (macht der Installer selbst):
  kundenanleitung.py --domain beispieldomain.de --verwalter anna --passwort '...' \
                     --betreiber 'Vorname Nachname, hilfe@example.de' --ausgabe /root/anleitung.html
"""
import argparse
import base64
import html
import os
import time

VORLAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Euer eigener Chat — {domain}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm 16mm; }}
  :root {{ --ink:#15201c; --leise:#5d6b65; --linie:#d8ded9; --eigen:#0e6f5a;
           --warn:#9c5a16; --flaeche:#f6f8f6; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; color:var(--ink); background:#fff;
          font:11.5pt/1.55 "Segoe UI", system-ui, -apple-system, sans-serif; }}
  .blatt {{ max-width: 185mm; margin: 0 auto; padding: 16px; }}
  h1 {{ font-size:25pt; line-height:1.1; margin:0 0 6px; letter-spacing:-.01em; }}
  h2 {{ font-size:14pt; margin:26px 0 8px; padding-bottom:5px;
        border-bottom:2px solid var(--eigen); color:var(--eigen); }}
  h3 {{ font-size:11.5pt; margin:16px 0 4px; }}
  p, li {{ margin:0 0 8px; }}
  ol.schritte {{ padding-left:0; list-style:none; counter-reset:s; }}
  ol.schritte > li {{ counter-increment:s; position:relative; padding-left:32px; margin-bottom:14px; }}
  ol.schritte > li::before {{ content:counter(s); position:absolute; left:0; top:1px;
        width:22px; height:22px; border-radius:50%; background:var(--eigen); color:#fff;
        font-size:10pt; font-weight:700; text-align:center; line-height:22px; }}
  .kasten {{ border:2px solid var(--eigen); border-radius:8px; padding:14px 16px; margin:14px 0;
        background:var(--flaeche); break-inside:avoid; }}
  .kasten.warn {{ border-color:var(--warn); }}
  .kasten h3 {{ margin-top:0; color:var(--eigen); }}
  .kasten.warn h3 {{ color:var(--warn); }}
  table.zugang {{ width:100%; border-collapse:collapse; margin-top:6px; }}
  table.zugang td {{ padding:7px 0; border-bottom:1px solid var(--linie); vertical-align:top; }}
  table.zugang td:first-child {{ width:38%; color:var(--leise); }}
  .wert {{ font-family:"Consolas", ui-monospace, monospace; font-size:11pt;
        font-weight:600; word-break:break-all; }}
  .leise {{ color:var(--leise); }}
  .klein {{ font-size:10pt; }}
  .deckel {{ border-bottom:3px solid var(--eigen); padding-bottom:14px; margin-bottom:8px; }}
  .fuss {{ margin-top:30px; padding-top:12px; border-top:1px solid var(--linie);
        font-size:9.5pt; color:var(--leise); }}
  .neu {{ break-before:page; }}
  figure {{ margin:14px 0 18px; break-inside:avoid; }}
  figure img {{ width:100%; max-width:120mm; display:block; margin:0 auto;
        border:1px solid var(--linie); border-radius:6px; }}
  figcaption {{ font-size:9.5pt; color:var(--leise); text-align:center; margin-top:6px; }}
  @media print {{ .blatt {{ padding:0; max-width:none; }} a {{ color:inherit; text-decoration:none; }} }}
</style></head>
<body><div class="blatt">

<div class="deckel">
  <h1>Euer eigener Chat</h1>
  <p class="leise">Verschlüsselt, auf eurem eigenen Server — <b>{domain}</b><br>
     Eingerichtet am {datum}{betreiber_zeile}</p>
</div>

<div class="kasten">
  <h3>Eure Zugangsdaten</h3>
  <table class="zugang">
    <tr><td>Chat im Browser</td><td class="wert">{chat}</td></tr>
    <tr><td>Leute einladen</td><td class="wert">{einladung}</td></tr>
    <tr><td>Verwaltung (nur für dich)</td><td class="wert">{verwaltung}</td></tr>
    <tr><td>Server für die App</td><td class="wert">{domain}</td></tr>
    <tr><td>Dein Benutzername</td><td class="wert">{verwalter}</td></tr>
    <tr><td>Deine Kennung</td><td class="wert">@{verwalter}:{domain}</td></tr>
    <tr><td>Erstes Passwort</td><td class="wert">{passwort}</td></tr>
  </table>
  <p class="klein leise" style="margin-top:10px;margin-bottom:0">
     Dieses Passwort ist nur für den Anfang. Ändere es bei der ersten Anmeldung.</p>
</div>

<h2>1. Das Wichtigste in drei Sätzen</h2>
<p>Ihr habt einen eigenen Chat-Server. Eure Nachrichten sind Ende-zu-Ende verschlüsselt —
   auf dem Server liegt nur Buchstabensalat. Wo die Grenzen liegen, steht in Abschnitt 7.</p>
<p>Niemand kann sich einfach so anmelden. Wer dabei sein soll, braucht eine <b>Einladung</b>,
   und die verschickst du (oder jemand, dem du es erlaubst).</p>
<p>Ihr seid mit dem weltweiten Matrix-Netz verbunden: Eure Leute können auch mit
   Menschen auf anderen Servern schreiben.</p>

<h2>2. Erste Anmeldung</h2>
<ol class="schritte">
  <li><b>Öffne {chat} im Browser.</b><br>
      <span class="leise">Benutzername und das Passwort aus dem Kasten oben. Der Browser
      ist für die Ersteinrichtung gut; für den Alltag lieber die App (Abschnitt 4).</span></li>
  <li><b>Ändere sofort dein Passwort.</b><br>
      <span class="leise">In den Einstellungen unter „Konto". Nimm etwas Langes —
      ein Satz ist besser als ein kurzes Kauderwelsch.</span></li>
  <li><b>Richte den zweiten Faktor ein.</b> Siehe Abschnitt 3 — bitte nicht überspringen.</li>
</ol>

<h2>3. Zweiter Faktor für dein Verwalter-Konto</h2>
<p>Dein Konto kann Leute anlegen, sperren und Passwörter ändern. Mit nur einem Passwort
   davor ist das zu wenig. Der zweite Faktor dauert zwei Minuten.</p>
<ol class="schritte">
  <li>Installiere eine Authenticator-App. Empfehlung: <b>Aegis</b> (Android) oder
      <b>Ente Auth</b> (beide Systeme). Die Passwort-App deines Telefons kann es meist auch.</li>
  <li>Öffne <span class="wert">{verwaltung}</span> und dort <b>„Zweiter Faktor"</b>.</li>
  <li>Scanne den angezeigten QR-Code mit der App.</li>
  <li>Tippe den sechsstelligen Code ein, den die App zeigt. Erst dann wird es scharf —
      du kannst dich beim Einrichten also nicht aussperren.</li>
</ol>
{bild_zweitfaktor}

<div class="kasten warn">
  <h3>Falls das Telefon verloren geht</h3>
  <p style="margin-bottom:0">Dann kommst du ohne Hilfe nicht mehr in die Verwaltung.
     Melde dich bei {betreiber_kurz} — der kann den zweiten Faktor auf dem Server zurücksetzen.</p>
</div>

<h2 class="neu">4. Die App auf dem Telefon</h2>
<p>Du brauchst keine App von uns — ihr nehmt eine fertige, die es in den Stores gibt.
   Das ist Absicht: So hat niemand die App selbst gebaut, der auch den Server betreibt.</p>
<ol class="schritte">
  <li><b>App installieren.</b> Empfehlung <b>FluffyChat</b> — sieht freundlich aus und ist
      am einfachsten. Wer lieber etwas Schnelleres mag, nimmt <b>Element X</b>
      (die Variante <i>mit</i> dem X).</li>
  <li><b>Server eintragen.</b> Die App fragt danach. Dort gehört hinein:
      <span class="wert">{domain}</span></li>
  <li><b>Anmelden</b> mit Benutzername und Passwort.</li>
  <li><b>Emoji vergleichen.</b> Beim ersten zweiten Gerät zeigen beide dieselben Bildchen.
      Stimmen sie überein, bestätigen. Das ist die Prüfung, dass niemand dazwischensitzt.</li>
</ol>

<h2>5. Leute einladen</h2>
<p>Unter <span class="wert">{einladung}</span> — dort melden sich <b>alle</b> eure Leute an,
   nicht nur du.</p>

{bild_anmelden}
<ol class="schritte">
  <li>Anmelden mit dem gewohnten Chat-Konto.</li>
  <li><b>Wählen, wohin die Person soll</b> — zum Beispiel „Familie" oder „Sportverein".
      Sie bekommt dann gleich eine Einladung in diesen Raum und steht nicht vor einer
      leeren App.</li>
  <li>Auf <b>„Einladung erzeugen"</b> drücken.</li>
  <li>Den Link verschicken — per WhatsApp, SMS, wie ihr mögt. Es gibt einen Knopf dafür.</li>
</ol>
{bild_nutzer}
{bild_erzeugt}

<p>Der Eingeladene öffnet den Link, installiert die App und gibt den Code ein. Fertig.
   Eine Einladung gilt <b>14 Tage</b> und funktioniert <b>genau einmal</b>.
   So sieht sie bei ihm aus:</p>

{bild_einladung}
<div class="kasten">
  <h3>Wer darf wie viele Leute einladen?</h3>
  <p>Das bestimmst du unter <span class="wert">{verwaltung}</span>. Je Person lässt sich eine
     freie Zahl eintragen — <b>0</b> heißt „darf niemanden einladen", und es gibt einen Haken
     für „unbegrenzt". Neue Konten bekommen eine Vorgabe, die du ebenfalls dort einstellst.</p>
  <p style="margin-bottom:0">Dort siehst du auch, <b>wer wen geholt hat</b>. Damit bleibt
     nachvollziehbar, über wen jemand dazugekommen ist.</p>
</div>

<h2 class="neu">6. Verwaltung — was du dort noch kannst</h2>

{bild_verwaltung}
<table class="zugang">
  <tr><td>Konto von Hand anlegen</td><td>Wenn jemand keinen Link öffnen kann. Das Passwort
      wird einmal angezeigt — gib es weiter und lass es ändern.</td></tr>
  <tr><td>Passwort zurücksetzen</td><td>Meldet alle Geräte dieses Kontos ab.</td></tr>
  <tr><td>Konto sperren</td><td>Es kann sich dann nicht mehr anmelden. Rückgängig machbar;
      beim Entsperren gibt es ein neues Passwort.</td></tr>
  <tr><td>Verwalter ernennen</td><td>Wer das bekommt, darf alles, was du darfst.</td></tr>
</table>
<p class="klein leise">Du kannst dich nicht selbst sperren und deine eigenen Rechte nicht
   entziehen — sonst wärst du aus deinem eigenen Server ausgesperrt.</p>

<h2>7. Was der Betreiber sehen kann — und was nicht</h2>
<p>Das soll offen auf dem Tisch liegen, damit ihr es euren Leuten weitersagen könnt.</p>
<table class="zugang">
  <tr><td><b>Eure Nachrichten</b></td><td><b>Nein.</b> Auf dem Server liegt nur Buchstabensalat.
      Die Schlüssel sind ausschließlich auf euren Geräten.</td></tr>
  <tr><td><b>Fotos und Dateien</b></td><td><b>Nein.</b> Ebenfalls verschlüsselt.</td></tr>
  <tr><td>Wer wann mit wem</td><td><b>Ja.</b> Dass eine Nachricht lief und zwischen wem,
      ist am Server sichtbar — der Inhalt nicht.</td></tr>
  <tr><td>Speicherplatz, Kontonamen</td><td><b>Ja.</b> Das gehört zum Betrieb.</td></tr>
  <tr><td>Verlauf wiederherstellen</td><td><b>Nein.</b> Ohne eure Schlüssel geht das nicht —
      auch nicht für uns.</td></tr>
  <tr><td>Passwort neu setzen</td><td><b>Ja.</b> Damit ließe sich ein Konto übernehmen und
      ab dann mitlesen — alte Nachrichten bleiben verschlossen, und alle Geräte des Kontos
      werden dabei abgemeldet, ihr merkt es also.</td></tr>
  <tr><td>Chat im Browser</td><td>Den Browser-Chat unter {chat} liefert der Betreiber
      selbst aus und könnte ihn verändern — dort schützt die Verschlüsselung nicht gegen
      ihn. Wer das ausschließen will, nimmt die App aus dem Store (Abschnitt 4).</td></tr>
</table>

<h2>8. Handy weg — was dann?</h2>
<p>Weil niemand außer euch die Schlüssel hat, kann auch niemand den Verlauf zurückholen.
   Deshalb zwei Vorkehrungen, die ihr <b>vorher</b> treffen solltet:</p>
<ol class="schritte">
  <li><b>Ein zweites Gerät anmelden</b> — Tablet oder Rechner. Das ist die beste Sicherung,
      weil sie ohne Zutun mitläuft. Geht das Telefon verloren, ist nichts weg.</li>
  <li><b>Die Schlüsselsicherung einschalten</b>, wenn die App danach fragt, und den
      Wiederherstellungsschlüssel gut aufheben. Nicht wegklicken — dieser eine Schritt
      entscheidet, ob der Verlauf einen Gerätewechsel übersteht.</li>
</ol>

<h2>9. Wenn etwas nicht geht</h2>
<table class="zugang">
  <tr><td>„Zu viele Versuche"</td><td>Der Server bremst nach ein paar Fehlversuchen.
      Eine Minute warten, dann geht es wieder. Das Passwort ist meist richtig.</td></tr>
  <tr><td>Code wird nicht angenommen</td><td>Stimmt die Uhrzeit auf dem Telefon?
      Automatische Zeit einschalten.</td></tr>
  <tr><td>Rotes Warnzeichen im Chat</td><td>Nicht raten — das bedeutet ein unbestätigtes
      Gerät. Beim Absender nachfragen oder {betreiber_kurz} fragen.</td></tr>
  <tr><td>Anruf kommt nicht zustande</td><td>Beide Seiten einmal aus dem WLAN ins Mobilfunknetz
      wechseln lassen. Hält es an, melden.</td></tr>
  <tr><td>Alles andere</td><td>{betreiber_kurz}</td></tr>
</table>

<div class="fuss">
  Euer eigener Chat auf {domain} · Anleitung erzeugt am {datum}<br>
  Bitte bewahrt diese Seiten auf — sie enthalten euer erstes Passwort.
</div>

</div></body></html>
"""


def main():
    a = argparse.ArgumentParser(description='Kundenanleitung erzeugen')
    a.add_argument('--domain', required=True)
    a.add_argument('--verwalter', required=True)
    a.add_argument('--passwort', required=True)
    a.add_argument('--betreiber', default='')
    a.add_argument('--bilder', default='', help='Verzeichnis mit den Bildschirmfotos')
    a.add_argument('--ausgabe', required=True)
    w = a.parse_args()

    e = html.escape
    betreiber = w.betreiber.strip()

    def bild(name, unterschrift):
        """Als data:-URI einbetten, damit die Anleitung EINE Datei bleibt."""
        if not w.bilder:
            return ''
        pfad = os.path.join(w.bilder, name)
        if not os.path.exists(pfad):
            return ''
        with open(pfad, 'rb') as d:
            roh = base64.b64encode(d.read()).decode()
        return (f'<figure><img src="data:image/png;base64,{roh}" alt="{e(unterschrift)}">'
                f'<figcaption>{e(unterschrift)}</figcaption></figure>')
    inhalt = VORLAGE.format(
        domain=e(w.domain),
        verwalter=e(w.verwalter),
        passwort=e(w.passwort),
        chat=f'https://chat.{e(w.domain)}',
        einladung=f'https://einladung.{e(w.domain)}',
        verwaltung=f'https://verwaltung.{e(w.domain)}',
        datum=time.strftime('%d.%m.%Y'),
        betreiber_zeile=f'<br>Betreut von {e(betreiber)}' if betreiber else '',
        betreiber_kurz=e(betreiber.split(',')[0]) if betreiber else 'deinem Betreuer',
        bild_anmelden=bild('01-anmelden.png',
                           'Die Einladungsseite — mit dem gewohnten Chat-Konto anmelden.'),
        bild_nutzer=bild('02-nutzer.png',
                         'Jeder sieht hier, wie viele Einladungen er noch hat.'),
        bild_erzeugt=bild('03-erzeugt.png',
                          'Nach dem Knopfdruck: der fertige Link zum Verschicken.'),
        bild_einladung=bild('04-einladung.png',
                            'Was der Eingeladene sieht — drei Schritte, mehr nicht.'),
        bild_verwaltung=bild('05-verwaltung.png',
                             'Die Verwaltung. Oben die Vorgabe, darunter alle Konten — '
                             'die beiden unteren sind hier zur Veranschaulichung gesperrt.'),
        bild_zweitfaktor=bild('06-zweitfaktor.png',
                              'Den QR-Code mit der Authenticator-App scannen.'),
    )
    with open(w.ausgabe, 'w', encoding='utf-8') as d:
        d.write(inhalt)
    print(w.ausgabe)


if __name__ == '__main__':
    main()
