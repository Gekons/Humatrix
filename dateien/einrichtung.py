#!/usr/bin/env python3
"""Einrichtung im Browser: fragt ab, was install.sh sonst als Schalter bekommt.

setup.sh startet diesen Dienst auf einem frischen Server und druckt Adresse und
Einmalcode ins Terminal. Im Browser folgen dann vier Schritte (Server, Namen
pruefen, Konto, Zusammenfassung), danach laeuft install.sh mit den Antworten,
die Seite zeigt den Fortschritt und am Ende die Zugangsdaten. "Abschliessen"
schaltet den Dienst ab.

Laeuft als root, weil install.sh Pakete installiert und Systemdateien schreibt.
Dafuer ist der Zugang eng: nur mit dem Einmalcode, nach zehn Fehlversuchen
sperrt sich die Seite und erzeugt einen neuen Code, und nach dem Abschluss
ist sie weg. Nur Bordmittel von Python.

Der Lauf von install.sh steht in einer Protokolldatei, nicht nur im Speicher:
wird dieser Dienst neu gestartet, laeuft install.sh weiter (KillMode=process),
und die Seite findet ueber lauf.json und das Protokoll wieder hin.
"""
import base64
import hashlib
import hmac
import html
import http.cookies
import http.server
import json
import os
import re
import secrets
import socketserver
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request

PORT = int(os.environ.get('HUMATRIX_SETUP_PORT', '8088'))
QUELLE = os.environ.get('HUMATRIX_QUELLE', '/opt/humatrix')       # dort liegt install.sh
ZUSTAND = os.environ.get('HUMATRIX_ZUSTAND', '/var/lib/humatrix-einrichtung')   # Tests legen ihn um
CODE_DATEI = f'{ZUSTAND}/code'
ANTWORTEN_DATEI = f'{ZUSTAND}/antworten.json'
LAUF_DATEI = f'{ZUSTAND}/lauf.json'
LAUF_LOG = f'{ZUSTAND}/lauf.log'
INSTALLER_LOG = '/var/log/messenger-installer.log'
ZUGANG = '/root/zugangsdaten-messenger.txt'
SYNAPSE_GEHEIMNISSE = '/etc/matrix-synapse/conf.d/05-secrets.yaml'   # da = schon eingerichtet
VERSUCHE_MAX = 10
SITZUNG_STUNDEN = 6
SEITEN = ('/', '/server', '/vorbereitung', '/dns', '/konto', '/pruefen', '/lauf', '/fertig', '/fehler', '/schon')
FERTIG_FRIST = 24 * 3600      # so lange bleibt die Seite nach 'Fertig' ohne Klick auf Abschliessen
LEER_FRIST = 48 * 3600        # so lange wartet eine Seite, auf der nie etwas eingerichtet wurde
TLS_PORT = PORT + 1           # nach 'Fertig' mit eigenem Zertifikat: dieselbe Seite verschluesselt
GESTARTET = time.time()
PRAEFIXE = ('', 'matrix.', 'chat.', 'invite.', 'service.', 'turn.')
SITUATIONEN = ('mietserver', 'zuhause', 'proxy')   # mietserver|zuhause -> eigenes Zertifikat, proxy -> --hinter-proxy

SITZUNGSSCHLUESSEL = secrets.token_bytes(32)      # je Prozess; Neustart heisst Code neu eingeben
VERSUCHE = {'anzahl': 0, 'gesperrt': False}
SCHLOSS = threading.Lock()


# Ablage
def lesen_json(pfad, vorgabe):
    try:
        with open(pfad, encoding='utf-8') as d:
            return json.load(d)
    except (OSError, ValueError):
        return vorgabe


def schreiben_json(pfad, daten):
    os.makedirs(ZUSTAND, mode=0o700, exist_ok=True)
    tmp = pfad + '.neu'
    with open(tmp, 'w', encoding='utf-8') as d:
        json.dump(daten, d, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, pfad)


def antworten():
    return lesen_json(ANTWORTEN_DATEI, {})


def antworten_setzen(**neu):
    a = antworten()
    a.update(neu)
    schreiben_json(ANTWORTEN_DATEI, a)
    return a


def code_lesen():
    try:
        with open(CODE_DATEI, encoding='utf-8') as d:
            return d.read().strip().upper()
    except OSError:
        return ''


def code_neu():
    os.makedirs(ZUSTAND, mode=0o700, exist_ok=True)
    code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
    alt = os.umask(0o077)
    try:
        with open(CODE_DATEI, 'w', encoding='utf-8') as d:
            d.write(code + '\n')
    finally:
        os.umask(alt)
    return code


def code_pruefen(eingabe):
    soll = code_lesen()
    ist = re.sub(r'[^A-Z0-9]', '', (eingabe or '').upper())
    return bool(soll) and hmac.compare_digest(soll.encode(), ist.encode())


def schon_eingerichtet():
    return os.path.exists(SYNAPSE_GEHEIMNISSE)


