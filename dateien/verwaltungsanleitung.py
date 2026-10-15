#!/usr/bin/env python3
"""Erzeugt die Verwaltungsanleitung: alles, was der Betreiber ueber SEINEN Server
wissen muss, mit den echten Namen, Adressen und Ports.

Gegenstueck zur Kundenanleitung (die ist fuer die Leute, die den Chat nutzen).
Eine einzelne HTML-Datei ohne Fremdbestandteile, zum Oeffnen, Drucken, als PDF
sichern. Enthaelt KEIN Passwort: die Datei liegt auch dort, wo die Verwaltung
sie zum Herunterladen anbietet (/var/lib/messenger), und soll weitergegeben
werden koennen, ohne dass ein Geheimnis mitgeht.

Aufruf (macht der Installer selbst):
  verwaltungsanleitung.py --domain beispieldomain.de --verwalter anna \
      --situation zuhause|mietserver|proxy --lokale-ip 192.168.1.20 \
      --oeffentliche-ip 203.0.113.5 --mail du@example.de --language de \
      --ausgabe /root/verwaltungsanleitung-beispieldomain.de.html
"""
import argparse
import html
import time

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
  ol.schritte > li { counter-increment:s; position:relative; padding-left:32px; margin-bottom:12px; }
  ol.schritte > li::before { content:counter(s); position:absolute; left:0; top:1px;
        width:22px; height:22px; border-radius:50%; background:var(--eigen); color:#fff;
        font-size:10pt; font-weight:700; text-align:center; line-height:22px; }
  .kasten { border:2px solid var(--eigen); border-radius:8px; padding:14px 16px; margin:14px 0;
        background:var(--flaeche); break-inside:avoid; }
  .kasten.warn { border-color:var(--warn); }
  .kasten h3 { margin-top:0; color:var(--eigen); }
  .kasten.warn h3 { color:var(--warn); }
  table { width:100%; border-collapse:collapse; margin-top:6px; }
  td, th { padding:7px 0; border-bottom:1px solid var(--linie); vertical-align:top; text-align:left; }
  td:first-child { width:38%; color:var(--leise); }
  th { font-size:10pt; color:var(--leise); font-weight:600; }
  .wert, code, pre { font-family:"Consolas", ui-monospace, monospace; font-size:10.5pt; }
  .wert { font-weight:600; word-break:break-all; }
  pre { background:var(--flaeche); border:1px solid var(--linie); border-radius:6px;
        padding:10px 12px; white-space:pre-wrap; word-break:break-all; margin:6px 0 10px; }
  .leise { color:var(--leise); }
  .klein { font-size:10pt; }
  .deckel { border-bottom:3px solid var(--eigen); padding-bottom:14px; margin-bottom:8px; }
  .fuss { margin-top:30px; padding-top:12px; border-top:1px solid var(--linie);
        font-size:9.5pt; color:var(--leise); }
  @media print { .blatt { padding:0; max-width:none; } a { color:inherit; text-decoration:none; } }
