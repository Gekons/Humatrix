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

# Vom Installer per Umgebung gesetzt; die Werte hier sind nur der Rueckfall.
# Die Vorgaben sind reine Platzhalter. Der Installer traegt die echten Werte
# als Umgebungsvariablen ein (siehe einladung.service.d/umgebung.conf).
HEIMSERVER = os.environ.get('MESSENGER_HEIMSERVER', 'https://matrix.beispieldomain.de')
SERVERNAME = os.environ.get('MESSENGER_SERVERNAME', 'beispieldomain.de')   # was in die App getippt wird
ELEMENT    = os.environ.get('MESSENGER_ELEMENT',    'https://chat.beispieldomain.de')
EIGENE_URL = os.environ.get('MESSENGER_EIGENE_URL', 'https://einladung.beispieldomain.de')
# Wie der Betreiber in Hinweisen fuer Endnutzer genannt wird ("sag ... Bescheid").
BETREIBER  = os.environ.get('MESSENGER_BETREIBER',  'dem Betreiber')
# Unter diesem Namen zeigt die Startseite direkt die Verwaltung statt der Nutzeransicht.
VERWALTUNG_HOST = os.environ.get('MESSENGER_VERWALTUNG_HOST', '')
PORT = 8082
# Nur oertlich lauschen. Davor steht ein nginx, der genau hierher weiterleitet;
# auf 0.0.0.0 waere der Dienst fuer jeden Rechner im selben Netzsegment direkt
# erreichbar - unter Umgehung von allem, was im nginx davor haengt.
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
VERSUCHE_FENSTER = 900      # Sekunden, ueber die gezaehlt wird

# ---------------------------------------------------------------- Ablage

def db():
    # timeout=30: jede Anfrage hat ihren eigenen Faden und ihre eigene Verbindung.
    # Mit dem Standard von 5 s brach ein paralleler Schreibzugriff mit
    # 'database is locked' ab, sobald ein anderer kurz laenger schrieb.
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
        """)
        # Nachruesten: bestehende Ablagen kennen die Spalte noch nicht
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


def menge(wert):
    """Kontingente lesbar machen: -1 ist keine Zahl, die jemand verstehen will."""
    return 'unbegrenzt' if wert < 0 else str(wert)


def verbraucht_von(nutzer):
    """Abgelaufene, nicht benutzte Einladungen zaehlen nicht mehr mit.

    Benutzt (benutzt_am) zaehlt wie eingeloest: ueber den Link kam jemand herein,
    auch wenn die Zuordnung zum Konto noch unklar ist - sonst fiele das Kontingent
    nach dem Ablauf an den Einladenden zurueck.
    """
    jetzt = int(time.time())
    with db() as v:
        return v.execute(
            'SELECT count(*) FROM einladung WHERE einlader=? '
            'AND (eingeloest_am IS NOT NULL OR benutzt_am IS NOT NULL OR laeuft_ab > ?)',
            (nutzer, jetzt)).fetchone()[0]


# ---------------------------------------------------------------- Protokoll

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


# ---------------------------------------------------------------- Zweiter Faktor

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
    # Wer den zweiten Faktor aendert, beendet damit alle anderen Sitzungen des Kontos.
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


def qr_svg(text, kante=5):
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
            f'width="{n * kante}" height="{n * kante}" role="img" aria-label="QR-Code">'
            f'<rect width="100%" height="100%" fill="#fff"/>'
            f'<g fill="#000">{felder}</g></svg>')


# ---------------------------------------------------------------- Drossel

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


# ---------------------------------------------------------------- Matrix

def matrix_aufruf(pfad, daten=None, token=None, methode=None):
    # 'daten is not None' statt 'if daten': ein LEERES Dictionary ist in Python falsch,
    # und {} ist bei Matrix ein voellig normaler POST-Rumpf (Anmelden, Abmelden, Beitreten).
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
# Synapse wuerfelt sonst Token wie "oGJ6vzzPIi5." - der Punkt am Ende sieht aus wie
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
    # Das PUT unten legt an ODER aendert. Ohne diese Pruefung bekaeme ein bestehendes
    # Konto bei einem Tippfehler ein neues Passwort, verloere seine Verwalterrechte
    # und wuerde entsperrt - bei einer Erfolgsmeldung "angelegt". 404 heisst: frei.
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
    # Merken, dass dieses Konto zu keiner Einladung gehoert - sonst hielte die
    # Einladungskette es fuer den Neuling einer offenen Einladung.
    with db() as v:
        v.execute('INSERT OR IGNORE INTO von_hand (kennung) VALUES (?)', (kennung,))
    return kennung, pw


def passwort_neu(kennung):
    pw = neues_passwort()
    matrix_aufruf('/_synapse/admin/v1/reset_password/' + urllib.parse.quote(kennung),
                  {'new_password': pw, 'logout_devices': True}, token=admin_token())
    sitzung_sperren(kennung)      # logout_devices trifft nur Matrix, nicht den Keks hier
    return pw


def konto_sperren(kennung):
    """Sperren, nicht loeschen: erase bleibt aus, damit es rueckgaengig gemacht werden kann."""
    matrix_aufruf('/_synapse/admin/v1/deactivate/' + urllib.parse.quote(kennung),
                  {'erase': False}, token=admin_token())
    sitzung_sperren(kennung)      # sonst liefe eine offene Einladungs-Sitzung 12 h weiter


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
                          'FROM einladung WHERE eingeloest_am IS NULL').fetchall()
        if not offen:
            return
        vergeben = {z[0] for z in v.execute(
            'SELECT eingeloest_von FROM einladung WHERE eingeloest_von IS NOT NULL')}
        vergeben |= {z[0] for z in v.execute('SELECT kennung FROM von_hand')}

    zustand = token_zustand()
    # Einmal als benutzt erkannt bleibt benutzt - auch wenn Synapse gerade nicht
    # antwortet oder den Token nicht mehr fuehrt.
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
        # fuer dieselbe Person waeren die Folge.
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


# ---------------------------------------------------------------- Sitzung

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


# ---------------------------------------------------------------- Seiten

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
input{width:100%;padding:13px;font:inherit;border:1.5px solid var(--linie);
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
.vtab td{vertical-align:middle}

.marke{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--eigen);
       border:1px solid var(--eigen);border-radius:20px;padding:1px 8px;margin-left:6px}
.ok{color:var(--eigen);font-weight:600}
"""


