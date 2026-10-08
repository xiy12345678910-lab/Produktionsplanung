#!/usr/bin/env python3
"""#52 Lizenzschlüssel (V12.27.0). Nur Standardbibliothek.

Lizenzdatei config/lizenz.key: JSON {"payload": {...}, "signature": "<base64>"}.
payload: {"id", "licensee" (Firma), "tenantId" (= tenantId in firma.json), "issued" (JJJJ-MM-TT), "expires" (JJJJ-MM-TT oder null)}.
Signiert wird die kanonische JSON-Form des payload (sort_keys, separators=(',', ':'), ensure_ascii=False, UTF-8)
mit RSA PKCS#1 v1.5 / SHA-256. Hier steht nur der ÖFFENTLICHE Schlüssel des Lizenzgebers; der private Schlüssel
liegt ausschließlich beim Lizenzgeber (tools/lizenz_werkzeug.py, docs/LIZENZ.md), nie im Repository oder im Paket.

Ohne eingetragenen öffentlichen Schlüssel ist die Prüfung NICHT aktiv: nichts wird eingeschränkt, keine Kulanz gezählt.
Aktiv und ohne gültige Lizenz: 30 Tage Kulanz ab dem ersten Moment ohne gültige Lizenz, danach nur Lesen.
Kulanzbeginn und zuletzt gesehene Uhrzeit liegen in der Datenbank (Tabelle app_meta); eine zurückgestellte
Uhr zählt nicht (wirksame Zeit = max(jetzt, zuletzt gesehen)).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import time
from datetime import date, datetime, timezone

# Öffentlicher Schlüssel des Lizenzgebers: n hexadezimal, e. Leer = Lizenzprüfung nicht aktiv.
# Eintrag per PR mit der Ausgabe von "python tools/lizenz_werkzeug.py schluessel-erzeugen" (docs/LIZENZ.md).
PUBLIC_KEY = {"n": "", "e": 65537}

GRACE_DAYS = 30
WARN_DAYS = 30            # Hinweis an den Admin, wenn eine befristete Lizenz in <= 30 Tagen abläuft
MAX_FILE = 16 * 1024
MIN_BITS = 2048
SEEN_STEP = 60            # "zuletzt gesehen" höchstens einmal je Minute schreiben
clock = time.time         # Tests ersetzen nur dieses Modulattribut; im Betrieb immer time.time

STATUS_TEXT = {"valid": "gültig", "missing": "fehlt", "invalid": "ungültig", "wrong_tenant": "falsche Firma",
               "expired": "abgelaufen", "inactive": "nicht aktiv"}
META_GRACE, META_SEEN = "license.graceStart", "license.lastSeen"
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")   # DER-Präfix SHA-256 (RFC 8017 9.2)
_TENANT = re.compile(r"^[a-z0-9-]{3,32}$")
_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAYLOAD_KEYS = ("id", "licensee", "tenantId", "issued", "expires")


class LicenseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def public_key() -> tuple[int, int] | None:
    """(n, e) des eingetragenen Schlüssels, None = Prüfung nicht aktiv. Ein unlesbarer Eintrag gilt als aktiv
    mit n=0: dann ist jede Lizenz ungültig (fällt in Tests auf, nie stilles Abschalten)."""
    raw = str((PUBLIC_KEY or {}).get("n") or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16), int(PUBLIC_KEY.get("e") or 65537)
    except (TypeError, ValueError):
        return 0, 65537


def active() -> bool:
    return public_key() is not None


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _emsa(data: bytes, k: int) -> bytes:
    """EMSA-PKCS1-v1_5 mit SHA-256 (RFC 8017 9.2), Länge k Bytes."""
    t = _DIGEST_INFO + hashlib.sha256(data).digest()
    if k < len(t) + 11:
        raise ValueError("Schlüssel zu kurz")
    return b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def verify(data: bytes, signature: bytes, n: int, e: int) -> bool:
    """RSASSA-PKCS1-v1_5-Prüfung: s^e mod n wird vollständig mit der selbst erzeugten Kodierung verglichen
    (kein Zerlegen der entschlüsselten Bytes, daher keine Fälschung über lockeres Parsen)."""
    if not isinstance(n, int) or n.bit_length() < MIN_BITS or e < 3 or e % 2 == 0:
        return False
    k = (n.bit_length() + 7) // 8
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    return hmac.compare_digest(pow(s, e, n).to_bytes(k, "big"), _emsa(data, k))


def sign(data: bytes, key: dict) -> bytes:
    """Nur für das Werkzeug des Lizenzgebers und Tests (braucht den privaten Schlüssel, der nie ausgeliefert wird)."""
    n, d = key["n"], key["d"]
    k = (n.bit_length() + 7) // 8
    return pow(int.from_bytes(_emsa(data, k), "big"), d, n).to_bytes(k, "big")


def make_license(payload: dict, key: dict) -> bytes:
    """Lizenzdatei (UTF-8-JSON) zu payload, signiert mit dem privaten Schlüssel."""
    _check_payload(payload)
    sig = base64.b64encode(sign(canonical(payload), key)).decode("ascii")
    return (json.dumps({"payload": payload, "signature": sig}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _valid_date(v) -> bool:
    if not isinstance(v, str) or not _DATE.match(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _check_payload(p) -> dict:
    if not isinstance(p, dict) or any(k not in p for k in PAYLOAD_KEYS):
        raise LicenseError("MP-LIC-003", "Lizenzdaten unvollständig (id, licensee, tenantId, issued, expires).")
    if not isinstance(p["id"], str) or not _ID.match(p["id"]):
        raise LicenseError("MP-LIC-003", "Lizenz-ID ungültig.")
    if not isinstance(p["licensee"], str) or not p["licensee"].strip() or len(p["licensee"]) > 200:
        raise LicenseError("MP-LIC-003", "Lizenznehmer ungültig.")
    if not isinstance(p["tenantId"], str) or not _TENANT.match(p["tenantId"]):
        raise LicenseError("MP-LIC-003", "Mandanten-ID ungültig.")
    if not _valid_date(p["issued"]):
        raise LicenseError("MP-LIC-003", "Ausstellungsdatum ungültig (JJJJ-MM-TT).")
    if p["expires"] is not None and (not _valid_date(p["expires"]) or p["expires"] < p["issued"]):
        raise LicenseError("MP-LIC-003", "Ablaufdatum ungültig (JJJJ-MM-TT oder unbefristet).")
    return p


def parse(raw: bytes) -> tuple[dict, bytes]:
    """Lizenzdatei lesen und Form prüfen (noch keine Signaturprüfung). LicenseError MP-LIC-003 bei Formfehlern."""
    if not isinstance(raw, (bytes, bytearray)) or not raw.strip():
        raise LicenseError("MP-LIC-003", "Lizenzdatei ist leer.")
    if len(raw) > MAX_FILE:
        raise LicenseError("MP-LIC-003", f"Lizenzdatei ist zu groß (max. {MAX_FILE // 1024} KB).")
    try:
        obj = json.loads(bytes(raw).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise LicenseError("MP-LIC-003", "Lizenzdatei ist kein gültiges JSON.") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("payload"), dict) or not isinstance(obj.get("signature"), str):
        raise LicenseError("MP-LIC-003", "Lizenzdatei: payload und signature erwartet.")
    payload = _check_payload(obj["payload"])
    try:
        sig = base64.b64decode(obj["signature"].strip(), validate=True)
    except (binascii.Error, ValueError):
        raise LicenseError("MP-LIC-003", "Signatur ist kein Base64.") from None
    return payload, sig


def check(raw: bytes | None, tenant: str, today: date, key: tuple[int, int] | None) -> dict:
    """Reine Prüfung ohne Datenbank. status: inactive | missing | invalid | wrong_tenant | expired | valid."""
    if key is None:
        return {"active": False, "status": "inactive"}
    if raw is None:
        return {"active": True, "status": "missing"}
    try:
        payload, sig = parse(raw)
    except LicenseError as e:
        return {"active": True, "status": "invalid", "error": e.message}
    if not verify(canonical(payload), sig, *key):
        return {"active": True, "status": "invalid", "error": "Signatur ungültig."}
    out = {"active": True, "id": payload["id"], "licensee": payload["licensee"], "tenantId": payload["tenantId"],
           "issued": payload["issued"], "expires": payload["expires"]}
    if payload["tenantId"] != tenant:
        out.update(status="wrong_tenant", error=f"Lizenz gilt für Mandant „{payload['tenantId']}“, diese Installation ist „{tenant}“.")
    elif payload["expires"] is not None and payload["expires"] < today.isoformat():
        out.update(status="expired", error=f"Lizenz ist am {payload['expires']} abgelaufen.")
    else:
        out["status"] = "valid"
        if payload["expires"] is not None:
            out["expiresInDays"] = (date.fromisoformat(payload["expires"]) - today).days
    return out


# ------------------------------- Kulanz (Datenbank) -------------------------------
def init_schema(con) -> None:
    """Additiv: kleine Schlüssel/Wert-Tabelle für Serverwerte, die kein Client schreiben kann."""
    con.execute("CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def _meta_get(con, key: str) -> float | None:
    row = con.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
    try:
        return float(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _meta_set(con, key: str, value: float) -> None:
    con.execute("INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(int(value))))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def effective_now(con, now: float | None = None) -> float:
    """max(jetzt, zuletzt gesehen); schreibt "zuletzt gesehen" nur vorwärts."""
    now = clock() if now is None else now
    seen = _meta_get(con, META_SEEN)
    if seen is None or now >= seen + SEEN_STEP:
        _meta_set(con, META_SEEN, now)
    return max(now, seen or 0)


def evaluate(con, raw: bytes | None, tenant: str, now: float | None = None) -> dict:
    """Status inkl. Kulanz. Inaktiv: Datenbank bleibt unberührt. readOnly erst nach Ablauf der Kulanz."""
    key = public_key()
    if key is None:
        return {"active": False, "status": "inactive", "statusText": STATUS_TEXT["inactive"], "readOnly": False}
    eff = effective_now(con, now)
    out = check(raw, tenant, datetime.fromtimestamp(eff).date(), key)
    start = _meta_get(con, META_GRACE)
    if out["status"] == "valid":
        if start is not None:   # gültige Lizenz beendet die Kulanz; ein späteres Ablaufen startet sie neu
            con.execute("DELETE FROM app_meta WHERE key=?", (META_GRACE,))
        out["readOnly"] = False
    else:
        if start is None:
            start = eff
            _meta_set(con, META_GRACE, start)
        start = min(start, eff)
        left = GRACE_DAYS * 86400 - (eff - start)
        out.update(graceStart=_iso(start), graceEnds=_iso(start + GRACE_DAYS * 86400),
                   graceDaysLeft=max(0, math.ceil(left / 86400)), readOnly=left <= 0)
    out["statusText"] = STATUS_TEXT[out["status"]]
    return out