# Sitzung
def sitzung_bauen():
    seit = int(time.time())
    inhalt = f'einrichtung|{seit}|{seit + SITZUNG_STUNDEN * 3600}'.encode()
    unterschrift = hmac.new(SITZUNGSSCHLUESSEL, inhalt, hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(inhalt + b'|' + unterschrift.encode()).decode()


def sitzung_gueltig(keks):
    try:
        roh = base64.urlsafe_b64decode(keks.encode())
        inhalt, unterschrift = roh.rsplit(b'|', 1)
        soll = hmac.new(SITZUNGSSCHLUESSEL, inhalt, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(soll.encode(), unterschrift):
            return False
        _, _, bis = inhalt.decode().split('|')
        return int(bis) > time.time()
    except Exception:
        return False


# Netz
IP_MERKER = {'oeffentlich': None, 'wann': 0}


def oeffentliche_ip():
    if IP_MERKER['oeffentlich'] and time.time() - IP_MERKER['wann'] < 300:
        return IP_MERKER['oeffentlich']
    try:
        with urllib.request.urlopen('https://api4.ipify.org', timeout=10) as a:
            ip = a.read().decode().strip()
        if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', ip):
            IP_MERKER.update(oeffentlich=ip, wann=time.time())
            return ip
    except Exception:
        pass
    return ''


def lokale_ip():
    try:
        aus = subprocess.run(['ip', '-4', 'route', 'get', '1.1.1.1'], capture_output=True,
                             text=True, timeout=5).stdout
        t = re.search(r'\bsrc (\d+\.\d+\.\d+\.\d+)', aus)
        if t:
            return t.group(1)
    except Exception:
        pass
    return ''


def geraet():
    """Hostname und MAC-Adresse des Weges nach draussen: damit findet man den
    Server in der Geraeteliste des Routers wieder (feste Adresse, Portfreigaben)."""
    name = ''
    try:
        with open('/etc/hostname', encoding='utf-8') as d:
            name = d.read().strip()
    except OSError:
        pass
    mac = ''
    try:
        aus = subprocess.run(['ip', '-4', 'route', 'get', '1.1.1.1'], capture_output=True, text=True, timeout=5).stdout
        t = re.search(r'\bdev (\S+)', aus)
        if t:
            with open(f'/sys/class/net/{t.group(1)}/address', encoding='utf-8') as d:
                mac = d.read().strip()
    except Exception:
        pass
    return name, mac


def modus_von(a):
    return 'proxy' if a.get('situation') == 'proxy' else 'zertifikat'


def aufloesen(name):
    """Erste IPv4-Adresse, wie sie auch install.sh sieht (getent ahostsv4)."""
    try:
        aus = subprocess.run(['getent', 'ahostsv4', name], capture_output=True,
                             text=True, timeout=10).stdout
        return aus.split()[0] if aus.split() else ''
    except Exception:
        return ''


def dns_pruefen(domain, situation):
    """Liefert (eintraege, bereit): je Name (name, ziel, zustand), zustand in ok|woanders|fehlt.

    bereit heisst: install.sh kaeme durch seine Vorpruefung. Ein Name, der gar
    nicht aufloest, haelt immer auf; einer, der woanders hinzeigt, nur ohne Proxy.
    """
    eigene = oeffentliche_ip()
    eintraege = []
    bereit = bool(eigene)
    for p in PRAEFIXE:
        name = p + domain
        ziel = aufloesen(name)
        if not ziel:
            zustand = 'fehlt'; bereit = False
        elif ziel == eigene:
            zustand = 'ok'
        else:
            zustand = 'woanders'
            if situation != 'proxy':
                bereit = False
        eintraege.append((name, ziel, zustand))
    return eintraege, bereit, eigene


# Lauf von install.sh
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def lauf_lesen():
    return lesen_json(LAUF_DATEI, {})


def boot_id():
    try:
        with open('/proc/sys/kernel/random/boot_id', encoding='utf-8') as d:
            return d.read().strip()
    except OSError:
        return ''


def prozess_lebt(lauf):
    """Lebt der install.sh-Lauf aus lauf.json noch? Nach einem Neustart gehoert die
    PID jemand anderem - daher Boot-Kennung und Kommandozeile mit pruefen."""
    if lauf.get('boot') != boot_id():
        return False
    try:
        with open(f"/proc/{int(lauf.get('pid'))}/cmdline", 'rb') as d:
            return b'install.sh' in d.read()
    except (OSError, ValueError, TypeError):
        return False


def lauf_zeilen():
    try:
        with open(LAUF_LOG, encoding='utf-8', errors='replace') as d:
            return [ANSI.sub('', z.rstrip('\n')) for z in d]
    except OSError:
        return []


def lauf_stand():
    """{'zustand': keiner|laeuft|fertig|fehler|abgebrochen, 'rc', 'zeilen', 'schritte'}"""
    lauf = lauf_lesen()
    if not lauf:
        return {'zustand': 'keiner', 'zeilen': [], 'schritte': [], 'rc': None}
    zeilen = lauf_zeilen()
    rc = None
    if zeilen and zeilen[-1].startswith('__RC__='):
        rc = int(zeilen[-1].split('=', 1)[1] or 1)
        zeilen = zeilen[:-1]
    if rc is None:
        zustand = 'laeuft' if prozess_lebt(lauf) else 'abgebrochen'
    else:
        zustand = 'fertig' if rc == 0 else 'fehler'
    schritte = [z[3:].strip() for z in zeilen if z.startswith('== ')]
    try:
        geendet = os.path.getmtime(LAUF_LOG)
    except OSError:
        geendet = 0
    return {'zustand': zustand, 'rc': rc, 'zeilen': zeilen, 'schritte': schritte,
            'begonnen': lauf.get('begonnen', 0), 'geendet': geendet}


def lauf_starten(a):
    """install.sh mit den Antworten starten; Ausgabe in LAUF_LOG, Ende als __RC__=n."""
    befehl = ['bash', f'{QUELLE}/install.sh', '--domain', a['domain'],
              '--verwalter', a['verwalter'], '--language', a['sprache']]
    if modus_von(a) == 'proxy':
        befehl.append('--hinter-proxy')
    else:
        befehl += ['--mail', a['mail']]
    if a.get('betreiber'):
        befehl += ['--betreiber', a['betreiber']]
    huelle = 'bash "$@"; printf "__RC__=%s\\n" "$?"'
    umgebung = dict(os.environ, HUMATRIX_SETUP_PORT=str(PORT), LANG='C.UTF-8',
                    DEBIAN_FRONTEND='noninteractive', TERM='dumb')
    os.makedirs(ZUSTAND, mode=0o700, exist_ok=True)
    with open(LAUF_LOG, 'wb') as log:
        os.fchmod(log.fileno(), 0o600)
        p = subprocess.Popen(['bash', '-c', huelle, 'huelle'] + befehl[1:], stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             cwd=QUELLE, env=umgebung, start_new_session=True)
    schreiben_json(LAUF_DATEI, {'pid': p.pid, 'begonnen': int(time.time()), 'boot': boot_id()})
    threading.Thread(target=p.wait, daemon=True).start()   # Zombie einsammeln


def zugangsdaten():
    """Passwort und Pfad der Kundenanleitung aus der Zugangsdatei von install.sh."""
    daten = {'passwort': '', 'anleitung': ''}
    try:
        with open(ZUGANG, encoding='utf-8') as d:
            for z in d:
                t = re.match(r'^  (Passwort|Password)   (.*)$', z.rstrip('\n'))
                if t and not daten['passwort']:
                    daten['passwort'] = t.group(2)
                t = re.search(r'(/root/kundenanleitung-\S+\.html)', z)
                if t:
                    daten['anleitung'] = t.group(1)
    except OSError:
        pass
    return daten


def nachkontrolle(zeilen):
    """(bestanden, fehlgeschlagen) aus den OK/FEHLT-Zeilen am Ende des Laufs."""
    gut, schlecht = [], []
    for z in zeilen:
        t = re.match(r'^\s{3}(OK|FEHLT|FAIL)\s+(.*)$', z)
        if t:
            (gut if t.group(1) == 'OK' else schlecht).append(t.group(2).strip())
    return gut, schlecht


def abbruchzeile(zeilen):
    for z in reversed(zeilen):
        if z.startswith('ABBRUCH:') or z.startswith('ABORT:'):
            return z
    return ''


def waechter():
    """Schaltet die Seite von selbst ab, wenn niemand auf Abschliessen klickt:
    einen Tag nach 'Fertig', zwei Tage, wenn nie etwas eingerichtet wurde."""
    while True:
        time.sleep(600)
        try:
            stand = lauf_stand()
            if stand['zustand'] == 'fertig' and time.time() - stand['geendet'] > FERTIG_FRIST:
                abschliessen(); return
            if stand['zustand'] == 'keiner' and time.time() - GESTARTET > LEER_FRIST:
                abschliessen(); return
        except Exception:
            pass


def abschliessen():
    """Firewall-Loch schliessen und diesen Dienst abschalten, nach der Antwort."""
    def spaeter():
        time.sleep(1.5)
        subprocess.run(['ufw', 'delete', 'allow', f'{PORT}/tcp'], capture_output=True)
        subprocess.run(['ufw', 'delete', 'allow', f'{TLS_PORT}/tcp'], capture_output=True)
        try:
            os.remove(CODE_DATEI)
        except OSError:
            pass
        subprocess.run(['systemctl', 'disable', '--now', 'humatrix-einrichtung.service'],
                       capture_output=True)
    threading.Thread(target=spaeter, daemon=True).start()


TLS = {'laeuft': False, 'host': ''}

# Einmal-Tickets für den Wechsel auf die sichere Seite: Der Code wurde schon
# unverschlüsselt eingegeben, ihn dort nochmal abzufragen schützt nichts.
# Ein Ticket gilt einmal und TICKET_SEKUNDEN lang.
TICKETS = {}
TICKET_SEKUNDEN = 300


def ticket_neu():
    jetzt = time.time()
    with SCHLOSS:
        for k in [k for k, bis in TICKETS.items() if bis < jetzt]:
            del TICKETS[k]
        k = secrets.token_urlsafe(24)
        TICKETS[k] = jetzt + TICKET_SEKUNDEN
    return k


def ticket_einloesen(k):
    with SCHLOSS:
        bis = TICKETS.pop(k, 0)
    return bis > time.time()


def sicherungsschluessel():
    try:
        with open('/root/.sicherung_passwort', encoding='utf-8') as d:
            return d.read().strip()
    except OSError:
        return ''


def tls_starten(a):
    """Nach 'Fertig' mit eigenem Zertifikat: dieselbe Seite zusaetzlich verschluesselt
    auf TLS_PORT. Ueberall dort, wo ein eigenes Zertifikat liegt (gemietet, zuhause).
    Hinter einem Proxy gibt es hier keines - dort bleibt der Browser ohnehin im
    eigenen Netz. Zuhause fuehrt der Name ueber den Router zurueck (Hairpin-NAT);
    klappt das nicht, nennt die Klartextseite den Weg ueber das Terminal."""
    if TLS['laeuft'] or a.get('situation') == 'proxy' or not a.get('domain'):
        return
    kette = f"/etc/letsencrypt/live/{a['domain']}/fullchain.pem"
    schluessel = f"/etc/letsencrypt/live/{a['domain']}/privkey.pem"
    if not (os.path.exists(kette) and os.path.exists(schluessel)):
        return
    try:
        kontext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        kontext.load_cert_chain(kette, schluessel)
        dienst = Dienst(('0.0.0.0', TLS_PORT), Bedienung)
        dienst.socket = kontext.wrap_socket(dienst.socket, server_side=True)
        threading.Thread(target=dienst.serve_forever, daemon=True).start()
        TLS.update(laeuft=True, host=f"service.{a['domain']}")
        subprocess.run(['ufw', 'allow', f'{TLS_PORT}/tcp'], capture_output=True)
    except Exception as f:
        print('TLS-Seite nicht gestartet:', f, flush=True)


def ist_tls(handler):
    return isinstance(handler.connection, ssl.SSLSocket)


# Texte
TEXTE = {
  'de': {
    'titel': 'Humatrix einrichten',
    'weiter': 'Weiter',
    'zurueck': 'Zurück',
    'sprache_wechseln': 'English',
    'schritte': ['Server', 'Vorbereitung', 'Namen prüfen', 'Konto', 'Zusammenfassung'],
    'sprache_wahl': 'Sprache / Language',
    # Code
    'code_kopf': 'Willkommen',
    'code_unter': 'Zuerst der Einmalcode. Er steht im Terminal, dort wo du setup.sh gestartet hast.',
    'code_feld': 'Einmalcode',
    'code_falsch': 'Der Code stimmt nicht. Groß- und Kleinschreibung ist egal, der Strich auch.',
    'code_gesperrt': 'Zu viele falsche Codes. Die Seite hat sich gesperrt und einen neuen Code erzeugt. '
                     'Auf dem Server: <code>systemctl restart humatrix-einrichtung</code>, dann '
                     '<code>cat /var/lib/humatrix-einrichtung/code</code>.',
    # Server
    'server_kopf': 'Dein Server',
    'server_unter': 'Zwei Angaben, die sich später nicht mehr ändern lassen.',
    'domain': 'Domain',
    'domain_hilfe': 'Die nackte Domain, zum Beispiel <b>beispieldomain.de</b>. Daraus werden die Kennungen '
                    '<b>@name:beispieldomain.de</b> und die Adressen chat., invite., service. und matrix.',
    'domain_ungueltig': 'Das sieht nicht nach einer Domain aus. Erwartet wird etwas wie beispieldomain.de, '
                        'ohne https:// und ohne Pfad.',
    'modus': 'Wo steht dieser Server?',
    'erkannt_router': 'Erkannt: Im eigenen Netz hat er die Adresse <b>{innen}</b>, nach außen erscheint er als '
                      '<b>{aussen}</b>. Er steht also hinter einem Router.',
    'erkannt_direkt': 'Erkannt: Seine Adresse <b>{aussen}</b> ist direkt die öffentliche. Das sieht nach einem '
                      'gemieteten Server aus.',
    'erkannt_unbekannt': 'Die öffentliche Adresse ließ sich gerade nicht ermitteln. Ohne Internetzugang geht es nicht weiter.',
    'sit_mietserver': 'Gemieteter Server (Hetzner, Ionos, Netcup …)',
    'sit_mietserver_hilfe': 'Er hat seine eigene öffentliche Adresse. Das Zertifikat holt Let’s Encrypt.',
    'sit_zuhause': 'Zuhause oder in der Firma hinter einem Router (FRITZ!Box …)',
    'sit_zuhause_hilfe': 'Der Router muss ein paar Ports an diesen Server weiterleiten. Der nächste Schritt zeigt genau '
                         'welche. Das Zertifikat holt Let’s Encrypt.',
    'sit_proxy': 'Hinter einem Reverse-Proxy, der schon Zertifikate macht',
    'sit_proxy_hilfe': 'Nginx Proxy Manager, Traefik, Caddy. Dieser Server liefert dann Klartext auf Port 80, der Proxy '
                       'braucht fünf Einträge. Der nächste Schritt listet sie.',
    'mail': 'E-Mail-Adresse für Let’s Encrypt',
    'mail_hilfe': 'Dort meldet sich Let’s Encrypt, falls mit dem Zertifikat etwas ist. Nur beim ersten Fall nötig.',
    'mail_fehlt': 'Für ein eigenes Zertifikat braucht Let’s Encrypt eine E-Mail-Adresse.',
    'mail_ungueltig': 'Die E-Mail-Adresse sieht nicht richtig aus.',
    # Vorbereitung
    'vorb_kopf': 'Vorbereitung',
    'vorb_unter': 'Das kann der Installer nicht für dich tun, weil es außerhalb dieses Servers liegt. Wer es schon '
                  'erledigt hat, geht unten weiter.',
    'vorb_weiter': 'Erledigt, weiter',
    'vorb_fest_kopf': 'Feste Adresse für diesen Server',
    'vorb_fest_text': 'Dieser Server hat gerade die Adresse <b>{innen}</b>. Der Router darf sie ihm nicht morgen wegnehmen, '
                      'sonst laufen Portfreigaben ins Leere. Trage sie im Router als fest ein.',
    'vorb_fest_fritz': 'FRITZ!Box: Heimnetz → Netzwerk → Netzwerkverbindungen → Stift beim Gerät <b>{name}</b> → Haken bei '
                       '„Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen“ → Übernehmen. Zur Sicherheit: '
                       'die MAC-Adresse des Servers ist <b>{mac}</b>.',
    'vorb_ports_kopf': 'Portfreigaben im Router',
    'vorb_ports_text': 'Diese Ports muss der Router aus dem Internet an <b>{innen}</b> weiterleiten. Vorher kommt kein '
                       'Zertifikat zustande und kein Anruf durch.',
    'vorb_ports_proxy_text': 'Ports 80 und 443 gehen zu deinem Proxy. Für Anrufe müssen diese Ports zusätzlich direkt an '
                             'diesen Server (<b>{innen}</b>):',
    'vorb_ports_fritz': 'FRITZ!Box: Internet → Freigaben → Portfreigaben → „Gerät für Freigaben hinzufügen“ → Gerät '
                        '<b>{name}</b> wählen → „Neue Freigabe“ → „Portfreigabe“, Anwendung „Andere Anwendung“. Je Zeile '
                        'eine Freigabe; bei „TCP + UDP“ zwei. Port und „an Port“ sind gleich.',
    'vorb_port': 'Port', 'vorb_protokoll': 'Protokoll', 'vorb_wofuer': 'Wofür',
    'vorb_p80': 'Zertifikat, Weiterleitung auf https', 'vorb_p443': 'Chat, Einladungen, Verwaltung, Matrix',
    'vorb_p3478': 'Anrufe (TURN)', 'vorb_p5349': 'Anrufe (TURN verschlüsselt)', 'vorb_p49160': 'Anrufe (Relais)',
    'vorb_dyn_kopf': 'Wechselt deine öffentliche Adresse?',
    'vorb_dyn_text': 'Bei den meisten Heimanschlüssen ja, spätestens nach einer Zwangstrennung. Dann müssen die sechs Namen '
                     'der neuen Adresse folgen, sonst ist der Server nach dem Wechsel von außen weg. Beim Anbieter deiner '
                     'Domain DynDNS einschalten und im Router eintragen (FRITZ!Box: Internet → Freigaben → DynDNS). '
                     'Für die Einrichtung heute reicht es, wenn die Namen auf die aktuelle Adresse zeigen.',
    'vorb_dslite': 'Zeigt dein Router unter „Internet“ nur IPv6 oder „DS-Lite“ (häufig bei Kabelanschlüssen), kommt '
                   'von außen über IPv4 gar nichts an. Dann hilft nur ein Anschluss mit echter IPv4-Adresse oder ein '
                   'gemieteter Server.',
    'vorb_dns_kopf': 'DNS-Einträge für deine Domain',
    'vorb_dns_text': 'Beim Anbieter deiner Domain (dort, wo du sie gekauft hast) unter „DNS“ oder „Nameserver-Einträge“ '
                     'sechs A-Einträge auf <b>{ziel}</b> anlegen. Der nächste Schritt prüft sie.',
    'vorb_dns_proxy_text': 'Beim Anbieter deiner Domain sechs A-Einträge anlegen, die auf die öffentliche Adresse deines '
                           'Proxys zeigen, in der Regel <b>{ziel}</b>. Der nächste Schritt prüft, ob sie auflösen.',
    'vorb_dns_name': 'Name', 'vorb_dns_typ': 'Typ', 'vorb_dns_wert': 'Wert',
    'vorb_proxy_kopf': 'Einträge in deinem Proxy',
    'vorb_proxy_text': 'Lege für diese fünf Namen je einen Eintrag an, der auf <b>http://{innen}:80</b> zeigt, mit '
                       'Zertifikat und Websockets. Du kannst das jetzt schon tun; bis der Server fertig ist, antworten '
                       'die Einträge mit einem Fehler, das ist in Ordnung.',
    'vorb_proxy_npm': 'Nginx Proxy Manager: Hosts → Proxy Hosts → „Add Proxy Host“: Domain Names = der Name, Scheme = http, '
                      'Forward Hostname = {innen}, Forward Port = 80, „Websockets Support“ an. Reiter SSL: „Request a new '
                      'SSL Certificate“, „Force SSL“, „HTTP/2 Support“. Beim Eintrag für <b>matrix.{domain}</b> unter '
                      '„Advanced“ zusätzlich <code>client_max_body_size 100M;</code> für größere Dateien.',
    'vorb_anbieter_kopf': 'Firewall deines Anbieters',
    'vorb_anbieter_text': 'Manche Anbieter (Hetzner Cloud, Ionos, Oracle) haben eine Firewall vor dem Server, die man in '
                          'deren Oberfläche einstellt. Dort müssen diese Ports offen sein. Gibt es dort keine Firewall, '
                          'ist nichts zu tun; die Firewall auf dem Server selbst richtet der Installer ein.',
    'vorb_ssh': 'Dein SSH-Zugang',
    # DNS
    'dns_kopf': 'Zeigen die Namen hierher?',
    'dns_unter_zertifikat': 'Sechs Namen müssen beim Anbieter deiner Domain als A-Eintrag auf die '
                            'öffentliche Adresse dieses Servers zeigen: <b>{ip}</b>.',
    'dns_unter_proxy': 'Sechs Namen müssen beim Anbieter deiner Domain als A-Eintrag angelegt sein. '
                       'Hinter einem Proxy zeigen sie auf den Proxy, nicht auf diesen Server (<b>{ip}</b>).',
    'dns_ip_unbekannt': 'Die öffentliche Adresse dieses Servers ließ sich nicht ermitteln (api4.ipify.org nicht '
                        'erreichbar). Ohne Internetzugang kann der Installer nicht arbeiten.',
    'dns_name': 'Name',
    'dns_zeigt': 'zeigt auf',
    'dns_ok': 'hierher',
    'dns_fehlt': 'noch kein Eintrag',
    'dns_woanders': '{ip}',
    'dns_bereit_zertifikat': 'Alle sechs Namen zeigen hierher.',
    'dns_bereit_proxy': 'Alle sechs Namen lösen auf.',
    'dns_nicht_bereit': 'So geht es noch nicht weiter. Lege die fehlenden Einträge an und prüfe dann erneut. '
                        'Neue Einträge brauchen manchmal ein paar Minuten, bis sie überall sichtbar sind.',
    'dns_woanders_hinweis': 'Namen, die woanders hinzeigen, nimmt der Installer ohne Proxy nicht an: '
                            'Let’s Encrypt könnte das Zertifikat sonst nicht ausstellen.',
    'dns_erneut': 'Erneut prüfen',
    # Konto
    'konto_kopf': 'Das erste Konto',
    'konto_unter': 'Dieses Konto hat Verwaltungsrechte. Das Passwort erzeugt der Installer; du siehst es am Ende.',
    'verwalter': 'Benutzername',
    'verwalter_hilfe': 'Kleinbuchstaben, Ziffern, Punkt, Unterstrich, Bindestrich. Daraus wird <b>@name:{domain}</b>.',
    'verwalter_ungueltig': 'Nur Kleinbuchstaben, Ziffern und . _ - sind erlaubt, und leer geht nicht.',
    'betreiber': 'Wer betreut den Server? (freiwillig)',
    'betreiber_hilfe': 'Steht in der Kundenanleitung und in Hinweisen an deine Leute, zum Beispiel '
                       '„Anna Beispiel, hilfe@example.com“. Leer heißt „der Betreiber“.',
    # Zusammenfassung
    'pruefen_kopf': 'Alles beisammen',
    'pruefen_unter': 'Ein Blick drüber, dann geht es los.',
    'z_domain': 'Domain',
    'z_modus': 'Steht',
    'z_sit_mietserver': 'auf einem gemieteten Server, Zertifikat von Let’s Encrypt',
    'z_sit_zuhause': 'zuhause hinter einem Router, Zertifikat von Let’s Encrypt',
    'z_sit_proxy': 'hinter einem Reverse-Proxy',
    'z_aendern': 'ändern',
    'z_mail': 'E-Mail (Let’s Encrypt)',
    'z_verwalter': 'Erstes Konto',
    'z_betreiber': 'Betreiber',
    'z_sprache': 'Sprache der Seiten',
    'z_leer': 'nicht angegeben',
    'einrichten': 'Jetzt einrichten',
    'einrichten_hinweis': 'Dauert meist zwei bis fünf Minuten. Du siehst den Fortschritt; das Fenster darf '
                          'zwischendurch zugehen.',
    'aendern': 'Angaben ändern',
    'f_vanleitung': 'Verwaltungsanleitung',
    'f_vanleitung_text': 'Alles über deinen Server auf einer Seite: Adressen, Netz, Sicherung, Aktualisieren, was tun, '
                         'wenn etwas klemmt. Ohne Passwörter. Später auch in der Verwaltung zu finden.',
    'f_vanleitung_knopf': 'Verwaltungsanleitung öffnen',
    'f_erinnerung': 'Falls noch nicht erledigt',
    'f_tls_kopf': 'Noch ein Schritt: die sichere Seite',
    'f_tls_text': 'Diese Seite kommt unverschlüsselt an. Das Startpasswort steht deshalb nur auf der '
                  'verschlüsselten Fassung, mit dem frischen Zertifikat deines Servers.',
    'f_tls_knopf': 'Weiter zur sicheren Seite',
    'f_tls_fallback': 'Ist der Server gerade erst eingerichtet, kennt dein Netz die neuen Namen manchmal noch '
                      'nicht. Ein paar Minuten warten und neu laden. Sonst steht das Startpasswort auf dem '
                      'Server in <code>/root/zugangsdaten-messenger.txt</code>.',
    'f_passwort_verborgen': 'nur auf der sicheren Seite',
    # Lauf
    'lauf_kopf': 'Einrichtung läuft',
    'lauf_unter': 'Pakete, Datenbank, Matrix, Webserver, Anrufe, Chat, Einladungen, Sicherung, Updates, Härtung.',
    'lauf_schritt_aktuell': 'gerade',
    'lauf_protokoll': 'Protokoll',
    'lauf_startet': 'Der Installer startet …',
    'lauf_warten': 'Die Seite aktualisiert sich von selbst.',
    # Fehler
    'fehler_kopf': 'Das hat nicht geklappt',
    'fehler_unter': 'Der Installer hat abgebrochen, bevor etwas kaputtgehen konnte. Unten steht, woran es lag.',
    'fehler_abgebrochen': 'Der Lauf ist nicht mehr da, aber auch nicht zu Ende gekommen. Vermutlich wurde der '
                          'Server zwischendurch neu gestartet.',
    'fehler_nochmal': 'Nochmal versuchen',
    'fehler_hinweis': 'Nochmal laufen lassen schadet nicht: Der Installer macht nur, was noch fehlt. '
                      'Das vollständige Protokoll liegt in <code>{log}</code>.',
    'fehler_teils_kopf': 'Fertig, mit Einschränkungen',
    'fehler_teils_unter': 'Der Server steht und das Konto ist angelegt, aber nicht alle Prüfungen am Ende '
                          'sind durchgegangen:',
    # Fertig
    'fertig_kopf': 'Fertig',
    'fertig_unter': 'Alle Prüfungen bestanden.',
    'f_adressen': 'Adressen',
    'f_chat': 'Chat im Browser',
    'f_einladungen': 'Einladungen',
    'f_verwaltung': 'Verwaltung',
    'f_app': 'In der App als Server eintragen',
    'f_konto': 'Dein Konto',
    'f_kennung': 'Kennung',
    'f_passwort': 'Startpasswort',
    'f_passwort_hinweis': 'Gilt nur für die erste Anmeldung in der Verwaltung. Dort legst du dein eigenes '
                          'Passwort fest; bis dahin ist das Konto im Chat gesperrt.',
    'f_sicherung': 'Schlüssel der Sicherung',
    'f_sicherung_text': 'Die tägliche Sicherung ist verschlüsselt. Ohne diesen Schlüssel lässt sich keine '
                        'Sicherung zurückspielen.',
    'f_proxy': 'Jetzt der Proxy',
    'f_proxy_text': 'Dieser Server liefert Klartext auf Port 80. Lege im Proxy für diese fünf Namen je einen '
                    'Eintrag auf <b>http://{ip}:80</b> an. Das Zertifikat gehört dorthin, nicht hierher.',
    'f_router': 'Hinter einem Router',
    'f_router_text': 'Die öffentliche Adresse ({aussen}) ist nicht die des Servers ({innen}). Im Router müssen '
                     'diese Ports an {innen} weitergeleitet sein, sonst bleiben Anrufe stumm:',
    'f_router_ports': 'TCP 80 und 443 (ohne Proxy), TCP und UDP 3478 und 5349, UDP 49160 bis 49200.',
    'f_anleitung': 'Kundenanleitung',
    'f_anleitung_text': 'Eine Seite für deine Leute mit Adresse, Kontonamen und den ersten Schritten. Im Browser '
                        'öffnen, drucken oder als PDF sichern.',
    'f_anleitung_knopf': 'Kundenanleitung öffnen',
    'f_abschliessen': 'Einrichtung abschließen und zur Verwaltung',
    'f_abschliessen_hinweis': 'Schaltet diese Seite ab. In der Verwaltung meldest du dich mit dem Startpasswort an.',
    'f_pruefungen': 'Prüfungen am Ende',
    'f_tls_klemmt': 'Die sichere Seite lädt nicht?',
    'f_sicher_unter': 'Alle Prüfungen bestanden. Lege jetzt Startpasswort und Sicherungsschlüssel in deinem '
                      'Passwortdepot ab.',
    'f_details': 'Weitere Angaben: Adressen und Prüfungen',
    'f_gespeichert': 'Ich habe Startpasswort und Sicherungsschlüssel gespeichert.',
    'kopieren': 'Kopieren',
    'kopiert': 'Kopiert',
    # Abgeschaltet / schon eingerichtet
    'aus_kopf': 'Die Einrichtungsseite ist abgeschaltet',
    'aus_text': 'Ab jetzt läuft alles über die Verwaltung unter <b>https://service.{domain}</b>.',
    'schon_kopf': 'Dieser Server ist schon eingerichtet',
    'schon_text': 'Auf diesem Server läuft bereits ein Matrix-Server aus diesem Installer. Die Einrichtungsseite '
                  'ist nur für den ersten Durchgang gedacht.',
    'schon_update': 'Aktualisieren oder einen Abschnitt wiederholen geht im Terminal: '
                    '<code>cd /opt/humatrix && ./install.sh --help</code>.',
    'schon_aus': 'Diese Seite abschalten',
  },
  'en': {
    'titel': 'Set up Humatrix',
    'weiter': 'Continue',
    'zurueck': 'Back',
    'sprache_wechseln': 'Deutsch',
    'schritte': ['Server', 'Preparation', 'Check names', 'Account', 'Summary'],
    'sprache_wahl': 'Sprache / Language',
    'code_kopf': 'Welcome',
    'code_unter': 'First the one-time code. It is in the terminal where you started setup.sh.',
    'code_feld': 'One-time code',
    'code_falsch': 'That code is not right. Case and the dash do not matter.',
    'code_gesperrt': 'Too many wrong codes. The page has locked itself and generated a new code. On the '
                     'server: <code>systemctl restart humatrix-einrichtung</code>, then '
                     '<code>cat /var/lib/humatrix-einrichtung/code</code>.',
    'server_kopf': 'Your server',
    'server_unter': 'Two things that cannot be changed later.',
    'domain': 'Domain',
    'domain_hilfe': 'The bare domain, for example <b>example.com</b>. User IDs become '
                    '<b>@name:example.com</b>, and the addresses chat., invite., service. and matrix. follow from it.',
    'domain_ungueltig': 'That does not look like a domain. Expected is something like example.com, '
                        'without https:// and without a path.',
    'modus': 'Where is this server?',
    'erkannt_router': 'Detected: in its own network it has the address <b>{innen}</b>, to the outside it appears as '
                      '<b>{aussen}</b>. So it sits behind a router.',
    'erkannt_direkt': 'Detected: its address <b>{aussen}</b> is the public one. That looks like a rented server.',
    'erkannt_unbekannt': 'The public address could not be determined right now. Without internet access this cannot continue.',
    'sit_mietserver': 'Rented server (Hetzner, Ionos, Netcup …)',
    'sit_mietserver_hilfe': 'It has its own public address. Let’s Encrypt issues the certificate.',
    'sit_zuhause': 'At home or in the office behind a router (FRITZ!Box …)',
    'sit_zuhause_hilfe': 'The router has to forward a few ports to this server. The next step shows exactly which. '
                         'Let’s Encrypt issues the certificate.',
    'sit_proxy': 'Behind a reverse proxy that already handles certificates',
    'sit_proxy_hilfe': 'Nginx Proxy Manager, Traefik, Caddy. This server then serves plain HTTP on port 80; the proxy '
                       'needs five entries. The next step lists them.',
    'mail': 'E-mail address for Let’s Encrypt',
    'mail_hilfe': 'Let’s Encrypt writes there if something is wrong with the certificate. Only needed for the first case.',
    'mail_fehlt': 'For its own certificate Let’s Encrypt needs an e-mail address.',
    'mail_ungueltig': 'That e-mail address does not look right.',
    # Vorbereitung
    'vorb_kopf': 'Preparation',
    'vorb_unter': 'The installer cannot do this for you, because it lies outside this server. If you have done it '
                  'already, continue at the bottom.',
    'vorb_weiter': 'Done, continue',
    'vorb_fest_kopf': 'Fixed address for this server',
    'vorb_fest_text': 'This server currently has the address <b>{innen}</b>. The router must not take it away tomorrow, '
                      'or the port forwards point nowhere. Mark it as fixed in the router.',
    'vorb_fest_fritz': 'FRITZ!Box: Home Network → Network → Network Connections → pencil at the device <b>{name}</b> → tick '
                       '"Always assign this network device the same IPv4 address" → Apply. To be sure: the MAC address '
                       'of the server is <b>{mac}</b>.',
    'vorb_ports_kopf': 'Port forwarding in the router',
    'vorb_ports_text': 'The router must forward these ports from the internet to <b>{innen}</b>. Before that there is no '
                       'certificate and no call gets through.',
    'vorb_ports_proxy_text': 'Ports 80 and 443 go to your proxy. For calls these ports must additionally go directly to '
                             'this server (<b>{innen}</b>):',
    'vorb_ports_fritz': 'FRITZ!Box: Internet → Permit Access → Port Sharing → "Add Device for Sharing" → choose device '
                        '<b>{name}</b> → "New Sharing" → "Port Sharing", application "Other application". One sharing per '
                        'row; two for "TCP + UDP". Port and "to port" are the same.',
    'vorb_port': 'Port', 'vorb_protokoll': 'Protocol', 'vorb_wofuer': 'Purpose',
    'vorb_p80': 'certificate, redirect to https', 'vorb_p443': 'chat, invitations, admin page, Matrix',
    'vorb_p3478': 'calls (TURN)', 'vorb_p5349': 'calls (TURN over TLS)', 'vorb_p49160': 'calls (relay)',
    'vorb_dyn_kopf': 'Does your public address change?',
    'vorb_dyn_text': 'On most home connections yes, at the latest after a forced reconnect. Then the six names must follow '
                     'the new address, otherwise the server is gone from outside after the change. Enable DynDNS at your '
                     'domain provider and enter it in the router (FRITZ!Box: Internet → Permit Access → DynDNS). For '
                     'today’s setup it is enough that the names point to the current address.',
    'vorb_dslite': 'If your router shows only IPv6 or "DS-Lite" under "Internet" (common on cable connections), nothing '
                   'arrives from outside over IPv4. Then only a connection with a real IPv4 address or a rented server helps.',
    'vorb_dns_kopf': 'DNS records for your domain',
    'vorb_dns_text': 'At your domain provider (where you bought it), under "DNS" or "name server records", create six A '
                     'records pointing to <b>{ziel}</b>. The next step checks them.',
    'vorb_dns_proxy_text': 'At your domain provider create six A records pointing to the public address of your proxy, '
                           'usually <b>{ziel}</b>. The next step checks that they resolve.',
    'vorb_dns_name': 'Name', 'vorb_dns_typ': 'Type', 'vorb_dns_wert': 'Value',
    'vorb_proxy_kopf': 'Entries in your proxy',
    'vorb_proxy_text': 'Create one entry per name for these five names, pointing to <b>http://{innen}:80</b>, with '
                       'certificate and websockets. You can do that now; until the server is ready the entries answer '
                       'with an error, which is fine.',
    'vorb_proxy_npm': 'Nginx Proxy Manager: Hosts → Proxy Hosts → "Add Proxy Host": Domain Names = the name, Scheme = http, '
                      'Forward Hostname = {innen}, Forward Port = 80, "Websockets Support" on. Tab SSL: "Request a new '
                      'SSL Certificate", "Force SSL", "HTTP/2 Support". On the entry for <b>matrix.{domain}</b> add '
                      '<code>client_max_body_size 100M;</code> under "Advanced" for larger files.',
    'vorb_anbieter_kopf': 'Your provider’s firewall',
    'vorb_anbieter_text': 'Some providers (Hetzner Cloud, Ionos, Oracle) have a firewall in front of the server, configured '
                          'in their panel. These ports must be open there. If there is no such firewall, nothing to do; '
                          'the firewall on the server itself is set up by the installer.',
    'vorb_ssh': 'Your SSH access',
    # DNS
    'dns_kopf': 'Do the names point here?',
    'dns_unter_zertifikat': 'Six names must exist as A records at your domain provider, pointing to the public '
                            'address of this server: <b>{ip}</b>.',
    'dns_unter_proxy': 'Six names must exist as A records at your domain provider. Behind a proxy they point '
                       'to the proxy, not to this server (<b>{ip}</b>).',
    'dns_ip_unbekannt': 'The public address of this server could not be determined (api4.ipify.org not '
                        'reachable). Without internet access the installer cannot work.',
    'dns_name': 'Name',
    'dns_zeigt': 'points to',
    'dns_ok': 'here',
    'dns_fehlt': 'no record yet',
    'dns_woanders': '{ip}',
    'dns_bereit_zertifikat': 'All six names point here.',
    'dns_bereit_proxy': 'All six names resolve.',
    'dns_nicht_bereit': 'Not ready yet. Create the missing records, then check again. New records sometimes '
                        'take a few minutes to become visible everywhere.',
    'dns_woanders_hinweis': 'Names that point elsewhere are not accepted without a proxy: Let’s Encrypt could '
                            'not issue the certificate.',
    'dns_erneut': 'Check again',
    'konto_kopf': 'The first account',
    'konto_unter': 'This account has admin rights. The installer generates the password; you will see it at the end.',
    'verwalter': 'Username',
    'verwalter_hilfe': 'Lowercase letters, digits, dot, underscore, dash. It becomes <b>@name:{domain}</b>.',
    'verwalter_ungueltig': 'Only lowercase letters, digits and . _ - are allowed, and it cannot be empty.',
    'betreiber': 'Who looks after the server? (optional)',
    'betreiber_hilfe': 'Appears in the customer guide and in notes to your people, for example '
                       '“Anna Example, help@example.com”. Empty means “the operator”.',
    'pruefen_kopf': 'All set',
    'pruefen_unter': 'One look over it, then it starts.',
    'z_domain': 'Domain',
    'z_modus': 'Located',
    'z_sit_mietserver': 'on a rented server, certificate from Let’s Encrypt',
    'z_sit_zuhause': 'at home behind a router, certificate from Let’s Encrypt',
    'z_sit_proxy': 'behind a reverse proxy',
    'z_aendern': 'change',
    'z_mail': 'E-mail (Let’s Encrypt)',
    'z_verwalter': 'First account',
    'z_betreiber': 'Operator',
    'z_sprache': 'Language of the pages',
    'z_leer': 'not given',
    'einrichten': 'Set up now',
    'einrichten_hinweis': 'Usually takes two to five minutes. You will see the progress; the window may be '
                          'closed in between.',
    'aendern': 'Change details',
    'f_vanleitung': 'Operator guide',
    'f_vanleitung_text': 'Everything about your server on one page: addresses, network, backup, updating, what to do '
                         'when something is stuck. No passwords. Later also available in the admin page.',
    'f_vanleitung_knopf': 'Open operator guide',
    'f_erinnerung': 'If not done yet',
    'f_tls_kopf': 'One more step: the secure page',
    'f_tls_text': 'This page arrives unencrypted. The initial password is therefore only on the '
                  'encrypted version, using your server’s fresh certificate.',
    'f_tls_knopf': 'Continue to the secure page',
    'f_tls_fallback': 'Right after setup your network sometimes does not know the new names yet. Wait a few '
                      'minutes and reload. Otherwise the initial password is on the server in '
                      '<code>/root/zugangsdaten-messenger.txt</code>.',
    'f_passwort_verborgen': 'only on the secure page',
    'lauf_kopf': 'Setting up',
    'lauf_unter': 'Packages, database, Matrix, web server, calls, chat, invitations, backup, updates, hardening.',
    'lauf_schritt_aktuell': 'now',
    'lauf_protokoll': 'Log',
    'lauf_startet': 'The installer is starting …',
    'lauf_warten': 'This page refreshes by itself.',
    'fehler_kopf': 'That did not work',
    'fehler_unter': 'The installer stopped before anything could break. Below is what it ran into.',
    'fehler_abgebrochen': 'The run is gone but did not finish. Most likely the server was restarted in between.',
    'fehler_nochmal': 'Try again',
    'fehler_hinweis': 'Running it again does no harm: the installer only does what is still missing. '
                      'The full log is in <code>{log}</code>.',
    'fehler_teils_kopf': 'Done, with reservations',
    'fehler_teils_unter': 'The server is up and the account exists, but not all final checks passed:',
    'fertig_kopf': 'Done',
    'fertig_unter': 'All checks passed.',
    'f_adressen': 'Addresses',
    'f_chat': 'Chat in the browser',
    'f_einladungen': 'Invitations',
    'f_verwaltung': 'Administration',
    'f_app': 'Server to enter in the app',
    'f_konto': 'Your account',
    'f_kennung': 'User ID',
    'f_passwort': 'Initial password',
    'f_passwort_hinweis': 'Only valid for the first sign-in on the admin page. There you choose your own '
                          'password; until then the account is locked in the chat.',
    'f_sicherung': 'Backup key',
    'f_sicherung_text': 'The daily backup is encrypted. Without this key no backup can be restored.',
    'f_proxy': 'Now the proxy',
    'f_proxy_text': 'This server serves plain HTTP on port 80. In the proxy, create one entry per name for '
                    'these five names, pointing to <b>http://{ip}:80</b>. The certificate belongs there, not here.',
    'f_router': 'Behind a router',
    'f_router_text': 'The public address ({aussen}) is not the server’s own ({innen}). The router must forward '
                     'these ports to {innen}, otherwise calls stay silent:',
    'f_router_ports': 'TCP 80 and 443 (without proxy), TCP and UDP 3478 and 5349, UDP 49160 to 49200.',
    'f_anleitung': 'Customer guide',
    'f_anleitung_text': 'A page for your people with the address, the account name and the first steps. Open it '
                        'in the browser, print it or save it as PDF.',
    'f_anleitung_knopf': 'Open customer guide',
    'f_abschliessen': 'Finish setup and go to the admin page',
    'f_abschliessen_hinweis': 'Turns this page off. On the admin page you sign in with the initial password.',
    'f_pruefungen': 'Final checks',
    'f_tls_klemmt': 'The secure page does not load?',
    'f_sicher_unter': 'All checks passed. Put the initial password and the backup key into your password manager now.',
    'f_details': 'More: addresses and checks',
    'f_gespeichert': 'I have saved the initial password and the backup key.',
    'kopieren': 'Copy',
    'kopiert': 'Copied',
    'aus_kopf': 'The setup page is off',
    'aus_text': 'From now on everything happens in the admin page at <b>https://service.{domain}</b>.',
    'schon_kopf': 'This server is already set up',
    'schon_text': 'A Matrix server from this installer is already running here. The setup page is only for '
                  'the first run.',
    'schon_update': 'Updating or repeating a section is done in the terminal: '
                    '<code>cd /opt/humatrix && ./install.sh --help</code>.',
    'schon_aus': 'Turn this page off',
  },
}


def t(schluessel, sprache, **werte):
    text = TEXTE[sprache if sprache in TEXTE else 'en'][schluessel]
    return text.format(**werte) if werte else text


def sprache_aus_header(kopf):
    for teil in (kopf or '').split(','):
        code = teil.split(';')[0].strip().lower()
        if code.startswith('de'):
            return 'de'
        if code.startswith('en'):
            return 'en'
    return 'en'


# Seiten
STIL = """
:root{--grund:#f4f6f4;--flaeche:#fff;--schrift:#16201c;--leise:#5a6762;
       --linie:#d6dcd6;--eigen:#0e7c63;--warn:#a8551c}
@media (prefers-color-scheme:dark){:root{--grund:#0f1412;--flaeche:#171f1c;
       --schrift:#e7ede9;--leise:#97a49e;--linie:#2a3430;--eigen:#45c3a3;--warn:#e09a4c}}
*{box-sizing:border-box}
body{margin:0;background:var(--grund);color:var(--schrift);
     font:16px/1.6 system-ui,-apple-system,sans-serif;padding:24px 16px 56px}
.k{max-width:600px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
h1{font-size:28px;line-height:1.15;margin:0}
h2{font-size:19px;margin:0 0 6px}
p{margin:0}
.karte{background:var(--flaeche);border:1px solid var(--linie);border-radius:14px;padding:20px}
label{display:block;font-size:14px;color:var(--leise);margin-bottom:5px}
.hilfe{font-size:14px;color:var(--leise);margin:6px 0 16px}
input[type=text],input[type=email]{width:100%;padding:13px;font:inherit;border:1.5px solid var(--linie);
      border-radius:9px;background:var(--grund);color:var(--schrift)}
button,.knopf{display:block;width:100%;padding:15px;font:inherit;font-weight:600;
      border:0;border-radius:9px;background:var(--eigen);color:#fff;cursor:pointer;
      text-align:center;text-decoration:none;margin-top:14px}
button:hover,.knopf:hover{filter:brightness(1.08)}
.still{background:transparent;color:var(--eigen);border:1.5px solid var(--eigen)}
.fehler{background:#fdecea;color:#9b2214;border-radius:9px;padding:12px;font-size:15px}
@media (prefers-color-scheme:dark){.fehler{background:#3a1512;color:#f7b4a8}}
.wert{font-family:ui-monospace,monospace;font-size:19px;font-weight:600;
      background:var(--grund);border:1.5px dashed var(--linie);border-radius:9px;
      padding:13px;word-break:break-all;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:14.5px;margin-top:10px}
td{padding:9px 0;border-bottom:1px solid var(--linie);vertical-align:top}
td:first-child{color:var(--leise);width:42%}
.leise{color:var(--leise);font-size:14px}
.ok{color:var(--eigen);font-weight:600}
.warn{color:var(--warn);font-weight:600}
.wahl{display:flex;gap:10px;align-items:flex-start;padding:12px;border:1.5px solid var(--linie);
      border-radius:9px;margin-bottom:10px;cursor:pointer}
.wahl input{margin-top:5px}
.wahl b{font-weight:600}
.wahl .hilfe{margin:2px 0 0}
.kopfzeile{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.kopfzeile a{color:var(--leise);font-size:14px;text-decoration:none}
.pfad{display:flex;gap:6px;flex-wrap:wrap;font-size:13px;color:var(--leise)}
.pfad span{padding:2px 9px;border:1px solid var(--linie);border-radius:20px}
.pfad span.hier{color:var(--eigen);border-color:var(--eigen);font-weight:600}
.pfad span.war{color:var(--eigen)}
pre{background:var(--grund);border:1px solid var(--linie);border-radius:9px;padding:12px;
    font-size:12.5px;line-height:1.45;overflow:auto;max-height:340px;margin:10px 0 0;white-space:pre-wrap}
ul.schritte{list-style:none;margin:10px 0 0;padding:0}
ul.schritte li{padding:6px 0;border-bottom:1px solid var(--linie)}
ul.schritte li.aktiv{color:var(--eigen);font-weight:600}
code{font-family:ui-monospace,monospace;font-size:14px;background:var(--grund);padding:1px 5px;border-radius:5px}
.sprachen{display:flex;gap:14px;align-items:center;font-size:14px}
.sprachen a{color:var(--eigen);text-decoration:none;padding:3px 10px;border:1px solid var(--linie);border-radius:20px}
.sprachen a.hier{border-color:var(--eigen);font-weight:600}
table.ports td:first-child{width:28%;color:var(--schrift);font-family:ui-monospace,monospace}
.erkannt{font-size:14px;color:var(--leise);background:var(--grund);border-radius:9px;padding:10px 12px;margin-bottom:14px}
.aendern{font-size:13px;color:var(--eigen);text-decoration:none;margin-left:8px;white-space:nowrap}
.kopierbar{display:flex;gap:10px;align-items:center;justify-content:space-between}
.kopierbar span{word-break:break-all}
button.kopieren{width:auto;margin:0;padding:6px 12px;font-size:13px;flex:none}
button:disabled{opacity:.45;cursor:not-allowed}
details summary{cursor:pointer;margin:14px 0}
"""


# Tab-Symbol: weiße Sprechblase auf der Akzentfarbe, als data:-URI, damit die
# Seite keine zweite Datei ausliefern muss.
ICON = ("data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        "%3Crect width='32' height='32' rx='7' fill='%2345c3a3'/%3E"
        "%3Cpath d='M9 9h14a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-8l-5 4v-4H9a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3z' fill='%23fff'/%3E"
        "%3C/svg%3E")


def seite(titel, inhalt, sprache, kopf_extra=''):
    return f"""<!doctype html><html lang="{sprache}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{ICON}">
<title>{html.escape(titel)}</title><style>{STIL}</style>{kopf_extra}</head>
<body><div class="k">{inhalt}</div></body></html>""".encode()


def kopfzeile(sprache, pfad='', umschalten=True):
    # Ab dem Start von install.sh kein Sprachschalter mehr: Schritte, Protokoll
    # und Ergebnis kommen in der gewählten Sprache aus install.sh.
    if not umschalten:
        return '<div class="kopfzeile"><span class="leise">Humatrix</span></div>'
    andere = 'en' if sprache == 'de' else 'de'
    return (f'<div class="kopfzeile"><span class="leise">Humatrix</span>'
            f'<a href="/sprache?zu={andere}&amp;dann={urllib.parse.quote(pfad or "/")}">'
            f'{t("sprache_wechseln", sprache)}</a></div>')


def pfadleiste(sprache, hier):
    teile = []
    for i, name in enumerate(t('schritte', sprache), 1):
        klasse = 'hier' if i == hier else ('war' if i < hier else '')
        teile.append(f'<span class="{klasse}">{i}. {html.escape(name)}</span>')
    return '<div class="pfad">' + ''.join(teile) + '</div>'


def fehlerkasten(text):
    return f'<p class="fehler">{text}</p>' if text else ''


def seite_sprache():
    """Allererste Seite, zweisprachig: erst die Sprache, dann alles andere darin."""
    return seite('Humatrix', """
      <div class="kopfzeile"><span class="leise">Humatrix</span></div>
      <h1>Sprache wählen<br>Choose your language</h1>
      <p class="leise">Alles Weitere erscheint in dieser Sprache. · Everything that follows will be in this language.</p>
      <div class="karte">
        <a class="knopf" href="/sprache?zu=de&amp;dann=/" style="margin-top:0">Deutsch</a>
        <a class="knopf" href="/sprache?zu=en&amp;dann=/">English</a>
      </div>""", 'de')


def seite_code(sprache, fehler=''):
    if VERSUCHE['gesperrt']:
        fehler = t('code_gesperrt', sprache)
        formular = ''
    else:
        formular = f"""
      <form method="post" action="/code" class="karte">
        <label for="c">{t('code_feld', sprache)}</label>
        <input id="c" name="code" type="text" autocomplete="off" autocapitalize="characters"
               spellcheck="false" placeholder="ABCD-1234" required autofocus>
        <button type="submit">{t('weiter', sprache)}</button>
      </form>"""
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache)}
      <h1>{t('code_kopf', sprache)}</h1>
      <p class="leise">{t('code_unter', sprache)}</p>
      {fehlerkasten(fehler)}
      {formular}""", sprache)


def seite_server(sprache, a, fehler=''):
    innen, aussen = lokale_ip(), oeffentliche_ip()
    if not aussen:
        erkannt = t('erkannt_unbekannt', sprache); vorschlag = 'zuhause'
    elif innen and innen != aussen:
        erkannt = t('erkannt_router', sprache, innen=html.escape(innen), aussen=html.escape(aussen)); vorschlag = 'zuhause'
    else:
        erkannt = t('erkannt_direkt', sprache, aussen=html.escape(aussen)); vorschlag = 'mietserver'
    gewaehlt = a.get('situation') or vorschlag
    wahlen = ''.join(f"""
        <label class="wahl"><input type="radio" name="situation" value="{sit}" {'checked' if sit == gewaehlt else ''}>
          <span><b>{t('sit_' + sit, sprache)}</b><p class="hilfe">{t('sit_' + sit + '_hilfe', sprache)}</p></span></label>"""
        for sit in SITUATIONEN)
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/server')}
      {pfadleiste(sprache, 1)}
      <h1>{t('server_kopf', sprache)}</h1>
      <p class="leise">{t('server_unter', sprache)}</p>
      {fehlerkasten(fehler)}
      <form method="post" action="/server" class="karte">
        <label for="d">{t('domain', sprache)}</label>
        <input id="d" name="domain" type="text" value="{html.escape(a.get('domain', ''))}"
               autocapitalize="none" autocomplete="off" spellcheck="false" placeholder="beispieldomain.de" required>
        <p class="hilfe">{t('domain_hilfe', sprache)}</p>
        <label>{t('modus', sprache)}</label>
        <p class="erkannt">{erkannt}</p>
        {wahlen}
        <div style="height:8px"></div>
        <label for="m">{t('mail', sprache)}</label>
        <input id="m" name="mail" type="email" value="{html.escape(a.get('mail', ''))}" autocomplete="email">
        <p class="hilfe">{t('mail_hilfe', sprache)}</p>
        <button type="submit">{t('weiter', sprache)}</button>
      </form>""", sprache)