"""


def e(s):
    return html.escape(str(s))


def datum(sprache):
    return time.strftime('%d.%m.%Y' if sprache == 'de' else '%Y-%m-%d')


def ports_tabelle(sprache, mit_web):
    zeilen = []
    if mit_web:
        zeilen += [('80', 'TCP', 'Zertifikat, Weiterleitung auf https' if sprache == 'de' else 'certificate, redirect to https'),
                   ('443', 'TCP', 'Chat, Einladungen, Verwaltung, Matrix' if sprache == 'de' else 'chat, invitations, admin page, Matrix')]
    zeilen += [('3478', 'TCP + UDP', 'Anrufe (TURN)' if sprache == 'de' else 'calls (TURN)')]
    if mit_web:
        zeilen += [('5349', 'TCP + UDP', 'Anrufe (TURN verschlüsselt)' if sprache == 'de' else 'calls (TURN over TLS)')]
    zeilen += [('49160–49200', 'UDP', 'Anrufe (Relais)' if sprache == 'de' else 'calls (relay)')]
    kopf = ('Port', 'Protokoll', 'Wofür') if sprache == 'de' else ('Port', 'Protocol', 'Purpose')
    return ('<table><tr>' + ''.join(f'<th>{k}</th>' for k in kopf) + '</tr>'
            + ''.join(f'<tr><td class="wert" style="color:inherit">{p}</td><td>{pr}</td><td>{z}</td></tr>' for p, pr, z in zeilen)
            + '</table>')


def seite(a):
    de = a.language == 'de'
    d = a.domain
    hosts = {'matrix': f'matrix.{d}', 'chat': f'chat.{d}', 'invite': f'invite.{d}',
             'service': f'service.{d}', 'turn': f'turn.{d}'}
    proxy = a.situation == 'proxy'
    zuhause = a.situation == 'zuhause'
    hinter_router = a.lokale_ip and a.oeffentliche_ip and a.lokale_ip != a.oeffentliche_ip
    installer = (f"./install.sh --domain {d} --verwalter {a.verwalter} --language {a.language}"
                 + (" --hinter-proxy" if proxy else f" --mail {a.mail or 'DEINE@ADRESSE'}")
                 + (f' --betreiber "{a.betreiber}"' if a.betreiber else ''))
    namen = [d] + [hosts[k] for k in ('matrix', 'chat', 'invite', 'service', 'turn')]
    ziel_dns = a.oeffentliche_ip or ('<öffentliche Adresse>' if de else '<public address>')

    T = {}
    if de:
        T.update(
            titel=f'Verwaltungsanleitung – {d}',
            unter='Alles über diesen Server an einem Ort. Ohne Passwörter, damit die Datei weitergegeben werden kann.',
            erstellt=f'Erstellt am {datum("de")} vom Installer. Betreuung: {a.betreiber or "–"}',
            adressen='Adressen',
            chat='Chat im Browser', einl='Einladungen', verw='Verwaltung (nur Verwalter)', app='In der App als Server eintragen',
            api='Matrix-Schnittstelle (für Apps und andere Server)',
            konto='Dein Konto',
            konto_text=f'Kennung <span class="wert">@{e(a.verwalter)}:{e(d)}</span>. Das Startpasswort steht in '
                       f'<code>/root/zugangsdaten-messenger.txt</code> auf dem Server. Ändere es nach der ersten '
                       f'Anmeldung in der Verwaltung („Passwort neu“ an deinem Konto) und lösche die Datei.',
            zf='Zweiter Faktor', zf_text='Die Verwaltung kann Konten löschen. Richte unter <b>Verwaltung → Zweiter Faktor</b> '
               'eine Authenticator-App ein. Notausgang, wenn das Telefon weg ist (als root auf dem Server):',
            netz='Netz: was außerhalb des Servers eingerichtet sein muss',
            dns='DNS-Einträge', dns_text=f'Beim Anbieter deiner Domain müssen diese sechs Namen als A-Eintrag auf '
                f'<span class="wert">{e(ziel_dns)}</span> zeigen'
                + (' (die Adresse des Proxys).' if proxy else '.'),
            dyn='Wechselnde Adresse (DynDNS)',
            dyn_text='Bei den meisten Heimanschlüssen wechselt die öffentliche Adresse, spätestens nach einer Zwangstrennung. '
                     'Dann müssen die sechs Namen ihr folgen: beim Domain-Anbieter DynDNS einschalten und im Router eintragen '
                     '(FRITZ!Box: Internet → Freigaben → DynDNS). Ohne das läuft alles nur bis zum nächsten Adresswechsel.',
            router='Portfreigaben im Router',
            router_text=f'Der Router muss diese Ports an <span class="wert">{e(a.lokale_ip)}</span> weiterleiten '
                        '(FRITZ!Box: Internet → Freigaben → Portfreigaben → „Gerät für Freigaben hinzufügen“). '
                        'Dafür braucht der Server eine feste Adresse im Heimnetz (FRITZ!Box: Heimnetz → Netzwerk → '
                        'Stift am Gerät → „immer die gleiche IPv4-Adresse zuweisen“).',
            router_proxy_text=f'Ports 80 und 443 gehen zum Proxy. Für Anrufe müssen diese Ports direkt an diesen Server '
                              f'(<span class="wert">{e(a.lokale_ip)}</span>) weitergeleitet sein:',
            proxy_k='Einträge im Reverse-Proxy',
            proxy_text=f'Dieser Server liefert Klartext auf Port 80. Im Proxy braucht jeder dieser fünf Namen einen Eintrag '
                       f'auf <span class="wert">http://{e(a.lokale_ip or "SERVER-IP")}:80</span>, mit Zertifikat und '
                       f'Websockets. Beim Eintrag für <code>{e(hosts["matrix"])}</code> größere Uploads erlauben '
                       f'(nginx: <code>client_max_body_size 100M;</code>).',
            anbieter='Firewall des Anbieters',
            anbieter_text='Manche Anbieter (Hetzner Cloud, Ionos, Oracle) haben eine Firewall vor dem Server. Dort müssen offen sein: '
                          'TCP 22, 80, 443, 3478, 5349 und UDP 3478, 5349, 49160–49200. Die Firewall auf dem Server selbst (ufw) hat '
                          'der Installer eingerichtet.',
            sicherung='Sicherung',
            sicherung_text='Jede Nacht um 3:30 Uhr: Datenbank, Konfiguration und Medien, verschlüsselt mit dem Schlüssel in '
                           '<code>/root/.sicherung_passwort</code>. <b>Ohne diesen Schlüssel ist keine Sicherung lesbar.</b> '
                           'Kopiere ihn ins Passwortdepot.',
            sicherung_ort=(f'Ziel: <code>{e(a.sicherung_ziel)}</code>.' if a.sicherung_ziel else
                           'Ohne entferntes Ziel liegen die Stände in <code>/var/backups/messenger/</code> (14 Tage) – '
                           'auf demselben Rechner, also kein Schutz gegen dessen Ausfall. Ein entferntes Ziel kommt mit '
                           '<code>--sicherung user@host:/pfad</code> und <code>--nur sicherung</code> (siehe Aktualisieren).'),
            update='Aktualisieren',
            update_text='Nichts wird blind aktualisiert. Die Verwaltung prüft täglich und zeigt an, was ansteht '
                        '(Systempakete, Synapse, Element Web); eingespielt wird erst nach deinem Klick. Humatrix selbst '
                        '(Einladungsseite, Verwaltung, Installer) aktualisierst du so, als root auf dem Server:',
            update_schritte=[
                'Neue Fassung holen:',
                'Installer mit denselben Angaben wie bei der Einrichtung laufen lassen (er macht nur, was sich geändert hat):',
                'Nachkontrolle am Ende muss grün sein. Danach ist der Server auf dem neuen Stand.'],
            passwort='Passwort eines Kontos zurücksetzen',
            passwort_text='In der Verwaltung: Zeile des Kontos → „Passwort neu“. Das neue Passwort erscheint einmal. '
                          'Wenn du selbst ausgesperrt bist, als root auf dem Server:',
            klemmt='Wenn etwas klemmt',
            klemmt_list=[
                ('Läuft alles?', 'systemctl status matrix-synapse einladung coturn nginx postgresql'),
                ('Was sagt der Chat-Server?', 'journalctl -u matrix-synapse -n 50'),
                ('Was sagt die Einladungsseite?', 'journalctl -u einladung -n 50'),
                ('Protokoll der Einrichtung', 'less /var/log/messenger-installer.log'),
                ('Ist der Server von außen richtig erreichbar?', f'https://federationtester.matrix.org/#{d}'),
                ('Einen Abschnitt neu machen (z. B. Anrufe)', f'cd /opt/humatrix && {installer} --nur turn')],
            wo='Wo liegt was',
            wo_list=[
                ('Installer und Einrichtungsseite', '/opt/humatrix'),
                ('Zugangsdaten (nach dem Lesen löschen)', '/root/zugangsdaten-messenger.txt'),
                ('Kundenanleitung für deine Leute', f'/root/kundenanleitung-{d}.html'),
                ('Diese Anleitung', f'/root/verwaltungsanleitung-{d}.html, in der Verwaltung unter „Verwaltungsanleitung“'),
                ('Synapse-Konfiguration (eigene Änderungen)', '/etc/matrix-synapse/conf.d/50-eigene.yaml'),
                ('Einladungsdienst', '/opt/einladung, Daten in /var/lib/einladung, Geheimnisse in /etc/einladung'),
                ('Anrufe (coturn)', '/etc/turnserver.conf'),
                ('Webserver', '/etc/nginx/sites-available/messenger'),
                ('Sicherungsschlüssel', '/root/.sicherung_passwort')],
            fuss='Erzeugt vom Humatrix-Installer. Diese Datei enthält keine Passwörter.',
        )
    else:
        T.update(
            titel=f'Operator guide – {d}',
            unter='Everything about this server in one place. No passwords, so the file can be passed on.',
            erstellt=f'Created {datum("en")} by the installer. Looked after by: {a.betreiber or "–"}',
            adressen='Addresses',
            chat='Chat in the browser', einl='Invitations', verw='Admin page (admins only)', app='Server to enter in the app',
            api='Matrix API (for apps and other servers)',
            konto='Your account',
            konto_text=f'User ID <span class="wert">@{e(a.verwalter)}:{e(d)}</span>. The initial password is in '
                       f'<code>/root/zugangsdaten-messenger.txt</code> on the server. Change it after the first login '
                       f'in the admin page ("New password" on your account) and delete the file.',
            zf='Second factor', zf_text='The admin page can delete accounts. Set up an authenticator app under '
               '<b>Admin page → Second factor</b>. Emergency exit if the phone is gone (as root on the server):',
            netz='Network: what must be set up outside the server',
            dns='DNS records', dns_text=f'At your domain provider these six names must be A records pointing to '
                f'<span class="wert">{e(ziel_dns)}</span>'
                + (' (the address of the proxy).' if proxy else '.'),
            dyn='Changing address (DynDNS)',
            dyn_text='On most home connections the public address changes, at the latest after a forced reconnect. '
                     'Then the six names must follow it: enable DynDNS at your domain provider and enter it in the router '
                     '(FRITZ!Box: Internet → Permit Access → DynDNS). Without it everything works until the next change.',
            router='Port forwarding in the router',
            router_text=f'The router must forward these ports to <span class="wert">{e(a.lokale_ip)}</span> '
                        '(FRITZ!Box: Internet → Permit Access → Port Sharing → "Add Device for Sharing"). '
                        'For that the server needs a fixed address in the home network (FRITZ!Box: Home Network → Network → '
                        'pencil at the device → "Always assign the same IPv4 address").',
            router_proxy_text=f'Ports 80 and 443 go to the proxy. For calls these ports must be forwarded directly to this '
                              f'server (<span class="wert">{e(a.lokale_ip)}</span>):',
            proxy_k='Entries in the reverse proxy',
            proxy_text=f'This server serves plain HTTP on port 80. In the proxy each of these five names needs an entry '
                       f'pointing to <span class="wert">http://{e(a.lokale_ip or "SERVER-IP")}:80</span>, with certificate '
                       f'and websockets. On the entry for <code>{e(hosts["matrix"])}</code> allow larger uploads '
                       f'(nginx: <code>client_max_body_size 100M;</code>).',
            anbieter='Provider firewall',
            anbieter_text='Some providers (Hetzner Cloud, Ionos, Oracle) have a firewall in front of the server. There, open '
                          'TCP 22, 80, 443, 3478, 5349 and UDP 3478, 5349, 49160–49200. The firewall on the server itself (ufw) was set '
                          'up by the installer.',
            sicherung='Backup',
            sicherung_text='Every night at 03:30: database, configuration and media, encrypted with the key in '
                           '<code>/root/.sicherung_passwort</code>. <b>Without this key no backup is readable.</b> '
                           'Copy it into your password manager.',
            sicherung_ort=(f'Target: <code>{e(a.sicherung_ziel)}</code>.' if a.sicherung_ziel else
                           'Without a remote target the backups are in <code>/var/backups/messenger/</code> (14 days), on the '
                           'same machine, so no protection against its loss. A remote target is added with '
                           '<code>--backup user@host:/path</code> and <code>--only sicherung</code> (see Updating).'),
            update='Updating',
            update_text='Nothing is updated blindly. The admin page checks daily and shows what is pending (system '
                        'packages, Synapse, Element Web); it is installed only after your click. Humatrix itself '
                        '(invitation page, admin page, installer) is updated like this, as root on the server:',
            update_schritte=[
                'Fetch the new version:',
                'Run the installer with the same details as at setup (it only does what changed):',
                'The final checks must be green. Then the server is up to date.'],
            passwort='Reset the password of an account',
            passwort_text='In the admin page: row of the account → "New password". The new password is shown once. '
                          'If you are locked out yourself, as root on the server:',
            klemmt='When something is stuck',
            klemmt_list=[
                ('Is everything running?', 'systemctl status matrix-synapse einladung coturn nginx postgresql'),
                ('What does the chat server say?', 'journalctl -u matrix-synapse -n 50'),
                ('What does the invitation page say?', 'journalctl -u einladung -n 50'),
                ('Log of the setup', 'less /var/log/messenger-installer.log'),
                ('Is the server reachable correctly from outside?', f'https://federationtester.matrix.org/#{d}'),
                ('Redo one section (e.g. calls)', f'cd /opt/humatrix && {installer} --only turn')],
            wo='Where things are',
            wo_list=[
                ('Installer and setup page', '/opt/humatrix'),
                ('Credentials (delete after reading)', '/root/zugangsdaten-messenger.txt'),
                ('Customer guide for your people', f'/root/kundenanleitung-{d}.html'),
                ('This guide', f'/root/verwaltungsanleitung-{d}.html, in the admin page under "Operator guide"'),
                ('Synapse configuration (your own changes)', '/etc/matrix-synapse/conf.d/50-eigene.yaml'),
                ('Invitation service', '/opt/einladung, data in /var/lib/einladung, secrets in /etc/einladung'),
                ('Calls (coturn)', '/etc/turnserver.conf'),
                ('Web server', '/etc/nginx/sites-available/messenger'),
                ('Backup key', '/root/.sicherung_passwort')],
            fuss='Generated by the Humatrix installer. This file contains no passwords.',
        )

    netz = [f'<h3>{T["dns"]}</h3><p>{T["dns_text"]}</p><table>'
            + ''.join(f'<tr><td>{e(n)}</td><td class="wert" style="color:inherit">A → {e(ziel_dns)}</td></tr>' for n in namen)
            + '</table>']
    if zuhause:
        netz.append(f'<div class="kasten warn"><h3>{T["dyn"]}</h3><p>{T["dyn_text"]}</p></div>')
        netz.append(f'<h3>{T["router"]}</h3><p>{T["router_text"]}</p>{ports_tabelle(a.language, True)}')
    if proxy:
        netz.append(f'<h3>{T["proxy_k"]}</h3><p>{T["proxy_text"]}</p><table>'
                    + ''.join(f'<tr><td>{e(n)}</td><td class="wert" style="color:inherit">http://{e(a.lokale_ip or "SERVER-IP")}:80</td></tr>' for n in namen[:5])
                    + '</table>')
        if hinter_router:
            netz.append(f'<h3>{T["router"]}</h3><p>{T["router_proxy_text"]}</p>{ports_tabelle(a.language, False)}')
    if a.situation == 'mietserver':
        netz.append(f'<h3>{T["anbieter"]}</h3><p>{T["anbieter_text"]}</p>')

    holen = ("cd /root && curl -fsSL -o humatrix.tgz https://github.com/Gekons/Humatrix/archive/refs/heads/main.tar.gz\n"
             "rm -rf /opt/humatrix.alt && mv /opt/humatrix /opt/humatrix.alt && mkdir /opt/humatrix\n"
             "tar -xzf humatrix.tgz -C /opt/humatrix --strip-components=1")
    reset = (f"curl -s -X POST -H \"Authorization: Bearer $(cat /etc/einladung/admin-token)\" \\\n"
             f"     -H 'Content-Type: application/json' \\\n"
             f"     -d '{{\"new_password\":\"NEUES-PASSWORT\",\"logout_devices\":true}}' \\\n"
             f"     http://127.0.0.1:8008/_synapse/admin/v1/reset_password/@{a.verwalter}:{d}")

    koerper = f"""
