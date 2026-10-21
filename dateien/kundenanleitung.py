#!/usr/bin/env python3
"""Erzeugt die Kundenanleitung, befuellt mit den echten Zugangsdaten.

Ausgabe ist eine einzelne HTML-Datei ohne Fremdbestandteile - sie laesst sich
weitergeben, oeffnen und im Browser als PDF sichern. Bewusst kein PDF-Erzeuger
auf dem Server: der zieht rund hundert Pakete nach, die ein Messenger nicht braucht.

Zwei Sprachen (--language en|de, Vorgabe en). Die Bildschirmfotos sind deutsch;
die englische Fassung sagt das dazu.

Aufruf (macht der Installer selbst):
  kundenanleitung.py --domain beispieldomain.de --verwalter anna \
                     --betreiber 'Vorname Nachname, hilfe@example.de' --language de \
                     --ausgabe /root/anleitung.html
"""
import argparse
import base64
import html
import os
import time

# Tab-Symbol: weiße Sprechblase auf der Akzentfarbe, als data:-URI, damit die
# Seite keine zweite Datei ausliefern muss.
ICON = ("data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        "%3Crect width='32' height='32' rx='7' fill='%2345c3a3'/%3E"
        "%3Cpath d='M9 9h14a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-8l-5 4v-4H9a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3z' fill='%23fff'/%3E"
        "%3C/svg%3E")

STIL = """
  @page { size: A4; margin: 18mm 16mm 16mm; }
  :root { --ink:#15201c; --leise:#5d6b65; --linie:#d8ded9; --eigen:#0e6f5a;
           --warn:#9c5a16; --flaeche:#f6f8f6; }
  * { box-sizing: border-box; }
  body { margin:0; color:var(--ink); background:#fff;
          font:11.5pt/1.55 "Segoe UI", system-ui, -apple-system, sans-serif; }
  .blatt { max-width: 185mm; margin: 0 auto; padding: 16px; }
  h1 { font-size:25pt; line-height:1.1; margin:0 0 6px; letter-spacing:-.01em; }
  h2 { font-size:14pt; margin:26px 0 8px; padding-bottom:5px;
        border-bottom:2px solid var(--eigen); color:var(--eigen); }
  h3 { font-size:11.5pt; margin:16px 0 4px; }
  p, li { margin:0 0 8px; }
  ol.schritte { padding-left:0; list-style:none; counter-reset:s; }
  ol.schritte > li { counter-increment:s; position:relative; padding-left:32px; margin-bottom:14px; }
  ol.schritte > li::before { content:counter(s); position:absolute; left:0; top:1px;
        width:22px; height:22px; border-radius:50%; background:var(--eigen); color:#fff;
        font-size:10pt; font-weight:700; text-align:center; line-height:22px; }
  .kasten { border:2px solid var(--eigen); border-radius:8px; padding:14px 16px; margin:14px 0;
        background:var(--flaeche); break-inside:avoid; }
  .kasten.warn { border-color:var(--warn); }
  .kasten h3 { margin-top:0; color:var(--eigen); }
  .kasten.warn h3 { color:var(--warn); }
  table.zugang { width:100%; border-collapse:collapse; margin-top:6px; }
  table.zugang td { padding:7px 0; border-bottom:1px solid var(--linie); vertical-align:top; }
  table.zugang td:first-child { width:38%; color:var(--leise); }
  .wert { font-family:"Consolas", ui-monospace, monospace; font-size:11pt;
        font-weight:600; word-break:break-all; }
  .leise { color:var(--leise); }
  .klein { font-size:10pt; }
  .deckel { border-bottom:3px solid var(--eigen); padding-bottom:14px; margin-bottom:8px; }
  .fuss { margin-top:30px; padding-top:12px; border-top:1px solid var(--linie);
        font-size:9.5pt; color:var(--leise); }
  .neu { break-before:page; }
  figure { margin:14px 0 18px; break-inside:avoid; }
  figure img { width:100%; max-width:120mm; display:block; margin:0 auto;
        border:1px solid var(--linie); border-radius:6px; }
  figcaption { font-size:9.5pt; color:var(--leise); text-align:center; margin-top:6px; }
  .druckleiste { display:flex; justify-content:flex-end; margin-bottom:10px; }
  .druckleiste button { font:inherit; font-size:10pt; padding:6px 14px; border-radius:6px;
        border:1px solid var(--eigen); background:#fff; color:var(--eigen); cursor:pointer; }
  @page { @bottom-center { content: counter(page) " / " counter(pages); font: 9pt system-ui, sans-serif; color: #5d6b65; } }
  @media print {
    .blatt { padding:0; max-width:none; }
    a { color:inherit; text-decoration:none; }
    .nur-bildschirm { display:none; }
    h2, h3, h2 + p, h3 + p { break-after:avoid; }
    .kapitelanfang, table.ganz { break-inside:avoid; }
    li, tr, .kasten, figure { break-inside:avoid; }
    p { orphans:3; widows:3; }
    figure img { width:auto; max-width:110mm; max-height:92mm; }
  }
"""