def ports_tabelle(sprache, mit_web):
    zeilen = []
    if mit_web:
        zeilen += [('80', 'TCP', 'vorb_p80'), ('443', 'TCP', 'vorb_p443')]
    zeilen += [('3478', 'TCP + UDP', 'vorb_p3478')]
    if mit_web:
        zeilen += [('5349', 'TCP + UDP', 'vorb_p5349')]     # TURN über TLS: nur mit eigenem Zertifikat
    zeilen += [('49160–49200', 'UDP', 'vorb_p49160')]
    return (f'<table class="ports"><tr><td class="leise">{t("vorb_port", sprache)}</td><td>{t("vorb_protokoll", sprache)}</td>'
            f'<td>{t("vorb_wofuer", sprache)}</td></tr>'
            + ''.join(f'<tr><td>{p}</td><td>{pr}</td><td>{t(k, sprache)}</td></tr>' for p, pr, k in zeilen) + '</table>')


def dns_tabelle(sprache, domain, ziel):
    return (f'<table><tr><td class="leise">{t("vorb_dns_name", sprache)}</td><td>{t("vorb_dns_typ", sprache)} / '
            f'{t("vorb_dns_wert", sprache)}</td></tr>'
            + ''.join(f'<tr><td>{html.escape(p + domain)}</td><td>A → {html.escape(ziel)}</td></tr>' for p in PRAEFIXE)
            + '</table>')