<div class="deckel"><h1>{e(T['titel'])}</h1><p class="leise">{T['unter']}</p><p class="klein leise">{e(T['erstellt'])}</p></div>

<h2>{T['adressen']}</h2>
<table>
<tr><td>{T['chat']}</td><td class="wert" style="color:inherit">https://{e(hosts['chat'])}</td></tr>
<tr><td>{T['einl']}</td><td class="wert" style="color:inherit">https://{e(hosts['invite'])}</td></tr>
<tr><td>{T['verw']}</td><td class="wert" style="color:inherit">https://{e(hosts['service'])}</td></tr>
<tr><td>{T['app']}</td><td class="wert" style="color:inherit">{e(d)}</td></tr>
<tr><td>{T['api']}</td><td class="wert" style="color:inherit">https://{e(hosts['matrix'])}</td></tr>
</table>

<h2>{T['konto']}</h2>
<p>{T['konto_text']}</p>
<div class="kasten"><h3>{T['zf']}</h3><p>{T['zf_text']}</p>
<pre>python3 /opt/einladung/einladung.py --zweitfaktor-aus {e(a.verwalter)}</pre></div>

<h2>{T['netz']}</h2>
{''.join(netz)}

<h2>{T['sicherung']}</h2>
<p>{T['sicherung_text']}</p>
<p>{T['sicherung_ort']}</p>