# Die Vorlagen je Sprache. Platzhalter in geschweiften Klammern; der Stil kommt
# als Wert hinein, damit seine Klammern nicht verdoppelt werden muessen.
VORLAGEN = {}

VORLAGEN['de'] = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<link rel="icon" href="{icon}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Euer eigener Chat — {domain}</title>
<style>{stil}</style></head>
<body><div class="blatt">
<div class="druckleiste nur-bildschirm"><button type="button" onclick="print()">Drucken / als PDF speichern</button></div>

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
    <tr><td>Erstes Passwort</td><td>bekommst du getrennt, nicht mit dieser Anleitung</td></tr>
  </table>
  <p class="klein leise" style="margin-top:10px;margin-bottom:0">
     Bei der ersten Anmeldung in der Verwaltung legst du ein eigenes Passwort fest.
     Bis dahin ist dein Konto im Chat gesperrt.</p>
</div>
{bild_hinweis}
<h2>1. Das Wichtigste in drei Sätzen</h2>
<p>Ihr habt einen eigenen Chat-Server. Eure Nachrichten sind Ende-zu-Ende verschlüsselt —
   auf dem Server liegt nur Buchstabensalat. Wo die Grenzen liegen, steht in Abschnitt 7.</p>
<p>Niemand kann sich einfach so anmelden. Wer dabei sein soll, braucht eine <b>Einladung</b>,
   und die verschickst du (oder jemand, dem du es erlaubst).</p>
<p>Ihr seid mit dem weltweiten Matrix-Netz verbunden: Eure Leute können auch mit
   Menschen auf anderen Servern schreiben.</p>

<h2>2. Erste Anmeldung</h2>
<ol class="schritte">
  <li><b>Öffne {verwaltung} im Browser</b> und melde dich mit deinem Benutzernamen und
      dem ersten Passwort an.</li>
  <li><b>Lege ein eigenes Passwort fest.</b><br>
      <span class="leise">Die Seite verlangt es, bevor sie etwas anderes zeigt. Nimm etwas
      Langes — ein Satz ist besser als ein kurzes Kauderwelsch.</span></li>
  <li><b>Richte den zweiten Faktor ein.</b> Die Seite bietet ihn direkt danach an,
      siehe Abschnitt 3 — bitte nicht überspringen.</li>
  <li><b>Öffne {chat}</b> und melde dich mit deinem neuen Passwort an.<br>
      <span class="leise">Der Browser ist für die Ersteinrichtung gut; für den Alltag
      lieber die App (Abschnitt 4).</span></li>
</ol>

<h2>3. Zweiter Faktor für dein Verwalter-Konto</h2>
<p>Dein Konto kann Leute anlegen, sperren und Passwörter ändern. Mit nur einem Passwort
   davor ist das zu wenig. Der zweite Faktor dauert zwei Minuten.</p>
<ol class="schritte">
  <li>Installiere eine Authenticator-App. Empfehlung: <b>Aegis</b> (Android) oder
      <b>Ente Auth</b> (beide Systeme). Die Passwort-App deines Telefons kann es meist auch.</li>
  <li>Die Verwaltung bietet ihn bei der ersten Anmeldung an. Später findest du ihn unter
      <span class="wert">{verwaltung}</span> → <b>„Zweiter Faktor"</b>.</li>
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
  <tr><td>Sprache umstellen</td><td>Deutsch oder Englisch — gilt sofort für alle Seiten.
      Jede Einladung bekommt außerdem ihre eigene Sprache.</td></tr>
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
  Diese Anleitung enthält keine Passwörter.