def seite_vorbereitung(sprache, a):
    """Was ausserhalb dieses Servers zu tun ist, je nach Situation - VOR der Pruefung."""
    sit = a.get('situation', 'mietserver')
    d = a['domain']
    innen, aussen = lokale_ip(), oeffentliche_ip()
    name, mac = geraet()
    w = dict(innen=html.escape(innen or '?'), aussen=html.escape(aussen or '?'), name=html.escape(name or '?'),
             mac=html.escape(mac or '?'), domain=html.escape(d), ziel=html.escape(aussen or '?'))
    karten = []
    if sit in ('zuhause', 'proxy') and innen and innen != aussen:
        karten.append(f"""<div class="karte"><h2>{t('vorb_fest_kopf', sprache)}</h2>
          <p class="leise">{t('vorb_fest_text', sprache, **w)}</p>
          <p class="hilfe" style="margin:10px 0 0">{t('vorb_fest_fritz', sprache, **w)}</p></div>""")
    if sit == 'zuhause':
        karten.append(f"""<div class="karte"><h2>{t('vorb_ports_kopf', sprache)}</h2>
          <p class="leise">{t('vorb_ports_text', sprache, **w)}</p>{ports_tabelle(sprache, True)}
          <p class="hilfe" style="margin:12px 0 0">{t('vorb_ports_fritz', sprache, **w)}</p></div>""")
        karten.append(f"""<div class="karte"><h2>{t('vorb_dyn_kopf', sprache)}</h2>
          <p class="leise">{t('vorb_dyn_text', sprache)}</p>
          <p class="hilfe" style="margin:10px 0 0">{t('vorb_dslite', sprache)}</p></div>""")
    if sit == 'proxy':
        karten.append(f"""<div class="karte"><h2>{t('vorb_proxy_kopf', sprache)}</h2>
          <p class="leise">{t('vorb_proxy_text', sprache, **w)}</p>
          <ul>{''.join(f'<li>{html.escape(p + d)}</li>' for p in PRAEFIXE[:5])}</ul>
          <p class="hilfe" style="margin:10px 0 0">{t('vorb_proxy_npm', sprache, **w)}</p></div>""")
        if innen and innen != aussen:
            karten.append(f"""<div class="karte"><h2>{t('vorb_ports_kopf', sprache)}</h2>
              <p class="leise">{t('vorb_ports_proxy_text', sprache, **w)}</p>{ports_tabelle(sprache, False)}
              <p class="hilfe" style="margin:12px 0 0">{t('vorb_ports_fritz', sprache, **w)}</p></div>""")
    if sit == 'mietserver':
        karten.append(f"""<div class="karte"><h2>{t('vorb_anbieter_kopf', sprache)}</h2>
          <p class="leise">{t('vorb_anbieter_text', sprache)}</p>{ports_tabelle(sprache, True)}</div>""")
    dns_text = t('vorb_dns_proxy_text' if sit == 'proxy' else 'vorb_dns_text', sprache, **w)
    karten.append(f"""<div class="karte"><h2>{t('vorb_dns_kopf', sprache)}</h2>
      <p class="leise">{dns_text}</p>{dns_tabelle(sprache, d, aussen or '?')}</div>""")
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/vorbereitung')}
      {pfadleiste(sprache, 2)}
      <h1>{t('vorb_kopf', sprache)}</h1>
      <p class="leise">{t('vorb_unter', sprache)}</p>
      {''.join(karten)}
      <form method="post" action="/vorbereitung"><button type="submit">{t('vorb_weiter', sprache)}</button></form>
      <a class="knopf still" href="/server">{t('zurueck', sprache)}</a>""", sprache)


def seite_dns(sprache, a):
    eintraege, bereit, eigene = dns_pruefen(a['domain'], a.get('situation'))
    proxy = a.get('situation') == 'proxy'
    zeilen = []
    for name, ziel, zustand in eintraege:
        if zustand == 'ok':
            wert = f'<span class="ok">{t("dns_ok", sprache)}</span>'
        elif zustand == 'fehlt':
            wert = f'<span class="warn">{t("dns_fehlt", sprache)}</span>'
        else:
            klasse = 'ok' if proxy else 'warn'
            wert = f'<span class="{klasse}">{html.escape(ziel)}</span>'
        zeilen.append(f'<tr><td>{html.escape(name)}</td><td>{wert}</td></tr>')
    if not eigene:
        urteil = fehlerkasten(t('dns_ip_unbekannt', sprache))
    elif bereit:
        urteil = f'<p class="ok">{t("dns_bereit_proxy" if proxy else "dns_bereit_zertifikat", sprache)}</p>'
    else:
        zusatz = '' if proxy else f' {t("dns_woanders_hinweis", sprache)}'
        urteil = fehlerkasten(t('dns_nicht_bereit', sprache) + zusatz)
    weiter = (f'<form method="post" action="/dns"><button type="submit">{t("weiter", sprache)}</button></form>'
              if bereit else '')
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/dns')}
      {pfadleiste(sprache, 3)}
      <h1>{t('dns_kopf', sprache)}</h1>
      <p class="leise">{t('dns_unter_proxy' if proxy else 'dns_unter_zertifikat', sprache, ip=html.escape(eigene or '?'))}</p>
      <div class="karte">
        <table><tr><td>{t('dns_name', sprache)}</td><td>{t('dns_zeigt', sprache)}</td></tr>{''.join(zeilen)}</table>
        <div style="height:14px"></div>
        {urteil}
        <a class="knopf still" href="/dns">{t('dns_erneut', sprache)}</a>
        {weiter}
      </div>
      <a class="knopf still" href="/vorbereitung">{t('zurueck', sprache)}</a>""", sprache)