<h2>{T['update']}</h2>
<p>{T['update_text']}</p>
<ol class="schritte">
<li>{T['update_schritte'][0]}<pre>{e(holen)}</pre></li>
<li>{T['update_schritte'][1]}<pre>cd /opt/humatrix && {e(installer)}</pre></li>
<li>{T['update_schritte'][2]}</li>
</ol>

<h2>{T['passwort']}</h2>
<p>{T['passwort_text']}</p>
<pre>{e(reset)}</pre>

<h2>{T['klemmt']}</h2>
<table>{''.join(f'<tr><td>{e(k)}</td><td><code>{e(v)}</code></td></tr>' for k, v in T['klemmt_list'])}</table>

<h2>{T['wo']}</h2>
<table>{''.join(f'<tr><td>{e(k)}</td><td><code>{e(v)}</code></td></tr>' for k, v in T['wo_list'])}</table>

<div class="fuss">{T['fuss']}</div>
"""
    return f"""<!doctype html><html lang="{a.language}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(T['titel'])}</title><style>{STIL}</style></head>
<body><div class="blatt">{koerper}</div></body></html>"""


def main():
    p = argparse.ArgumentParser(description='Verwaltungsanleitung erzeugen / create operator guide')
    p.add_argument('--domain', required=True)
    p.add_argument('--verwalter', required=True)
    p.add_argument('--situation', default='mietserver', choices=('mietserver', 'zuhause', 'proxy'))
    p.add_argument('--lokale-ip', default='')
    p.add_argument('--oeffentliche-ip', default='')
    p.add_argument('--mail', default='')
    p.add_argument('--betreiber', default='')
    p.add_argument('--sicherung-ziel', default='')
    p.add_argument('--language', default='en', choices=('en', 'de'))
    p.add_argument('--ausgabe', required=True)
    a = p.parse_args()
    with open(a.ausgabe, 'w', encoding='utf-8') as d:
        d.write(seite(a))
    print(a.ausgabe)


if __name__ == '__main__':
    main()
