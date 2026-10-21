#!/usr/bin/env python3
"""A-Eintrag eines Namens direkt bei den zuständigen Nameservern der Domain.

Warum nicht einfach getent: Der Resolver des Anbieters merkt sich auch ein
"gibt es nicht". Wer den Assistenten vor dem Anlegen der DNS-Einträge einmal
prüfen lässt, bekam danach bis zu einer Stunde lang "noch kein Eintrag", obwohl
die Einträge längst standen (gesehen bei Ionos). Die zuständigen Nameserver
cachen nichts, und Let's Encrypt fragt ebenfalls dort.

Nur Standardbibliothek. Geht die direkte Frage nicht (UDP 53 nach außen
gesperrt o. Ä.), bleibt getent als Rückfall.

Aufruf: dns_frage.py NAME   -> gibt die erste IPv4-Adresse aus, sonst nichts
"""
import random
import socket
import struct
import subprocess
import sys

OEFFENTLICH = ('1.1.1.1', '9.9.9.9', '8.8.8.8')   # nur für NS-Einträge und Adressen der Nameserver
TYP_A, TYP_NS, TYP_CNAME = 1, 2, 5


def _name_kodieren(name):
    return b''.join(bytes([len(t)]) + t.encode('idna') for t in name.rstrip('.').split('.')) + b'\0'


def _name_lesen(daten, pos):
    """Namen ab pos lesen (mit Kompression); liefert (name, pos danach)."""
    teile, sprung, ende = [], False, pos
    for _ in range(64):
        laenge = daten[pos]
        if laenge & 0xC0 == 0xC0:
            if not sprung:
                ende = pos + 2
            pos = ((laenge & 0x3F) << 8) | daten[pos + 1]
            sprung = True
            continue
        if laenge == 0:
            if not sprung:
                ende = pos + 1
            break
        teile.append(daten[pos + 1:pos + 1 + laenge].decode('ascii', 'replace'))
        pos += 1 + laenge
    return '.'.join(teile), ende


def frage(server, name, typ, rekursiv=True, zeit=3.0):
    """Liefert (rcode, [(typ, wert)]) aus Antwort- und Autoritätsteil."""
    kennung = random.randint(0, 0xFFFF)
    kopf = struct.pack('>HHHHHH', kennung, 0x0100 if rekursiv else 0x0000, 1, 0, 0, 0)
    anfrage = kopf + _name_kodieren(name) + struct.pack('>HH', typ, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(zeit)
        s.sendto(anfrage, (server, 53))
        daten, _ = s.recvfrom(4096)
    kid, flaggen, qd, an, ns, _ar = struct.unpack('>HHHHHH', daten[:12])
    if kid != kennung:
        raise OSError('falsche Antwort')
    pos = 12
    for _ in range(qd):
        _, pos = _name_lesen(daten, pos)
        pos += 4
    eintraege = []
    for _ in range(an + ns):
        _, pos = _name_lesen(daten, pos)
        rtyp, _klasse, _ttl, laenge = struct.unpack('>HHIH', daten[pos:pos + 10])
        pos += 10
        if rtyp == TYP_A and laenge == 4:
            eintraege.append((TYP_A, socket.inet_ntoa(daten[pos:pos + 4])))
        elif rtyp in (TYP_NS, TYP_CNAME):
            eintraege.append((rtyp, _name_lesen(daten, pos)[0]))
        pos += laenge
    return flaggen & 0xF, eintraege


def _oeffentlich(name, typ):
    for server in OEFFENTLICH:
        try:
            return [w for t, w in frage(server, name, typ)[1] if t == typ]
        except OSError:
            continue
    return []


def nameserver(name):
    """Adressen der zuständigen Nameserver: von name aus aufwärts die erste Zone mit NS."""
    teile = name.rstrip('.').split('.')
    for i in range(len(teile) - 1):
        zone = '.'.join(teile[i:])
        ns = _oeffentlich(zone, TYP_NS)
        if ns:
            adressen = []
            for n in ns[:4]:
                adressen += _oeffentlich(n, TYP_A)[:1]
            return adressen
    return []


def a_eintrag(name, tiefe=0):
    """Erste IPv4-Adresse laut zuständigem Nameserver; '' wenn es keinen Eintrag gibt."""
    try:
        for server in nameserver(name):
            try:
                rcode, eintraege = frage(server, name, TYP_A, rekursiv=False)
            except OSError:
                continue
            if rcode == 3:                     # NXDOMAIN: gibt es (noch) nicht
                return ''
            a = [w for t, w in eintraege if t == TYP_A]
            ziel = [w for t, w in eintraege if t == TYP_CNAME]
            if rcode == 0 and not a and ziel and tiefe < 5:
                return a_eintrag(ziel[0], tiefe + 1)      # CNAME: dem Ziel folgen
            if rcode == 0:
                return a[0] if a else ''
    except Exception:
        pass
    # Rückfall: der Resolver des Systems
    try:
        aus = subprocess.run(['getent', 'ahostsv4', name], capture_output=True, text=True, timeout=10).stdout
        return aus.split()[0] if aus.split() else ''
    except Exception:
        return ''


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        raise SystemExit(2)
    ip = a_eintrag(sys.argv[1])
    if ip:
        print(ip)
    raise SystemExit(0 if ip else 1)