def seite_konto(sprache, a, fehler=''):
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/konto')}
      {pfadleiste(sprache, 4)}
      <h1>{t('konto_kopf', sprache)}</h1>
      <p class="leise">{t('konto_unter', sprache)}</p>
      {fehlerkasten(fehler)}
      <form method="post" action="/konto" class="karte">
        <label for="v">{t('verwalter', sprache)}</label>
        <input id="v" name="verwalter" type="text" value="{html.escape(a.get('verwalter', ''))}"
               autocapitalize="none" autocomplete="off" spellcheck="false" placeholder="anna" required>
        <p class="hilfe">{t('verwalter_hilfe', sprache, domain=html.escape(a.get('domain', '')))}</p>
        <label for="b">{t('betreiber', sprache)}</label>
        <input id="b" name="betreiber" type="text" value="{html.escape(a.get('betreiber', ''))}" autocomplete="off">
        <p class="hilfe">{t('betreiber_hilfe', sprache)}</p>
        <button type="submit">{t('weiter', sprache)}</button>
      </form>
      <a class="knopf still" href="/dns">{t('zurueck', sprache)}</a>""", sprache)


def seite_pruefen(sprache, a):
    sit = a.get('situation', 'mietserver')
    leer = f'<span class="leise">{t("z_leer", sprache)}</span>'
    def zeile(schluessel, wert, ziel):
        return (f'<tr><td>{t(schluessel, sprache)}</td><td>{wert}'
                f'<a class="aendern" href="{ziel}">{t("z_aendern", sprache)}</a></td></tr>')
    zeilen = [zeile('z_domain', html.escape(a['domain']), '/server'),
              zeile('z_modus', t('z_sit_' + sit, sprache), '/server')]
    if sit != 'proxy':
        zeilen.append(zeile('z_mail', html.escape(a.get('mail', '')), '/server'))
    zeilen += [zeile('z_verwalter', f"@{html.escape(a['verwalter'])}:{html.escape(a['domain'])}", '/konto'),
               zeile('z_betreiber', html.escape(a.get('betreiber', '')) or leer, '/konto'),
               zeile('z_sprache', 'Deutsch' if sprache == 'de' else 'English',
                     f"/sprache?zu={'en' if sprache == 'de' else 'de'}&amp;dann=/pruefen")]
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/pruefen')}
      {pfadleiste(sprache, 5)}
      <h1>{t('pruefen_kopf', sprache)}</h1>
      <p class="leise">{t('pruefen_unter', sprache)}</p>
      <div class="karte">
        <table>{''.join(zeilen)}</table>
        <form method="post" action="/start">
          <button type="submit">{t('einrichten', sprache)}</button>
        </form>
        <p class="hilfe" style="margin:10px 0 0">{t('einrichten_hinweis', sprache)}</p>
      </div>
      <a class="knopf still" href="/konto">{t('zurueck', sprache)}</a>""", sprache)