</div>

</div><script>
// Kapitelanfang zusammenhalten: Überschrift mit den nächsten beiden Abschnitten.
// Passt das nicht mehr auf die Seite, beginnt das Kapitel auf der nächsten.
document.querySelectorAll('.blatt h2, .blatt h3').forEach(function (h) {{
  if (h.parentNode.classList.contains('kapitelanfang')) return;
  var anfang = document.createElement('div'), n = h.tagName === 'H2' ? 2 : 1;
  anfang.className = 'kapitelanfang';
  h.parentNode.insertBefore(anfang, h);
  anfang.appendChild(h);
  // Folgt direkt eine Unterüberschrift, gehört sie samt ihrem ersten Abschnitt dazu.
  if (n === 2 && anfang.nextElementSibling && anfang.nextElementSibling.tagName === 'H3') n = 3;
  while (n-- > 0 && anfang.nextElementSibling && !/^H2$/.test(anfang.nextElementSibling.tagName))
    anfang.appendChild(anfang.nextElementSibling);
}});
// Kurze Tabellen nicht teilen.
document.querySelectorAll('.blatt table').forEach(function (t) {{
  if (t.rows.length <= 8) t.classList.add('ganz');
}});
</script>
<script>if (/[?&]drucken=1/.test(location.search)) addEventListener('load', function () {{ setTimeout(print, 300); }});</script>
</body></html>
"""

VORLAGEN['en'] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="icon" href="{icon}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your own chat — {domain}</title>
<style>{stil}</style></head>
<body><div class="blatt">
<div class="druckleiste nur-bildschirm"><button type="button" onclick="print()">Print / save as PDF</button></div>

<div class="deckel">
  <h1>Your own chat</h1>
  <p class="leise">Encrypted, on your own server — <b>{domain}</b><br>
     Set up on {datum}{betreiber_zeile}</p>
</div>

<div class="kasten">
  <h3>Your access details</h3>
  <table class="zugang">
    <tr><td>Chat in the browser</td><td class="wert">{chat}</td></tr>
    <tr><td>Invite people</td><td class="wert">{einladung}</td></tr>
    <tr><td>Admin page (for you only)</td><td class="wert">{verwaltung}</td></tr>
    <tr><td>Server for the app</td><td class="wert">{domain}</td></tr>
    <tr><td>Your username</td><td class="wert">{verwalter}</td></tr>
    <tr><td>Your user ID</td><td class="wert">@{verwalter}:{domain}</td></tr>
    <tr><td>Initial password</td><td>you get it separately, not with this guide</td></tr>
  </table>
  <p class="klein leise" style="margin-top:10px;margin-bottom:0">
     At the first sign-in on the admin page you choose your own password.
     Until then your account is locked in the chat.</p>
</div>
{bild_hinweis}
<h2>1. The essentials in three sentences</h2>
<p>You have your own chat server. Your messages are end-to-end encrypted —
   the server only holds scrambled text. Section 7 says where the limits are.</p>
<p>Nobody can simply sign up. Whoever is meant to be in needs an <b>invitation</b>,
   and you send it (or someone you allow to).</p>
<p>You are connected to the worldwide Matrix network: your people can also write to
   people on other servers.</p>

<h2>2. First login</h2>
<ol class="schritte">
  <li><b>Open {verwaltung} in the browser</b> and sign in with your username and the
      initial password.</li>
  <li><b>Choose your own password.</b><br>
      <span class="leise">The page asks for it before it shows anything else. Pick something
      long — a sentence beats a short jumble.</span></li>
  <li><b>Set up the second factor.</b> The page offers it right after,
      see section 3 — please don't skip it.</li>
  <li><b>Open {chat}</b> and sign in with your new password.<br>
      <span class="leise">The browser is fine for the initial setup; for everyday use
      the app is better (section 4).</span></li>
</ol>

<h2>3. Second factor for your admin account</h2>
<p>Your account can create accounts, block them and change passwords. A single password
   in front of that is not enough. The second factor takes two minutes.</p>
<ol class="schritte">
  <li>Install an authenticator app. Recommended: <b>Aegis</b> (Android) or
      <b>Ente Auth</b> (both systems). Your phone's password app can usually do it too.</li>
  <li>The admin page offers it at the first sign-in. Later you find it under
      <span class="wert">{verwaltung}</span> → <b>"Second factor"</b>.</li>
  <li>Scan the QR code shown with the app.</li>
  <li>Type in the six-digit code the app shows. Only then does it go live —
      so you cannot lock yourself out while setting it up.</li>
</ol>
{bild_zweitfaktor}

<div class="kasten warn">
  <h3>If the phone gets lost</h3>
  <p style="margin-bottom:0">Then you cannot get into the admin page without help.
     Get in touch with {betreiber_kurz} — they can reset the second factor on the server.</p>
</div>

<h2 class="neu">4. The app on the phone</h2>
<p>You don't need an app from us — you use a ready-made one from the stores.
   That is deliberate: this way the person who built the app is not the one who runs the server.</p>
<ol class="schritte">
  <li><b>Install the app.</b> Recommended: <b>FluffyChat</b> — looks friendly and is the
      easiest. If you prefer something faster, go for <b>Element X</b>
      (the variant <i>with</i> the X).</li>
  <li><b>Enter the server.</b> The app asks for it. This goes in:
      <span class="wert">{domain}</span></li>
  <li><b>Log in</b> with username and password.</li>
  <li><b>Compare emoji.</b> When you add a second device, both show the same little pictures.
      If they match, confirm. That is the check that nobody is sitting in the middle.</li>
</ol>

<h2>5. Inviting people</h2>
<p>At <span class="wert">{einladung}</span> — <b>all</b> your people log in there,
   not just you.</p>

{bild_anmelden}
<ol class="schritte">
  <li>Log in with the usual chat account.</li>
  <li><b>Choose where the person should go</b> — for example "Family" or "Sports club".
      They then get an invitation into that room right away and are not left with an
      empty app.</li>
  <li>Press <b>"Create invitation"</b>.</li>
  <li>Send the link — via WhatsApp, SMS, whatever you like. There is a button for it.</li>
</ol>
{bild_nutzer}
{bild_erzeugt}

<p>The invited person opens the link, installs the app and enters the code. Done.
   An invitation is valid for <b>14 days</b> and works <b>exactly once</b>.
   This is what it looks like on their end:</p>

{bild_einladung}
<div class="kasten">
  <h3>Who may invite how many people?</h3>
  <p>You decide that at <span class="wert">{verwaltung}</span>. For each person you can enter
     any number — <b>0</b> means "may not invite anyone", and there is a tick box for
     "unlimited". New accounts get a default, which you also set there.</p>
  <p style="margin-bottom:0">There you also see <b>who brought in whom</b>. So it stays
     traceable through whom someone joined.</p>
</div>

<h2 class="neu">6. Admin page — what else you can do there</h2>

{bild_verwaltung}
<table class="zugang">
  <tr><td>Create account by hand</td><td>When someone cannot open a link. The password
      is shown once — pass it on and have it changed.</td></tr>
  <tr><td>Reset password</td><td>Logs out all devices of that account.</td></tr>
  <tr><td>Block account</td><td>It can no longer log in. Reversible; on unblocking
      there is a new password.</td></tr>
  <tr><td>Appoint admin</td><td>Whoever gets this may do everything you may.</td></tr>
  <tr><td>Switch language</td><td>English or German — applies immediately to all pages.
      Each invitation also gets its own language.</td></tr>
</table>
<p class="klein leise">You cannot block yourself or remove your own rights —
   otherwise you would be locked out of your own server.</p>

<h2>7. What the operator can see — and what not</h2>
<p>This should be out in the open, so you can pass it on to your people.</p>
<table class="zugang">
  <tr><td><b>Your messages</b></td><td><b>No.</b> The server only holds scrambled text.
      The keys are exclusively on your devices.</td></tr>
  <tr><td><b>Photos and files</b></td><td><b>No.</b> Also encrypted.</td></tr>
  <tr><td>Who, when, with whom</td><td><b>Yes.</b> That a message went and between whom
      is visible on the server — the content is not.</td></tr>
  <tr><td>Disk usage, account names</td><td><b>Yes.</b> That is part of running it.</td></tr>
  <tr><td>Restore history</td><td><b>No.</b> Without your keys that is not possible —
      not for us either.</td></tr>
  <tr><td>Set a new password</td><td><b>Yes.</b> That would allow taking over an account and
      reading messages from then on — old messages stay locked, and all devices of the account
      are logged out in the process, so you would notice.</td></tr>
  <tr><td>Chat in the browser</td><td>The browser chat at {chat} is served by the operator
      themselves and could be altered — there the encryption does not protect against them.
      If you want to rule that out, use the app from the store (section 4).</td></tr>
</table>

<h2>8. Phone gone — what then?</h2>
<p>Because nobody but you has the keys, nobody can bring the history back either.
   So take two precautions <b>beforehand</b>:</p>
<ol class="schritte">
  <li><b>Log in a second device</b> — tablet or computer. That is the best backup,
      because it works without you doing anything. If the phone gets lost, nothing is gone.</li>
  <li><b>Turn on the key backup</b> when the app asks for it, and keep the
      recovery key safe. Don't dismiss it — this one step decides whether the
      history survives a change of device.</li>
</ol>

<h2>9. When something doesn't work</h2>
<table class="zugang">
  <tr><td>"Too many attempts"</td><td>The server slows down after a few failed attempts.
      Wait a minute, then it works again. The password is usually right.</td></tr>
  <tr><td>Code is not accepted</td><td>Is the time on the phone correct?
      Turn on automatic time.</td></tr>
  <tr><td>Red warning sign in the chat</td><td>Don't guess — it means an unverified
      device. Ask the sender, or ask {betreiber_kurz}.</td></tr>
  <tr><td>Call doesn't connect</td><td>Have both sides switch from Wi-Fi to mobile data
      once. If it keeps happening, report it.</td></tr>
  <tr><td>Everything else</td><td>{betreiber_kurz}</td></tr>
</table>

<div class="fuss">
  Your own chat at {domain} · guide created on {datum}<br>
  This guide contains no passwords.
</div>

</div><script>
// Kapitelanfang zusammenhalten: Überschrift mit den nächsten beiden Abschnitten.
// Passt das nicht mehr auf die Seite, beginnt das Kapitel auf der nächsten.
document.querySelectorAll('.blatt h2, .blatt h3').forEach(function (h) {{
  if (h.parentNode.classList.contains('kapitelanfang')) return;
  var anfang = document.createElement('div'), n = h.tagName === 'H2' ? 2 : 1;
  anfang.className = 'kapitelanfang';
  h.parentNode.insertBefore(anfang, h);
  anfang.appendChild(h);
  // Folgt direkt eine Unterüberschrift, gehört sie samt ihrem ersten Abschnitt dazu.
  if (n === 2 && anfang.nextElementSibling && anfang.nextElementSibling.tagName === 'H3') n = 3;
  while (n-- > 0 && anfang.nextElementSibling && !/^H2$/.test(anfang.nextElementSibling.tagName))
    anfang.appendChild(anfang.nextElementSibling);
}});
// Kurze Tabellen nicht teilen.
document.querySelectorAll('.blatt table').forEach(function (t) {{
  if (t.rows.length <= 8) t.classList.add('ganz');
}});
</script>
<script>if (/[?&]drucken=1/.test(location.search)) addEventListener('load', function () {{ setTimeout(print, 300); }});</script>
</body></html>
"""