def seite(titel, inhalt):
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(titel)}</title><style>{STIL}</style></head>
<body><div class="k">{inhalt}</div></body></html>""".encode()


def seite_anmelden(fehler='', verwaltung=False):
    """Dieselbe Anmeldung, aber unter dem Verwaltungsnamen mit passender Aufschrift.

    Vorher stand auch dort "Jemanden einladen" - das verwirrt, weil die Seite
    unter verwaltung.<domain> gerade NICHT zum Einladen da ist.
    """
    hinweis = f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else ''
    titel = 'Verwaltung' if verwaltung else 'Einladungen'
    kopf = 'Serververwaltung' if verwaltung else 'Jemanden einladen'
    unter = ('Anmeldung für die Verwalter dieses Servers.' if verwaltung
             else 'Melde dich mit deinem gewohnten Konto an — dasselbe wie im Chat.')
    return seite(titel, f"""
      <h1>{kopf}</h1>
      <p class="leise">{unter}</p>
      {hinweis}
      <form method="post" action="/anmelden" class="karte">
        <label for="b">Benutzername</label>
        <input id="b" name="benutzer" autocomplete="username" autocapitalize="none" required>
        <div style="height:14px"></div>
        <label for="p">Passwort</label>
        <input id="p" name="passwort" type="password" autocomplete="current-password" required>
        <button type="submit">Anmelden</button>
      </form>""")


def seite_code(hinweis=''):
    fehler = f'<p class="fehler">{html.escape(hinweis)}</p>' if hinweis else ''
    return seite('Bestätigung', f"""
      <h1>Code aus der App</h1>
      <p class="leise">Dein Passwort stimmt. Jetzt noch die sechs Ziffern aus deiner
         Authenticator-App.</p>
      {fehler}
      <form method="post" action="/code" class="karte">
        <label for="c">Sechsstelliger Code</label>
        <input id="c" name="code" inputmode="numeric" autocomplete="one-time-code"
               pattern="[0-9 ]{{6,9}}" required autofocus>
        <button type="submit">Weiter</button>
      </form>
      <a class="knopf still" href="/abmelden">Abbrechen</a>""")


def seite_zweitfaktor(nutzer, hinweis='', fehler=''):
    eingerichtet, aktiv = zf_stand(nutzer)
    meldung = f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else ''
    if hinweis:
        meldung += f'<div class="karte" style="border-color:var(--eigen)"><p><b>{html.escape(hinweis)}</b></p></div>'

    if aktiv:
        return seite('Zweiter Faktor', f"""
          <h1>Zweiter Faktor ist an</h1>
          {meldung}
          <div class="karte">
            <p>Beim Anmelden wirst du zusätzlich nach dem Code aus deiner App gefragt.</p>
            <p class="leise" style="margin-top:12px">Zum Abschalten brauchst du einen gültigen
               Code — damit niemand das heimlich ausknipst, der kurz an deinem Rechner sitzt.</p>
            <form method="post" action="/zweitfaktor" style="margin-top:14px">
              <input type="hidden" name="tat" value="aus">
              <label for="ca">Code zur Bestätigung</label>
              <input id="ca" name="code" inputmode="numeric" pattern="[0-9 ]{{6,9}}" required>
              <button type="submit" class="gefahrgross">Zweiten Faktor abschalten</button>
            </form>
          </div>
          <a class="knopf still" href="/">Zurück</a>""")

    g = zf_geheimnis(nutzer) if eingerichtet else None
    if not g:
        g = zf_vorbereiten(nutzer)
    uri = zf_uri(nutzer, g)
    lesbar = ' '.join(g[i:i + 4] for i in range(0, len(g), 4))

    return seite('Zweiter Faktor', f"""
      <h1>Zweiten Faktor einrichten</h1>
      {meldung}
      <div class="karte">
        <p>Scanne das Bild mit einer Authenticator-App — zum Beispiel
           <b>Aegis</b> (Android), <b>Ente Auth</b> oder der Passwort-App deines Telefons.</p>
        <div style="text-align:center;margin:18px 0">{qr_svg(uri)}</div>
        <p class="leise">Geht das Scannen nicht, tippe diesen Schlüssel von Hand ein:</p>
        <div class="wert">{html.escape(lesbar)}</div>
      </div>
      <form method="post" action="/zweitfaktor" class="karte">
        <input type="hidden" name="tat" value="bestaetigen">
        <label for="cb">Zur Bestätigung: der Code, den die App jetzt anzeigt</label>
        <input id="cb" name="code" inputmode="numeric" pattern="[0-9 ]{{6,9}}" required autofocus>
        <button type="submit">Einschalten</button>
      </form>
      <p class="leise">Erst wenn ein richtiger Code ankommt, wird der zweite Faktor scharf.
         Du kannst dich also nicht aussperren.</p>
      <a class="knopf still" href="/">Später</a>""")


def restlaufzeit(sekunden):
    """'noch 3 Tage', 'noch 1 Tag' oder 'laeuft heute ab'.

    Vorher stand bei 20 Minuten Restzeit 'noch 1 Tage' - der Einladende
    verschickte den Link, und der Eingeladene sah 'abgelaufen'.
    """
    if sekunden < 86400:
        return 'läuft heute ab'
    tage = int(sekunden // 86400)
    return f'noch {tage} {"Tag" if tage == 1 else "Tage"}'


def seite_uebersicht(nutzer, neuer_link=''):
    gesamt = kontingent_von(nutzer)
    offen = verbleibend(nutzer)
    kurz = html.escape(nutzer.split(':')[0].lstrip('@'))

    with db() as v:
        liste = v.execute('SELECT code, erstellt, laeuft_ab, eingeloest_am, eingeloest_von, '
                          'benutzt_am, raum_name, raum_fehler FROM einladung WHERE einlader=? '
                          'ORDER BY erstellt DESC LIMIT 20', (nutzer,)).fetchall()
    zeilen = ''
    for e in liste:
        if e['eingeloest_von']:
            zustand = (f'<span class="ok">angenommen</span><br>'
                       f'<span class="leise">{html.escape(e["eingeloest_von"])}</span>')
        elif e['eingeloest_am'] or e['benutzt_am']:
            # Token benutzt, aber kein Konto sicher zuzuordnen - nicht 'offen' anzeigen,
            # sonst wird der verbrauchte Link nochmal verschickt.
            zustand = '<span class="ok">eingelöst</span><br><span class="leise">Zuordnung unklar</span>'
        elif e['laeuft_ab'] < time.time():
            zustand = '<span class="leise">abgelaufen</span>'
        else:
            zustand = f'offen<br><span class="leise">{restlaufzeit(e["laeuft_ab"] - time.time())}</span>'
        datum = time.strftime('%d.%m.%Y', time.localtime(e['erstellt']))
        ziel = (f'<br><span class="leise">→ {html.escape(e["raum_name"])}</span>'
                if e['raum_name'] else '')
        if e['raum_fehler']:
            ziel += ('<br><span class="leise" style="color:var(--warn)">'
                     'Raumeinladung klappte nicht</span>')
        zeilen += f'<tr><td>{datum}{ziel}</td><td>{zustand}</td></tr>'
    tabelle = (f'<div class="karte"><h2>Deine Einladungen</h2><table>{zeilen}</table></div>'
               if zeilen else '')

    if neuer_link:
        link = html.escape(neuer_link)
        frisch = f"""
        <div class="karte">
          <h2>Fertig — diesen Link verschicken</h2>
          <p class="leise">Per WhatsApp, SMS oder wie du magst. Er gilt {GUELTIG_TAGE} Tage
             und funktioniert genau einmal.</p>
          <div class="wert">{link}</div>
          <a class="knopf" href="https://wa.me/?text={urllib.parse.quote(neuer_link)}">Per WhatsApp schicken</a>
        </div>"""
    else:
        frisch = ''

    if offen != 0:
        raeume = raeume_von(nutzer)
        if raeume:
            wahl = ''.join(
                f'<option value="{html.escape(r["id"])}">{html.escape(r["name"])}'
                f' ({r["mitglieder"]})</option>' for r in raeume)
            auswahl = (f'<label for="rm" style="margin-top:4px">Wohin soll die Person?</label>'
                       f'<select id="rm" name="raum" style="width:100%">{wahl}'
                       f'<option value="">— erstmal nirgendwohin —</option></select>')
        else:
            auswahl = ('<p class="leise">Du bist noch in keinem benannten Raum. '
                       'Lege im Chat einen an, dann kannst du Leute gleich dorthin einladen.</p>')
        knopf = ('<form method="post" action="/erzeugen">' + auswahl +
                 '<button type="submit">Einladung erzeugen</button></form>')
    elif gesamt == 0:
        knopf = ('<p class="leise">Für dich sind Einladungen nicht freigeschaltet. '
                 'Frag den Betreiber, wenn du jemanden holen möchtest.</p>')
    else:
        knopf = ('<p class="leise">Du hast gerade keine Einladungen mehr übrig. '
                 'Frag den Betreiber, wenn du weitere brauchst.</p>')

    anzeige = 'unbegrenzt' if offen < 0 else str(offen)
    darunter = ('ohne Begrenzung' if gesamt < 0 else f'von {gesamt} insgesamt')
    verwaltung = ('<a class="knopf still" href="/verwaltung">Nutzer und Kontingente verwalten</a>'
                  if ist_verwalter(nutzer) else '')

    return seite('Einladungen', f"""
      <h1>Hallo {kurz}</h1>
      {frisch}
      <div class="karte">
        <p class="leise">Du kannst noch einladen</p>
        <p class="gross">{anzeige}</p>
        <p class="leise">{darunter}</p>
        {knopf}
      </div>
      {tabelle}
      {verwaltung}
      <a class="knopf still" href="/abmelden">Abmelden</a>""")


HOECHSTWERT = 10000      # keine Begrenzung im Sinne, aber auch kein Tippfehler mit acht Nullen


def feld(jetzt, kennzeichen):
    """Freies Zahlenfeld plus Haken fuer unbegrenzt. 0 heisst: darf nicht einladen."""
    zahl = '' if jetzt < 0 else str(jetzt)
    haken = ' checked' if jetzt < 0 else ''
    return (f'<input type="number" name="gesamt" min="0" max="{HOECHSTWERT}" '
            f'value="{zahl}" placeholder="0" aria-label="Anzahl Einladungen" class="zahl">'
            f'<label class="haken"><input type="checkbox" name="unbegrenzt" value="1"'
            f'{haken} aria-label="unbegrenzt"> unbegrenzt</label>')


def seite_verwaltung(nutzer, hinweis='', geheim=None):
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
          <h2>Neues Passwort für {html.escape(wer)}</h2>
          <p class="leise">Einmal sichtbar. Gib es weiter und lass es ändern.</p>
          <div class="wert">{html.escape(pw)}</div>
        </div>"""

    zeilen = ''
    for u in alle_nutzer():
        k = u['kennung']
        kurz = k.split(':')[0].lstrip('@')
        gesamt = kontingent_von(k)
        benutzt = verbraucht_von(k)
        offen = 'unbegrenzt' if gesamt < 0 else str(max(0, gesamt - benutzt))
        ich = (k == nutzer)

        marken = ''
        if u['verwalter']:
            marken += '<span class="marke">Verwalter</span>'
        if u['gesperrt']:
            marken += '<span class="marke warnmarke">gesperrt</span>'

        def knopf(weg, text, zusatz='', richtung=''):
            versteckt = (f'<input type="hidden" name="richtung" value="{richtung}">'
                         if richtung else '')
            return (f'<form method="post" action="{weg}" style="display:inline">'
                    f'<input type="hidden" name="nutzer" value="{html.escape(k)}">{versteckt}'
                    f'<button type="submit" class="klein{zusatz}">{text}</button></form>')

        if u['gesperrt']:
            taten = knopf('/entsperren', 'Entsperren')
        else:
            taten = knopf('/passwort', 'Passwort neu')
            if not ich:
                taten += (knopf('/verwalter', 'Verwalter aus', richtung='nein') if u['verwalter']
                          else knopf('/verwalter', 'Verwalter an', richtung='ja'))
                taten += knopf('/sperren', 'Sperren', ' gefahr')
            else:
                taten += '<span class="leise"> &nbsp;das bist du</span>'

        kontingentteil = '' if u['gesperrt'] else f"""
            <form method="post" action="/kontingent" class="reihe">
              <input type="hidden" name="nutzer" value="{html.escape(k)}">
              {feld(gesamt, k)}
              <button type="submit">Setzen</button>
            </form>"""

        zeilen += f"""<tr><td colspan="2">
          <div><b>{html.escape(u['anzeige'])}</b>{marken}</div>
          <div class="leise mono">{html.escape(k)}</div>
          <div class="leise">{benutzt} Einladungen verschickt · {offen} offen</div>
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
               else '<span style="color:var(--warn)">unklar</span>')
        kzeilen += (f'<tr><td>{wer}</td>'
                    f'<td>geholt von {html.escape(e["einlader"].split(":")[0].lstrip("@"))}<br>'
                    f'<span class="leise">{time.strftime("%d.%m.%Y", time.localtime(e["wann"]))}'
                    f'</span></td></tr>')
    kettenteil = (f'<div class="karte"><h2>Wer hat wen geholt</h2>'
                  f'<table class="vtab kette">{kzeilen}</table></div>') if kzeilen else ''

    _, zf_an = zf_stand(nutzer)
    if not zf_an:
        meldung += """
        <div class="karte" style="border-color:var(--warn)">
          <h2 style="color:var(--warn)">Dein Konto hat keinen zweiten Faktor</h2>
          <p class="leise">Diese Seite kann Konten anlegen, sperren und Passwörter ändern.
             Mit nur einem Passwort davor ist das dünn.</p>
          <a class="knopf" href="/zweitfaktor">Jetzt einrichten</a>
        </div>"""

    return seite('Verwaltung', f"""
      <h1>Verwaltung</h1>
      {meldung}
      <div class="karte">
        <h2>Vorgabe für neue Konten</h2>
        <p class="leise">So viele Einladungen bekommt, wer neu dazukommt.
           <b>0</b> bedeutet: darf niemanden einladen.</p>
        <form method="post" action="/standard" class="reihe" style="margin-top:10px">
          {feld(vorgabe, 'standard')}
          <button type="submit">Speichern</button>
        </form>
      </div>

      <div class="karte">
        <h2>Konto von Hand anlegen</h2>
        <p class="leise">Normalerweise nicht nötig — dafür gibt es Einladungen.
           Nützlich, wenn jemand keinen Link öffnen kann.</p>
        <form method="post" action="/anlegen">
          <label for="bn">Benutzername (klein, ohne Leerzeichen)</label>
          <input id="bn" name="name" pattern="[a-z0-9._=-]{{1,60}}" required autocapitalize="none">
          <div style="height:12px"></div>
          <label for="az">Anzeigename (frei, z.B. „Oma Erna“)</label>
          <input id="az" name="anzeige">
          <button type="submit">Anlegen</button>
        </form>
      </div>

      <div class="karte">
        <h2>Alle Konten</h2>
        <table class="vtab">{zeilen}</table>
      </div>
      {kettenteil}
      <a class="knopf still" href="/zweitfaktor">Zweiter Faktor</a>
      <a class="knopf still" href="/eigene">Meine eigenen Einladungen</a>
      <a class="knopf still" href="/abmelden">Abmelden</a>""")


def seite_sperren_sicher(kennung):
    kurz = html.escape(kennung.split(':')[0].lstrip('@'))
    return seite('Sperren', f"""
      <h1>{kurz} wirklich sperren?</h1>
      <div class="karte">
        <p>Das Konto kann sich danach nicht mehr anmelden und wird aus allen Räumen entfernt.
           <b>Der Chatverlauf der anderen bleibt erhalten.</b></p>
        <p class="leise" style="margin-top:10px">Rückgängig machbar: Beim Entsperren
           vergibt der Server ein neues Passwort. Die alten Schlüssel sind dann aber weg —
           frühere verschlüsselte Nachrichten kann das Konto nicht mehr lesen.</p>
        <form method="post" action="/sperren-ja">
          <input type="hidden" name="nutzer" value="{html.escape(kennung)}">
          <button type="submit" class="gefahrgross">Ja, sperren</button>
        </form>
        <a class="knopf still" href="/">Abbrechen</a>
      </div>""")


def seite_einladung(einlader, token, abgelaufen=False, schon_benutzt=False):
    wer = html.escape(einlader.split(':')[0].lstrip('@'))
    if abgelaufen or schon_benutzt:
        grund = 'ist abgelaufen' if abgelaufen else 'wurde schon benutzt'
        return seite('Einladung', f"""
          <h1>Diese Einladung {grund}</h1>
          <p>Bitte frag {wer} nach einer neuen.</p>""")
    return seite('Einladung', f"""
      <h1>{wer} lädt dich ein</h1>
      <p class="leise">In einen Chat, bei dem kein Konzern mitliest. Drei Schritte.</p>
      <div class="karte">
        <ol>
          <li><b>App installieren</b><br>
              <span class="leise">Suche im App Store oder bei Google Play nach
              <b>Element</b> — oder nutze unten einfach den Browser.</span></li>
          <li><b>Server eintragen</b><br>
              <span class="leise">Die App fragt danach:</span>
              <div class="wert">{SERVERNAME}</div></li>
          <li><b>Diesen Einladungscode eingeben</b>
              <div class="wert">{html.escape(token)}</div></li>
        </ol>
        <a class="knopf" href="{ELEMENT}/#/register">Lieber im Browser öffnen</a>
      </div>
      <p class="leise">Ohne diesen Code kommt niemand auf den Server — es gibt keine
         offene Anmeldung. Deshalb bitte nicht weiterleiten.</p>""")


# ---------------------------------------------------------------- Netz

ERZEUGEN_SPERRE = threading.Lock()   # ein Prozess, ein Schloss: Kontingent pruefen + eintragen


class Bedienung(http.server.BaseHTTPRequestHandler):
    server_version = 'Einladung'
    sys_version = ''

    def log_message(self, *_):
        pass                                  # keine Zugriffsprotokolle: Datensparsamkeit

    # -------------------------------------------------- Hilfen
    def antwort(self, koerper, code=200, kopf=None):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(koerper)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        # no-store: Seiten mit Einmal-Passwort, TOTP-Schluessel oder frischem Link
        # duerfen nicht aus dem Verlaufscache kommen, wenn nach dem Abmelden
        # jemand anderes am selben Browser "Zurueck" drueckt.
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

    # -------------------------------------------------- Abrufe
    def do_GET(self):
        pfad = urllib.parse.urlparse(self.path).path
        if pfad.startswith('/e/'):
            return self.einladung_zeigen(pfad[3:])
        if pfad == '/abmelden':
            # Den Keks nicht nur im Browser loeschen, sondern serverseitig sperren:
            # eine Kopie des Kekses liefe sonst bis zu SITZUNG_STUNDEN weiter.
            nutzer, _stufe = self.sitzung()
            if nutzer:
                sitzung_sperren(nutzer)
            return self.weiter('/', {'Set-Cookie': 'sitzung=; Path=/; Max-Age=0'})
        if pfad == '/zweitfaktor':
            nutzer = self.wer()
            return self.antwort(seite_zweitfaktor(nutzer)) if nutzer else self.weiter('/')
        if pfad not in ('/', '/verwaltung', '/eigene'):
            return self.antwort(seite('Nicht gefunden', '<h1>Nicht gefunden</h1>'), 404)

        nutzer = self.wer()
        if not nutzer:
            return self.antwort(seite_anmelden(verwaltung=self.unter_verwaltung()))

        # Unter dem Verwaltungsnamen zeigt die Startseite direkt die Verwaltung.
        # Dieselbe Anwendung, zwei Gesichter - getrennt nach Zustaendigkeit,
        # nicht nach Technik.
        hier_verwaltung = (pfad == '/verwaltung') or \
                          (pfad == '/' and VERWALTUNG_HOST and
                           self.headers.get('Host', '').split(':')[0] == VERWALTUNG_HOST)
        if hier_verwaltung:
            if not ist_verwalter(nutzer):
                return self.antwort(seite('Verwaltung',
                    '<h1>Kein Zugriff</h1><p>Diese Seite ist den Verwaltern des Servers '
                    'vorbehalten.</p><a class="knopf still" href="/eigene">Zu meinen '
                    'Einladungen</a>'), 403)
            return self.antwort(seite_verwaltung(nutzer))

        try:
            kette_nachfuehren()
        except Exception:
            pass
        self.antwort(seite_uebersicht(nutzer))

    def do_POST(self):
        pfad = urllib.parse.urlparse(self.path).path
        if pfad == '/anmelden':
            return self.anmelden()
        if pfad == '/code':
            return self.code_pruefen()
        if pfad == '/zweitfaktor':
            return self.zweitfaktor()
        if pfad == '/erzeugen':
            return self.erzeugen()
        if pfad in ('/kontingent', '/standard'):
            return self.kontingent_aendern(pfad)
        if pfad in ('/anlegen', '/passwort', '/sperren', '/sperren-ja',
                    '/entsperren', '/verwalter'):
            return self.konten_verwalten(pfad)
        self.antwort(seite('Nicht gefunden', '<h1>Nicht gefunden</h1>'), 404)

    # -------------------------------------------------- Vorgaenge
    MELDUNG = {
        'falsch':   'Benutzername oder Passwort stimmt nicht.',
        'gebremst': 'Zu viele Versuche kurz hintereinander. Bitte eine Minute warten '
                    'und es dann nochmal probieren — dein Passwort ist vermutlich richtig.',
        'weg':      'Der Chat-Server antwortet gerade nicht. Bitte später nochmal.',
    }

    def unter_verwaltung(self):
        """Wird diese Anfrage unter dem Verwaltungsnamen gestellt?"""
        return bool(VERWALTUNG_HOST) and \
            self.headers.get('Host', '').split(':')[0] == VERWALTUNG_HOST

    def anmelden(self):
        f = self.formular()
        zustand, nutzer = anmeldung_pruefen(f.get('benutzer', '').strip(),
                                            f.get('passwort', ''))
        if zustand != 'ok':
            if zustand == 'falsch':
                time.sleep(1.5)               # bremst Durchprobieren zusaetzlich
                protokoll('anmeldung_falsch', f.get('benutzer', '')[:40])
            code = 429 if zustand == 'gebremst' else (502 if zustand == 'weg' else 401)
            return self.antwort(
                seite_anmelden(self.MELDUNG[zustand], self.unter_verwaltung()), code)

        _, zf_an = zf_stand(nutzer)
        if zf_an:
            # Passwort stimmt, aber die Sitzung bleibt halb, bis der Code da ist.
            return self.antwort(seite_code(), 200, self.keks_setzen(nutzer, 'halb'))
        protokoll('angemeldet', nutzer)
        self.weiter('/', self.keks_setzen(nutzer))

    def code_pruefen(self):
        nutzer, stufe = self.sitzung()
        if not nutzer or stufe != 'halb':
            return self.weiter('/')
        if gebremst(nutzer):
            protokoll('2fa_gebremst', nutzer)
            return self.antwort(seite_code(
                'Zu viele falsche Codes. Bitte eine Viertelstunde warten.'), 429)
        if not zf_pruefen(nutzer, self.formular().get('code', '')):
            fehlversuch(nutzer)
            protokoll('2fa_falsch', nutzer)
            time.sleep(1.0)
            return self.antwort(seite_code('Der Code stimmt nicht. '
                                           'Stimmt die Uhrzeit auf deinem Telefon?'), 401)
        versuche_loeschen(nutzer)
        protokoll('2fa_ok', nutzer)
        self.weiter('/', self.keks_setzen(nutzer))

    def zweitfaktor(self):
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        f = self.formular()
        tat = f.get('tat', '')
        if gebremst(nutzer):
            return self.antwort(seite_zweitfaktor(
                nutzer, fehler='Zu viele falsche Codes. Bitte eine Viertelstunde warten.'), 429)
        if not zf_pruefen(nutzer, f.get('code', '')):
            fehlversuch(nutzer)
            protokoll('2fa_einrichtung_falsch', nutzer)
            return self.antwort(seite_zweitfaktor(
                nutzer, fehler='Der Code stimmt nicht. Stimmt die Uhrzeit auf dem Telefon?'), 401)
        versuche_loeschen(nutzer)
        # Das Aendern sperrt alle bisherigen Sitzungen des Kontos - auch diese.
        # Deshalb gleich ein frischer Keks, damit nur die ANDEREN hinausfliegen.
        if tat == 'aus':
            zf_entfernen(nutzer)
            return self.antwort(seite_zweitfaktor(nutzer, hinweis='Zweiter Faktor ist aus.'),
                                200, self.keks_setzen(nutzer))
        zf_einschalten(nutzer)
        return self.antwort(seite_zweitfaktor(
            nutzer, hinweis='Zweiter Faktor ist an. Ab jetzt beim Anmelden nötig.'),
            200, self.keks_setzen(nutzer))

    def erzeugen(self):
        gewaehlt = self.formular().get('raum', '').strip()   # Rumpf zuerst, er ist nur einmal lesbar
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        raum = raum_name = None
        if gewaehlt:
            # nur Raeume zulassen, in denen der Einladende selbst ist. Laesst sich der
            # gewaehlte gerade nicht bestaetigen (Synapse antwortet nicht), lieber
            # abbrechen als die Raumwahl still fallen zu lassen: der Neue landete
            # sonst nie dort, und niemand erfuehre davon. Vor dem Token, damit kein
            # Registrierungs-Token bei Synapse verwaist.
            treffer = next((r for r in raeume_von(nutzer) if r['id'] == gewaehlt), None)
            if not treffer:
                return self.antwort(seite('Einladungen',
                    '<h1>Das hat nicht geklappt</h1><p>Der gewählte Raum ließ sich gerade '
                    'nicht bestätigen. Bitte die Seite neu laden und nochmal versuchen.</p>'
                    '<a class="knopf" href="/">Zurück</a>'), 502)
            raum, raum_name = treffer['id'], treffer['name']
        # Pruefung, Token und Eintrag unter EINER Sperre: jede Anfrage laeuft in ihrem
        # eigenen Faden, und zwischen Pruefung und Eintrag liegt ein Netzaufruf. Ohne
        # Sperre kamen zehn gleichzeitige Klicks bei Kontingent 1 alle durch.
        with ERZEUGEN_SPERRE:
            if verbleibend(nutzer) == 0:
                return self.antwort(seite_uebersicht(nutzer))
            try:
                token, laeuft_ab = token_erzeugen()
            except Exception:
                return self.antwort(seite('Einladungen',
                    '<h1>Das hat nicht geklappt</h1><p>Der Server antwortet gerade nicht. '
                    f'Bitte später nochmal — oder sag {BETREIBER} Bescheid.</p>'
                    '<a class="knopf" href="/">Zurück</a>'), 502)
            code = secrets.token_urlsafe(9)
            with db() as v:
                v.execute('INSERT INTO einladung (code, token, einlader, erstellt, laeuft_ab, '
                          'raum, raum_name) VALUES (?,?,?,?,?,?,?)',
                          (code, token, nutzer, int(time.time()), laeuft_ab, raum, raum_name))
        self.antwort(seite_uebersicht(nutzer, f'{EIGENE_URL}/e/{code}'))

    def kontingent_aendern(self, pfad):
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        if not ist_verwalter(nutzer):
            return self.antwort(seite('Verwaltung', '<h1>Kein Zugriff</h1>'), 403)
        f = self.formular()
        if f.get('unbegrenzt'):
            wert = -1
        else:
            try:
                wert = int(f.get('gesamt', '').strip() or '0')
            except ValueError:
                return self.antwort(seite_verwaltung(
                    nutzer, 'Bitte eine Zahl eintragen oder „unbegrenzt“ ankreuzen.'), 400)
            if wert < 0 or wert > HOECHSTWERT:
                return self.antwort(seite_verwaltung(
                    nutzer, f'Bitte eine Zahl zwischen 0 und {HOECHSTWERT}.'), 400)

        if pfad == '/standard':
            standard_setzen(wert)
            return self.antwort(seite_verwaltung(nutzer, f'Vorgabe steht auf {menge(wert)}.'))

        ziel = f.get('nutzer', '').strip()
        if not ziel.startswith('@'):
            return self.antwort(seite_verwaltung(nutzer, 'Unbekanntes Konto.'), 400)
        kontingent_setzen(ziel, wert)
        kurz = ziel.split(':')[0].lstrip('@')
        return self.antwort(seite_verwaltung(nutzer, f'{kurz}: {menge(wert)}.'))

    def konten_verwalten(self, pfad):
        nutzer = self.wer()
        if not nutzer:
            return self.weiter('/')
        if not ist_verwalter(nutzer):
            return self.antwort(seite('Verwaltung', '<h1>Kein Zugriff</h1>'), 403)
        f = self.formular()
        ziel = f.get('nutzer', '').strip()

        try:
            if pfad == '/anlegen':
                name = f.get('name', '').strip().lower()
                if not re.fullmatch(r'[a-z0-9._=-]{1,60}', name):
                    return self.antwort(seite_verwaltung(nutzer,
                        'Benutzername: nur Kleinbuchstaben, Ziffern und . _ = -'), 400)
                try:
                    kennung, pw = nutzer_anlegen(name, f.get('anzeige', '').strip())
                except KontoVergeben:
                    return self.antwort(seite_verwaltung(nutzer,
                        f'Diesen Benutzernamen gibt es schon: {name}.'), 400)
                return self.antwort(seite_verwaltung(nutzer,
                    f'Konto {name} angelegt.', geheim=(name, pw)))

            if not ziel.startswith('@'):
                return self.antwort(seite_verwaltung(nutzer, 'Unbekanntes Konto.'), 400)
            kurz = ziel.split(':')[0].lstrip('@')

            if pfad == '/passwort':
                pw = passwort_neu(ziel)
                # Eigenes Passwort: die Sperre traefe auch diese Sitzung - frischer Keks.
                frisch = self.keks_setzen(nutzer) if ziel == nutzer else None
                return self.antwort(seite_verwaltung(nutzer,
                    f'Neues Passwort für {kurz}. Alle Geräte wurden abgemeldet.',
                    geheim=(kurz, pw)), 200, frisch)

            if pfad == '/sperren':
                if ziel == nutzer:
                    return self.antwort(seite_verwaltung(nutzer,
                        'Dich selbst kannst du nicht sperren.'), 400)
                return self.antwort(seite_sperren_sicher(ziel))

            if pfad == '/sperren-ja':
                if ziel == nutzer:
                    return self.antwort(seite_verwaltung(nutzer,
                        'Dich selbst kannst du nicht sperren.'), 400)
                konto_sperren(ziel)
                return self.antwort(seite_verwaltung(nutzer, f'{kurz} ist gesperrt.'))

            if pfad == '/entsperren':
                return self.antwort(seite_verwaltung(nutzer,
                    f'{kurz} ist wieder frei — mit neuem Passwort.',
                    geheim=(kurz, konto_entsperren(ziel))))

            if pfad == '/verwalter':
                if ziel == nutzer:
                    return self.antwort(seite_verwaltung(nutzer,
                        'Deine eigenen Rechte kannst du hier nicht ändern.'), 400)
                # Die Richtung kommt vom Knopf, nicht vom Umschalten des Ist-Zustands:
                # lieferte die Kontenliste gerade nichts, machte 'Verwalter aus' sonst
                # das Konto zum Verwalter.
                richtung = f.get('richtung', '')
                if richtung not in ('ja', 'nein'):
                    return self.antwort(seite_verwaltung(nutzer,
                        'Unklare Anfrage. Bitte die Seite neu laden.'), 400)
                verwalter_setzen(ziel, richtung == 'ja')
                return self.antwort(seite_verwaltung(nutzer,
                    f'{kurz} ist {"jetzt Verwalter" if richtung == "ja" else "kein Verwalter mehr"}.'))
        except urllib.error.HTTPError as fehl:
            return self.antwort(seite_verwaltung(nutzer,
                f'Der Server hat abgelehnt (HTTP {fehl.code}).'), 502)
        except Exception:
            return self.antwort(seite_verwaltung(nutzer,
                'Das hat nicht geklappt. Bitte nochmal.'), 502)

    def einladung_zeigen(self, code):
        with db() as v:
            e = v.execute('SELECT * FROM einladung WHERE code=?', (code,)).fetchone()
        if not e:
            return self.antwort(seite('Einladung',
                '<h1>Diese Einladung gibt es nicht</h1>'
                '<p>Vielleicht ist der Link unterwegs abgeschnitten worden.</p>'), 404)
        self.antwort(seite_einladung(e['einlader'], e['token'],
                                     abgelaufen=e['laeuft_ab'] < time.time(),
                                     schon_benutzt=e['eingeloest_am'] is not None
                                                   or e['benutzt_am'] is not None))


class Dienst(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def kommandozeile(argumente):
    """Notausgang: Wer SSH auf den Server hat, kann den zweiten Faktor zuruecksetzen.
    Das ersetzt Rueckfallcodes - der Betreiber kommt immer an seine Maschine."""
    if argumente[:1] == ['--zweitfaktor-aus'] and len(argumente) == 2:
        nutzer = argumente[1]
        if not nutzer.startswith('@'):
            # Den nackten Namen in der Ablage nachschlagen, nicht mit SERVERNAME
            # ergaenzen: von der Shell aus ist MESSENGER_SERVERNAME nicht gesetzt,
            # und der Platzhalter traf nie - 'kein zweiter Faktor hinterlegt'.
            with db() as v:
                alle = [z[0] for z in v.execute('SELECT nutzer FROM zweiter_faktor')]
            treffer = [k for k in alle if k.split(':')[0] == f'@{nutzer}']
            nutzer = treffer[0] if treffer else f'@{nutzer}:{SERVERNAME}'
        eingerichtet, _ = zf_stand(nutzer)
        if not eingerichtet:
            print(f'{nutzer}: kein zweiter Faktor hinterlegt.'); return 1
        zf_entfernen(nutzer)
        versuche_loeschen(nutzer)
        print(f'{nutzer}: zweiter Faktor entfernt. Beim naechsten Anmelden reicht das Passwort.')
        return 0
    print('Aufruf:\n'
          '  einladung.py                         Dienst starten\n'
          '  einladung.py --zweitfaktor-aus NAME  zweiten Faktor eines Kontos zuruecksetzen')
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
    protokoll('dienst_gestartet', '', f'{ADRESSE}:{PORT}')
    threading.Thread(target=im_hintergrund, daemon=True).start()
    Dienst((ADRESSE, PORT), Bedienung).serve_forever()