def seite_lauf(sprache, stand):
    schritte = stand['schritte']
    punkte = ''.join(f'<li class="{"aktiv" if i == len(schritte) - 1 else ""}">{html.escape(s)}</li>'
                     for i, s in enumerate(schritte)) or f'<li class="aktiv">{t("lauf_startet", sprache)}</li>'
    protokoll = html.escape('\n'.join(stand['zeilen'][-80:]))
    skript = """<script>
(function(){
  function tick(){
    fetch('/stand',{cache:'no-store'}).then(function(r){if(r.status===401){location.reload();return null;}return r.json()}).then(function(d){
      if(!d){return;}
      if(d.weiter){location.href=d.weiter;return;}
      var ul=document.getElementById('schritte');
      if(d.schritte.length){ ul.innerHTML=''; }
      d.schritte.forEach(function(s,i){var li=document.createElement('li');
        li.textContent=s; if(i==d.schritte.length-1) li.className='aktiv'; ul.appendChild(li);});
      var pre=document.getElementById('protokoll'); pre.textContent=d.text; pre.scrollTop=pre.scrollHeight;
      setTimeout(tick,2000);
    }).catch(function(){setTimeout(tick,4000)});
  }
  setTimeout(tick,2000);
})();
</script>"""
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/lauf', umschalten=False)}
      <h1>{t('lauf_kopf', sprache)}</h1>
      <p class="leise">{t('lauf_unter', sprache)}</p>
      <div class="karte">
        <ul class="schritte" id="schritte">{punkte}</ul>
        <h2 style="margin-top:18px">{t('lauf_protokoll', sprache)}</h2>
        <pre id="protokoll">{protokoll}</pre>
        <p class="hilfe" style="margin:10px 0 0">{t('lauf_warten', sprache)}</p>
      </div>
      <noscript><meta http-equiv="refresh" content="5"></noscript>""", sprache,
                 kopf_extra=skript + '<noscript><meta http-equiv="refresh" content="5"></noscript>')


def seite_fehler(sprache, stand, a):
    zeilen = stand['zeilen']
    abbruch = abbruchzeile(zeilen)
    gut, schlecht = nachkontrolle(zeilen)
    teils = bool(schlecht) and os.path.exists(ZUGANG) and os.path.getmtime(ZUGANG) >= stand.get('begonnen', 0) - 5
    if teils:
        kopf, unter = t('fehler_teils_kopf', sprache), t('fehler_teils_unter', sprache)
        liste = '<ul>' + ''.join(f'<li class="warn">{html.escape(s)}</li>' for s in schlecht) + '</ul>'
        zugang = block_zugang(sprache, a)
    else:
        kopf = t('fehler_kopf', sprache)
        unter = t('fehler_abgebrochen', sprache) if stand['zustand'] == 'abgebrochen' else t('fehler_unter', sprache)
        liste = fehlerkasten(html.escape(abbruch)) if abbruch else ''
        zugang = ''
    protokoll = html.escape('\n'.join(zeilen[-60:]))
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/fehler', umschalten=False)}
      <h1>{kopf}</h1>
      <p class="leise">{unter}</p>
      {liste}
      {zugang}
      <div class="karte">
        <h2>{t('lauf_protokoll', sprache)}</h2>
        <pre>{protokoll}</pre>
        <p class="hilfe" style="margin:10px 0 0">{t('fehler_hinweis', sprache, log=INSTALLER_LOG)}</p>
        <form method="post" action="/start"><button type="submit">{t('fehler_nochmal', sprache)}</button></form>
      </div>
      <a class="knopf still" href="/server">{t('aendern', sprache)}</a>""", sprache)