# Bildunterschriften und Kleintexte je Sprache. Die Bilder selbst sind deutsch.
TEXTE = {
  'de': {
    'betreiber_zeile': '<br>Betreut von {betreiber}',
    'betreiber_vorgabe': 'deinem Betreuer',
    'bild_hinweis': '',
    '01-anmelden.png': 'Die Einladungsseite — mit dem gewohnten Chat-Konto anmelden.',
    '02-nutzer.png': 'Jeder sieht hier, wie viele Einladungen er noch hat.',
    '03-erzeugt.png': 'Nach dem Knopfdruck: der fertige Link zum Verschicken.',
    '04-einladung.png': 'Was der Eingeladene sieht — drei Schritte, mehr nicht.',
    '05-verwaltung.png': 'Die Verwaltung: Aktualisierungen, Vorgabe für neue Konten, Sprache, '
                         'Konten anlegen und verwalten.',
    '06-zweitfaktor.png': 'Den QR-Code mit der Authenticator-App scannen.',
  },
  'en': {
    'betreiber_zeile': '<br>Looked after by {betreiber}',
    'betreiber_vorgabe': 'your contact person',
    'bild_hinweis': '',
    '01-anmelden.png': 'The invitation page — log in with the usual chat account.',
    '02-nutzer.png': 'Everyone sees here how many invitations they have left.',
    '03-erzeugt.png': 'After the click: the finished link to send.',
    '04-einladung.png': 'What the invited person sees — three steps, nothing more.',
    '05-verwaltung.png': 'The admin page: updates, the default for new accounts, language, '
                         'creating and managing accounts.',
    '06-zweitfaktor.png': 'Scan the QR code with the authenticator app.',
  },
}


