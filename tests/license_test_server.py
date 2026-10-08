#!/usr/bin/env python3
"""Nur für tests/e2e_license.mjs (tests/ wird nicht ausgeliefert): startet den Server mit einem zur Laufzeit erzeugten
Test-Schlüsselpaar (mp_license.PUBLIC_KEY wird hier im Prozess ersetzt) und einer verstellbaren Uhr.

Aufruf: license_test_server.py <ordner> <port> <passwort>
  <ordner>/valid.key, wrong_tenant.key, bad_signature.key  Lizenzdateien zum Hochladen
  <ordner>/clock_offset                                      Sekunden, um die die Uhr vorgeht (Datei vom Test beschreibbar)
"""
import ipaddress
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
work, port, password = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
os.environ['MP_CONFIG_DIR'] = str(work / 'config')
import server  # noqa: E402
import mp_license  # noqa: E402

server.DATA_DIR = work / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK = ipaddress.ip_network('127.0.0.0/8')
key, other = server.rsa_generate(2048), server.rsa_generate(2048)
mp_license.PUBLIC_KEY = {'n': f"{key['n']:x}", 'e': key['e']}
offset_file = work / 'clock_offset'
offset_file.write_text('0')


def clock():
    try:
        return time.time() + float(offset_file.read_text() or 0)
    except (OSError, ValueError):
        return time.time()


mp_license.clock = clock
server.init_db(seed='werbetechnik')
tenant = server.current_config()['tenantId']
server.create_or_reset_admin('admin', password)
payload = {'id': 'L-E2E-1', 'licensee': 'E2E Test GmbH', 'tenantId': tenant, 'issued': '2026-01-01', 'expires': None}
(work / 'valid.key').write_bytes(mp_license.make_license(payload, key))
(work / 'wrong_tenant.key').write_bytes(mp_license.make_license({**payload, 'tenantId': 'andere-firma'}, key))
(work / 'bad_signature.key').write_bytes(mp_license.make_license(payload, other))
httpd = server.MPHTTPServer(('127.0.0.1', port), server.Handler)
print('READY', flush=True)
httpd.serve_forever()