def block_adressen(sprache, a):
    d = a['domain']
    return f"""
        <h2>{t('f_adressen', sprache)}</h2>
        <table>
          <tr><td>{t('f_chat', sprache)}</td><td><a href="https://chat.{d}">https://chat.{d}</a></td></tr>
          <tr><td>{t('f_einladungen', sprache)}</td><td><a href="https://invite.{d}">https://invite.{d}</a></td></tr>
          <tr><td>{t('f_verwaltung', sprache)}</td><td><a href="https://service.{d}">https://service.{d}</a></td></tr>
          <tr><td>{t('f_app', sprache)}</td><td>{d}</td></tr>
        </table>"""


def wert_kopierbar(wert, sprache):
    return (f'<div class="wert kopierbar"><span>{html.escape(wert)}</span>'
            f'<button type="button" class="kopieren" data-fertig="{t("kopiert", sprache)}">'
            f'{t("kopieren", sprache)}</button></div>')


SKRIPT_FERTIG = """<script>
(function(){
  document.querySelectorAll('.kopieren').forEach(function(k){
    k.addEventListener('click',function(){
      var w=k.parentNode.querySelector('span').textContent, alt=k.textContent;
      navigator.clipboard.writeText(w).then(function(){k.textContent=k.dataset.fertig;
        setTimeout(function(){k.textContent=alt},1500)});
    });
  });
  var h=document.getElementById('gespeichert'), b=document.getElementById('abschliessen');
  if(h&&b){ b.disabled=!h.checked; h.addEventListener('change',function(){b.disabled=!h.checked}); }
})();
</script>"""


def seite_fertig(sprache, stand, a, sicher=True):
    d = a['domain']
    gut, schlecht = nachkontrolle(stand['zeilen'])
    proxy = a.get('situation') == 'proxy'
    innen, aussen = lokale_ip(), oeffentliche_ip()

    # Erinnerungen, die außerhalb des Servers zu erledigen sind, bleiben sichtbar.
    erinnerungen = ''
    if proxy:
        namen = ''.join(f'<li>{p}{d}</li>' for p in PRAEFIXE[:5])
        erinnerungen += f"""
      <div class="karte">
        <h2>{t('f_proxy', sprache)} <span class="leise" style="font-weight:400">· {t('f_erinnerung', sprache)}</span></h2>
        <p class="leise">{t('f_proxy_text', sprache, ip=html.escape(innen or '?'))}</p>
        <ul>{namen}</ul>
      </div>"""
    if innen and aussen and innen != aussen and not proxy:
        erinnerungen += f"""
      <div class="karte">
        <h2>{t('f_router', sprache)} <span class="leise" style="font-weight:400">· {t('f_erinnerung', sprache)}</span></h2>
        <p class="leise">{t('f_router_text', sprache, aussen=html.escape(aussen), innen=html.escape(innen))}</p>
        <p style="margin-top:8px"><b>{t('f_router_ports', sprache)}</b></p>
      </div>"""

    if not sicher:
        ziel = f"https://{html.escape(TLS['host'])}:{TLS_PORT}/uebergabe?k={ticket_neu()}"
        return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/fertig', umschalten=False)}
      <h1>{t('fertig_kopf', sprache)}</h1>
      <p class="leise">{t('fertig_unter', sprache)}</p>
      <div class="karte" style="border-color:var(--eigen)">
        <h2>{t('f_tls_kopf', sprache)}</h2>
        <p class="leise">{t('f_tls_text', sprache)}</p>
        <a class="knopf" href="{ziel}">{t('f_tls_knopf', sprache)}</a>
        <details style="margin-top:12px"><summary class="leise">{t('f_tls_klemmt', sprache)}</summary>
          <p class="hilfe" style="margin:8px 0 0">{t('f_tls_fallback', sprache)}</p></details>
      </div>
      {erinnerungen}""", sprache)

    z = zugangsdaten()
    pw = wert_kopierbar(z['passwort'], sprache) if z['passwort'] else \
        '<div class="wert"><span class="leise">/root/zugangsdaten-messenger.txt</span></div>'
    sk = sicherungsschluessel()
    schluessel = wert_kopierbar(sk, sprache) if sk else \
        '<div class="wert"><span class="leise">/root/.sicherung_passwort</span></div>'

    details = f'<div class="karte">{block_adressen(sprache, a)}</div>'
    pruefungen = ''.join(f'<li class="ok">{html.escape(s)}</li>' for s in gut)
    details += f"""
      <div class="karte">
        <h2>{t('f_pruefungen', sprache)}</h2>
        <ul class="schritte">{pruefungen}</ul>
      </div>"""

    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache, '/fertig', umschalten=False)}
      <h1>{t('fertig_kopf', sprache)}</h1>
      <p class="leise">{t('f_sicher_unter', sprache)}</p>
      <div class="karte">
        <h2>{t('f_konto', sprache)}</h2>
        <label>{t('f_kennung', sprache)}</label>
        <div class="wert">@{html.escape(a['verwalter'])}:{d}</div>
        <div style="height:12px"></div>
        <label>{t('f_passwort', sprache)}</label>
        {pw}
        <p class="hilfe" style="margin:10px 0 0">{t('f_passwort_hinweis', sprache)}</p>
      </div>
      <div class="karte">
        <h2>{t('f_sicherung', sprache)}</h2>
        <p class="leise">{t('f_sicherung_text', sprache)}</p>
        {schluessel}
      </div>
      {erinnerungen}
      <form method="post" action="/beenden" class="karte" style="border-color:var(--eigen)">
        <input type="hidden" name="weiter" value="verwaltung">
        <label style="display:flex;gap:8px;align-items:flex-start;margin-bottom:12px">
          <input type="checkbox" id="gespeichert" required style="width:auto;margin-top:3px">
          <span>{t('f_gespeichert', sprache)}</span></label>
        <button type="submit" id="abschliessen">{t('f_abschliessen', sprache)}</button>
        <p class="hilfe" style="margin:8px 0 0">{t('f_abschliessen_hinweis', sprache)}</p>
      </form>
      <details><summary class="leise">{t('f_details', sprache)}</summary>{details}</details>
      {SKRIPT_FERTIG}""", sprache)


def seite_kurz(sprache, kopf, text, knoepfe=''):
    return seite(t('titel', sprache), f"""
      {kopfzeile(sprache)}
      <h1>{kopf}</h1>
      <div class="karte"><p>{text}</p>{knoepfe}</div>""", sprache)