def heutiges_datum(sprache):
    """13.10.2026 bzw. 5 October 2026 - englisch ohne fuehrende Null beim Tag."""
    jetzt = time.localtime()
    if sprache == 'de':
        return time.strftime('%d.%m.%Y', jetzt)
    # %-d wäre nur eine glibc-Erweiterung, deshalb der Tag von Hand.
    return f"{jetzt.tm_mday} {time.strftime('%B %Y', jetzt)}"


def main():
    a = argparse.ArgumentParser(description='Kundenanleitung erzeugen / create customer guide')
    a.add_argument('--domain', required=True)
    a.add_argument('--verwalter', required=True)
    a.add_argument('--betreiber', default='')
    a.add_argument('--bilder', default='', help='Verzeichnis mit den Bildschirmfotos')
    a.add_argument('--language', default='en', choices=('en', 'de'))
    a.add_argument('--ausgabe', required=True)
    w = a.parse_args()

    e = html.escape
    sprache = w.language
    texte = TEXTE[sprache]
    betreiber = w.betreiber.strip()
    bilder_da = False

    def bild(name):
        """Als data:-URI einbetten, damit die Anleitung EINE Datei bleibt."""
        nonlocal bilder_da
        if not w.bilder:
            return ''
        pfad = os.path.join(w.bilder, name)
        if not os.path.exists(pfad):
            return ''
        with open(pfad, 'rb') as d:
            roh = base64.b64encode(d.read()).decode()
        bilder_da = True
        unterschrift = texte[name]
        return (f'<figure><img src="data:image/png;base64,{roh}" alt="{e(unterschrift)}">'
                f'<figcaption>{e(unterschrift)}</figcaption></figure>')

    eingebettet = {
        'bild_anmelden': bild('01-anmelden.png'),
        'bild_nutzer': bild('02-nutzer.png'),
        'bild_erzeugt': bild('03-erzeugt.png'),
        'bild_einladung': bild('04-einladung.png'),
        'bild_verwaltung': bild('05-verwaltung.png'),
        'bild_zweitfaktor': bild('06-zweitfaktor.png'),
    }
    inhalt = VORLAGEN[sprache].format(
        stil=STIL,
        icon=ICON,
        domain=e(w.domain),
        verwalter=e(w.verwalter),
        chat=f'https://chat.{e(w.domain)}',
        einladung=f'https://invite.{e(w.domain)}',
        verwaltung=f'https://service.{e(w.domain)}',
        datum=heutiges_datum(sprache),
        betreiber_zeile=texte['betreiber_zeile'].format(betreiber=e(betreiber)) if betreiber else '',
        betreiber_kurz=e(betreiber.split(',')[0]) if betreiber else texte['betreiber_vorgabe'],
        # Der Hinweis auf die deutschen Bildschirmfotos nur, wenn welche drin sind.
        bild_hinweis=texte['bild_hinweis'] if bilder_da else '',
        **eingebettet,
    )
    with open(w.ausgabe, 'w', encoding='utf-8') as d:
        d.write(inhalt)
    print(w.ausgabe)


if __name__ == '__main__':
    main()
