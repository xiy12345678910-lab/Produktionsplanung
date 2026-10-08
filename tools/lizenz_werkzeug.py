#!/usr/bin/env python3
"""#52 Lizenzwerkzeug für den Lizenzgeber (Jonas). Wird NICHT ausgeliefert (tools/ steht nicht in $MP_AppFiles).

  schluessel-erzeugen --ordner DIR      RSA-3072-Schlüsselpaar erzeugen; privater Schlüssel nach DIR (nie ins Repository),
                                        öffentlicher Schlüssel als Zeile zum Eintragen in mp_license.py
  erstellen --privat KEY.pem --firma "Name" --mandant TENANT [--bis JJJJ-MM-TT] [--id ID] [--ausgestellt JJJJ-MM-TT] --datei lizenz.key
  pruefen --datei lizenz.key [--oeffentlich KEY.pem|HEX] [--mandant TENANT]

Siehe docs/LIZENZ.md. Nur Standardbibliothek.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mp_license  # noqa: E402

KEY_BITS = 3072   # reines Python: Erzeugung dauert einige Sekunden bis ca. 1 Minute (einmalig)
PRIVATE_NAME = "lizenz_privat.pem"


def _server():
    import server   # rsa_generate/_rsa_private_pem/_rsa_private_load (bereits für HTTPS vorhanden)
    return server


def inside_repo(path: Path) -> bool:
    """True, wenn path im Git-Repository liegt (dieses Repository oder ein anderes) – dort nie private Schlüssel ablegen."""
    path = path.resolve()
    try:
        path.relative_to(ROOT)
        return True
    except ValueError:
        pass
    probe = path if path.is_dir() else path.parent
    while not probe.exists():
        probe = probe.parent
    try:
        r = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def cmd_keygen(a) -> int:
    folder = Path(a.ordner).expanduser()
    if inside_repo(folder):
        print(f"FEHLER: {folder} liegt in einem Git-Repository. Der private Schlüssel gehört nur auf den eigenen PC (z. B. USB-Stick/Tresor), nie ins Repository.", file=sys.stderr)
        return 2
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / PRIVATE_NAME
    if target.exists():
        print(f"FEHLER: {target} existiert bereits – vorhandenen Schlüssel nicht überschreiben (alle Lizenzen hängen daran).", file=sys.stderr)
        return 2
    srv = _server()
    print(f"Erzeuge RSA-{a.bits}-Schlüsselpaar … (kann etwas dauern)", flush=True)
    key = srv.rsa_generate(a.bits)
    srv._write_private(target, srv._rsa_private_pem(key))
    print(f"Privater Schlüssel: {target}")
    print("  -> sicher aufbewahren (zweite Kopie offline), NIE weitergeben, NIE ins Repository oder in eine Cloud.")
    print("\nÖffentlichen Schlüssel per PR in mp_license.py eintragen (Zeile PUBLIC_KEY ersetzen):\n")
    print(f'PUBLIC_KEY = {{"n": "{key["n"]:x}", "e": {key["e"]}}}')
    return 0


def _load_private(path: str) -> dict:
    p = Path(path).expanduser()
    if inside_repo(p):
        print(f"WARNUNG: {p} liegt in einem Git-Repository – privaten Schlüssel dort entfernen!", file=sys.stderr)
    return _server()._rsa_private_load(p)


def cmd_create(a) -> int:
    key = _load_private(a.privat)
    payload = {"id": a.id or f"L-{date.today():%Y%m%d}-{secrets.token_hex(3)}", "licensee": a.firma.strip(), "tenantId": a.mandant,
               "issued": a.ausgestellt or date.today().isoformat(), "expires": a.bis or None}
    try:
        raw = mp_license.make_license(payload, key)
    except mp_license.LicenseError as e:
        print(f"FEHLER: {e.message}", file=sys.stderr)
        return 2
    payload2, sig = mp_license.parse(raw)
    if not mp_license.verify(mp_license.canonical(payload2), sig, key["n"], key["e"]):
        print("FEHLER: Selbstprüfung der Signatur fehlgeschlagen.", file=sys.stderr)
        return 2
    out = Path(a.datei)
    if out.exists() and not a.ueberschreiben:
        print(f"FEHLER: {out} existiert bereits (--ueberschreiben).", file=sys.stderr)
        return 2
    out.write_bytes(raw)
    print(f"Lizenzdatei geschrieben: {out}")
    print(f"  Lizenznehmer {payload['licensee']} · Mandant {payload['tenantId']} · ID {payload['id']} · gültig {'unbefristet' if payload['expires'] is None else 'bis ' + payload['expires']}")
    return 0


def _public_from(arg: str | None) -> tuple[int, int] | None:
    if not arg:
        return mp_license.public_key()
    p = Path(arg).expanduser()
    if p.is_file():
        k = _server()._rsa_private_load(p)
        return k["n"], k["e"]
    return int(arg.strip().removeprefix("0x"), 16), 65537


def cmd_check(a) -> int:
    key = _public_from(a.oeffentlich)
    if key is None:
        print("FEHLER: Kein öffentlicher Schlüssel (mp_license.PUBLIC_KEY leer) – --oeffentlich angeben.", file=sys.stderr)
        return 2
    raw = Path(a.datei).read_bytes()
    try:
        payload, _ = mp_license.parse(raw)
    except mp_license.LicenseError as e:
        print(f"UNGÜLTIG: {e.message}")
        return 1
    res = mp_license.check(raw, a.mandant or payload["tenantId"], date.today(), key)
    print(json.dumps({k: v for k, v in res.items() if k != "active"}, ensure_ascii=False, indent=2))
    return 0 if res["status"] == "valid" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lizenzwerkzeug (nur für den Lizenzgeber)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("schluessel-erzeugen", help="Schlüsselpaar erzeugen (einmalig)")
    k.add_argument("--ordner", required=True, help="Ordner AUSSERHALB jedes Git-Repositorys")
    k.add_argument("--bits", type=int, default=KEY_BITS, choices=(2048, 3072, 4096))
    c = sub.add_parser("erstellen", help="Lizenzdatei für einen Kunden erstellen")
    c.add_argument("--privat", required=True)
    c.add_argument("--firma", required=True)
    c.add_argument("--mandant", required=True, help="tenantId aus config\\firma.json des Kunden")
    c.add_argument("--bis", help="Ablaufdatum JJJJ-MM-TT; ohne = unbefristet")
    c.add_argument("--id")
    c.add_argument("--ausgestellt", help="Ausstellungsdatum JJJJ-MM-TT (Standard heute)")
    c.add_argument("--datei", required=True)
    c.add_argument("--ueberschreiben", action="store_true")
    p = sub.add_parser("pruefen", help="Lizenzdatei prüfen")
    p.add_argument("--datei", required=True)
    p.add_argument("--oeffentlich", help="privater Schlüssel (.pem) oder n als Hex; Standard: mp_license.PUBLIC_KEY")
    p.add_argument("--mandant", help="erwartete tenantId (Standard: die aus der Datei)")
    a = ap.parse_args(argv)
    return {"schluessel-erzeugen": cmd_keygen, "erstellen": cmd_create, "pruefen": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