# Bedienung
class Bedienung(http.server.BaseHTTPRequestHandler):
    server_version = 'Humatrix-Setup'
    sys_version = ''

    def log_message(self, *_):
        pass

    def antwort(self, koerper, code=200, kopf=None, typ='text/html; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(koerper)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        for k, w in (kopf or {}).items():
            self.send_header(k, w)
        self.end_headers()
        self.wfile.write(koerper)

    def weiter(self, ziel, kopf=None):
        self.send_response(303)
        self.send_header('Location', ziel)
        for k, w in (kopf or {}).items():
            self.send_header(k, w)
        self.end_headers()

    def angemeldet(self):
        roh = self.headers.get('Cookie', '')
        if not roh:
            return False
        try:
            keks = http.cookies.SimpleCookie(roh)
        except http.cookies.CookieError:
            return False
        return 'einrichtung' in keks and sitzung_gueltig(keks['einrichtung'].value)

    def sprache(self):
        a = antworten()
        return a.get('sprache') or sprache_aus_header(self.headers.get('Accept-Language'))

    def formular(self):
        try:
            laenge = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return {}
        if laenge < 0 or laenge > 65536:
            return {}
        roh = self.rfile.read(laenge).decode('utf-8', 'replace')
        return {k: v[0].strip() for k, v in urllib.parse.parse_qs(roh, keep_blank_values=True).items()}

    def ziel_nach_zustand(self):
        """Wohin '/' fuehrt: je nach Lauf und Antworten."""
        stand = lauf_stand()
        if stand['zustand'] == 'laeuft':
            return '/lauf'
        if stand['zustand'] == 'fertig':
            return '/fertig'
        if stand['zustand'] in ('fehler', 'abgebrochen'):
            return '/fehler'
        if schon_eingerichtet():
            return '/schon'
        a = antworten()
        if not a.get('domain'):
            return '/server'
        if not a.get('vorbereitet'):
            return '/vorbereitung'
        if not a.get('verwalter'):
            return '/dns'
        return '/pruefen'

    # GET
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        pfad, frage = url.path, urllib.parse.parse_qs(url.query)
        sprache = self.sprache()
        if pfad == '/sprache':
            zu = frage.get('zu', [''])[0]
            if zu in ('de', 'en'):
                antworten_setzen(sprache=zu)
            dann = frage.get('dann', ['/'])[0]
            return self.weiter(dann if dann in SEITEN else '/')
        if not antworten().get('sprache'):
            return self.antwort(seite_sprache())
        if pfad == '/uebergabe' and ist_tls(self):
            if ticket_einloesen(frage.get('k', [''])[0]):
                keks = (f'einrichtung={sitzung_bauen()}; Path=/; HttpOnly; Secure; SameSite=Lax; '
                        f'Max-Age={SITZUNG_STUNDEN * 3600}')
                return self.weiter('/fertig', {'Set-Cookie': keks})
            return self.weiter('/')           # abgelaufen oder benutzt: dann eben der Code
        if not self.angemeldet():
            return self.antwort(seite_code(sprache), 401 if pfad != '/' else 200)
        a = antworten()
        stand = lauf_stand()
        if pfad == '/':
            return self.weiter(self.ziel_nach_zustand())
        if pfad == '/stand':
            weiter = {'fertig': '/fertig', 'fehler': '/fehler', 'abgebrochen': '/fehler'}.get(stand['zustand'], '')
            daten = {'zustand': stand['zustand'], 'schritte': stand['schritte'],
                     'text': '\n'.join(stand['zeilen'][-80:]), 'weiter': weiter}
            return self.antwort(json.dumps(daten).encode(), typ='application/json')
        if stand['zustand'] == 'laeuft' and pfad != '/lauf':
            return self.weiter('/lauf')
        if pfad == '/lauf':
            if stand['zustand'] != 'laeuft':
                return self.weiter(self.ziel_nach_zustand())
            return self.antwort(seite_lauf(sprache, stand))
        if pfad == '/fertig':
            if stand['zustand'] != 'fertig':
                return self.weiter(self.ziel_nach_zustand())
            tls_starten(a)
            return self.antwort(seite_fertig(sprache, stand, a, sicher=ist_tls(self) or not TLS['laeuft']))
        if pfad == '/fehler':
            if stand['zustand'] not in ('fehler', 'abgebrochen'):
                return self.weiter(self.ziel_nach_zustand())
            return self.antwort(seite_fehler(sprache, stand, a))
        if pfad == '/verwaltungsanleitung':
            datei = f"/root/verwaltungsanleitung-{a.get('domain', '')}.html"
            if a.get('domain') and os.path.exists(datei):
                with open(datei, 'rb') as d:
                    return self.antwort(d.read())
            return self.weiter('/')
        if pfad == '/anleitung':
            z = zugangsdaten()
            if TLS['laeuft'] and not ist_tls(self):
                return self.weiter('/fertig')          # enthält das Passwort: nur verschluesselt
            if z['anleitung'] and os.path.exists(z['anleitung']):
                with open(z['anleitung'], 'rb') as d:
                    return self.antwort(d.read())
            return self.weiter('/')
        if pfad == '/schon':
            if not schon_eingerichtet() or stand['zustand'] != 'keiner':
                return self.weiter('/')
            knopf = f'<form method="post" action="/beenden"><button type="submit" class="still">{t("schon_aus", sprache)}</button></form>'
            return self.antwort(seite_kurz(sprache, t('schon_kopf', sprache),
                                           t('schon_text', sprache) + '</p><p class="hilfe">' + t('schon_update', sprache), knopf))
        if schon_eingerichtet() and stand['zustand'] == 'keiner':
            return self.weiter('/schon')
        if pfad == '/server':
            return self.antwort(seite_server(sprache, a))
        if pfad == '/vorbereitung':
            if not a.get('domain'):
                return self.weiter('/server')
            return self.antwort(seite_vorbereitung(sprache, a))
        if pfad == '/dns':
            if not a.get('domain'):
                return self.weiter('/server')
            return self.antwort(seite_dns(sprache, a))
        if pfad == '/konto':
            if not a.get('domain'):
                return self.weiter('/server')
            return self.antwort(seite_konto(sprache, a))
        if pfad == '/pruefen':
            if not (a.get('domain') and a.get('verwalter')):
                return self.weiter('/')
            return self.antwort(seite_pruefen(sprache, a))
        self.antwort(seite_kurz(sprache, '404', html.escape(pfad)), 404)

    # POST
    def do_POST(self):
        pfad = urllib.parse.urlsplit(self.path).path
        sprache = self.sprache()
        f = self.formular()
        if pfad == '/code':
            if not antworten().get('sprache'):
                return self.weiter('/')
            with SCHLOSS:
                if VERSUCHE['gesperrt']:
                    return self.antwort(seite_code(sprache), 403)
                if code_pruefen(f.get('code', '')):
                    VERSUCHE['anzahl'] = 0
                    keks = (f'einrichtung={sitzung_bauen()}; Path=/; HttpOnly; SameSite=Strict; '
                            f'Max-Age={SITZUNG_STUNDEN * 3600}')
                    return self.weiter('/', {'Set-Cookie': keks})
                VERSUCHE['anzahl'] += 1
                if VERSUCHE['anzahl'] >= VERSUCHE_MAX:
                    VERSUCHE['gesperrt'] = True
                    code_neu()
                    print('Einrichtungsseite gesperrt: zu viele falsche Codes; neuer Code in', CODE_DATEI, flush=True)
                    return self.antwort(seite_code(sprache), 403)
            time.sleep(1.5)
            return self.antwort(seite_code(sprache, t('code_falsch', sprache)), 401)
        if not self.angemeldet():
            return self.antwort(seite_code(sprache), 401)
        a = antworten()
        stand = lauf_stand()
        if stand['zustand'] == 'laeuft':
            return self.weiter('/lauf')
        if pfad == '/beenden':
            abschliessen()
            if f.get('weiter') == 'verwaltung' and a.get('domain'):
                return self.weiter(f"https://service.{a['domain']}/")
            text = t('aus_text', sprache, domain=html.escape(a['domain'])) if a.get('domain') else t('aus_kopf', sprache) + '.'
            return self.antwort(seite_kurz(sprache, t('aus_kopf', sprache), text))
        if schon_eingerichtet() and stand['zustand'] == 'keiner':
            return self.weiter('/schon')
        if stand['zustand'] == 'fertig':
            return self.weiter('/fertig')
        if pfad == '/server':
            domain = f.get('domain', '').strip().lower().rstrip('.')
            domain = re.sub(r'^https?://', '', domain).split('/')[0]
            situation = f.get('situation', '')
            mail = f.get('mail', '').strip()
            if situation not in SITUATIONEN:
                situation = 'mietserver'
            vorlage = dict(a, domain=domain, situation=situation, mail=mail)
            if not re.fullmatch(r'(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
                return self.antwort(seite_server(sprache, vorlage, t('domain_ungueltig', sprache)), 400)
            if situation != 'proxy':
                if not mail:
                    return self.antwort(seite_server(sprache, vorlage, t('mail_fehlt', sprache)), 400)
                if not re.fullmatch(r'[A-Za-z0-9._%+-]*[A-Za-z0-9_][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', mail) or mail.startswith('-'):
                    return self.antwort(seite_server(sprache, vorlage, t('mail_ungueltig', sprache)), 400)
            # Neue Domain oder Situation heisst neue Vorbereitung.
            vorbereitet = a.get('vorbereitet') if (domain, situation) == (a.get('domain'), a.get('situation')) else False
            antworten_setzen(domain=domain, situation=situation, mail=mail, vorbereitet=vorbereitet)
            return self.weiter('/vorbereitung' if not vorbereitet else '/dns')
        if pfad == '/vorbereitung':
            antworten_setzen(vorbereitet=True)
            return self.weiter('/dns')
        if pfad == '/dns':
            _, bereit, _ = dns_pruefen(a.get('domain', ''), a.get('situation'))
            return self.weiter('/konto' if bereit else '/dns')
        if pfad == '/konto':
            verwalter = f.get('verwalter', '').strip()
            betreiber = re.sub(r'[\x00-\x1f"\\]', ' ', f.get('betreiber', '')).strip().lstrip('-').strip()[:120]
            vorlage = dict(a, verwalter=verwalter, betreiber=betreiber)
            if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', verwalter):
                return self.antwort(seite_konto(sprache, vorlage, t('verwalter_ungueltig', sprache)), 400)
            antworten_setzen(verwalter=verwalter, betreiber=betreiber)
            return self.weiter('/pruefen')
        if pfad == '/start':
            if not (a.get('domain') and a.get('verwalter')):
                return self.weiter('/')
            if a.get('situation') != 'proxy' and not a.get('mail'):
                return self.weiter('/server')
            # Wer die Namensprüfung umgangen hat, landet wieder dort - install.sh
            # bräche sonst nach den ersten Sekunden ab, mit weniger Erklärung.
            if not dns_pruefen(a['domain'], a.get('situation'))[1]:
                return self.weiter('/dns')
            with SCHLOSS:
                if lauf_stand()['zustand'] != 'laeuft':
                    lauf_starten(dict(a, sprache=sprache))
            return self.weiter('/lauf')
        self.antwort(seite_kurz(sprache, '404', html.escape(pfad)), 404)


class Dienst(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    os.makedirs(ZUSTAND, mode=0o700, exist_ok=True)
    if not code_lesen():
        code_neu()
    print(f'Einrichtungsseite auf Port {PORT}, Quelle {QUELLE}', flush=True)
    threading.Thread(target=waechter, daemon=True).start()
    Dienst(('0.0.0.0', PORT), Bedienung).serve_forever()
