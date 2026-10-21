#!/usr/bin/env python3
"""Einladungen fuer den eigenen Matrix-Server.

Jeder Nutzer meldet sich mit seinem normalen Konto an, sieht wie viele Einladungen
er noch hat, und erzeugt mit einem Knopfdruck einen Link. Der Link fuehrt auf eine
Seite, die dem Eingeladenen in drei Schritten erklaert, was er tun muss.

Warum es das braucht: Matrix kann das nicht. Dort darf nur der Serververwalter
Registrierungs-Token erzeugen, und das sind Zeichenketten zum Abtippen. Hier
bekommt stattdessen jeder Nutzer ein Kontingent und verschickt Links.

Nebenbei entsteht die Einladungskette: zu jedem Konto laesst sich sagen, wer es
hereingeholt hat. Das ist die Nachvollziehbarkeit, die ein Einladungsnetz
verantwortbar macht, ohne dass irgendjemand Nachrichten mitliest.

Bis auf pyotp, qrcode und cryptography (alle als Debian-Pakete, kein pip) nur mit
dem, was Python mitbringt. Zugangsdaten stehen in /etc/einladung/ und werden nie
ausgeliefert.
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
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken

# Vom Installer per Umgebung gesetzt; die Werte hier sind nur der Rückfall.
# Die Vorgaben sind reine Platzhalter. Der Installer trägt die echten Werte
# als Umgebungsvariablen ein (siehe einladung.service.d/umgebung.conf).
HEIMSERVER = os.environ.get('MESSENGER_HEIMSERVER', 'https://matrix.beispieldomain.de')
SERVERNAME = os.environ.get('MESSENGER_SERVERNAME', 'beispieldomain.de')   # was in die App getippt wird
ELEMENT    = os.environ.get('MESSENGER_ELEMENT',    'https://chat.beispieldomain.de')
EIGENE_URL = os.environ.get('MESSENGER_EIGENE_URL', 'https://invite.beispieldomain.de')
# Wie der Betreiber in Hinweisen für Endnutzer genannt wird ("sag ... Bescheid").
# Leer heisst: die Vorgabe der jeweiligen Sprache (siehe betreiber()).
BETREIBER  = os.environ.get('MESSENGER_BETREIBER',  '').strip()
# Sprache der Seiten, solange der Verwalter keine gewaehlt hat: en oder de,
# vom Installer per --language gesetzt (MESSENGER_LANGUAGE). Siehe Abschnitt Sprache.
# Unter diesem Namen zeigt die Startseite direkt die Verwaltung statt der Nutzeransicht.
VERWALTUNG_HOST = os.environ.get('MESSENGER_VERWALTUNG_HOST', '')
PORT = 8082
# Nur örtlich lauschen. Davor steht ein nginx, der genau hierher weiterleitet;
# auf 0.0.0.0 wäre der Dienst für jeden Rechner im selben Netzsegment direkt
# erreichbar - unter Umgehung von allem, was im nginx davor hängt.
# Wer ihn bewusst anders binden will, setzt MESSENGER_ADRESSE.
ADRESSE = os.environ.get('MESSENGER_ADRESSE', '127.0.0.1')
DB_DATEI = '/var/lib/einladung/einladung.db'
ADMIN_TOKEN_DATEI = '/etc/einladung/admin-token'
SITZUNGSSCHLUESSEL_DATEI = '/etc/einladung/sitzungsschluessel'
STANDARD_KONTINGENT = 3
GUELTIG_TAGE = 14                    # so lange laeuft eine Einladung
SITZUNG_STUNDEN = 12
SCHLUESSEL_DATEI = '/etc/einladung/datenschluessel'   # verschluesselt die TOTP-Geheimnisse
CODE_FRIST_MIN = 5          # so lange gilt die halbe Sitzung zwischen Passwort und Code
VERSUCHE_MAX = 5            # falsche Codes, bevor gebremst wird
VERSUCHE_FENSTER = 900      # Sekunden, über die gezählt wird
# Befund der täglichen Update-Prüfung (schreibt root, liest dieser Dienst) und
# der Auftragsordner, in dem "Jetzt aktualisieren" seine Datei ablegt.
UPDATES_DATEI = '/var/lib/messenger/updates.json'
VERWALTUNGSANLEITUNG_DATEI = '/var/lib/messenger/verwaltungsanleitung.html'   # schreibt der Installer
AUFTRAG_DATEI = '/var/lib/messenger/auftraege/update'
UPDATES_FRIST = 3 * 86400   # älter gilt als "nicht geprüft" - der Timer laeuft dann nicht
LAUF_FRIST = 2 * 3600       # länger "laeuft" kein Einspielen - dann ist der Lauf abgebrochen

# Ablage

def db():
    # timeout=30: jede Anfrage hat ihren eigenen Faden und ihre eigene Verbindung.
    # Mit dem Standard von 5 s brach ein paralleler Schreibzugriff mit
    # 'database is locked' ab, sobald ein anderer kurz länger schrieb.
    v = sqlite3.connect(DB_DATEI, timeout=30)
    v.row_factory = sqlite3.Row
    return v


def ablage_vorbereiten():
    os.makedirs(os.path.dirname(DB_DATEI), exist_ok=True)
    with db() as v:
        v.executescript("""
        CREATE TABLE IF NOT EXISTS kontingent (
            nutzer   TEXT PRIMARY KEY,
            gesamt   INTEGER NOT NULL      -- -1 bedeutet unbegrenzt, 0 bedeutet darf nicht
        );
        CREATE TABLE IF NOT EXISTS einstellung (
            schluessel TEXT PRIMARY KEY,
            wert       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS einladung (
            code          TEXT PRIMARY KEY,
            token         TEXT NOT NULL,
            einlader      TEXT NOT NULL,
            erstellt      INTEGER NOT NULL,
            laeuft_ab     INTEGER NOT NULL,
            eingeloest_am INTEGER,
            eingeloest_von TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_einlader ON einladung(einlader);
        CREATE TABLE IF NOT EXISTS zweiter_faktor (
            nutzer     TEXT PRIMARY KEY,
            geheimnis  BLOB NOT NULL,       -- verschluesselt, nie im Klartext in der Ablage
            aktiv      INTEGER NOT NULL DEFAULT 0,
            seit       INTEGER
        );
        CREATE TABLE IF NOT EXISTS versuch (
            nutzer TEXT NOT NULL,
            wann   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_versuch ON versuch(nutzer, wann);
        CREATE TABLE IF NOT EXISTS sitzung_sperre (
            nutzer TEXT PRIMARY KEY,
            ab     INTEGER NOT NULL        -- Sitzungen, die vorher ausgegeben wurden, gelten nicht mehr
        );
        CREATE TABLE IF NOT EXISTS von_hand (
            kennung TEXT PRIMARY KEY       -- ueber die Verwaltung angelegt, gehoert zu keiner Einladung
        );
        CREATE TABLE IF NOT EXISTS erstanmeldung (
            nutzer TEXT PRIMARY KEY,
            hash   TEXT NOT NULL,          -- scrypt des Start- bzw. neuen Passworts, nie Klartext
            pw_neu INTEGER NOT NULL DEFAULT 0,
            seit   INTEGER NOT NULL
        );
        """)
        # Nachrüsten: bestehende Ablagen kennen die Spalte noch nicht
        spalten = {z[1] for z in v.execute('PRAGMA table_info(einladung)')}
        if 'raum' not in spalten:
            v.execute('ALTER TABLE einladung ADD COLUMN raum TEXT')
        if 'raum_fehler' not in spalten:
            v.execute('ALTER TABLE einladung ADD COLUMN raum_fehler TEXT')
        if 'raum_name' not in spalten:
            v.execute('ALTER TABLE einladung ADD COLUMN raum_name TEXT')
        if 'benutzt_am' not in spalten:
            # Token laut Synapse benutzt - getrennt von eingeloest_am/_von, die es
            # nur bei eindeutiger Zuordnung zu einem Konto gibt (kette_nachfuehren).
            v.execute('ALTER TABLE einladung ADD COLUMN benutzt_am INTEGER')
        if 'zurueckgezogen_am' not in spalten:
            # Vom Einladenden zurueckgezogen: Token bei Synapse gelöscht, Kontingent
            # wieder frei, der Link sagt es dem Eingeladenen.
            v.execute('ALTER TABLE einladung ADD COLUMN zurueckgezogen_am INTEGER')
        if 'sprache' not in spalten:
            # Sprache der Einladungsseite ('de'/'en'), beim Erzeugen gewaehlt. NULL bei
            # alten Einladungen: dann entscheidet der Browser des Eingeladenen.
            v.execute('ALTER TABLE einladung ADD COLUMN sprache TEXT')
        spalten = {z[1] for z in v.execute('PRAGMA table_info(zweiter_faktor)')}
        if 'letzter_schritt' not in spalten:
            v.execute('ALTER TABLE zweiter_faktor ADD COLUMN letzter_schritt INTEGER')


def standard_kontingent():
    with db() as v:
        z = v.execute("SELECT wert FROM einstellung WHERE schluessel='standard'").fetchone()
        return int(z['wert']) if z else STANDARD_KONTINGENT


def standard_setzen(wert):
    with db() as v:
        v.execute("INSERT OR REPLACE INTO einstellung (schluessel, wert) VALUES ('standard', ?)",
                  (str(int(wert)),))


def kontingent_von(nutzer):
    with db() as v:
        z = v.execute('SELECT gesamt FROM kontingent WHERE nutzer=?', (nutzer,)).fetchone()
        if z is None:
            # OR IGNORE: zwei gleichzeitige Seitenaufrufe desselben neuen Kontos lesen
            # beide "keine Zeile" - der zweite INSERT brach sonst mit IntegrityError ab.
            v.execute('INSERT OR IGNORE INTO kontingent (nutzer, gesamt) VALUES (?,?)',
                      (nutzer, standard_kontingent()))
            z = v.execute('SELECT gesamt FROM kontingent WHERE nutzer=?', (nutzer,)).fetchone()
        return z['gesamt']


def kontingent_setzen(nutzer, gesamt):
    with db() as v:
        v.execute('INSERT OR REPLACE INTO kontingent (nutzer, gesamt) VALUES (?,?)',
                  (nutzer, int(gesamt)))


def verbleibend(nutzer):
    """Wie viele Einladungen noch offen sind. -1 heisst unbegrenzt."""
    gesamt = kontingent_von(nutzer)
    if gesamt < 0:
        return -1
    return max(0, gesamt - verbraucht_von(nutzer))


def menge(wert, sprache):
    """Kontingente lesbar machen: -1 ist keine Zahl, die jemand verstehen will."""
    return t('unbegrenzt', sprache) if wert < 0 else str(wert)


def verbraucht_von(nutzer):
    """Abgelaufene, nicht benutzte Einladungen zaehlen nicht mehr mit.

    Benutzt (benutzt_am) zaehlt wie eingeloest: ueber den Link kam jemand herein,
    auch wenn die Zuordnung zum Konto noch unklar ist - sonst fiele das Kontingent
    nach dem Ablauf an den Einladenden zurueck.
    """
    jetzt = int(time.time())
    with db() as v:
        return v.execute(
            'SELECT count(*) FROM einladung WHERE einlader=? AND zurueckgezogen_am IS NULL '
            'AND (eingeloest_am IS NOT NULL OR benutzt_am IS NOT NULL OR laeuft_ab > ?)',
            (nutzer, jetzt)).fetchone()[0]


# Protokoll

def protokoll(was, nutzer='', zusatz=''):
    """Sicherheitsrelevante Vorgaenge ins Journal - nie Inhalte, nie Geheimnisse.

    Steuerzeichen werden ersetzt: Nutzer und Zusatz kommen zum Teil aus
    Formularfeldern, und ein Zeilenumbruch darin erzeugte sonst eine zweite,
    taeuschend echte Journalzeile.
    """
    def sauber(t):
        return ''.join(c if c.isprintable() else '?' for c in str(t))
    print(f'[{time.strftime("%F %T")}] {was} {sauber(nutzer)} {sauber(zusatz)}'.rstrip(),
          flush=True)


# Zweiter Faktor

def datenschluessel():
    try:
        with open(SCHLUESSEL_DATEI, 'rb') as d:
            return Fernet(d.read().strip())
    except FileNotFoundError:
        raise RuntimeError(f'{SCHLUESSEL_DATEI} fehlt — der Installer legt sie an.')


def zf_stand(nutzer):
    """(eingerichtet, aktiv) fuer dieses Konto."""
    with db() as v:
        z = v.execute('SELECT aktiv FROM zweiter_faktor WHERE nutzer=?', (nutzer,)).fetchone()
    return (z is not None, bool(z['aktiv']) if z else False)


def zf_geheimnis(nutzer):
    with db() as v:
        z = v.execute('SELECT geheimnis FROM zweiter_faktor WHERE nutzer=?', (nutzer,)).fetchone()
    if not z:
        return None
    try:
        return datenschluessel().decrypt(z['geheimnis']).decode()
    except InvalidToken:
        return None


def zf_vorbereiten(nutzer):
    """Neues Geheimnis anlegen - noch NICHT scharf. Erst ein richtiger Code schaltet es ein."""
    g = pyotp.random_base32()
    with db() as v:
        v.execute('INSERT OR REPLACE INTO zweiter_faktor (nutzer, geheimnis, aktiv, seit) '
                  'VALUES (?,?,0,NULL)', (nutzer, datenschluessel().encrypt(g.encode())))
    return g


def zf_einschalten(nutzer):
    with db() as v:
        v.execute('UPDATE zweiter_faktor SET aktiv=1, seit=? WHERE nutzer=?',
                  (int(time.time()), nutzer))
    # Wer den zweiten Faktor ändert, beendet damit alle anderen Sitzungen des Kontos.
    sitzung_sperren(nutzer)
    protokoll('2fa_eingeschaltet', nutzer)


def zf_entfernen(nutzer):
    with db() as v:
        v.execute('DELETE FROM zweiter_faktor WHERE nutzer=?', (nutzer,))
    sitzung_sperren(nutzer)
    protokoll('2fa_entfernt', nutzer)


def zf_pruefen(nutzer, code):
    """Stimmt der Code - und wurde dieser Zeitschritt nicht schon benutzt?

    Ein Code gilt 30 Sekunden, mit Nachbarschritt rund 90. Ohne Merken des
    verbrauchten Schritts liesse sich ein mitgelesener Code in dieser Zeit
    nochmal einspielen - genau das soll der zweite Faktor verhindern.
    """
    g = zf_geheimnis(nutzer)
    if not g:
        return False
    code = (code or '').strip().replace(' ', '')
    if not re.fullmatch(r'[0-9]{6}', code):
        return False
    t = pyotp.TOTP(g)
    jetzt = int(time.time())
    for d in (0, -1, 1):                   # Nachbarschritt weiter erlaubt - Uhren laufen nie genau gleich
        schritt = jetzt // 30 + d
        if hmac.compare_digest(t.generate_otp(schritt), code):
            with db() as v:
                n = v.execute('UPDATE zweiter_faktor SET letzter_schritt=? WHERE nutzer=? '
                              'AND (letzter_schritt IS NULL OR letzter_schritt < ?)',
                              (schritt, nutzer, schritt)).rowcount
            return n == 1                  # 0 heisst: dieser Schritt ist schon verbraucht
    return False


def zf_uri(nutzer, geheimnis):
    kurz = nutzer.split(':')[0].lstrip('@')
    return pyotp.TOTP(geheimnis).provisioning_uri(name=f'{kurz}@{SERVERNAME}',
                                                  issuer_name=SERVERNAME)


def qr_svg(text, kante=5, beschriftung='QR code'):
    """QR als eingebettetes SVG - kein Bild, keine fremde Seite, kein JavaScript."""
    q = qrcode.QRCode(border=2)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    felder = ''.join(
        f'<rect x="{x * kante}" y="{y * kante}" width="{kante}" height="{kante}"/>'
        for y, zeile in enumerate(m) for x, an in enumerate(zeile) if an)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n * kante} {n * kante}" '
            f'width="{n * kante}" height="{n * kante}" role="img" aria-label="{html.escape(beschriftung)}">'
            f'<rect width="100%" height="100%" fill="#fff"/>'
            f'<g fill="#000">{felder}</g></svg>')


# Drossel

def gebremst(nutzer):
    grenze = int(time.time()) - VERSUCHE_FENSTER
    with db() as v:
        v.execute('DELETE FROM versuch WHERE wann < ?', (grenze,))
        n = v.execute('SELECT count(*) FROM versuch WHERE nutzer=? AND wann >= ?',
                      (nutzer, grenze)).fetchone()[0]
    return n >= VERSUCHE_MAX


def fehlversuch(nutzer):
    with db() as v:
        v.execute('INSERT INTO versuch (nutzer, wann) VALUES (?,?)', (nutzer, int(time.time())))


def versuche_loeschen(nutzer):
    with db() as v:
        v.execute('DELETE FROM versuch WHERE nutzer=?', (nutzer,))


# Matrix

def matrix_aufruf(pfad, daten=None, token=None, methode=None):
    # 'daten is not None' statt 'if daten': ein LEERES Dictionary ist in Python falsch,
    # und {} ist bei Matrix ein völlig normaler POST-Rumpf (Anmelden, Abmelden, Beitreten).
    # Mit 'if daten' gingen die als GET raus - der Server antwortet dann mit 405.
    anfrage = urllib.request.Request(
        HEIMSERVER + pfad, method=methode or ('POST' if daten is not None else 'GET'))
    anfrage.add_header('Content-Type', 'application/json')
    if token:
        anfrage.add_header('Authorization', 'Bearer ' + token)
    koerper = json.dumps(daten).encode() if daten is not None else None
    with urllib.request.urlopen(anfrage, koerper, timeout=15) as a:
        return json.load(a)


def admin_token():
    with open(ADMIN_TOKEN_DATEI) as d:
        return d.read().strip()


def anmeldung_pruefen(benutzer, passwort):
    """Gegen den Heimserver pruefen. Das Passwort bleibt hier nicht liegen.

    Liefert (zustand, kennung). Zustand ist 'ok', 'falsch', 'gebremst' oder 'weg'.

    Die Unterscheidung ist wichtig: Synapse drosselt Anmeldungen schon nach wenigen
    Versuchen und antwortet dann mit 429. Wer sich einmal vertippt und es gleich
    nochmal probiert, bekaeme sonst faelschlich "Passwort stimmt nicht" zu lesen
    und wuerde weiter probieren - genau das Falsche.
    """
    try:
        antwort = matrix_aufruf('/_matrix/client/v3/login', {
            'type': 'm.login.password',
            'identifier': {'type': 'm.id.user', 'user': benutzer},
            'password': passwort,
            'initial_device_display_name': 'Einladungsseite',
        })
    except urllib.error.HTTPError as f:
        return ('gebremst', None) if f.code == 429 else ('falsch', None)
    except Exception:
        return ('weg', None)
    # Die frische Sitzung wird nicht gebraucht - sofort wieder abmelden.
    try:
        matrix_aufruf('/_matrix/client/v3/logout', {}, token=antwort['access_token'])
    except Exception:
        pass
    return ('ok', antwort['user_id'])


# Zeichen, die sich nicht verwechseln lassen: kein O/0, kein I/l/1, keine Satzzeichen.
# Synapse würfelt sonst Token wie "oGJ6vzzPIi5." - der Punkt am Ende sieht aus wie
# Zeichensetzung, und I/l/1 kostet am Telefon Nerven.
TOKEN_ZEICHEN = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def token_text():
    gruppen = (''.join(secrets.choice(TOKEN_ZEICHEN) for _ in range(4)) for _ in range(3))
    return '-'.join(gruppen)


def token_erzeugen():
    bis = int((time.time() + GUELTIG_TAGE * 86400) * 1000)
    antwort = matrix_aufruf('/_synapse/admin/v1/registration_tokens/new',
                            {'token': token_text(), 'uses_allowed': 1, 'expiry_time': bis},
                            token=admin_token())
    return antwort['token'], bis // 1000


def token_loeschen(token):
    """Registrierungs-Token bei Synapse entfernen. 404 heisst: war schon weg - auch gut."""
    try:
        matrix_aufruf('/_synapse/admin/v1/registration_tokens/' + urllib.parse.quote(token),
                      token=admin_token(), methode='DELETE')
    except urllib.error.HTTPError as f:
        if f.code != 404:
            raise


def token_zustand():
    """{token: eingeloeste Nutzungen} von Synapse holen."""
    try:
        d = matrix_aufruf('/_synapse/admin/v1/registration_tokens', token=admin_token())
        return {t['token']: t.get('completed', 0) for t in d.get('registration_tokens', [])}
    except Exception:
        return {}


def nutzer_seit(zeitpunkt):
    """Konten, die nach diesem Zeitpunkt angelegt wurden - fuer die Einladungskette.

    Nach Anlegezeit absteigend holen: ohne order_by liefert Synapse die ersten 200
    nach Kennung, und ein neues @zoe-Konto fiele ab 200 Konten hinten heraus.
    Gesperrte bleiben drin, sonst liesse sich ein inzwischen gesperrter Neuling
    nie zuordnen und seine Einladung bliebe fuer immer offen.
    """
    try:
        d = matrix_aufruf('/_synapse/admin/v2/users?from=0&limit=200&guests=false'
                          '&deactivated=true&order_by=creation_ts&dir=b',
                          token=admin_token())
        return sorted((u['creation_ts'] // 1000, u['name']) for u in d.get('users', [])
                      if u.get('creation_ts', 0) // 1000 >= zeitpunkt
                      and not u['name'].startswith('@einladungsdienst:'))
    except Exception:
        return []


def ist_verwalter(nutzer):
    """Verwalter ist, wer es auch am Matrix-Server ist - keine zweite Rechteverwaltung."""
    try:
        d = matrix_aufruf('/_synapse/admin/v2/users/' + urllib.parse.quote(nutzer),
                          token=admin_token())
        return bool(d.get('admin'))
    except Exception:
        return False


def alle_nutzer():
    """Alle Konten auf diesem Server - gesperrte eingeschlossen, Dienstkonto ausgenommen."""
    try:
        d = matrix_aufruf('/_synapse/admin/v2/users?from=0&limit=500&guests=false&deactivated=true',
                          token=admin_token())
    except Exception:
        return []
    raus = []
    for u in d.get('users', []):
        if u['name'].startswith('@einladungsdienst:'):
            continue
        raus.append({'kennung': u['name'],
                     'anzeige': u.get('displayname') or u['name'].split(':')[0].lstrip('@'),
                     'verwalter': bool(u.get('admin')),
                     'gesperrt': bool(u.get('deactivated'))})
    return sorted(raus, key=lambda x: (x['gesperrt'], x['anzeige'].lower()))


def neues_passwort():
    """Sprechbar statt kryptisch - es wird am Telefon weitergegeben."""
    silben = ('ba be bi bo da de di do fa fe fi fo ga ge gi go ha he hi ho '
              'ka ke ki ko la le li lo ma me mi mo na ne ni no pa pe pi po '
              'ra re ri ro sa se si so ta te ti to').split()
    return '-'.join(''.join(secrets.choice(silben) for _ in range(2)) for _ in range(3)) \
           + '-' + str(secrets.randbelow(90) + 10)


class KontoVergeben(Exception):
    """Die Kennung gibt es schon - ein bestehendes Konto wird nie ueberschrieben."""


def nutzer_anlegen(name, anzeige=''):
    kennung = f'@{name}:{SERVERNAME}'
    # Das PUT unten legt an ODER ändert. Ohne diese Prüfung bekäme ein bestehendes
    # Konto bei einem Tippfehler ein neues Passwort, verlöre seine Verwalterrechte
    # und würde entsperrt - bei einer Erfolgsmeldung "angelegt". 404 heisst: frei.
    try:
        matrix_aufruf('/_synapse/admin/v2/users/' + urllib.parse.quote(kennung),
                      token=admin_token())
    except urllib.error.HTTPError as f:
        if f.code != 404:
            raise
    else:
        raise KontoVergeben(kennung)
    pw = neues_passwort()
    daten = {'password': pw, 'admin': False, 'deactivated': False}
    if anzeige:
        daten['displayname'] = anzeige
    matrix_aufruf('/_synapse/admin/v2/users/' + urllib.parse.quote(kennung), daten,
                  token=admin_token(), methode='PUT')
    # Merken, dass dieses Konto zu keiner Einladung gehört - sonst hielte die
    # Einladungskette es für den Neuling einer offenen Einladung.
    with db() as v:
        v.execute('INSERT OR IGNORE INTO von_hand (kennung) VALUES (?)', (kennung,))
    return kennung, pw


def passwort_neu(kennung):
    pw = neues_passwort()
    matrix_aufruf('/_synapse/admin/v1/reset_password/' + urllib.parse.quote(kennung),
                  {'new_password': pw, 'logout_devices': True}, token=admin_token())
    sitzung_sperren(kennung)      # logout_devices trifft nur Matrix, nicht den Keks hier
    return pw


# Erstanmeldung
# Das erste Verwalterkonto legt der Installer in Synapse gesperrt an. In den Chat
# kommt es erst, wenn hier ein eigenes Passwort und der zweite Faktor stehen.
# Bis dahin prüft dieser Dienst das Passwort selbst gegen einen Hash, denn
# Synapse lässt ein gesperrtes Konto nicht anmelden.

def pw_hash(passwort):
    salz = secrets.token_bytes(16)
    h = hashlib.scrypt(passwort.encode(), salt=salz, n=2 ** 14, r=8, p=1)
    return salz.hex() + ':' + h.hex()


def pw_passt(passwort, gespeichert):
    try:
        salz, h = gespeichert.split(':')
        soll = hashlib.scrypt(passwort.encode(), salt=bytes.fromhex(salz), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(soll.hex(), h)
    except Exception:
        return False


def passwort_stark(pw):
    """Mindestens 12 Zeichen aus drei Zeichenarten - oder eine lange Passphrase ab 16."""
    arten = sum(bool(re.search(m, pw)) for m in (r'[a-z]', r'[A-Z]', r'[0-9]', r'[^A-Za-z0-9]'))
    return len(pw) >= 16 or (len(pw) >= 12 and arten >= 3)


def erst_eintrag(nutzer):
    with db() as v:
        return v.execute('SELECT * FROM erstanmeldung WHERE nutzer=?', (nutzer,)).fetchone()


def erst_kennung(eingabe):
    """Kennung zu 'name' oder '@name:server', aber nur bei offener Erstanmeldung."""
    kurz = (eingabe or '').strip().split(':')[0].lstrip('@').lower()
    with db() as v:
        for z in v.execute('SELECT nutzer FROM erstanmeldung'):
            if z['nutzer'].split(':')[0].lstrip('@').lower() == kurz:
                return z['nutzer']
    return None


def erst_passwort_setzen(nutzer, passwort):
    matrix_aufruf('/_synapse/admin/v1/reset_password/' + urllib.parse.quote(nutzer),
                  {'new_password': passwort, 'logout_devices': True}, token=admin_token())
    with db() as v:
        v.execute('UPDATE erstanmeldung SET hash=?, pw_neu=1 WHERE nutzer=?',
                  (pw_hash(passwort), nutzer))
    sitzung_sperren(nutzer)
    protokoll('erst_passwort', nutzer)


def erst_abschliessen(nutzer):
    """Passwort und zweiter Faktor stehen: Sperre in Synapse aufheben, Eintrag weg."""
    matrix_aufruf('/_synapse/admin/v2/users/' + urllib.parse.quote(nutzer),
                  {'locked': False}, token=admin_token(), methode='PUT')
    with db() as v:
        v.execute('DELETE FROM erstanmeldung WHERE nutzer=?', (nutzer,))
    protokoll('erst_fertig', nutzer)


def pflicht_schritt(nutzer):
    """Was ein Konto erledigen muss, bevor es irgendetwas anderes darf.

    'passwort'    - Erstanmeldung, das Startpasswort gilt noch
    'zweitfaktor' - Erstanmeldung, zweiter Faktor angeboten (einrichten oder ablehnen)
    None          - nichts offen
    """
    e = erst_eintrag(nutzer)
    if e and not e['pw_neu']:
        return 'passwort'
    if zf_stand(nutzer)[1]:
        if e:
            # Abbruch zwischen Einschalten und Entsperren: hier nachholen.
            try:
                erst_abschliessen(nutzer)
            except Exception as f:
                protokoll('erst_entsperren_fehlgeschlagen', nutzer, str(f)[:120])
        return None
    return 'zweitfaktor' if e else None


def konto_sperren(kennung):
    """Sperren, nicht loeschen: erase bleibt aus, damit es rueckgaengig gemacht werden kann."""
    matrix_aufruf('/_synapse/admin/v1/deactivate/' + urllib.parse.quote(kennung),
                  {'erase': False}, token=admin_token())
    sitzung_sperren(kennung)      # sonst liefe eine offene Einladungs-Sitzung 12 h weiter


def einladungen_zurueckziehen(kennung):
    """Alle offenen Einladungen eines Kontos ungueltig machen (beim Sperren): Die Links
    haengen am Server, nicht am Konto - sonst kaeme ueber sie weiter jemand herein.
    Erst die Token bei Synapse, dann die Ablage; liefert die Anzahl."""
    with db() as v:
        offen = v.execute('SELECT code, token FROM einladung WHERE einlader=? AND '
                          'eingeloest_am IS NULL AND benutzt_am IS NULL AND '
                          'zurueckgezogen_am IS NULL', (kennung,)).fetchall()
    for e in offen:
        token_loeschen(e['token'])
    jetzt = int(time.time())
    with db() as v:
        for e in offen:
            v.execute('UPDATE einladung SET zurueckgezogen_am=? WHERE code=? AND '
                      'eingeloest_am IS NULL AND benutzt_am IS NULL AND zurueckgezogen_am IS NULL',
                      (jetzt, e['code']))
    return len(offen)


def konto_entsperren(kennung):
    pw = neues_passwort()
    matrix_aufruf('/_synapse/admin/v2/users/' + urllib.parse.quote(kennung),
                  {'deactivated': False, 'password': pw}, token=admin_token(), methode='PUT')
    sitzung_sperren(kennung)
    return pw


def verwalter_setzen(kennung, ja):
    matrix_aufruf('/_synapse/admin/v2/users/' + urllib.parse.quote(kennung),
                  {'admin': bool(ja)}, token=admin_token(), methode='PUT')


def raeume_von(nutzer):
    """Raeume des Einladenden, mit Namen - fuer die Auswahl beim Erzeugen."""
    try:
        d = matrix_aufruf('/_synapse/admin/v1/users/' + urllib.parse.quote(nutzer)
                          + '/joined_rooms', token=admin_token())
    except Exception:
        return []
    raus = []
    for r in d.get('joined_rooms', []):
        try:
            i = matrix_aufruf('/_synapse/admin/v1/rooms/' + urllib.parse.quote(r),
                              token=admin_token())
        except Exception:
            continue
        name = i.get('name') or i.get('canonical_alias') or ''
        if not name:
            continue                      # namenlose Einzelchats taugen nicht als Ziel
        raus.append({'id': r, 'name': name, 'mitglieder': i.get('joined_members', 0)})
    return sorted(raus, key=lambda x: x['name'].lower())


def in_raum_bitten(einlader, neuer, raum):
    """Laedt den Neuen in den Raum ein - im Namen des EINLADENDEN.

    Nicht ueber das Dienstkonto: Synapse verlangt dafuer, dass der Handelnde selbst
    im Raum ist, und ein Dienstkonto in jedem Familienchat waere das falsche Bild.
    Stattdessen holt sich der Dienst kurz einen Zugang des Einladenden, schickt die
    Raumeinladung und meldet ihn gleich wieder ab. Im Chat steht dann korrekt:
    "<Einlader> hat dich eingeladen."

    Bewusst eine Einladung und kein Hineinsetzen - niemand wird ungefragt
    in einen Raum gesteckt.
    """
    d = matrix_aufruf('/_synapse/admin/v1/users/' + urllib.parse.quote(einlader) + '/login',
                      {}, token=admin_token())
    zugang = d['access_token']
    try:
        matrix_aufruf('/_matrix/client/v3/rooms/' + urllib.parse.quote(raum) + '/invite',
                      {'user_id': neuer}, token=zugang)
    finally:
        try:
            matrix_aufruf('/_matrix/client/v3/logout', {}, token=zugang)
        except Exception:
            pass


def zuordnen(eingeloest, konten, vergeben):
    """Eindeutige Paare (Einladung, Kennung) bilden - rein, ohne Ablage und Netz.

    eingeloest: Einladungen (mit 'erstellt'), deren Token laut Synapse benutzt wurde.
    konten:     [(zeitpunkt, kennung)] der Konten ab dem aeltesten Erstellzeitpunkt.
    vergeben:   Kennungen, die schon zugeschrieben oder von Hand angelegt sind.

    Fuer eine Einladung kommen nur Konten in Frage, die nach ihrem Erzeugen
    entstanden und noch frei sind. Zugeordnet wird nur, wenn genau EIN solches
    Konto uebrig bleibt - zwei Kandidaten heisst: nicht raten, offen lassen.
    Die juengste Einladung zuerst: ihr Fenster ist das engste, und jede sichere
    Zuordnung engt die Fenster der aelteren weiter ein.

    Liefert (paare, unklar): paare = [(einladung, kennung)],
    unklar = [(einladung, anzahl_kandidaten)].
    """
    vergeben = set(vergeben)
    paare, unklar = [], []
    for e in sorted(eingeloest, key=lambda e: e['erstellt'], reverse=True):
        passend = [name for ts, name in konten
                   if ts >= e['erstellt'] and name not in vergeben]
        if len(passend) == 1:
            paare.append((e, passend[0]))
            vergeben.add(passend[0])
        else:
            unklar.append((e, len(passend)))
    return paare, unklar


KETTE_GEMELDET = set()       # Codes, deren unklare Zuordnung schon im Journal steht


def kette_nachfuehren():
    """Eingeloeste Einladungen erkennen und dem neuen Konto zuordnen.

    Synapse verraet nicht, WER ein Token benutzt hat. Zugeordnet wird deshalb
    ueber den Anmeldezeitpunkt - aber nur, wenn das eindeutig ist (zuordnen).
    Vorher nahm die Schleife das erste freie Konto nach dem Erzeugen: bei zwei
    Einloesungen in derselben Minute oder einem zwischendurch von Hand angelegten
    Konto landete so ein Fremder im Familienraum. Zwei Dinge werden deshalb
    getrennt gefuehrt: benutzt_am sagt nur, dass der Token laut Synapse benutzt
    ist (Anzeige, Kontingent, Einloeseseite); eingeloest_am/eingeloest_von gibt
    es erst bei eindeutiger Zuordnung. Unklares bleibt ohne Konto und wird bei
    jedem Durchlauf erneut versucht - es wird spaeter eindeutig, wenn die anderen
    Kandidaten inzwischen zugeordnet sind. Zwei Neulinge in derselben Minute ueber
    zwei Einladungen bleiben dagegen dauerhaft unklar: dafuer gibt es bewusst keine
    Rate-Funktion und keine Zuordnungs-Oberflaeche - lieber eine ehrliche Luecke.

    Keine Verbindung zur Ablage bleibt ueber die Matrix-Aufrufe hinweg offen:
    die hielt sonst die Schreibsperre, waehrend auf Synapse gewartet wurde, und
    parallele Seitenaufrufe brachen mit 'database is locked' ab.
    """
    with db() as v:
        offen = v.execute('SELECT code, token, erstellt, einlader, raum, benutzt_am '
                          'FROM einladung WHERE eingeloest_am IS NULL '
                          'AND zurueckgezogen_am IS NULL').fetchall()
        if not offen:
            return
        vergeben = {z[0] for z in v.execute(
            'SELECT eingeloest_von FROM einladung WHERE eingeloest_von IS NOT NULL')}
        vergeben |= {z[0] for z in v.execute('SELECT kennung FROM von_hand')}

    zustand = token_zustand()
    # Einmal als benutzt erkannt bleibt benutzt - auch wenn Synapse gerade nicht
    # antwortet oder den Token nicht mehr führt.
    eingeloest = [e for e in offen if e['benutzt_am'] or zustand.get(e['token'], 0) > 0]
    if not eingeloest:
        return
    jetzt = int(time.time())
    with db() as v:
        for e in eingeloest:
            if not e['benutzt_am']:
                v.execute('UPDATE einladung SET benutzt_am=? WHERE code=? AND benutzt_am IS NULL',
                          (jetzt, e['code']))
    konten = nutzer_seit(min(e['erstellt'] for e in eingeloest))
    paare, unklar = zuordnen(eingeloest, konten, vergeben)

    for e, n in unklar:
        if e['code'] not in KETTE_GEMELDET:       # einmal melden, nicht jede Minute
            KETTE_GEMELDET.add(e['code'])
            protokoll('kette_unklar', e['einlader'], f'{e["code"]} {n} Kandidaten')

    for e, wer in paare:
        # Erst die Zeile beanspruchen (nur wenn noch offen), dann in den Raum bitten:
        # Hintergrundfaden und Seitenaufruf laufen parallel, und zwei Raumeinladungen
        # für dieselbe Person wären die Folge.
        with db() as v:
            n = v.execute('UPDATE einladung SET eingeloest_am=?, eingeloest_von=? '
                          'WHERE code=? AND eingeloest_am IS NULL',
                          (int(time.time()), wer, e['code'])).rowcount
        if n != 1:
            continue
        KETTE_GEMELDET.discard(e['code'])
        protokoll('eingeloest', wer, e['code'])
        if not e['raum']:
            continue
        try:
            in_raum_bitten(e['einlader'], wer, e['raum'])
            protokoll('raumeinladung', wer, e['raum'])
        except Exception as f:
            fehler = str(f)[:120]
            protokoll('raumeinladung_fehlgeschlagen', wer, fehler)
            with db() as v:
                v.execute('UPDATE einladung SET raum_fehler=? WHERE code=?', (fehler, e['code']))


# Sitzung

def sitzungsschluessel():
    with open(SITZUNGSSCHLUESSEL_DATEI, 'rb') as d:
        return d.read().strip()


def sitzung_sperren(nutzer):
    """Alle bisher ausgegebenen Sitzungen dieses Kontos beenden.

    Die Sitzung ist ein signierter Keks ohne Eintrag auf dem Server. Widerrufen
    laesst sie sich deshalb nur ueber einen Zeitpunkt: was vor 'ab' ausgegeben
    wurde, nimmt sitzung_pruefen nicht mehr an. Noetig beim Sperren, bei neuem
    Passwort, beim Aendern des zweiten Faktors und beim Abmelden - sonst liefe
    ein kopierter Keks trotz Sperre bis zu SITZUNG_STUNDEN weiter. Die Sperre
    gilt je Konto, nicht je Geraet: Abmelden beendet auch die anderen Sitzungen
    desselben Kontos.
    """
    with db() as v:
        v.execute('INSERT OR REPLACE INTO sitzung_sperre (nutzer, ab) VALUES (?,?)',
                  (nutzer, int(time.time())))


def sitzung_gesperrt_ab(nutzer):
    with db() as v:
        z = v.execute('SELECT ab FROM sitzung_sperre WHERE nutzer=?', (nutzer,)).fetchone()
    return z['ab'] if z else 0


def sitzung_bauen(nutzer, stufe='voll'):
    """stufe 'halb' = Passwort stimmt, der zweite Faktor fehlt noch.

    Im signierten Inhalt steht auch der Ausgabezeitpunkt 'seit' - daran erkennt
    sitzung_pruefen, ob eine Sperre (sitzung_sperren) juenger ist als der Keks.

    Die Unterschrift steht als Hex hinter dem Trenner, nicht als rohe Bytes.
    Vorher standen dort 16 Rohbytes hinter einem '|'; enthielt die Unterschrift
    selbst ein '|' (0x7C), trennte das Gegenstueck an der falschen Stelle und
    die Sitzung war sofort ungueltig. Das traf rund 6 von 100 Anmeldungen -
    also etwa jede siebzehnte, scheinbar grundlos. Hex kann kein '|' enthalten.
    """
    dauer = CODE_FRIST_MIN * 60 if stufe == 'halb' else SITZUNG_STUNDEN * 3600
    seit = int(time.time())
    bis = seit + dauer
    inhalt = f'{nutzer}|{seit}|{bis}|{stufe}'.encode()
    unterschrift = hmac.new(sitzungsschluessel(), inhalt, hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(inhalt + b'|' + unterschrift.encode()).decode()


def sitzung_pruefen(keks):
    """Liefert (kennung, stufe) oder (None, None)."""
    try:
        roh = base64.urlsafe_b64decode(keks.encode())
        inhalt, unterschrift = roh.rsplit(b'|', 1)
        soll = hmac.new(sitzungsschluessel(), inhalt, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(soll.encode(), unterschrift):
            return (None, None)
        nutzer, seit, bis, stufe = inhalt.decode().rsplit('|', 3)
        if int(bis) <= time.time():
            return (None, None)
        if int(seit) < sitzung_gesperrt_ab(nutzer):
            return (None, None)           # widerrufen: Sperre, neues Passwort, 2FA, Abmelden
        return (nutzer, stufe)
    except Exception:
        return (None, None)


# Sprache
# Alle Nutzertexte stehen hier, je Sprache ein Wörterbuch mit denselben
# Schluesseln. Das Journal (protokoll) bleibt deutsch - es liest nur der
# Betreiber. Platzhalter sind str.format-Felder; wer einen Wert einsetzt, der
# aus Formularen oder von Synapse kommt, maskiert ihn vorher mit html.escape,
# denn die Texte landen ungeschützt im HTML (einige enthalten <b>).

TEXTE = {
  'de': {
    # Allgemein
    'titel_einladungen': 'Einladungen',
    'titel_verwaltung': 'Verwaltung',
    'nicht_gefunden': 'Nicht gefunden',
    'kein_zugriff': 'Kein Zugriff',
    'kein_zugriff_text': 'Diese Seite ist den Verwaltern des Servers vorbehalten.',
    'zu_meinen_einladungen': 'Zu meinen Einladungen',
    'zurueck': 'Zurück',
    'abbrechen': 'Abbrechen',
    'abmelden': 'Abmelden',
    'qr': 'QR-Code',
    'betreiber_vorgabe': 'dem Betreiber',
    'unbegrenzt': 'unbegrenzt',
    # Anmeldung
    'anmelden_kopf': 'Jemanden einladen',
    'anmelden_kopf_verwaltung': 'Serververwaltung',
    'anmelden_unter': 'Melde dich mit deinem gewohnten Konto an — dasselbe wie im Chat.',
    'anmelden_unter_verwaltung': 'Anmeldung für die Verwalter dieses Servers.',
    'benutzername': 'Benutzername',
    'passwort': 'Passwort',
    'anmelden': 'Anmelden',
    'anmeldung_falsch': 'Benutzername oder Passwort stimmt nicht.',
    'anmeldung_gebremst': 'Zu viele Versuche kurz hintereinander. Bitte eine Minute warten '
                          'und es dann nochmal probieren — dein Passwort ist vermutlich richtig.',
    'anmeldung_weg': 'Der Chat-Server antwortet gerade nicht. Bitte später nochmal.',
    # Code aus der App
    'code_titel': 'Bestätigung',
    'code_kopf': 'Code aus der App',
    'code_unter': 'Dein Passwort stimmt. Jetzt noch die sechs Ziffern aus deiner Authenticator-App.',
    'code_feld': 'Sechsstelliger Code',
    'weiter': 'Weiter',
    'code_zu_viele': 'Zu viele falsche Codes. Bitte eine Viertelstunde warten.',
    'code_falsch': 'Der Code stimmt nicht. Stimmt die Uhrzeit auf deinem Telefon?',
    # Zweiter Faktor
    'zf_titel': 'Zweiter Faktor',
    'zf_an_kopf': 'Zweiter Faktor ist an',
    'zf_an_text': 'Beim Anmelden wirst du zusätzlich nach dem Code aus deiner App gefragt.',
    'zf_an_leise': 'Zum Abschalten brauchst du einen gültigen Code — damit niemand das heimlich '
                   'ausknipst, der kurz an deinem Rechner sitzt.',
    'zf_code_bestaetigung': 'Code zur Bestätigung',
    'zf_abschalten': 'Zweiten Faktor abschalten',
    'zf_einrichten_kopf': 'Zweiten Faktor einrichten',
    'zf_scannen': 'Scanne das Bild mit einer Authenticator-App — zum Beispiel <b>Aegis</b> (Android), '
                  '<b>Ente Auth</b> oder der Passwort-App deines Telefons.',
    'zf_von_hand': 'Geht das Scannen nicht, tippe diesen Schlüssel von Hand ein:',
    'zf_bestaetigen_feld': 'Zur Bestätigung: der Code, den die App jetzt anzeigt',
    'einschalten': 'Einschalten',
    'zf_scharf': 'Erst wenn ein richtiger Code ankommt, wird der zweite Faktor scharf. '
                 'Du kannst dich also nicht aussperren.',
    'spaeter': 'Später',
    'zf_ist_aus': 'Zweiter Faktor ist aus.',
    'zf_ist_an': 'Zweiter Faktor ist an. Ab jetzt beim Anmelden nötig.',
    # Übersicht
    'hallo': 'Hallo {name}',
    'restlauf_heute': 'läuft heute ab',
    'restlauf_ein_tag': 'noch 1 Tag',
    'restlauf_tage': 'noch {tage} Tage',
    'angenommen': 'angenommen',
    'eingeloest': 'eingelöst',
    'zuordnung_unklar': 'Zuordnung unklar',
    'zurueckgezogen': 'zurückgezogen',
    'abgelaufen': 'abgelaufen',
    'offen': 'offen',
    'einladung_zurueckziehen': 'Einladung zurückziehen',
    'raumeinladung_fehlgeschlagen': 'Raumeinladung klappte nicht',
    'deine_einladungen': 'Deine Einladungen',
    'fertig_link': 'Fertig — diesen Link verschicken',
    'fertig_link_text': 'Per WhatsApp, SMS oder wie du magst. Er gilt {tage} Tage '
                        'und funktioniert genau einmal.',
    'per_whatsapp': 'Per WhatsApp schicken',
    'wohin': 'Wohin soll die Person?',
    'nirgendwohin': '— erstmal nirgendwohin —',
    'kein_raum': 'Du bist noch in keinem benannten Raum. Lege im Chat einen an, '
                 'dann kannst du Leute gleich dorthin einladen.',
    'sprache_der_einladung': 'Sprache der Einladung',
    'sprache_automatisch': 'Automatisch (Sprache des Eingeladenen)',
    'einladung_erzeugen': 'Einladung erzeugen',
    'nicht_freigeschaltet': 'Für dich sind Einladungen nicht freigeschaltet. '
                            'Sag {betreiber} Bescheid, wenn du jemanden holen möchtest.',
    'keine_mehr': 'Du hast gerade keine Einladungen mehr übrig. '
                  'Sag {betreiber} Bescheid, wenn du weitere brauchst.',
    'du_kannst_einladen': 'Du kannst noch einladen',
    'ohne_begrenzung': 'ohne Begrenzung',
    'von_insgesamt': 'von {gesamt} insgesamt',
    'verwalten_knopf': 'Nutzer und Kontingente verwalten',
    # Verwaltung
    'anzahl_einladungen': 'Anzahl Einladungen',
    'neues_passwort_fuer': 'Neues Passwort für {wer}',
    'einmal_sichtbar': 'Einmal sichtbar. Gib es weiter und lass es ändern.',
    'verwalter': 'Verwalter',
    'gesperrt': 'gesperrt',
    'entsperren': 'Entsperren',
    'passwort_neu': 'Passwort neu',
    'verwalter_aus': 'Verwalter aus',
    'verwalter_an': 'Verwalter an',
    'sperren': 'Sperren',
    'das_bist_du': 'das bist du',
    'setzen': 'Setzen',
    'verschickt_offen': '{benutzt} Einladungen verschickt · {offen} offen',
    'unklar': 'unklar',
    'geholt_von': 'geholt von {wer}',
    'wer_hat_wen': 'Wer hat wen geholt',
    'kein_zf_kopf': 'Dein Konto hat keinen zweiten Faktor',
    'kein_zf_text': 'Diese Seite kann Konten anlegen, sperren und Passwörter ändern. '
                    'Mit nur einem Passwort davor ist das dünn.',
    'jetzt_einrichten': 'Jetzt einrichten',
    'vorgabe_kopf': 'Vorgabe für neue Konten',
    'vorgabe_text': 'So viele Einladungen bekommt, wer neu dazukommt. '
                    '<b>0</b> bedeutet: darf niemanden einladen.',
    'speichern': 'Speichern',
    'sprache_kopf': 'Sprache / Language',
    'anleitung_kopf': 'Verwaltungsanleitung',
    'anleitung_text': 'Alles über diesen Server für den Betreiber: Adressen, Netz, Sicherung, Aktualisieren, '
                      'was zu tun ist, wenn etwas klemmt. Vom Installer erzeugt, ohne Passwörter.',
    'anleitung_knopf': 'Anleitung öffnen',
    'anleitung_fehlt': 'Die Datei fehlt auf dem Server. Der Installer legt sie bei seinem nächsten Lauf an.',
    'sprache_text': 'Gilt für alle Seiten dieses Dienstes. Die Einladungsseite folgt der Sprache '
                    'der Einladung, sonst der des Browsers.',
    'sprache_gesetzt': 'Sprache: Deutsch.',
    'sprache_ungueltig': 'Unbekannte Sprache.',
    'anlegen_kopf': 'Konto von Hand anlegen',
    'anlegen_text': 'Normalerweise nicht nötig — dafür gibt es Einladungen. '
                    'Nützlich, wenn jemand keinen Link öffnen kann.',
    'benutzername_klein': 'Benutzername (klein, ohne Leerzeichen)',
    'anzeigename': 'Anzeigename (frei, z.B. „Oma Erna“)',
    'anlegen': 'Anlegen',
    'alle_konten': 'Alle Konten',
    'meine_einladungen': 'Meine eigenen Einladungen',
    # Rückfragen
    'pw_titel': 'Passwort',
    'pw_wen_selbst': 'dein eigenes Passwort',
    'pw_wen_anderer': 'das Passwort von {kurz}',
    'pw_frage': 'Wirklich {wen} zurücksetzen?',
    'pw_folge_selbst': '<b>Du wirst sofort überall abgemeldet</b> — auf dem Handy, im Browser, auch hier. '
                       'Das neue Passwort siehst du <b>genau einmal</b>; notiere es, bevor du weiterklickst.',
    'pw_folge_anderer': '<b>{kurz} wird auf allen Geräten abgemeldet</b> und braucht das neue Passwort '
                        'von dir. Es wird <b>genau einmal</b> angezeigt.',
    'pw_schluessel': 'Verschlüsselte Nachrichten bleiben lesbar, sobald das Konto auf einem Gerät '
                     'wieder angemeldet und die Schlüsselsicherung eingegeben ist.',
    'pw_ja': 'Ja, zurücksetzen',
    'sperren_titel': 'Sperren',
    'sperren_frage': '{kurz} wirklich sperren?',
    'sperren_text': 'Das Konto kann sich danach nicht mehr anmelden und wird aus allen Räumen entfernt. '
                    'Seine offenen Einladungen werden zurückgezogen. '
                    '<b>Der Chatverlauf der anderen bleibt erhalten.</b>',
    'sperren_einladungen_weg': '{n} offene Einladung(en) zurückgezogen.',
    'sperren_leise': 'Rückgängig machbar: Beim Entsperren vergibt der Server ein neues Passwort. '
                     'Die alten Schlüssel sind dann aber weg — frühere verschlüsselte Nachrichten '
                     'kann das Konto nicht mehr lesen.',
    'sperren_ja': 'Ja, sperren',
    # Aktualisierungen
    'vor_unbekannt': 'unbekannt',
    'gerade_eben': 'gerade eben',
    'vor_minute': 'vor 1 Minute',
    'vor_minuten': 'vor {n} Minuten',
    'vor_stunde': 'vor 1 Stunde',
    'vor_stunden': 'vor {n} Stunden',
    'vor_tag': 'vor 1 Tag',
    'vor_tagen': 'vor {n} Tagen',
    'upd_kopf': 'Aktualisierungen',
    'upd_laeuft': '<b>Aktualisierung läuft gerade</b> <span class="leise">(begonnen {wann}). '
                  'Der Chat kann dabei kurz nicht erreichbar sein; lade diese Seite in ein paar '
                  'Minuten neu.</span>',
    'upd_erfolgreich': 'erfolgreich',
    'upd_abgebrochen': 'abgebrochen <span class="leise">(nicht zu Ende gekommen, etwa durch einen '
                       'Neustart - einfach erneut anstoßen)</span>',
    'upd_mit_fehlern': 'mit Fehlern',
    'protokoll': 'Protokoll',
    'upd_letzte': 'Letzte Aktualisierung {wann}: ',
    'upd_nicht_geprueft': 'Noch nicht geprüft.',
    'upd_alt': 'Zuletzt geprüft {wann} — die tägliche Prüfung läuft offenbar nicht mehr. '
               'Sag {betreiber} Bescheid.',
    'upd_kein_befund': 'Die tägliche Prüfung hat noch kein Ergebnis abgelegt. Schau morgen wieder herein.',
    'upd_befund_unlesbar': 'Befund unlesbar',
    'upd_nicht_installiert': 'nicht installiert',
    'upd_neueste_unbekannt': 'neueste Fassung gerade nicht ermittelbar',
    'aktuell': 'aktuell',
    'upd_neu': 'neu: {neu}',
    'upd_synapse': 'Synapse (Chat-Server)',
    'upd_element': 'Element (Chat im Browser)',
    'upd_pakete': 'Systempakete',
    'upd_alles_aktuell': 'alles aktuell',
    'upd_ausstehend': '{n} ausstehend',
    'upd_davon_sicherheit': ', davon {n} Sicherheit',
    'upd_welche': 'Welche Pakete',
    'upd_auftrag_liegt': 'Ein Auftrag liegt vor und wird gleich ausgeführt.',
    'jetzt_aktualisieren': 'Jetzt aktualisieren',
    'upd_nachgefragt': 'Vorher wird nachgefragt. Von allein wird nie etwas eingespielt.',
    'upd_geprueft': 'Geprüft {wann}. Eingespielt wird nur auf Klick.',
    'upd_sicher_titel': 'Aktualisieren',
    'upd_sicher_frage': 'Jetzt wirklich aktualisieren?',
    'upd_sicher_hintergrund': 'Das passiert dann im Hintergrund:',
    'upd_punkt_paket_ein': '<b>1 Systempaket</b>{davon} wird eingespielt.',
    'upd_punkt_pakete': '<b>{n} Systempakete</b>{davon} werden eingespielt.',
    'upd_davon_ein': ' (davon 1 Sicherheitsupdate)',
    'upd_davon_mehr': ' (davon {n} Sicherheitsupdates)',
    'upd_punkt_synapse': '<b>Synapse</b> (der Chat-Server) wird auf {neu} gebracht und <b>startet neu</b> — '
                         'der Chat ist dabei kurz nicht erreichbar. Apps verbinden sich danach '
                         'von selbst wieder.',
    'upd_punkt_element': '<b>Element</b> (Chat im Browser) wird auf {neu} gebracht — wer die Seite '
                         'offen hat, lädt sie einmal neu.',
    'upd_sicher_dauer': 'Das dauert einige Minuten. Das Ergebnis erscheint danach oben auf der '
                        'Verwaltungsseite; bis dahin wird kein zweiter Auftrag angenommen.',
    'upd_ja': 'Ja, jetzt aktualisieren',
    'upd_schon_beauftragt': 'Eine Aktualisierung ist schon beauftragt oder läuft gerade. '
                            'Das Ergebnis erscheint hier.',
    'upd_nichts': 'Es liegt nichts an, das eingespielt werden müsste.',
    'upd_auftrag_fehler': 'Der Auftrag ließ sich nicht ablegen. Sag {betreiber} Bescheid.',
    'upd_angenommen': 'Auftrag angenommen. Die Aktualisierung läuft im Hintergrund und dauert '
                      'einige Minuten; das Ergebnis erscheint hier oben.',
    # Einladungsseite
    'einladung_titel': 'Einladung',
    'einl_zurueckgezogen': 'wurde zurückgezogen',
    'einl_abgelaufen': 'ist abgelaufen',
    'einl_benutzt': 'wurde schon benutzt',
    'einl_ungueltig_kopf': 'Diese Einladung {grund}',
    'einl_neue': 'Bitte frag {wer} nach einer neuen.',
    'einl_kopf': '{wer} lädt dich ein',
    'einl_unter': 'In einen Chat, bei dem kein Konzern mitliest. Drei Schritte.',
    'einl_schritt1': 'Konto anlegen',
    'einl_schritt1_text': 'Im Browser, mit Benutzername und Passwort deiner Wahl. '
                          'Wenn nach einem Einladungscode gefragt wird, ist es dieser:',
    'einl_schritt2': 'App installieren',
    'einl_schritt2_text': 'Suche im App Store oder bei Google Play nach <b>FluffyChat</b> '
                          '(oder <b>Element X</b>). Konto anlegen geht nur im Browser, '
                          'anmelden dann in der App.',
    'einl_schritt3': 'In der App anmelden',
    'einl_schritt3_text': 'Als Server eintragen:',
    'einl_schritt3_text2': 'dann Benutzername und Passwort aus Schritt 1.',
    'einl_browser': 'Konto jetzt anlegen',
    'einl_nicht_weiterleiten': 'Ohne diesen Code kommt niemand auf den Server — es gibt keine '
                               'offene Anmeldung. Deshalb bitte nicht weiterleiten.',
    'einl_gibt_es_nicht': 'Diese Einladung gibt es nicht',
    'einl_abgeschnitten': 'Vielleicht ist der Link unterwegs abgeschnitten worden.',
    # Meldungen aus den Vorgängen
    'nicht_geklappt': 'Das hat nicht geklappt',
    'server_antwortet_nicht': 'Der Server antwortet gerade nicht. Bitte später nochmal — '
                              'oder sag {betreiber} Bescheid.',
    'raum_nicht_bestaetigt': 'Der gewählte Raum ließ sich gerade nicht bestätigen. '
                             'Bitte die Seite neu laden und nochmal versuchen.',
    'einl_gibt_es_nicht_kurz': 'Diese Einladung gibt es nicht.',
    'einl_schon_benutzt': 'Diese Einladung ist schon benutzt oder zurückgezogen.',
    'einl_zu_spaet': 'Zu spät — die Einladung wurde gerade eingelöst.',
    'einl_zurueckgezogen_ok': 'Einladung zurückgezogen. Der Link funktioniert nicht mehr, '
                              'dein Kontingent ist wieder frei.',
    'zahl_oder_unbegrenzt': 'Bitte eine Zahl eintragen oder „unbegrenzt“ ankreuzen.',
    'zahl_zwischen': 'Bitte eine Zahl zwischen 0 und {max}.',
    'vorgabe_steht': 'Vorgabe steht auf {menge}.',
    'unbekanntes_konto': 'Unbekanntes Konto.',
    'kontingent_gesetzt': '{kurz}: {menge}.',
    'name_regel': 'Benutzername: nur Kleinbuchstaben, Ziffern und . _ = -',
    'name_vergeben': 'Diesen Benutzernamen gibt es schon: {name}.',
    'konto_angelegt': 'Konto {name} angelegt.',
    'pw_gesetzt': 'Neues Passwort für {kurz}. Alle Geräte wurden abgemeldet.',
    'selbst_sperren': 'Dich selbst kannst du nicht sperren.',
    'ist_gesperrt': '{kurz} ist gesperrt.',
    'wieder_frei': '{kurz} ist wieder frei — mit neuem Passwort.',
    'eigene_rechte': 'Deine eigenen Rechte kannst du hier nicht ändern.',
    'unklare_anfrage': 'Unklare Anfrage. Bitte die Seite neu laden.',
    'jetzt_verwalter': '{kurz} ist jetzt Verwalter.',
    'kein_verwalter_mehr': '{kurz} ist kein Verwalter mehr.',
    'server_abgelehnt': 'Der Server hat abgelehnt (HTTP {code}).',
    'nochmal': 'Das hat nicht geklappt. Bitte nochmal.',
    # Erstanmeldung und Pflicht zum zweiten Faktor
    'erst_titel': 'Erstanmeldung',
    'erst_kopf': 'Eigenes Passwort festlegen',
    'erst_schritt1': 'Schritt 1 von 2',
    'erst_schritt2': 'Schritt 2 von 2',
    'erst_unter': 'Das Startpasswort aus der Einrichtung gilt nur für diese erste Anmeldung. '
                  'Danach folgt der zweite Faktor, erst dann ist das Konto im Chat nutzbar.',
    'pw_neu_feld': 'Neues Passwort',
    'pw_nochmal_feld': 'Neues Passwort wiederholen',
    'pw_regel': 'Mindestens 12 Zeichen mit Groß- und Kleinbuchstaben, Ziffern oder Sonderzeichen '
                '— oder eine Passphrase ab 16 Zeichen.',
    'pw_ungleich': 'Die beiden Eingaben stimmen nicht überein.',
    'pw_zu_schwach': 'Das Passwort ist zu schwach.',
    'pw_wie_vorher': 'Bitte ein anderes Passwort als das Startpasswort wählen.',
    'zf_angebot_text': 'Als Verwalter kannst du Konten anlegen, sperren und Passwörter ändern. '
                       'Ein zweiter Faktor schützt das, falls dein Passwort einmal in falsche Hände gerät.',
    'zf_ablehnen_kopf': 'Ohne zweiten Faktor weiter',
    'zf_ablehnen_text': 'Dann schützt nur dein Passwort die Verwaltung. Einrichten kannst du den zweiten '
                        'Faktor später jederzeit unter Verwaltung → Zweiter Faktor.',
    'zf_risiko_ok': 'Ich habe verstanden, dass das ein Risiko ist.',
    'zf_ablehnen': 'Ohne zweiten Faktor fortfahren',
    'erst_fertig_ohne_zf': 'Dein Passwort steht, und dein Konto ist jetzt auch im Chat freigeschaltet. '
                           'Ein zweiter Faktor ist nicht eingerichtet; die Verwaltung erinnert dich daran.',
    'erst_fertig_kopf': 'Fertig eingerichtet',
    'erst_fertig_text': 'Passwort und zweiter Faktor stehen. Dein Konto ist jetzt auch im Chat freigeschaltet. '
                        'Melde dich dort mit deinem neuen Passwort an.',
    'erst_zum_chat': 'Weiter zum Chat',
    'erst_zur_verwaltung': 'Zur Verwaltung',
    'erst_entsperren_fehler': 'Der zweite Faktor ist eingerichtet, aber der Chat-Server hat das Konto '
                              'nicht freigeschaltet. Bitte gleich nochmal versuchen.',
    'nochmal_versuchen': 'Nochmal versuchen',
    # Kommandozeile (Notausgang für den Betreiber)
    'cli_kein_zf': '{nutzer}: kein zweiter Faktor hinterlegt.',
    'cli_zf_entfernt': '{nutzer}: zweiter Faktor entfernt. Beim nächsten Anmelden reicht das Passwort.',
    'cli_aufruf': 'Aufruf:\n'
                  '  einladung.py                         Dienst starten\n'
                  '  einladung.py --zweitfaktor-aus NAME  zweiten Faktor eines Kontos zurücksetzen',
  },
  'en': {
    # Allgemein
    'titel_einladungen': 'Invitations',
    'titel_verwaltung': 'Admin',
    'nicht_gefunden': 'Not found',
    'kein_zugriff': 'No access',
    'kein_zugriff_text': 'This page is reserved for the admins of this server.',
    'zu_meinen_einladungen': 'To my invitations',
    'zurueck': 'Back',
    'abbrechen': 'Cancel',
    'abmelden': 'Log out',
    'qr': 'QR code',
    'betreiber_vorgabe': 'the operator',
    'unbegrenzt': 'unlimited',
    # Anmeldung
    'anmelden_kopf': 'Invite someone',
    'anmelden_kopf_verwaltung': 'Server admin',
    'anmelden_unter': 'Log in with your usual account — the same one as in the chat.',
    'anmelden_unter_verwaltung': 'Login for the admins of this server.',
    'benutzername': 'Username',
    'passwort': 'Password',
    'anmelden': 'Log in',
    'anmeldung_falsch': 'Username or password is wrong.',
    'anmeldung_gebremst': 'Too many attempts in a row. Please wait a minute and try again — '
                          'your password is probably right.',
    'anmeldung_weg': 'The chat server is not responding right now. Please try again later.',
    # Code aus der App
    'code_titel': 'Confirmation',
    'code_kopf': 'Code from the app',
    'code_unter': 'Your password is correct. Now the six digits from your authenticator app.',
    'code_feld': 'Six-digit code',
    'weiter': 'Continue',
    'code_zu_viele': 'Too many wrong codes. Please wait fifteen minutes.',
    'code_falsch': 'The code is wrong. Is the time on your phone correct?',
    # Zweiter Faktor
    'zf_titel': 'Second factor',
    'zf_an_kopf': 'Second factor is on',
    'zf_an_text': 'When logging in you will also be asked for the code from your app.',
    'zf_an_leise': 'Switching it off needs a valid code — so nobody who briefly sits at your '
                   'computer can quietly turn it off.',
    'zf_code_bestaetigung': 'Code to confirm',
    'zf_abschalten': 'Switch off second factor',
    'zf_einrichten_kopf': 'Set up second factor',
    'zf_scannen': 'Scan the image with an authenticator app — for example <b>Aegis</b> (Android), '
                  "<b>Ente Auth</b> or your phone's password app.",
    'zf_von_hand': "If scanning doesn't work, type this key in by hand:",
    'zf_bestaetigen_feld': 'To confirm: the code the app shows right now',
    'einschalten': 'Switch on',
    'zf_scharf': 'The second factor only goes live once a correct code arrives. '
                 'So you cannot lock yourself out.',
    'spaeter': 'Later',
    'zf_ist_aus': 'Second factor is off.',
    'zf_ist_an': 'Second factor is on. Required at login from now on.',
    # Übersicht
    'hallo': 'Hello {name}',
    'restlauf_heute': 'expires today',
    'restlauf_ein_tag': '1 day left',
    'restlauf_tage': '{tage} days left',
    'angenommen': 'accepted',
    'eingeloest': 'redeemed',
    'zuordnung_unklar': 'Assignment unclear',
    'zurueckgezogen': 'revoked',
    'abgelaufen': 'expired',
    'offen': 'open',
    'einladung_zurueckziehen': 'Revoke invitation',
    'raumeinladung_fehlgeschlagen': 'Room invitation failed',
    'deine_einladungen': 'Your invitations',
    'fertig_link': 'Done — send this link',
    'fertig_link_text': 'Via WhatsApp, SMS or however you like. It is valid for {tage} days '
                        'and works exactly once.',
    'per_whatsapp': 'Send via WhatsApp',
    'wohin': 'Where should this person go?',
    'nirgendwohin': '— nowhere for now —',
    'kein_raum': 'You are not in any named room yet. Create one in the chat, '
                 'then you can invite people straight into it.',
    'sprache_der_einladung': 'Language of the invitation',
    'sprache_automatisch': 'Automatic (the invitee\'s browser language)',
    'einladung_erzeugen': 'Create invitation',
    'nicht_freigeschaltet': 'Invitations are not enabled for you. '
                            'Ask {betreiber} if you want to bring someone in.',
    'keine_mehr': 'You have no invitations left right now. '
                  'Ask {betreiber} if you need more.',
    'du_kannst_einladen': 'You can still invite',
    'ohne_begrenzung': 'no limit',
    'von_insgesamt': 'of {gesamt} in total',
    'verwalten_knopf': 'Manage users and quotas',
    # Verwaltung
    'anzahl_einladungen': 'Number of invitations',
    'neues_passwort_fuer': 'New password for {wer}',
    'einmal_sichtbar': 'Shown once. Pass it on and have it changed.',
    'verwalter': 'Admin',
    'gesperrt': 'blocked',
    'entsperren': 'Unblock',
    'passwort_neu': 'New password',
    'verwalter_aus': 'Admin off',
    'verwalter_an': 'Admin on',
    'sperren': 'Block',
    'das_bist_du': "that's you",
    'setzen': 'Set',
    'verschickt_offen': '{benutzt} invitations sent · {offen} open',
    'unklar': 'unclear',
    'geholt_von': 'brought in by {wer}',
    'wer_hat_wen': 'Who brought in whom',
    'kein_zf_kopf': 'Your account has no second factor',
    'kein_zf_text': 'This page can create accounts, block them and change passwords. '
                    'A single password in front of that is not enough.',
    'jetzt_einrichten': 'Set it up now',
    'vorgabe_kopf': 'Default for new accounts',
    'vorgabe_text': 'This many invitations go to everyone who joins. '
                    '<b>0</b> means: may not invite anyone.',
    'speichern': 'Save',
    'sprache_kopf': 'Sprache / Language',
    'anleitung_kopf': 'Operator guide',
    'anleitung_text': 'Everything about this server for the operator: addresses, network, backup, updating, '
                      'what to do when something is stuck. Generated by the installer, no passwords.',
    'anleitung_knopf': 'Open guide',
    'anleitung_fehlt': 'The file is missing on the server. The installer creates it on its next run.',
    'sprache_text': 'Applies to all pages of this service. The invitation page follows the language '
                    "of the invitation, otherwise the browser's.",
    'sprache_gesetzt': 'Language: English.',
    'sprache_ungueltig': 'Unknown language.',
    'anlegen_kopf': 'Create account by hand',
    'anlegen_text': "Usually not needed — that's what invitations are for. "
                    'Useful when someone cannot open a link.',
    'benutzername_klein': 'Username (lowercase, no spaces)',
    'anzeigename': 'Display name (free, e.g. "Grandma Erna")',
    'anlegen': 'Create',
    'alle_konten': 'All accounts',
    'meine_einladungen': 'My own invitations',
    # Rückfragen
    'pw_titel': 'Password',
    'pw_wen_selbst': 'your own password',
    'pw_wen_anderer': "{kurz}'s password",
    'pw_frage': 'Really reset {wen}?',
    'pw_folge_selbst': '<b>You will be logged out everywhere immediately</b> — on the phone, in the '
                       'browser, here too. You will see the new password <b>exactly once</b>; '
                       'write it down before you continue.',
    'pw_folge_anderer': '<b>{kurz} will be logged out on all devices</b> and needs the new password '
                        'from you. It is shown <b>exactly once</b>.',
    'pw_schluessel': 'Encrypted messages stay readable once the account is logged in again on a '
                     'device and the key backup has been entered.',
    'pw_ja': 'Yes, reset',
    'sperren_titel': 'Block',
    'sperren_frage': 'Really block {kurz}?',
    'sperren_text': 'The account can no longer log in afterwards and is removed from all rooms. '
                    'Its open invitations are revoked. '
                    "<b>The other people's chat history is kept.</b>",
    'sperren_einladungen_weg': '{n} open invitation(s) revoked.',
    'sperren_leise': 'Reversible: on unblocking, the server issues a new password. The old keys '
                     'are gone by then, though — the account can no longer read earlier '
                     'encrypted messages.',
    'sperren_ja': 'Yes, block',
    # Aktualisierungen
    'vor_unbekannt': 'unknown',
    'gerade_eben': 'just now',
    'vor_minute': '1 minute ago',
    'vor_minuten': '{n} minutes ago',
    'vor_stunde': '1 hour ago',
    'vor_stunden': '{n} hours ago',
    'vor_tag': '1 day ago',
    'vor_tagen': '{n} days ago',
    'upd_kopf': 'Updates',
    'upd_laeuft': '<b>Update in progress</b> <span class="leise">(started {wann}). '
                  'The chat may be briefly unreachable; reload this page in a few minutes.</span>',
    'upd_erfolgreich': 'successful',
    'upd_abgebrochen': 'aborted <span class="leise">(did not finish, e.g. because of a reboot — '
                       'just trigger it again)</span>',
    'upd_mit_fehlern': 'with errors',
    'protokoll': 'Log',
    'upd_letzte': 'Last update {wann}: ',
    'upd_nicht_geprueft': 'Not checked yet.',
    'upd_alt': "Last checked {wann} — the daily check apparently isn't running any more. "
               'Tell {betreiber}.',
    'upd_kein_befund': 'The daily check has not stored a result yet. Come back tomorrow.',
    'upd_befund_unlesbar': 'result unreadable',
    'upd_nicht_installiert': 'not installed',
    'upd_neueste_unbekannt': 'latest version unknown right now',
    'aktuell': 'up to date',
    'upd_neu': 'new: {neu}',
    'upd_synapse': 'Synapse (chat server)',
    'upd_element': 'Element (chat in the browser)',
    'upd_pakete': 'System packages',
    'upd_alles_aktuell': 'all up to date',
    'upd_ausstehend': '{n} pending',
    'upd_davon_sicherheit': ', {n} of them security',
    'upd_welche': 'Which packages',
    'upd_auftrag_liegt': 'A job is queued and will run shortly.',
    'jetzt_aktualisieren': 'Update now',
    'upd_nachgefragt': 'You will be asked first. Nothing is ever installed on its own.',
    'upd_geprueft': 'Checked {wann}. Installed only on a click.',
    'upd_sicher_titel': 'Update',
    'upd_sicher_frage': 'Really update now?',
    'upd_sicher_hintergrund': 'This will then happen in the background:',
    'upd_punkt_paket_ein': '<b>1 system package</b>{davon} will be installed.',
    'upd_punkt_pakete': '<b>{n} system packages</b>{davon} will be installed.',
    'upd_davon_ein': ' (1 of them a security update)',
    'upd_davon_mehr': ' ({n} of them security updates)',
    'upd_punkt_synapse': '<b>Synapse</b> (the chat server) will be updated to {neu} and <b>restarts</b> — '
                         'the chat is briefly unreachable. Apps reconnect on their own afterwards.',
    'upd_punkt_element': '<b>Element</b> (chat in the browser) will be updated to {neu} — anyone with '
                         'the page open just needs to reload it once.',
    'upd_sicher_dauer': 'This takes a few minutes. The result then appears at the top of the '
                        'admin page; until then no second job is accepted.',
    'upd_ja': 'Yes, update now',
    'upd_schon_beauftragt': 'An update is already queued or running. The result will appear here.',
    'upd_nichts': 'There is nothing that would need installing.',
    'upd_auftrag_fehler': 'The job could not be stored. Tell {betreiber}.',
    'upd_angenommen': 'Job accepted. The update runs in the background and takes a few minutes; '
                      'the result appears up here.',
    # Einladungsseite
    'einladung_titel': 'Invitation',
    'einl_zurueckgezogen': 'was revoked',
    'einl_abgelaufen': 'has expired',
    'einl_benutzt': 'has already been used',
    'einl_ungueltig_kopf': 'This invitation {grund}',
    'einl_neue': 'Please ask {wer} for a new one.',
    'einl_kopf': '{wer} invites you',
    'einl_unter': 'A chat where no big tech company is listening in. Three steps.',
    'einl_schritt1': 'Create your account',
    'einl_schritt1_text': 'In the browser, with a username and password of your choice. '
                          'If it asks for an invitation code, it is this one:',
    'einl_schritt2': 'Install the app',
    'einl_schritt2_text': 'Search the App Store or Google Play for <b>FluffyChat</b> '
                          '(or <b>Element X</b>). Creating the account only works in the '
                          'browser; logging in is done in the app.',
    'einl_schritt3': 'Log in to the app',
    'einl_schritt3_text': 'Enter as the server:',
    'einl_schritt3_text2': 'then the username and password from step 1.',
    'einl_browser': 'Create the account now',
    'einl_nicht_weiterleiten': 'Without this code nobody gets onto the server — there is no open '
                               'registration. So please do not forward it.',
    'einl_gibt_es_nicht': 'This invitation does not exist',
    'einl_abgeschnitten': 'Maybe the link got cut off on the way.',
    # Meldungen aus den Vorgängen
    'nicht_geklappt': "That didn't work",
    'server_antwortet_nicht': 'The server is not responding right now. Please try again later — '
                              'or tell {betreiber}.',
    'raum_nicht_bestaetigt': 'The chosen room could not be confirmed just now. '
                             'Please reload the page and try again.',
    'einl_gibt_es_nicht_kurz': 'This invitation does not exist.',
    'einl_schon_benutzt': 'This invitation has already been used or revoked.',
    'einl_zu_spaet': 'Too late — the invitation was just redeemed.',
    'einl_zurueckgezogen_ok': 'Invitation revoked. The link no longer works, your quota is free again.',
    'zahl_oder_unbegrenzt': 'Please enter a number or tick "unlimited".',
    'zahl_zwischen': 'Please enter a number between 0 and {max}.',
    'vorgabe_steht': 'Default is now {menge}.',
    'unbekanntes_konto': 'Unknown account.',
    'kontingent_gesetzt': '{kurz}: {menge}.',
    'name_regel': 'Username: only lowercase letters, digits and . _ = -',
    'name_vergeben': 'This username already exists: {name}.',
    'konto_angelegt': 'Account {name} created.',
    'pw_gesetzt': 'New password for {kurz}. All devices were logged out.',
    'selbst_sperren': 'You cannot block yourself.',
    'ist_gesperrt': '{kurz} is blocked.',
    'wieder_frei': '{kurz} is unblocked — with a new password.',
    'eigene_rechte': 'You cannot change your own rights here.',
    'unklare_anfrage': 'Unclear request. Please reload the page.',
    'jetzt_verwalter': '{kurz} is now an admin.',
    'kein_verwalter_mehr': '{kurz} is no longer an admin.',
    'server_abgelehnt': 'The server refused (HTTP {code}).',
    'nochmal': "That didn't work. Please try again.",
    # Erstanmeldung und Pflicht zum zweiten Faktor
    'erst_titel': 'First sign-in',
    'erst_kopf': 'Choose your own password',
    'erst_schritt1': 'Step 1 of 2',
    'erst_schritt2': 'Step 2 of 2',
    'erst_unter': 'The initial password from the setup only works for this first sign-in. '
                  'The second factor comes next; only then can the account be used in the chat.',
    'pw_neu_feld': 'New password',
    'pw_nochmal_feld': 'Repeat new password',
    'pw_regel': 'At least 12 characters with upper and lower case, digits or symbols '
                '— or a passphrase of 16 characters or more.',
    'pw_ungleich': 'The two entries do not match.',
    'pw_zu_schwach': 'The password is too weak.',
    'pw_wie_vorher': 'Please choose a password other than the initial one.',
    'zf_angebot_text': 'As an admin you can create and block accounts and change passwords. '
                       'A second factor protects that in case your password ever ends up in the wrong hands.',
    'zf_ablehnen_kopf': 'Continue without a second factor',
    'zf_ablehnen_text': 'Then only your password protects the admin page. You can set up the second '
                        'factor at any time later under Admin → Second factor.',
    'zf_risiko_ok': 'I understand that this is a risk.',
    'zf_ablehnen': 'Continue without second factor',
    'erst_fertig_ohne_zf': 'Your password is set and your account is now enabled in the chat as well. '
                           'No second factor is set up; the admin page will remind you.',
    'erst_fertig_kopf': 'All set',
    'erst_fertig_text': 'Password and second factor are in place. Your account is now enabled in the chat as well. '
                        'Sign in there with your new password.',
    'erst_zum_chat': 'Continue to the chat',
    'erst_zur_verwaltung': 'Go to the admin page',
    'erst_entsperren_fehler': 'The second factor is set up, but the chat server did not enable the account. '
                              'Please try again in a moment.',
    'nochmal_versuchen': 'Try again',
    # Kommandozeile (Notausgang für den Betreiber)
    'cli_kein_zf': '{nutzer}: no second factor stored.',
    'cli_zf_entfernt': '{nutzer}: second factor removed. The password alone is enough at the next login.',
    'cli_aufruf': 'Usage:\n'
                  '  einladung.py                         start the service\n'
                  '  einladung.py --zweitfaktor-aus NAME  reset the second factor of an account',
  },
}

SPRACHEN = ('en', 'de')


def t(schluessel, sprache, **werte):
    """Nutzertext in der gewuenschten Sprache; Platzhalter per str.format.

    Ein fehlender Schluessel ist ein Programmierfehler und soll krachen,
    nicht still eine leere Zeichenkette liefern - der Test prueft vorher,
    dass beide Tabellen dieselben Schluessel haben.
    """
    text = TEXTE[sprache if sprache in TEXTE else 'en'][schluessel]
    return text.format(**werte) if werte else text


def sprache_normieren(wert, rueckfall=None):
    """'de-DE' -> 'de', 'en_US' -> 'en', alles andere -> rueckfall."""
    w = (wert or '').strip().lower()
    for s in SPRACHEN:
        if w == s or w.startswith(s + '-') or w.startswith(s + '_'):
            return s
    return rueckfall


SPRACHE_VORGABE = sprache_normieren(os.environ.get('MESSENGER_LANGUAGE', ''), 'en')


def server_sprache():
    """Vom Verwalter gewaehlte Sprache; vorher die aus der Umgebung, sonst Englisch."""
    with db() as v:
        z = v.execute("SELECT wert FROM einstellung WHERE schluessel='sprache'").fetchone()
    return sprache_normieren(z['wert'] if z else '', SPRACHE_VORGABE)


def sprache_setzen(wert):
    with db() as v:
        v.execute("INSERT OR REPLACE INTO einstellung (schluessel, wert) VALUES ('sprache', ?)",
                  (sprache_normieren(wert, 'en'),))


def sprache_aus_header(accept_language):
    """Die erste Sprache aus Accept-Language, die wir koennen - sonst None.

    Nach Gewicht (q) sortiert, wie der Browser es meint: 'fr,en;q=0.5' ergibt
    Englisch, 'de-DE,de;q=0.9' Deutsch, 'fr' nichts (dann gilt die Serversprache).
    """
    eintraege = []
    for i, teil in enumerate((accept_language or '').split(',')):
        teil = teil.strip()
        if not teil:
            continue
        tag, _, rest = teil.partition(';')
        q = 1.0
        for p in rest.split(';'):
            p = p.strip()
            if p.startswith('q='):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        eintraege.append((-q, i, tag.strip()))
    for _, _, tag in sorted(eintraege):
        s = sprache_normieren(tag)
        if s:
            return s
    return None


def betreiber(sprache):
    """Anrede des Betreibers in Hinweisen. Ohne --betreiber die Vorgabe der Sprache.

    'dem Betreiber' schrieb der fruehere Installer als Vorgabe fest in die
    Umgebung - das ist keine Angabe, sondern die alte deutsche Vorgabe.
    """
    if BETREIBER and BETREIBER != 'dem Betreiber':
        return BETREIBER
    return t('betreiber_vorgabe', sprache)


def datum(zeitpunkt, sprache):
    """Datum in der Schreibweise der Sprache: 13.10.2026 bzw. 2026-10-13."""
    return time.strftime('%d.%m.%Y' if sprache == 'de' else '%Y-%m-%d', time.localtime(zeitpunkt))


# Seiten

STIL = """
:root{--grund:#f4f6f4;--flaeche:#fff;--schrift:#16201c;--leise:#5a6762;
       --linie:#d6dcd6;--eigen:#0e7c63;--warn:#a8551c}
@media (prefers-color-scheme:dark){:root{--grund:#0f1412;--flaeche:#171f1c;
       --schrift:#e7ede9;--leise:#97a49e;--linie:#2a3430;--eigen:#45c3a3;--warn:#e09a4c}}
*{box-sizing:border-box}
body{margin:0;background:var(--grund);color:var(--schrift);
     font:16px/1.6 system-ui,-apple-system,sans-serif;padding:24px 16px 56px}
.k{max-width:560px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
h1{font-size:28px;line-height:1.15;margin:0}
h2{font-size:19px;margin:0 0 6px}
p{margin:0}
.karte{background:var(--flaeche);border:1px solid var(--linie);border-radius:14px;padding:20px}
.gross{font-size:40px;font-weight:700;color:var(--eigen);line-height:1}
label{display:block;font-size:14px;color:var(--leise);margin-bottom:5px}
input,select{width:100%;padding:13px;font:inherit;border:1.5px solid var(--linie);
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
ol{margin:0;padding-left:22px}
ol li{margin-bottom:18px}
table{width:100%;border-collapse:collapse;font-size:14.5px;margin-top:10px}
td{padding:9px 0;border-bottom:1px solid var(--linie)}
table.kette td:last-child{text-align:right;color:var(--leise)}
.leise{color:var(--leise);font-size:14px}
.mono{font-family:ui-monospace,monospace;font-size:12.5px;word-break:break-all}
input.zahl{width:5.5em;padding:7px;font:inherit;border:1.5px solid var(--linie);
       border-radius:7px;background:var(--grund);color:var(--schrift);text-align:right}
.haken{display:inline-flex;align-items:center;gap:5px;font-size:13.5px;color:var(--leise);margin:0}
.haken input{width:auto;margin:0}
.taten{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-top:10px}
button.klein{width:auto;margin:0;padding:6px 11px;font-size:13.5px;background:transparent;
       color:var(--eigen);border:1.5px solid var(--eigen)}
button.klein.gefahr{color:var(--warn);border-color:var(--warn)}
button.gefahrgross{background:var(--warn)}
.marke.warnmarke{color:var(--warn);border-color:var(--warn)}
.vtab td{padding:14px 0}
.reihe{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.reihe button{width:auto;margin:0;padding:7px 13px;font-size:14px}
.sprachen .haken{font-size:15px;color:var(--schrift);margin-right:10px}
.vtab td{vertical-align:middle}

.marke{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--eigen);
       border:1px solid var(--eigen);border-radius:20px;padding:1px 8px;margin-left:6px}
.ok{color:var(--eigen);font-weight:600}
.staerke{display:flex;gap:4px;margin-top:8px}
.staerke i{flex:1;height:4px;border-radius:2px;background:var(--linie)}
.staerke i.an1,.staerke i.an2{background:var(--warn)}
.staerke i.an3,.staerke i.an4{background:var(--eigen)}
"""

# Tab-Symbol: weiße Sprechblase auf der Akzentfarbe, als data:-URI, damit die
# Seite keine zweite Datei ausliefern muss.
ICON = ("data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        "%3Crect width='32' height='32' rx='7' fill='%2345c3a3'/%3E"
        "%3Cpath d='M9 9h14a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-8l-5 4v-4H9a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3z' fill='%23fff'/%3E"
        "%3C/svg%3E")


def seite(titel, inhalt, sprache):
    return f"""<!doctype html><html lang="{sprache}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{ICON}">
<title>{html.escape(titel)}</title><style>{STIL}</style></head>
<body><div class="k">{inhalt}</div></body></html>""".encode()


def seite_anmelden(sprache, fehler='', verwaltung=False):
    """Dieselbe Anmeldung, aber unter dem Verwaltungsnamen mit passender Aufschrift.

    Vorher stand auch dort "Jemanden einladen" - das verwirrt, weil die Seite
    unter service.<domain> gerade NICHT zum Einladen da ist.
    """
    hinweis = f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else ''
    titel = t('titel_verwaltung' if verwaltung else 'titel_einladungen', sprache)
    kopf = t('anmelden_kopf_verwaltung' if verwaltung else 'anmelden_kopf', sprache)
    unter = t('anmelden_unter_verwaltung' if verwaltung else 'anmelden_unter', sprache)
    return seite(titel, f"""
      <h1>{kopf}</h1>
      <p class="leise">{unter}</p>
      {hinweis}
      <form method="post" action="/anmelden" class="karte">
        <label for="b">{t('benutzername', sprache)}</label>
        <input id="b" name="benutzer" autocomplete="username" autocapitalize="none" required>
        <div style="height:14px"></div>
        <label for="p">{t('passwort', sprache)}</label>
        <input id="p" name="passwort" type="password" autocomplete="current-password" required>
        <button type="submit">{t('anmelden', sprache)}</button>
      </form>""", sprache)


def seite_code(sprache, hinweis=''):
    fehler = f'<p class="fehler">{html.escape(hinweis)}</p>' if hinweis else ''
    return seite(t('code_titel', sprache), f"""
      <h1>{t('code_kopf', sprache)}</h1>
      <p class="leise">{t('code_unter', sprache)}</p>
      {fehler}
      <form method="post" action="/code" class="karte">
        <label for="c">{t('code_feld', sprache)}</label>
        <input id="c" name="code" inputmode="numeric" autocomplete="one-time-code"
               pattern="[0-9 ]{{6,9}}" required autofocus>
        <button type="submit">{t('weiter', sprache)}</button>
      </form>
      <a class="knopf still" href="/abmelden">{t('abbrechen', sprache)}</a>""", sprache)


def seite_zweitfaktor(nutzer, sprache, hinweis='', fehler='', pflicht=False):
    eingerichtet, aktiv = zf_stand(nutzer)
    meldung = f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else ''
    if hinweis:
        meldung += f'<div class="karte" style="border-color:var(--eigen)"><p><b>{html.escape(hinweis)}</b></p></div>'

    if aktiv:
        return seite(t('zf_titel', sprache), f"""
          <h1>{t('zf_an_kopf', sprache)}</h1>
          {meldung}
          <div class="karte">
            <p>{t('zf_an_text', sprache)}</p>
            <p class="leise" style="margin-top:12px">{t('zf_an_leise', sprache)}</p>
            <form method="post" action="/zweitfaktor" style="margin-top:14px">
              <input type="hidden" name="tat" value="aus">
              <label for="ca">{t('zf_code_bestaetigung', sprache)}</label>
              <input id="ca" name="code" inputmode="numeric" pattern="[0-9 ]{{6,9}}" required>
              <button type="submit" class="gefahrgross">{t('zf_abschalten', sprache)}</button>
            </form>
          </div>
          <a class="knopf still" href="/">{t('zurueck', sprache)}</a>""", sprache)

    g = zf_geheimnis(nutzer) if eingerichtet else None
    if not g:
        g = zf_vorbereiten(nutzer)
    uri = zf_uri(nutzer, g)
    lesbar = ' '.join(g[i:i + 4] for i in range(0, len(g), 4))

    if pflicht:
        schritt = t('erst_schritt2', sprache) if erst_eintrag(nutzer) else ''
        oben = (f'<p class="leise">{schritt}</p>' if schritt else '') + \
            f'<p class="leise">{t("zf_angebot_text", sprache)}</p>'
        unten = f"""
      <details class="karte">
        <summary>{t('zf_ablehnen_kopf', sprache)}</summary>
        <p class="leise" style="margin-top:10px">{t('zf_ablehnen_text', sprache)}</p>
        <form method="post" action="/zweitfaktor">
          <input type="hidden" name="tat" value="ablehnen">
          <label style="display:flex;gap:8px;align-items:flex-start;margin:10px 0">
            <input type="checkbox" name="risiko" value="ja" required style="width:auto;margin-top:3px">
            <span>{t('zf_risiko_ok', sprache)}</span></label>
          <button type="submit" class="gefahrgross">{t('zf_ablehnen', sprache)}</button>
        </form>
      </details>
      <a class="knopf still" href="/abmelden">{t('abmelden', sprache)}</a>"""
    else:
        oben = ''
        unten = f'<a class="knopf still" href="/">{t("spaeter", sprache)}</a>'
    return seite(t('zf_titel', sprache), f"""
      <h1>{t('zf_einrichten_kopf', sprache)}</h1>
      {oben}
      {meldung}
      <div class="karte">
        <p>{t('zf_scannen', sprache)}</p>
        <div style="text-align:center;margin:18px 0">{qr_svg(uri, beschriftung=t('qr', sprache))}</div>
        <p class="leise">{t('zf_von_hand', sprache)}</p>
        <div class="wert">{html.escape(lesbar)}</div>
      </div>
      <form method="post" action="/zweitfaktor" class="karte">
        <input type="hidden" name="tat" value="bestaetigen">
        <label for="cb">{t('zf_bestaetigen_feld', sprache)}</label>
        <input id="cb" name="code" inputmode="numeric" pattern="[0-9 ]{{6,9}}" required autofocus>
        <button type="submit">{t('einschalten', sprache)}</button>
      </form>
      <p class="leise">{t('zf_scharf', sprache)}</p>
      {unten}""", sprache)


def seite_erstpasswort(sprache, fehler=''):
    meldung = f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else ''
    return seite(t('erst_titel', sprache), f"""
      <h1>{t('erst_kopf', sprache)}</h1>
      <p class="leise">{t('erst_schritt1', sprache)}</p>
      <p class="leise">{t('erst_unter', sprache)}</p>
      {meldung}
      <form method="post" action="/erstpasswort" class="karte">
        <label for="n1">{t('pw_neu_feld', sprache)}</label>
        <input id="n1" name="neu" type="password" autocomplete="new-password" minlength="12" required autofocus>
        <div class="staerke"><i></i><i></i><i></i><i></i></div>
        <p class="leise" style="margin:6px 0 14px">{t('pw_regel', sprache)}</p>
        <label for="n2">{t('pw_nochmal_feld', sprache)}</label>
        <input id="n2" name="nochmal" type="password" autocomplete="new-password" minlength="12" required>
        <button type="submit">{t('weiter', sprache)}</button>
      </form>
      <a class="knopf still" href="/abmelden">{t('abmelden', sprache)}</a>
      <script>
      (function(){{
        var f=document.getElementById('n1'), b=document.querySelectorAll('.staerke i');
        f.addEventListener('input',function(){{
          var w=f.value, n=0;
          if(w.length>=12) n++;
          if(/[a-z]/.test(w)&&/[A-Z]/.test(w)) n++;
          if(/[0-9]/.test(w)) n++;
          if(/[^A-Za-z0-9]/.test(w)||w.length>=16) n++;
          for(var i=0;i<b.length;i++) b[i].className=i<n?'an'+n:'';
        }});
      }})();
      </script>""", sprache)


def seite_erst_fertig(sprache, ohne_zf=False):
    text = t('erst_fertig_ohne_zf' if ohne_zf else 'erst_fertig_text', sprache)
    return seite(t('erst_titel', sprache), f"""
      <h1>{t('erst_fertig_kopf', sprache)}</h1>
      <div class="karte">
        <p>{text}</p>
        <a class="knopf" href="{html.escape(ELEMENT)}">{t('erst_zum_chat', sprache)}</a>
      </div>
      <a class="knopf still" href="/">{t('erst_zur_verwaltung', sprache)}</a>""", sprache)


def restlaufzeit(sekunden, sprache):
    """'noch 3 Tage', 'noch 1 Tag' oder 'laeuft heute ab'.

    Vorher stand bei 20 Minuten Restzeit 'noch 1 Tage' - der Einladende
    verschickte den Link, und der Eingeladene sah 'abgelaufen'.
    """
    if sekunden < 86400:
        return t('restlauf_heute', sprache)
    tage = int(sekunden // 86400)
    if tage == 1:
        return t('restlauf_ein_tag', sprache)
    return t('restlauf_tage', sprache, tage=tage)


def sprachwahl(name, gewaehlt, kennung, automatisch=''):
    """Auswahlfeld fuer die Sprache - Namen der Sprachen stehen in ihrer eigenen Sprache.

    Mit 'automatisch' gibt es eine erste Option ohne Wert: dann entscheidet der
    Browser des Eingeladenen (Accept-Language), sonst die Serversprache. Das ist
    die Vorgabe beim Erzeugen - die deutsche Familie laedt den englischen Freund
    ein, ohne daran denken zu muessen.
    """
    namen = {'en': 'English', 'de': 'Deutsch'}
    wahl = (f'<option value=""{" selected" if not gewaehlt else ""}>{automatisch}</option>'
            if automatisch else '')
    wahl += ''.join(f'<option value="{s}"{" selected" if s == gewaehlt else ""}>{namen[s]}</option>'
                    for s in SPRACHEN)
    return f'<select id="{kennung}" name="{name}" style="width:100%">{wahl}</select>'


def seite_uebersicht(nutzer, sprache, neuer_link='', hinweis=''):
    gesamt = kontingent_von(nutzer)
    offen = verbleibend(nutzer)
    kurz = html.escape(nutzer.split(':')[0].lstrip('@'))

    with db() as v:
        liste = v.execute('SELECT code, erstellt, laeuft_ab, eingeloest_am, eingeloest_von, '
                          'benutzt_am, zurueckgezogen_am, raum_name, raum_fehler '
                          'FROM einladung WHERE einlader=? '
                          'ORDER BY erstellt DESC LIMIT 20', (nutzer,)).fetchall()
    zeilen = ''
    for e in liste:
        weg = ''                       # das x gibt es nur bei offenen Einladungen
        if e['eingeloest_von']:
            zustand = (f'<span class="ok">{t("angenommen", sprache)}</span><br>'
                       f'<span class="leise">{html.escape(e["eingeloest_von"])}</span>')
        elif e['eingeloest_am'] or e['benutzt_am']:
            # Token benutzt, aber kein Konto sicher zuzuordnen - nicht 'offen' anzeigen,
            # sonst wird der verbrauchte Link nochmal verschickt.
            zustand = (f'<span class="ok">{t("eingeloest", sprache)}</span><br>'
                       f'<span class="leise">{t("zuordnung_unklar", sprache)}</span>')
        elif e['zurueckgezogen_am']:
            zustand = f'<span class="leise">{t("zurueckgezogen", sprache)}</span>'
        elif e['laeuft_ab'] < time.time():
            zustand = f'<span class="leise">{t("abgelaufen", sprache)}</span>'
        else:
            zustand = (f'{t("offen", sprache)}<br><span class="leise">'
                       f'{restlaufzeit(e["laeuft_ab"] - time.time(), sprache)}</span>')
            # Offen: darf zurueckgezogen werden - für die doppelt verschickte Einladung.
            # Ein eigenes x am rechten Rand, gross genug für den Daumen.
            aufschrift = html.escape(t('einladung_zurueckziehen', sprache))
            weg = (f'<form method="post" action="/zurueckziehen" style="margin:0">'
                   f'<input type="hidden" name="code" value="{html.escape(e["code"])}">'
                   f'<button type="submit" aria-label="{aufschrift}" title="{aufschrift}" '
                   f'style="width:auto;padding:4px 10px;font-size:24px;line-height:1;background:transparent;'
                   f'color:var(--warn);border:0">✕</button></form>')
        ziel = (f'<br><span class="leise">→ {html.escape(e["raum_name"])}</span>'
                if e['raum_name'] else '')
        if e['raum_fehler']:
            ziel += (f'<br><span class="leise" style="color:var(--warn)">'
                     f'{t("raumeinladung_fehlgeschlagen", sprache)}</span>')
        zeilen += (f'<tr><td>{datum(e["erstellt"], sprache)}{ziel}</td><td>{zustand}</td>'
                   f'<td style="text-align:right;width:1%;vertical-align:middle">{weg}</td></tr>')
    tabelle = (f'<div class="karte"><h2>{t("deine_einladungen", sprache)}</h2><table>{zeilen}</table></div>'
               if zeilen else '')
    meldung = (f'<div class="karte" style="border-color:var(--eigen)"><p>{html.escape(hinweis)}</p></div>'
               if hinweis else '')

    if neuer_link:
        link = html.escape(neuer_link)
        frisch = f"""
        <div class="karte">
          <h2>{t('fertig_link', sprache)}</h2>
          <p class="leise">{t('fertig_link_text', sprache, tage=GUELTIG_TAGE)}</p>
          <div class="wert">{link}</div>
          <a class="knopf" href="https://wa.me/?text={urllib.parse.quote(neuer_link)}">{t('per_whatsapp', sprache)}</a>
        </div>"""
    else:
        frisch = ''

    if offen != 0:
        raeume = raeume_von(nutzer)
        if raeume:
            wahl = ''.join(
                f'<option value="{html.escape(r["id"])}">{html.escape(r["name"])}'
                f' ({r["mitglieder"]})</option>' for r in raeume)
            auswahl = (f'<label for="rm" style="margin-top:4px">{t("wohin", sprache)}</label>'
                       f'<select id="rm" name="raum" style="width:100%">{wahl}'
                       f'<option value="">{t("nirgendwohin", sprache)}</option></select>')
        else:
            auswahl = f'<p class="leise">{t("kein_raum", sprache)}</p>'
        # Die Sprache der Einladungsseite: Vorgabe "automatisch" (Browser des
        # Eingeladenen, sonst Serversprache); wer es fest will, wählt eine.
        auswahl += (f'<label for="sl" style="margin-top:12px">{t("sprache_der_einladung", sprache)}</label>'
                    + sprachwahl('sprache', None, 'sl', t('sprache_automatisch', sprache)))
        knopf = ('<form method="post" action="/erzeugen">' + auswahl +
                 f'<button type="submit">{t("einladung_erzeugen", sprache)}</button></form>')
    elif gesamt == 0:
        knopf = f'<p class="leise">{t("nicht_freigeschaltet", sprache, betreiber=html.escape(betreiber(sprache)))}</p>'
    else:
        knopf = f'<p class="leise">{t("keine_mehr", sprache, betreiber=html.escape(betreiber(sprache)))}</p>'

    anzeige = t('unbegrenzt', sprache) if offen < 0 else str(offen)
    darunter = (t('ohne_begrenzung', sprache) if gesamt < 0
                else t('von_insgesamt', sprache, gesamt=gesamt))
    verwaltung = (f'<a class="knopf still" href="/verwaltung">{t("verwalten_knopf", sprache)}</a>'
                  if ist_verwalter(nutzer) else '')

    return seite(t('titel_einladungen', sprache), f"""
      <h1>{t('hallo', sprache, name=kurz)}</h1>
      {meldung}
      {frisch}
      <div class="karte">
        <p class="leise">{t('du_kannst_einladen', sprache)}</p>
        <p class="gross">{anzeige}</p>
        <p class="leise">{darunter}</p>
        {knopf}
      </div>
      {tabelle}
      {verwaltung}
      <a class="knopf still" href="/abmelden">{t('abmelden', sprache)}</a>""", sprache)


HOECHSTWERT = 10000      # keine Begrenzung im Sinne, aber auch kein Tippfehler mit acht Nullen


def feld(jetzt, kennzeichen, sprache):
    """Freies Zahlenfeld plus Haken fuer unbegrenzt. 0 heisst: darf nicht einladen."""
    zahl = '' if jetzt < 0 else str(jetzt)
    haken = ' checked' if jetzt < 0 else ''
    unbegrenzt = t('unbegrenzt', sprache)
    return (f'<input type="number" name="gesamt" min="0" max="{HOECHSTWERT}" '
            f'value="{zahl}" placeholder="0" aria-label="{t("anzahl_einladungen", sprache)}" class="zahl">'
            f'<label class="haken"><input type="checkbox" name="unbegrenzt" value="1"'
            f'{haken} aria-label="{unbegrenzt}"> {unbegrenzt}</label>')


def seite_verwaltung(nutzer, sprache, hinweis='', geheim=None):
    """Die eine Seite fuer den Betreiber: Konten, Kontingente, Einladungsketten."""
    try:
        kette_nachfuehren()
    except Exception as f:                # die Seite soll trotzdem kommen
        protokoll('nachfuehren_fehlgeschlagen', '', str(f)[:120])
    vorgabe = standard_kontingent()

    meldung = (f'<div class="karte" style="border-color:var(--eigen)">'
               f'<p><b>{html.escape(hinweis)}</b></p></div>') if hinweis else ''

    if geheim:
        wer, pw = geheim
        meldung += f"""
        <div class="karte" style="border-color:var(--eigen)">
          <h2>{t('neues_passwort_fuer', sprache, wer=html.escape(wer))}</h2>
          <p class="leise">{t('einmal_sichtbar', sprache)}</p>
          <div class="wert">{html.escape(pw)}</div>
        </div>"""

    zeilen = ''
    for u in alle_nutzer():
        k = u['kennung']
        gesamt = kontingent_von(k)
        benutzt = verbraucht_von(k)
        offen = t('unbegrenzt', sprache) if gesamt < 0 else str(max(0, gesamt - benutzt))
        ich = (k == nutzer)

        marken = ''
        if u['verwalter']:
            marken += f'<span class="marke">{t("verwalter", sprache)}</span>'
        if u['gesperrt']:
            marken += f'<span class="marke warnmarke">{t("gesperrt", sprache)}</span>'

        def knopf(weg, text, zusatz='', richtung=''):
            versteckt = (f'<input type="hidden" name="richtung" value="{richtung}">'
                         if richtung else '')
            return (f'<form method="post" action="{weg}" style="display:inline">'
                    f'<input type="hidden" name="nutzer" value="{html.escape(k)}">{versteckt}'
                    f'<button type="submit" class="klein{zusatz}">{text}</button></form>')

        if u['gesperrt']:
            taten = knopf('/entsperren', t('entsperren', sprache))
        else:
            taten = knopf('/passwort', t('passwort_neu', sprache), ' gefahr')
            if not ich:
                taten += (knopf('/verwalter', t('verwalter_aus', sprache), richtung='nein') if u['verwalter']
                          else knopf('/verwalter', t('verwalter_an', sprache), richtung='ja'))
                taten += knopf('/sperren', t('sperren', sprache), ' gefahr')
            else:
                taten += f'<span class="leise"> &nbsp;{t("das_bist_du", sprache)}</span>'

        kontingentteil = '' if u['gesperrt'] else f"""
            <form method="post" action="/kontingent" class="reihe">
              <input type="hidden" name="nutzer" value="{html.escape(k)}">
              {feld(gesamt, k, sprache)}
              <button type="submit">{t('setzen', sprache)}</button>
            </form>"""

        zeilen += f"""<tr><td colspan="2">
          <div><b>{html.escape(u['anzeige'])}</b>{marken}</div>
          <div class="leise mono">{html.escape(k)}</div>
          <div class="leise">{t('verschickt_offen', sprache, benutzt=benutzt, offen=offen)}</div>
          {kontingentteil}
          <div class="taten">{taten}</div>
        </td></tr>"""

    with db() as v:
        # Benutzte Einladungen ohne sicheres Konto stehen mit drin: der Betreiber
        # soll sehen, dass jemand hereinkam, auch wenn unklar ist, wer.
        kette = v.execute(
            'SELECT einlader, eingeloest_von, coalesce(eingeloest_am, benutzt_am) AS wann '
            'FROM einladung WHERE eingeloest_von IS NOT NULL OR eingeloest_am IS NOT NULL '
            'OR benutzt_am IS NOT NULL ORDER BY wann DESC LIMIT 40').fetchall()
    kzeilen = ''
    for e in kette:
        wer = (html.escape(e['eingeloest_von'].split(':')[0].lstrip('@')) if e['eingeloest_von']
               else f'<span style="color:var(--warn)">{t("unklar", sprache)}</span>')
        einlader = html.escape(e['einlader'].split(':')[0].lstrip('@'))
        kzeilen += (f'<tr><td>{wer}</td>'
                    f'<td>{t("geholt_von", sprache, wer=einlader)}<br>'
                    f'<span class="leise">{datum(e["wann"], sprache)}</span></td></tr>')
    kettenteil = (f'<div class="karte"><h2>{t("wer_hat_wen", sprache)}</h2>'
                  f'<table class="vtab kette">{kzeilen}</table></div>') if kzeilen else ''

    _, zf_an = zf_stand(nutzer)
    if not zf_an:
        meldung += f"""
        <div class="karte" style="border-color:var(--warn)">
          <h2 style="color:var(--warn)">{t('kein_zf_kopf', sprache)}</h2>
          <p class="leise">{t('kein_zf_text', sprache)}</p>
          <a class="knopf" href="/zweitfaktor">{t('jetzt_einrichten', sprache)}</a>
        </div>"""

    sprachknoepfe = ''.join(
        f'<label class="haken"><input type="radio" name="sprache" value="{s}"'
        f'{" checked" if s == sprache else ""}> {name}</label>'
        for s, name in (('en', 'English'), ('de', 'Deutsch')))

    return seite(t('titel_verwaltung', sprache), f"""
      <h1>{t('titel_verwaltung', sprache)}</h1>
      {meldung}
      {karte_updates(sprache)}
      <div class="karte">
        <h2>{t('vorgabe_kopf', sprache)}</h2>
        <p class="leise">{t('vorgabe_text', sprache)}</p>
        <form method="post" action="/standard" class="reihe" style="margin-top:10px">
          {feld(vorgabe, 'standard', sprache)}
          <button type="submit">{t('speichern', sprache)}</button>
        </form>
      </div>

      <div class="karte">
        <h2>{t('anleitung_kopf', sprache)}</h2>
        <p class="leise">{t('anleitung_text', sprache)}</p>
        <a class="knopf still" href="/verwaltungsanleitung" target="_blank">{t('anleitung_knopf', sprache)}</a>
      </div>

      <div class="karte">
        <h2>{t('sprache_kopf', sprache)}</h2>
        <p class="leise">{t('sprache_text', sprache)}</p>
        <form method="post" action="/sprache" class="reihe sprachen" style="margin-top:10px">
          {sprachknoepfe}
          <button type="submit">{t('speichern', sprache)}</button>
        </form>
      </div>

      <div class="karte">
        <h2>{t('anlegen_kopf', sprache)}</h2>
        <p class="leise">{t('anlegen_text', sprache)}</p>
        <form method="post" action="/anlegen">
          <label for="bn">{t('benutzername_klein', sprache)}</label>
          <input id="bn" name="name" pattern="[a-z0-9._=-]{{1,60}}" required autocapitalize="none">
          <div style="height:12px"></div>
          <label for="az">{t('anzeigename', sprache)}</label>
          <input id="az" name="anzeige">
          <button type="submit">{t('anlegen', sprache)}</button>
        </form>
      </div>

      <div class="karte">
        <h2>{t('alle_konten', sprache)}</h2>
        <table class="vtab">{zeilen}</table>
      </div>
      {kettenteil}
      <a class="knopf still" href="/zweitfaktor">{t('zf_titel', sprache)}</a>
      <a class="knopf still" href="/eigene">{t('meine_einladungen', sprache)}</a>
      <a class="knopf still" href="/abmelden">{t('abmelden', sprache)}</a>""", sprache)


def seite_passwort_sicher(kennung, selbst, sprache):
    """Rueckfrage vor dem Zuruecksetzen - das neue Passwort erscheint nur einmal.

    Ohne diese Seite reichte ein Fehlklick: Der Verwalter setzte sich selbst ein
    Passwort, klickte die Anzeige weg und stand ausgesperrt da.
    """
    kurz = html.escape(kennung.split(':')[0].lstrip('@'))
    wen = (t('pw_wen_selbst', sprache) if selbst
           else t('pw_wen_anderer', sprache, kurz=kurz))
    folge = (t('pw_folge_selbst', sprache) if selbst
             else t('pw_folge_anderer', sprache, kurz=kurz))
    return seite(t('pw_titel', sprache), f"""
      <h1>{t('pw_frage', sprache, wen=wen)}</h1>
      <div class="karte">
        <p>{folge}</p>
        <p class="leise" style="margin-top:10px">{t('pw_schluessel', sprache)}</p>
        <form method="post" action="/passwort-ja">
          <input type="hidden" name="nutzer" value="{html.escape(kennung)}">
          <button type="submit" class="gefahrgross">{t('pw_ja', sprache)}</button>
        </form>
        <a class="knopf still" href="/verwaltung">{t('abbrechen', sprache)}</a>
      </div>""", sprache)


def seite_sperren_sicher(kennung, sprache):
    kurz = html.escape(kennung.split(':')[0].lstrip('@'))
    return seite(t('sperren_titel', sprache), f"""
      <h1>{t('sperren_frage', sprache, kurz=kurz)}</h1>
      <div class="karte">
        <p>{t('sperren_text', sprache)}</p>
        <p class="leise" style="margin-top:10px">{t('sperren_leise', sprache)}</p>
        <form method="post" action="/sperren-ja">
          <input type="hidden" name="nutzer" value="{html.escape(kennung)}">
          <button type="submit" class="gefahrgross">{t('sperren_ja', sprache)}</button>
        </form>
        <a class="knopf still" href="/">{t('abbrechen', sprache)}</a>
      </div>""", sprache)


# Aktualisierungen
# Grundsatz: nichts wird blind aktualisiert. root prüft täglich und legt den
# Befund in UPDATES_DATEI ab; dieser Dienst zeigt ihn nur an. Klickt ein
# Verwalter "Jetzt aktualisieren" und bestätigt, legt der Dienst eine
# Auftragsdatei ab - mehr nicht. Einspielen, Neustart, Element: alles root,
# angestossen durch eine Path-Unit. Hier gibt es kein apt und kein Netz.

def updates_stand():
    """Befund der taeglichen Pruefung: (daten, frisch).

    daten ist None, wenn die Datei fehlt oder unlesbar ist. frisch ist False,
    wenn der Befund aelter als UPDATES_FRIST ist - dann laeuft der Timer
    offenbar nicht, und ein alter Stand darf nicht als aktuell durchgehen.
    Nichts hier darf die Seite blockieren: jeder Zugriff abgefangen.
    """
    try:
        with open(UPDATES_DATEI) as d:
            daten = json.load(d)
        if not isinstance(daten, dict):
            return (None, False)
        frisch = time.time() - int(daten.get('geprueft') or 0) <= UPDATES_FRIST
        return (daten, frisch)
    except Exception:
        return (None, False)


def objekt(wert):
    """Nur ein JSON-Objekt zaehlt; alles andere (kaputte updates.json) gilt als leer.

    Die Datei schreibt root, aber die Seite darf an keinem Inhalt scheitern.
    """
    return wert if isinstance(wert, dict) else {}


def zahl(wert):
    """Ganzzahl aus dem Befund, bei Unsinn 0 - jede Zahl fuer sich, damit ein
    kaputtes Feld nicht die Nachbarn mit auf 0 zieht."""
    try:
        return int(wert or 0)
    except (TypeError, ValueError):
        return 0


def updates_ausstehend(daten):
    """Gibt es etwas einzuspielen? Rein, ohne Dateizugriff - fuer Karte und Test."""
    if not isinstance(daten, dict):
        return False
    pakete = objekt(daten.get('pakete'))
    synapse = objekt(daten.get('synapse'))
    element = objekt(daten.get('element'))
    if zahl(pakete.get('gesamt')) > 0:
        return True
    if synapse.get('neu') and synapse.get('neu') != synapse.get('ist'):
        return True
    # 'unbekannt' schreibt die Prüfung, wenn GitHub nicht antwortete - das ist
    # kein Update, sondern eine Lücke im Befund.
    if element.get('neu') not in (None, '', 'unbekannt') and element.get('neu') != element.get('ist'):
        return True
    return False


def vor_zeit(zeitpunkt, sprache):
    """'gerade eben', 'vor 5 Minuten', 'vor 3 Stunden', 'vor 2 Tagen'."""
    try:
        s = int(time.time() - int(zeitpunkt))
    except (TypeError, ValueError):
        return t('vor_unbekannt', sprache)
    if s < 90:
        return t('gerade_eben', sprache)
    if s < 3600:
        m = s // 60
        return t('vor_minute', sprache) if m == 1 else t('vor_minuten', sprache, n=m)
    if s < 86400:
        h = s // 3600
        return t('vor_stunde', sprache) if h == 1 else t('vor_stunden', sprache, n=h)
    d = s // 86400
    return t('vor_tag', sprache) if d == 1 else t('vor_tagen', sprache, n=d)


def auftrag_liegt_vor():
    # Nur eine Datei ist ein Auftrag. Ein Ordner gleichen Namens ist Unfug, den
    # die Update-Unit beim nächsten Start wegräumt - er darf nicht blockieren.
    try:
        return os.path.isfile(AUFTRAG_DATEI)
    except Exception:
        return False


def lauf_stand(daten):
    """'letzter_lauf' aus dem Befund, bereinigt: (lauf, laeuft).

    laeuft ist nur dann True, wenn das Ausfuehrskript es gesetzt hat UND der
    Beginn juenger als LAUF_FRIST ist. Ein aelteres "laeuft" stammt von einem
    Lauf, der nie zu Ende kam (Neustart, Stromausfall) - er wird als
    abgebrochen gezeigt, und der Knopf steht wieder zur Verfuegung. Sonst
    bliebe die Karte fuer immer gesperrt.
    """
    lauf = objekt(objekt(daten).get('letzter_lauf'))
    if not lauf.get('laeuft'):
        return (lauf, False)
    try:
        frisch = time.time() - int(lauf.get('begonnen')) <= LAUF_FRIST
    except (TypeError, ValueError):
        frisch = False            # ohne brauchbaren Beginn ist nichts belegt
    if frisch:
        return (lauf, True)
    return (dict(lauf, laeuft=False, status='abgebrochen'), False)


def auftrag_laeuft(daten):
    """Setzt das Ausfuehrskript, solange es arbeitet - auch in einem alten Befund gueltig."""
    return lauf_stand(daten)[1]


def auftrag_schreiben():
    """Legt die Auftragsdatei ab. Inhalt: nur der Zeitpunkt - er wird nie ausgewertet.

    Erst in eine Nebendatei, dann umbenennen: die Path-Unit zuendet, sobald der
    Name auftaucht, und soll nie eine halb geschriebene Datei vorfinden.
    """
    tmp = AUFTRAG_DATEI + '.neu'
    try:
        if os.path.isdir(AUFTRAG_DATEI):
            # os.replace würde hier mit IsADirectoryError abbrechen; lieber
            # klar benennen, was im Weg liegt.
            protokoll('update_auftrag_fehler', '', 'Am Auftragspfad liegt ein Ordner')
            return False
        with open(tmp, 'w') as d:
            d.write(f'{int(time.time())}\n')
        os.replace(tmp, AUFTRAG_DATEI)
        return True
    except Exception as f:
        protokoll('update_auftrag_fehler', '', str(f)[:120])
        try:
            os.remove(tmp)        # keine Nebendatei liegen lassen
        except Exception:
            pass
        return False


def karte_updates(sprache):
    """Die Karte 'Aktualisierungen' oben auf der Verwaltungsseite."""
    daten, frisch = updates_stand()
    lauf, laeuft = lauf_stand(daten)

    # Ergebnis des letzten Einspielens - auch dann, wenn der Befund selbst alt ist.
    lauf_teil = ''
    if laeuft:
        lauf_teil = (f'<p style="margin-top:12px">'
                     f'{t("upd_laeuft", sprache, wann=vor_zeit(lauf.get("begonnen"), sprache))}</p>')
    elif lauf.get('status'):
        gut = lauf.get('status') == 'ok'
        farbe = 'var(--eigen)' if gut else 'var(--warn)'
        grund = html.escape(str(lauf.get('fehlgeschlagen') or ''))
        if gut:
            text = t('upd_erfolgreich', sprache)
        elif lauf.get('status') == 'abgebrochen':
            text = t('upd_abgebrochen', sprache)
        else:
            text = t('upd_mit_fehlern', sprache) + ((' (' + grund + ')') if grund else '')
        zeilen = lauf.get('protokoll') or []
        proto = ''
        if isinstance(zeilen, list) and zeilen:
            proto = (f'<details style="margin-top:6px"><summary class="leise">{t("protokoll", sprache)}</summary>'
                     '<pre class="mono" style="white-space:pre-wrap;margin:6px 0 0">'
                     + html.escape('\n'.join(str(z) for z in zeilen[-40:])) + '</pre></details>')
        # Ein abgebrochener Lauf hat kein Ende - dann zählt sein Beginn.
        wann = vor_zeit(lauf.get('beendet') if lauf.get('beendet') is not None else lauf.get('begonnen'),
                        sprache)
        lauf_teil = (f'<p style="margin-top:12px">{t("upd_letzte", sprache, wann=wann)}'
                     f'<b style="color:{farbe}">{text}</b></p>{proto}')

    if not daten or not frisch:
        if daten:
            hinweis = t('upd_alt', sprache, wann=vor_zeit(daten.get('geprueft'), sprache),
                        betreiber=html.escape(betreiber(sprache)))
        else:
            hinweis = t('upd_kein_befund', sprache)
        return (f'<div class="karte"><h2>{t("upd_kopf", sprache)}</h2>'
                f'<p><b>{t("upd_nicht_geprueft", sprache)}</b></p>'
                f'<p class="leise">{hinweis}</p>{lauf_teil}</div>')

    # Kaputte Teile (kein Objekt, keine Liste) blockieren die Seite nicht: sie
    # erscheinen als "unbekannt", der Rest wird normal gezeigt.
    synapse = daten.get('synapse')
    element = daten.get('element')
    pakete = daten.get('pakete')

    def zeile(was, teil):
        if not isinstance(teil, dict):
            return (f'<tr><td>{was}</td><td class="leise">{t("vor_unbekannt", sprache)}</td>'
                    f'<td style="text-align:right"><span class="leise">{t("upd_befund_unlesbar", sprache)}</span></td></tr>')
        ist, neu = teil.get('ist'), teil.get('neu')
        ist_html = (html.escape(str(ist)) if ist
                    else f'<span class="leise">{t("upd_nicht_installiert", sprache)}</span>')
        if not neu or neu == 'unbekannt':
            stand = f'<span class="leise">{t("upd_neueste_unbekannt", sprache)}</span>'
        elif neu == ist:
            stand = f'<span class="ok">{t("aktuell", sprache)}</span>'
        else:
            stand = (f'<span style="color:var(--warn)">'
                     f'{t("upd_neu", sprache, neu=html.escape(str(neu)))}</span>')
        return (f'<tr><td>{was}</td><td class="mono" style="font-size:14px">{ist_html}</td>'
                f'<td style="text-align:right">{stand}</td></tr>')

    gesamt = zahl(objekt(pakete).get('gesamt'))
    sicher = zahl(objekt(pakete).get('sicherheit'))
    if not isinstance(pakete, dict):
        p_stand = f'<span class="leise">{t("vor_unbekannt", sprache)}</span>'
    elif gesamt == 0:
        p_stand = f'<span class="ok">{t("upd_alles_aktuell", sprache)}</span>'
    else:
        p_stand = (f'<span style="color:var(--warn)">{t("upd_ausstehend", sprache, n=gesamt)}'
                   + (t('upd_davon_sicherheit', sprache, n=sicher) if sicher else '') + '</span>')
    liste = objekt(pakete).get('liste')
    namen = ', '.join(html.escape(str(p.get('name', '')))
                      for p in (liste if isinstance(liste, list) else [])
                      if isinstance(p, dict) and p.get('name'))
    liste_teil = (f'<details><summary class="leise">{t("upd_welche", sprache)}</summary>'
                  f'<p class="leise mono">{namen}</p></details>') if namen else ''
    zeilen = (zeile(t('upd_synapse', sprache), synapse)
              + zeile(t('upd_element', sprache), element)
              + f'<tr><td>{t("upd_pakete", sprache)}</td><td></td><td style="text-align:right">{p_stand}</td></tr>')

    tat = ''
    if updates_ausstehend(daten):
        if laeuft:
            pass                                  # steht schon oben in lauf_teil
        elif auftrag_liegt_vor():
            tat = f'<p class="leise" style="margin-top:12px">{t("upd_auftrag_liegt", sprache)}</p>'
        else:
            tat = ('<form method="post" action="/update">'
                   f'<button type="submit" class="gefahrgross">{t("jetzt_aktualisieren", sprache)}</button></form>'
                   f'<p class="leise" style="margin-top:8px">{t("upd_nachgefragt", sprache)}</p>')

    return (f'<div class="karte"><h2>{t("upd_kopf", sprache)}</h2>'
            f'<p class="leise">{t("upd_geprueft", sprache, wann=vor_zeit(daten.get("geprueft"), sprache))}</p>'
            f'<table>{zeilen}</table>{liste_teil}{tat}{lauf_teil}</div>')


def seite_update_sicher(daten, sprache):
    """Rueckfrage vor dem Einspielen - wie seite_passwort_sicher, ohne JavaScript."""
    synapse = objekt(daten.get('synapse'))
    element = objekt(daten.get('element'))
    pakete = objekt(daten.get('pakete'))
    gesamt = zahl(pakete.get('gesamt'))
    sicher = zahl(pakete.get('sicherheit'))
    punkte = []
    if gesamt:
        davon = ''
        if sicher == 1:
            davon = t('upd_davon_ein', sprache)
        elif sicher:
            davon = t('upd_davon_mehr', sprache, n=sicher)
        if gesamt == 1:
            punkte.append('<li>' + t('upd_punkt_paket_ein', sprache, davon=davon) + '</li>')
        else:
            punkte.append('<li>' + t('upd_punkt_pakete', sprache, n=gesamt, davon=davon) + '</li>')
    if synapse.get('neu') and synapse.get('neu') != synapse.get('ist'):
        punkte.append('<li>' + t('upd_punkt_synapse', sprache,
                                 neu=html.escape(str(synapse['neu']))) + '</li>')
    if element.get('neu') not in (None, '', 'unbekannt') and element.get('neu') != element.get('ist'):
        punkte.append('<li>' + t('upd_punkt_element', sprache,
                                 neu=html.escape(str(element['neu']))) + '</li>')
    return seite(t('upd_sicher_titel', sprache), f"""
      <h1>{t('upd_sicher_frage', sprache)}</h1>
      <div class="karte">
        <p>{t('upd_sicher_hintergrund', sprache)}</p>
        <ul style="margin:8px 0 0;padding-left:22px">{''.join(punkte)}</ul>
        <p class="leise" style="margin-top:10px">{t('upd_sicher_dauer', sprache)}</p>
        <form method="post" action="/update-ja">
          <button type="submit" class="gefahrgross">{t('upd_ja', sprache)}</button>
        </form>
        <a class="knopf still" href="/verwaltung">{t('abbrechen', sprache)}</a>
      </div>""", sprache)


def seite_einladung(einlader, token, sprache, abgelaufen=False, schon_benutzt=False,
                    zurueckgezogen=False):
    wer = html.escape(einlader.split(':')[0].lstrip('@'))
    if abgelaufen or schon_benutzt or zurueckgezogen:
        grund = t('einl_zurueckgezogen' if zurueckgezogen
                  else 'einl_abgelaufen' if abgelaufen else 'einl_benutzt', sprache)
        return seite(t('einladung_titel', sprache), f"""
          <h1>{t('einl_ungueltig_kopf', sprache, grund=grund)}</h1>
          <p>{t('einl_neue', sprache, wer=wer)}</p>""", sprache)
    return seite(t('einladung_titel', sprache), f"""
      <h1>{t('einl_kopf', sprache, wer=wer)}</h1>
      <p class="leise">{t('einl_unter', sprache)}</p>
      <div class="karte">
        <ol>
          <li><b>{t('einl_schritt1', sprache)}</b><br>
              <span class="leise">{t('einl_schritt1_text', sprache)}</span>
              <div class="wert">{html.escape(token)}</div>
              <a class="knopf" href="{ELEMENT}/#/register">{t('einl_browser', sprache)}</a></li>
          <li><b>{t('einl_schritt2', sprache)}</b><br>
              <span class="leise">{t('einl_schritt2_text', sprache)}</span></li>
          <li><b>{t('einl_schritt3', sprache)}</b><br>
              <span class="leise">{t('einl_schritt3_text', sprache)}</span>
              <div class="wert">{SERVERNAME}</div>
              <span class="leise">{t('einl_schritt3_text2', sprache)}</span></li>
        </ol>
      </div>
      <p class="leise">{t('einl_nicht_weiterleiten', sprache)}</p>""", sprache)


def seite_kurz(titel, kopf, text, sprache, knopf_ziel='/', knopf_text=None, knopf_still=False):
    """Eine Ueberschrift, ein Satz, ein Knopf - fuer Fehler und 'nicht gefunden'."""
    knopf = ''
    if knopf_text:
        klasse = 'knopf still' if knopf_still else 'knopf'
        knopf = f'<a class="{klasse}" href="{knopf_ziel}">{knopf_text}</a>'
    absatz = f'<p>{text}</p>' if text else ''
    return seite(titel, f'<h1>{kopf}</h1>{absatz}{knopf}', sprache)


# Netz

ERZEUGEN_SPERRE = threading.Lock()   # ein Prozess, ein Schloss: Kontingent prüfen + eintragen


class Bedienung(http.server.BaseHTTPRequestHandler):
    server_version = 'Einladung'
    sys_version = ''

    def log_message(self, *_):
        pass                                  # keine Zugriffsprotokolle: Datensparsamkeit

    # Hilfen
    def antwort(self, koerper, code=200, kopf=None):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(koerper)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        # no-store: Seiten mit Einmal-Passwort, TOTP-Schluessel oder frischem Link
        # dürfen nicht aus dem Verlaufscache kommen, wenn nach dem Abmelden
        # jemand anderes am selben Browser "Zurück" drückt.
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

    def sitzung(self):
        roh = self.headers.get('Cookie', '')
        if not roh:
            return (None, None)
        keks = http.cookies.SimpleCookie(roh)
        return sitzung_pruefen(keks['sitzung'].value) if 'sitzung' in keks else (None, None)

    def wer(self):
        """Nur voll angemeldete Kennungen - eine halbe Sitzung zaehlt nirgends."""
        nutzer, stufe = self.sitzung()
        return nutzer if stufe == 'voll' else None

    def keks_setzen(self, nutzer, stufe='voll'):
        dauer = CODE_FRIST_MIN * 60 if stufe == 'halb' else SITZUNG_STUNDEN * 3600
        return {'Set-Cookie': f'sitzung={sitzung_bauen(nutzer, stufe)}; Path=/; HttpOnly; '
                              f'SameSite=Lax; Secure; Max-Age={dauer}'}

    def formular(self):
        laenge = int(self.headers.get('Content-Length') or 0)
        if laenge > 8192:
            return {}
        roh = self.rfile.read(laenge).decode('utf-8', 'replace')
        return {k: v[0] for k, v in urllib.parse.parse_qs(roh).items()}

    def fehlerseite(self, schluessel_text, code, sprache):
        """'Das hat nicht geklappt' mit Grund und Zurueck-Knopf."""
        return self.antwort(seite_kurz(
            t('titel_einladungen', sprache), t('nicht_geklappt', sprache),
            t(schluessel_text, sprache, betreiber=html.escape(betreiber(sprache))),
            sprache, '/', t('zurueck', sprache)), code)

    def kein_zugriff(self, sprache, mit_knopf=False):
        knopf = t('zu_meinen_einladungen', sprache) if mit_knopf else None
        return self.antwort(seite_kurz(
            t('titel_verwaltung', sprache), t('kein_zugriff', sprache),
            t('kein_zugriff_text', sprache) if mit_knopf else '',
            sprache, '/eigene', knopf, knopf_still=True), 403)

    # Abrufe
    def do_GET(self):
        pfad = urllib.parse.urlparse(self.path).path
        if pfad.startswith('/e/'):
            return self.einladung_zeigen(pfad[3:])
        # Alle anderen Seiten sprechen die Sprache, die der Verwalter gewaehlt hat.
        sprache = server_sprache()
        if pfad == '/abmelden':
            # Den Keks nicht nur im Browser löschen, sondern serverseitig sperren:
            # eine Kopie des Kekses liefe sonst bis zu SITZUNG_STUNDEN weiter.
            nutzer, _stufe = self.sitzung()
            if nutzer:
                sitzung_sperren(nutzer)
            return self.weiter('/', {'Set-Cookie': 'sitzung=; Path=/; Max-Age=0'})
        if pfad == '/einrichten':
            nutzer = self.wer()
            if not nutzer:
                return self.weiter('/')
            schritt = pflicht_schritt(nutzer)
            if schritt == 'passwort':
                return self.antwort(seite_erstpasswort(sprache))
            if schritt == 'zweitfaktor':
                return self.antwort(seite_zweitfaktor(nutzer, sprache, pflicht=True))
            return self.weiter('/')
        if pfad == '/zweitfaktor':
            nutzer = self.wer()
            if nutzer and pflicht_schritt(nutzer):
                return self.weiter('/einrichten')
            return self.antwort(seite_zweitfaktor(nutzer, sprache)) if nutzer else self.weiter('/')
        if pfad == '/verwaltungsanleitung':
            # Nur für Verwalter; die Datei schreibt der Installer (root), ohne Passwoerter.
            nutzer = self.wer()
            if not nutzer:
                return self.weiter('/')
            if pflicht_schritt(nutzer):
                return self.weiter('/einrichten')
            if not ist_verwalter(nutzer):
                return self.kein_zugriff(sprache, mit_knopf=True)
            try:
                with open(VERWALTUNGSANLEITUNG_DATEI, 'rb') as d:
                    return self.antwort(d.read())
            except OSError:
                return self.antwort(seite_verwaltung(nutzer, sprache, t('anleitung_fehlt', sprache)), 404)
        if pfad not in ('/', '/verwaltung', '/eigene'):
            return self.antwort(seite_kurz(t('nicht_gefunden', sprache), t('nicht_gefunden', sprache),
                                           '', sprache), 404)

        nutzer = self.wer()
        if not nutzer:
            return self.antwort(seite_anmelden(sprache, verwaltung=self.unter_verwaltung()))
        if pflicht_schritt(nutzer):
            return self.weiter('/einrichten')

        # Unter dem Verwaltungsnamen zeigt die Startseite direkt die Verwaltung.
        # Dieselbe Anwendung, zwei Gesichter - getrennt nach Zuständigkeit,
        # nicht nach Technik.
        hier_verwaltung = (pfad == '/verwaltung') or (pfad == '/' and self.unter_verwaltung())
        if hier_verwaltung:
            if not ist_verwalter(nutzer):
                return self.kein_zugriff(sprache, mit_knopf=True)
            return self.antwort(seite_verwaltung(nutzer, sprache))

        try:
            kette_nachfuehren()
        except Exception:
            pass
        self.antwort(seite_uebersicht(nutzer, sprache))

    def do_POST(self):
        pfad = urllib.parse.urlparse(self.path).path
        sprache = server_sprache()
        if pfad == '/anmelden':
            return self.anmelden(sprache)
        if pfad == '/code':
            return self.code_pruefen(sprache)
        if pfad == '/zweitfaktor':
            return self.zweitfaktor(sprache)
        if pfad == '/erstpasswort':
            return self.erstpasswort(sprache)
        # Offene Pflichtschritte sperren jede andere Handlung, nicht nur die Seiten.
        nutzer = self.wer()
        if nutzer and pflicht_schritt(nutzer):
            return self.weiter('/einrichten')
        if pfad == '/zurueckziehen':
            return self.zurueckziehen(sprache)
        if pfad == '/erzeugen':
            return self.erzeugen(sprache)
        if pfad in ('/kontingent', '/standard'):
            return self.kontingent_aendern(pfad, sprache)
        if pfad == '/sprache':
            return self.sprache_aendern(sprache)
        if pfad in ('/anlegen', '/passwort', '/passwort-ja', '/sperren', '/sperren-ja',
                    '/entsperren', '/verwalter'):
            return self.konten_verwalten(pfad, sprache)
        if pfad in ('/update', '/update-ja'):
            return self.aktualisieren(pfad, sprache)
        self.antwort(seite_kurz(t('nicht_gefunden', sprache), t('nicht_gefunden', sprache),
                                '', sprache), 404)

    # Vorgänge
    # Welcher Text zu welchem Ergebnis von anmeldung_pruefen gehört.
    ANMELDE_MELDUNG = {'falsch': 'anmeldung_falsch', 'gebremst': 'anmeldung_gebremst',
                       'weg': 'anmeldung_weg'}

    def unter_verwaltung(self):
        """Wird diese Anfrage unter dem Verwaltungsnamen gestellt?"""
        return bool(VERWALTUNG_HOST) and \
            self.headers.get('Host', '').split(':')[0] == VERWALTUNG_HOST

    def anmelden(self, sprache):
        f = self.formular()
        erst = erst_kennung(f.get('benutzer', ''))
        if erst:
            # Synapse kennt das Konto nur gesperrt - das Passwort prüft der Hash hier.
            if gebremst(erst):
                return self.antwort(seite_anmelden(
                    sprache, t('anmeldung_gebremst', sprache), self.unter_verwaltung()), 429)
            if not pw_passt(f.get('passwort', ''), erst_eintrag(erst)['hash']):
                fehlversuch(erst)
                time.sleep(1.5)
                protokoll('anmeldung_falsch', erst)
                return self.antwort(seite_anmelden(
                    sprache, t('anmeldung_falsch', sprache), self.unter_verwaltung()), 401)
            versuche_loeschen(erst)
            protokoll('angemeldet_erst', erst)
            return self.weiter('/einrichten', self.keks_setzen(erst))
        zustand, nutzer = anmeldung_pruefen(f.get('benutzer', '').strip(),
                                            f.get('passwort', ''))
        if zustand != 'ok':
            if zustand == 'falsch':
                time.sleep(1.5)               # bremst Durchprobieren zusätzlich
                protokoll('anmeldung_falsch', f.get('benutzer', '')[:40])
            code = 429 if zustand == 'gebremst' else (502 if zustand == 'weg' else 401)
            return self.antwort(seite_anmelden(
                sprache, t(self.ANMELDE_MELDUNG[zustand], sprache), self.unter_verwaltung()), code)

        _, zf_an = zf_stand(nutzer)
        if zf_an:
            # Passwort stimmt, aber die Sitzung bleibt halb, bis der Code da ist.
            return self.antwort(seite_code(sprache), 200, self.keks_setzen(nutzer, 'halb'))
        protokoll('angemeldet', nutzer)
        self.weiter('/', self.keks_setzen(nutzer))

    def code_pruefen(self, sprache):
        nutzer, stufe = self.sitzung()
        if not nutzer or stufe != 'halb':
            return self.weiter('/')
        if gebremst(nutzer):
            protokoll('2fa_gebremst', nutzer)
            return self.antwort(seite_code(sprache, t('code_zu_viele', sprache)), 429)
        if not zf_pruefen(nutzer, self.formular().get('code', '')):
            fehlversuch(nutzer)
            protokoll('2fa_falsch', nutzer)
            time.sleep(1.0)
            return self.antwort(seite_code(sprache, t('code_falsch', sprache)), 401)
        versuche_loeschen(nutzer)
        protokoll('2fa_ok', nutzer)
        self.weiter('/', self.keks_setzen(nutzer))

    def zweitfaktor(self, sprache):
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        f = self.formular()
        tat = f.get('tat', '')
        if tat == 'ablehnen':
            # Nur in der Erstanmeldung und nur mit bestätigtem Risiko.
            if pflicht_schritt(nutzer) != 'zweitfaktor' or f.get('risiko') != 'ja':
                return self.weiter('/einrichten')
            try:
                erst_abschliessen(nutzer)
            except Exception as e:
                protokoll('erst_entsperren_fehlgeschlagen', nutzer, str(e)[:120])
                return self.antwort(seite_kurz(
                    t('titel_verwaltung', sprache), t('nicht_geklappt', sprache),
                    t('erst_entsperren_fehler', sprache), sprache, '/einrichten',
                    t('nochmal_versuchen', sprache)), 502)
            protokoll('2fa_abgelehnt', nutzer)
            return self.antwort(seite_erst_fertig(sprache, ohne_zf=True))
        if gebremst(nutzer):
            return self.antwort(seite_zweitfaktor(
                nutzer, sprache, fehler=t('code_zu_viele', sprache)), 429)
        if not zf_pruefen(nutzer, f.get('code', '')):
            fehlversuch(nutzer)
            protokoll('2fa_einrichtung_falsch', nutzer)
            return self.antwort(seite_zweitfaktor(
                nutzer, sprache, fehler=t('code_falsch', sprache)), 401)
        versuche_loeschen(nutzer)
        # Das Ändern sperrt alle bisherigen Sitzungen des Kontos - auch diese.
        # Deshalb gleich ein frischer Keks, damit nur die ANDEREN hinausfliegen.
        if tat == 'aus':
            zf_entfernen(nutzer)
            return self.antwort(seite_zweitfaktor(nutzer, sprache, hinweis=t('zf_ist_aus', sprache)),
                                200, self.keks_setzen(nutzer))
        zf_einschalten(nutzer)
        if erst_eintrag(nutzer):
            try:
                erst_abschliessen(nutzer)
            except Exception as f:
                protokoll('erst_entsperren_fehlgeschlagen', nutzer, str(f)[:120])
                return self.antwort(seite_kurz(
                    t('titel_verwaltung', sprache), t('nicht_geklappt', sprache),
                    t('erst_entsperren_fehler', sprache), sprache, '/einrichten',
                    t('nochmal_versuchen', sprache)), 502, self.keks_setzen(nutzer))
            return self.antwort(seite_erst_fertig(sprache), 200, self.keks_setzen(nutzer))
        return self.antwort(seite_zweitfaktor(nutzer, sprache, hinweis=t('zf_ist_an', sprache)),
                            200, self.keks_setzen(nutzer))

    def erstpasswort(self, sprache):
        nutzer = self.wer()
        if not nutzer or pflicht_schritt(nutzer) != 'passwort':
            return self.weiter('/einrichten' if nutzer else '/')
        f = self.formular()
        neu, nochmal = f.get('neu', ''), f.get('nochmal', '')
        fehler = None
        if neu != nochmal:
            fehler = 'pw_ungleich'
        elif not passwort_stark(neu):
            fehler = 'pw_zu_schwach'
        elif pw_passt(neu, erst_eintrag(nutzer)['hash']):
            fehler = 'pw_wie_vorher'
        if fehler:
            return self.antwort(seite_erstpasswort(sprache, t(fehler, sprache)), 400)
        try:
            erst_passwort_setzen(nutzer, neu)
        except Exception as e:
            protokoll('erst_passwort_fehlgeschlagen', nutzer, str(e)[:120])
            return self.antwort(seite_erstpasswort(sprache, t('nochmal', sprache)), 502)
        # erst_passwort_setzen beendet alle Sitzungen - frischer Keks für diese.
        self.weiter('/einrichten', self.keks_setzen(nutzer))

    def zurueckziehen(self, sprache):
        """Eine offene Einladung ungueltig machen - nur die eigene, Verwalter jede."""
        code = self.formular().get('code', '').strip()   # Rumpf zuerst, er ist nur einmal lesbar
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        with db() as v:
            e = v.execute('SELECT * FROM einladung WHERE code=?', (code,)).fetchone()
        if not e or (e['einlader'] != nutzer and not ist_verwalter(nutzer)):
            return self.antwort(seite_uebersicht(
                nutzer, sprache, hinweis=t('einl_gibt_es_nicht_kurz', sprache)), 404)
        if e['eingeloest_am'] or e['benutzt_am'] or e['zurueckgezogen_am']:
            return self.antwort(seite_uebersicht(
                nutzer, sprache, hinweis=t('einl_schon_benutzt', sprache)), 400)
        # Erst der Token bei Synapse, dann die Ablage: schlägt das Löschen fehl, bleibt
        # die Einladung sichtbar offen, statt hier "zurueckgezogen" und dort noch gültig.
        try:
            token_loeschen(e['token'])
        except Exception:
            return self.fehlerseite('server_antwortet_nicht', 502, sprache)
        with db() as v:
            n = v.execute('UPDATE einladung SET zurueckgezogen_am=? WHERE code=? '
                          'AND eingeloest_am IS NULL AND benutzt_am IS NULL',
                          (int(time.time()), code)).rowcount
        if n != 1:   # der Hintergrundfaden war schneller: inzwischen eingeloest
            return self.antwort(seite_uebersicht(
                nutzer, sprache, hinweis=t('einl_zu_spaet', sprache)), 409)
        protokoll('zurueckgezogen', nutzer, code)
        return self.antwort(seite_uebersicht(
            nutzer, sprache, hinweis=t('einl_zurueckgezogen_ok', sprache)))

    def erzeugen(self, sprache):
        f = self.formular()                                 # Rumpf zuerst, er ist nur einmal lesbar
        gewaehlt = f.get('raum', '').strip()
        # Sprache der Einladungsseite: was der Einladende gewaehlt hat, sonst NULL
        # (= automatisch: Browser des Eingeladenen, sonst Serversprache).
        einladungssprache = sprache_normieren(f.get('sprache', ''))
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        raum = raum_name = None
        if gewaehlt:
            # nur Räume zulassen, in denen der Einladende selbst ist. Laesst sich der
            # gewählte gerade nicht bestätigen (Synapse antwortet nicht), lieber
            # abbrechen als die Raumwahl still fallen zu lassen: der Neue landete
            # sonst nie dort, und niemand erführe davon. Vor dem Token, damit kein
            # Registrierungs-Token bei Synapse verwaist.
            treffer = next((r for r in raeume_von(nutzer) if r['id'] == gewaehlt), None)
            if not treffer:
                return self.fehlerseite('raum_nicht_bestaetigt', 502, sprache)
            raum, raum_name = treffer['id'], treffer['name']
        # Prüfung, Token und Eintrag unter EINER Sperre: jede Anfrage laeuft in ihrem
        # eigenen Faden, und zwischen Prüfung und Eintrag liegt ein Netzaufruf. Ohne
        # Sperre kamen zehn gleichzeitige Klicks bei Kontingent 1 alle durch.
        with ERZEUGEN_SPERRE:
            if verbleibend(nutzer) == 0:
                return self.antwort(seite_uebersicht(nutzer, sprache))
            try:
                token, laeuft_ab = token_erzeugen()
            except Exception:
                return self.fehlerseite('server_antwortet_nicht', 502, sprache)
            code = secrets.token_urlsafe(9)
            with db() as v:
                v.execute('INSERT INTO einladung (code, token, einlader, erstellt, laeuft_ab, '
                          'raum, raum_name, sprache) VALUES (?,?,?,?,?,?,?,?)',
                          (code, token, nutzer, int(time.time()), laeuft_ab, raum, raum_name,
                           einladungssprache))
        self.antwort(seite_uebersicht(nutzer, sprache, f'{EIGENE_URL}/e/{code}'))

    def kontingent_aendern(self, pfad, sprache):
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        if not ist_verwalter(nutzer):
            return self.kein_zugriff(sprache)
        f = self.formular()
        if f.get('unbegrenzt'):
            wert = -1
        else:
            try:
                wert = int(f.get('gesamt', '').strip() or '0')
            except ValueError:
                return self.antwort(seite_verwaltung(
                    nutzer, sprache, t('zahl_oder_unbegrenzt', sprache)), 400)
            if wert < 0 or wert > HOECHSTWERT:
                return self.antwort(seite_verwaltung(
                    nutzer, sprache, t('zahl_zwischen', sprache, max=HOECHSTWERT)), 400)

        if pfad == '/standard':
            standard_setzen(wert)
            return self.antwort(seite_verwaltung(
                nutzer, sprache, t('vorgabe_steht', sprache, menge=menge(wert, sprache))))

        ziel = f.get('nutzer', '').strip()
        if not ziel.startswith('@'):
            return self.antwort(seite_verwaltung(nutzer, sprache, t('unbekanntes_konto', sprache)), 400)
        kontingent_setzen(ziel, wert)
        kurz = ziel.split(':')[0].lstrip('@')
        return self.antwort(seite_verwaltung(
            nutzer, sprache, t('kontingent_gesetzt', sprache, kurz=kurz, menge=menge(wert, sprache))))

    def sprache_aendern(self, sprache):
        """Serversprache umstellen - gilt sofort fuer alle Seiten, auch fuer diese Antwort."""
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        if not ist_verwalter(nutzer):
            return self.kein_zugriff(sprache)
        neu = sprache_normieren(self.formular().get('sprache', ''))
        if not neu:
            return self.antwort(seite_verwaltung(nutzer, sprache, t('sprache_ungueltig', sprache)), 400)
        sprache_setzen(neu)
        protokoll('sprache_gesetzt', nutzer, neu)
        return self.antwort(seite_verwaltung(nutzer, neu, t('sprache_gesetzt', neu)))

    def konten_verwalten(self, pfad, sprache):
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        if not ist_verwalter(nutzer):
            return self.kein_zugriff(sprache)
        f = self.formular()
        ziel = f.get('nutzer', '').strip()

        try:
            if pfad == '/anlegen':
                name = f.get('name', '').strip().lower()
                if not re.fullmatch(r'[a-z0-9._=-]{1,60}', name):
                    return self.antwort(seite_verwaltung(nutzer, sprache, t('name_regel', sprache)), 400)
                try:
                    kennung, pw = nutzer_anlegen(name, f.get('anzeige', '').strip())
                except KontoVergeben:
                    return self.antwort(seite_verwaltung(
                        nutzer, sprache, t('name_vergeben', sprache, name=name)), 400)
                return self.antwort(seite_verwaltung(
                    nutzer, sprache, t('konto_angelegt', sprache, name=name), geheim=(name, pw)))

            if not ziel.startswith('@'):
                return self.antwort(seite_verwaltung(nutzer, sprache, t('unbekanntes_konto', sprache)), 400)
            kurz = ziel.split(':')[0].lstrip('@')

            if pfad == '/passwort':
                # Erst fragen. Das Zurücksetzen selbst macht /passwort-ja.
                return self.antwort(seite_passwort_sicher(ziel, ziel == nutzer, sprache))

            if pfad == '/passwort-ja':
                pw = passwort_neu(ziel)
                # Eigenes Passwort: die Sperre träfe auch diese Sitzung - frischer Keks.
                frisch = self.keks_setzen(nutzer) if ziel == nutzer else None
                return self.antwort(seite_verwaltung(
                    nutzer, sprache, t('pw_gesetzt', sprache, kurz=kurz), geheim=(kurz, pw)),
                    200, frisch)

            if pfad == '/sperren':
                if ziel == nutzer:
                    return self.antwort(seite_verwaltung(
                        nutzer, sprache, t('selbst_sperren', sprache)), 400)
                return self.antwort(seite_sperren_sicher(ziel, sprache))

            if pfad == '/sperren-ja':
                if ziel == nutzer:
                    return self.antwort(seite_verwaltung(
                        nutzer, sprache, t('selbst_sperren', sprache)), 400)
                try:
                    n = einladungen_zurueckziehen(ziel)
                except Exception:
                    return self.fehlerseite('server_antwortet_nicht', 502, sprache)
                konto_sperren(ziel)
                hinweis = t('ist_gesperrt', sprache, kurz=kurz)
                if n:
                    hinweis += ' ' + t('sperren_einladungen_weg', sprache, n=n)
                return self.antwort(seite_verwaltung(nutzer, sprache, hinweis))

            if pfad == '/entsperren':
                return self.antwort(seite_verwaltung(
                    nutzer, sprache, t('wieder_frei', sprache, kurz=kurz),
                    geheim=(kurz, konto_entsperren(ziel))))

            if pfad == '/verwalter':
                if ziel == nutzer:
                    return self.antwort(seite_verwaltung(
                        nutzer, sprache, t('eigene_rechte', sprache)), 400)
                # Die Richtung kommt vom Knopf, nicht vom Umschalten des Ist-Zustands:
                # lieferte die Kontenliste gerade nichts, machte 'Verwalter aus' sonst
                # das Konto zum Verwalter.
                richtung = f.get('richtung', '')
                if richtung not in ('ja', 'nein'):
                    return self.antwort(seite_verwaltung(
                        nutzer, sprache, t('unklare_anfrage', sprache)), 400)
                verwalter_setzen(ziel, richtung == 'ja')
                return self.antwort(seite_verwaltung(
                    nutzer, sprache,
                    t('jetzt_verwalter' if richtung == 'ja' else 'kein_verwalter_mehr', sprache, kurz=kurz)))
        except urllib.error.HTTPError as fehl:
            return self.antwort(seite_verwaltung(
                nutzer, sprache, t('server_abgelehnt', sprache, code=fehl.code)), 502)
        except Exception:
            return self.antwort(seite_verwaltung(nutzer, sprache, t('nochmal', sprache)), 502)

    def aktualisieren(self, pfad, sprache):
        """'/update' fragt nach, '/update-ja' legt den Auftrag ab - mehr tut der Dienst nicht.

        Das Einspielen selbst macht root (messenger-update.service), angestossen
        ueber die Auftragsdatei. Hier laeuft kein apt, kein Netz, kein Neustart:
        der Dienst hat dafuer weder Rechte noch soll er sie haben.
        """
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        if not ist_verwalter(nutzer):
            return self.kein_zugriff(sprache)
        daten, frisch = updates_stand()
        if auftrag_laeuft(daten) or auftrag_liegt_vor():
            return self.antwort(seite_verwaltung(
                nutzer, sprache, t('upd_schon_beauftragt', sprache)), 409)
        if not frisch or not updates_ausstehend(daten):
            return self.antwort(seite_verwaltung(nutzer, sprache, t('upd_nichts', sprache)), 400)
        if pfad == '/update':
            return self.antwort(seite_update_sicher(daten, sprache))
        if not auftrag_schreiben():
            protokoll('update_auftrag_fehlgeschlagen', nutzer)
            return self.antwort(seite_verwaltung(
                nutzer, sprache, t('upd_auftrag_fehler', sprache, betreiber=betreiber(sprache))), 500)
        protokoll('update_beauftragt', nutzer)
        return self.antwort(seite_verwaltung(nutzer, sprache, t('upd_angenommen', sprache)))

    def einladungssprache(self, e):
        """Sprache der Einladungsseite: fest gewaehlt, sonst Browser, sonst Server.

        Die Einladungsseite ist die einzige, die Fremde sehen - ihr Browser
        darf mitreden, wenn der Einladende nichts festgelegt hat.
        """
        fest = sprache_normieren(e['sprache'] if 'sprache' in e.keys() else '')
        if fest:
            return fest
        return sprache_aus_header(self.headers.get('Accept-Language', '')) or server_sprache()

    def einladung_zeigen(self, code):
        with db() as v:
            e = v.execute('SELECT * FROM einladung WHERE code=?', (code,)).fetchone()
        if not e:
            sprache = sprache_aus_header(self.headers.get('Accept-Language', '')) or server_sprache()
            return self.antwort(seite_kurz(
                t('einladung_titel', sprache), t('einl_gibt_es_nicht', sprache),
                t('einl_abgeschnitten', sprache), sprache), 404)
        self.antwort(seite_einladung(e['einlader'], e['token'], self.einladungssprache(e),
                                     abgelaufen=e['laeuft_ab'] < time.time(),
                                     schon_benutzt=e['eingeloest_am'] is not None
                                                   or e['benutzt_am'] is not None,
                                     zurueckgezogen=e['zurueckgezogen_am'] is not None))


class Dienst(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def kommandozeile(argumente):
    """Notausgang: Wer SSH auf den Server hat, kann den zweiten Faktor zuruecksetzen.
    Das ersetzt Rueckfallcodes - der Betreiber kommt immer an seine Maschine."""
    sprache = server_sprache()
    if argumente[:1] == ['--zweitfaktor-aus'] and len(argumente) == 2:
        nutzer = argumente[1]
        if not nutzer.startswith('@'):
            # Den nackten Namen in der Ablage nachschlagen, nicht mit SERVERNAME
            # ergänzen: von der Shell aus ist MESSENGER_SERVERNAME nicht gesetzt,
            # und der Platzhalter traf nie - 'kein zweiter Faktor hinterlegt'.
            with db() as v:
                alle = [z[0] for z in v.execute('SELECT nutzer FROM zweiter_faktor')]
            treffer = [k for k in alle if k.split(':')[0] == f'@{nutzer}']
            nutzer = treffer[0] if treffer else f'@{nutzer}:{SERVERNAME}'
        eingerichtet, _ = zf_stand(nutzer)
        if not eingerichtet:
            print(t('cli_kein_zf', sprache, nutzer=nutzer)); return 1
        zf_entfernen(nutzer)
        versuche_loeschen(nutzer)
        print(t('cli_zf_entfernt', sprache, nutzer=nutzer))
        return 0
    if argumente[:1] == ['--erstanmeldung'] and len(argumente) == 2:
        # Nur die Ablage; das Sperren in Synapse macht der Installer selbst.
        nutzer = argumente[1]
        passwort = sys.stdin.readline().rstrip('\n')
        if not nutzer.startswith('@') or ':' not in nutzer or not passwort:
            print(t('cli_aufruf', sprache)); return 2
        with db() as v:
            v.execute('INSERT OR REPLACE INTO erstanmeldung (nutzer, hash, pw_neu, seit) '
                      'VALUES (?,?,0,?)', (nutzer, pw_hash(passwort), int(time.time())))
        protokoll('erst_eingetragen', nutzer)
        return 0
    print(t('cli_aufruf', sprache))
    return 2


def im_hintergrund():
    """Eingeloeste Einladungen regelmaessig nachfuehren.

    Sonst bekaeme der Neue seine Raumeinladung erst, wenn zufaellig jemand die
    Seite oeffnet - und stuende bis dahin in einer leeren App.
    """
    while True:
        time.sleep(60)
        try:
            kette_nachfuehren()
        except Exception as f:
            protokoll('nachfuehren_fehlgeschlagen', '', str(f)[:120])


if __name__ == '__main__':
    ablage_vorbereiten()
    if len(sys.argv) > 1:
        raise SystemExit(kommandozeile(sys.argv[1:]))
    protokoll('dienst_gestartet', '', f'{ADRESSE}:{PORT} {SPRACHE_VORGABE}')
    threading.Thread(target=im_hintergrund, daemon=True).start()
    Dienst((ADRESSE, PORT), Bedienung).serve_forever()
