#!/usr/bin/env python3
"""HTTPS im LAN (V12.21.0): Firmen-CA, Serverzertifikat, RSA-Schlüssel, X.509/DER-Kodierung, SSL-Kontext.

#73 Phase 1: aus server.py herausgelöst (flaches Modul neben server.py, Teil von $MP_AppFiles). Nur Standardbibliothek.
server.py importiert alle Namen von hier; die Pfade und Laufzeiten (TLS_DIR, TLS_CERT ..., TLS_SERVER_DAYS) leben NUR hier.
server.X = ... wird von server.py auf dieses Modul umgeleitet (Tests bleiben gültig).
tools/lizenz_werkzeug.py nutzt rsa_generate/_rsa_private_pem/_rsa_private_load/_write_private von hier.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mp_config


# Namen dieses PCs (Serverzertifikat und Schutz gegen DNS-Rebinding in server.host_name_allowed).
def _local_host_names() -> set[str]:
    names = set()
    for fn in (socket.gethostname, socket.getfqdn):
        try:
            n = str(fn() or "").strip().lower().rstrip(".")
        except OSError:
            n = ""
        if n:
            names.add(n)
            names.add(n.split(".", 1)[0])
    return names


LOCAL_HOST_NAMES = _local_host_names()


# ---------------------------------------------------------------------------------------------
# V12.21.0: HTTPS im LAN. Ohne Fremdpakete: eigene Firmen-CA und Serverzertifikat (RSA 2048, SHA-256)
# werden einmalig mit "server.py --tls-einrichten --host <LAN-IP>" in config/tls/ erzeugt.
# Liegen server.crt und server.key dort, läuft der Server nur noch per HTTPS (Cookie mit Secure).
# Die CA (firmen-ca.crt) wird auf den Arbeitsplätzen als vertrauenswürdige Stammzertifizierungsstelle
# importiert; ihr Schlüssel bleibt auf dem Server und erlaubt spätere Erneuerung ohne neuen Import.
# ---------------------------------------------------------------------------------------------
TLS_DIR = mp_config.CONFIG_DIR / "tls"
TLS_CERT, TLS_KEY = TLS_DIR / "server.crt", TLS_DIR / "server.key"
TLS_CA_CERT, TLS_CA_KEY = TLS_DIR / "firmen-ca.crt", TLS_DIR / "firmen-ca.key"
TLS_SERVER_DAYS, TLS_CA_DAYS = 825, 3650


def _is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(40):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _rsa_prime(bits: int, e: int) -> int:
    while True:
        c = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if c % e != 1 and _is_probable_prime(c):
            return c


def rsa_generate(bits: int = 2048) -> dict:
    e = 65537
    while True:
        p, q = _rsa_prime(bits // 2, e), _rsa_prime(bits // 2, e)
        n = p * q
        if p != q and n.bit_length() == bits:
            break
    d = pow(e, -1, (p - 1) * (q - 1))
    return {"n": n, "e": e, "d": d, "p": p, "q": q}


def _der(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        size = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        size = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + size + body


def _der_int(v: int) -> bytes:
    raw = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big")
    return _der(0x02, raw)


def _der_seq(*items: bytes) -> bytes:
    return _der(0x30, b"".join(items))


def _der_oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    out = bytes([parts[0] * 40 + parts[1]])
    for v in parts[2:]:
        chunk = [v & 0x7F]
        v >>= 7
        while v:
            chunk.append(0x80 | (v & 0x7F))
            v >>= 7
        out += bytes(reversed(chunk))
    return _der(0x06, out)


def _der_time(t: datetime) -> bytes:
    t = t.astimezone(timezone.utc)
    if t.year < 2050:
        return _der(0x17, t.strftime("%y%m%d%H%M%SZ").encode())
    return _der(0x18, t.strftime("%Y%m%d%H%M%SZ").encode())


def _der_name(common_name: str, org: str) -> bytes:
    rdn = lambda oid, v: _der(0x31, _der_seq(_der_oid(oid), _der(0x0C, v.encode("utf-8"))))
    return _der_seq(rdn("2.5.4.10", org), rdn("2.5.4.3", common_name))


def _der_ext(oid: str, value: bytes, critical: bool = False) -> bytes:
    return _der_seq(_der_oid(oid), *([_der(0x01, b"\xff")] if critical else []), _der(0x04, value))


def _rsa_public_info(key: dict) -> bytes:
    pub = _der_seq(_der_int(key["n"]), _der_int(key["e"]))
    return _der_seq(_der_seq(_der_oid("1.2.840.113549.1.1.1"), _der(0x05, b"")), _der(0x03, b"\x00" + pub))


def _key_id(key: dict) -> bytes:
    return hashlib.sha1(_der_seq(_der_int(key["n"]), _der_int(key["e"]))).digest()


def _rsa_sign_sha256(key: dict, data: bytes) -> bytes:
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(data).digest()
    k = (key["n"].bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


def x509_certificate(subject_key: dict, issuer_key: dict, subject_cn: str, issuer_cn: str, org: str,
                     days: int, ca: bool, dns_names: list[str] = (), ips: list[str] = ()) -> bytes:
    """Ein X.509-v3-Zertifikat (DER), signiert mit issuer_key (sha256WithRSAEncryption)."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sig_alg = _der_seq(_der_oid("1.2.840.113549.1.1.11"), _der(0x05, b""))
    exts = [_der_ext("2.5.29.19", _der_seq(_der(0x01, b"\xff")) if ca else _der_seq(), True),
            _der_ext("2.5.29.15", _der(0x03, b"\x01\x06") if ca else _der(0x03, b"\x05\xa0"), True),
            _der_ext("2.5.29.14", _der(0x04, _key_id(subject_key))),
            _der_ext("2.5.29.35", _der_seq(_der(0x80, _key_id(issuer_key))))]
    if not ca:
        exts.append(_der_ext("2.5.29.37", _der_seq(_der_oid("1.3.6.1.5.5.7.3.1"))))
        names = [_der(0x82, n.encode("ascii")) for n in dns_names] + [_der(0x87, ipaddress.ip_address(i).packed) for i in ips]
        exts.append(_der_ext("2.5.29.17", _der_seq(*names)))
    tbs = _der_seq(
        _der(0xA0, _der_int(2)), _der_int(secrets.randbits(120) | (1 << 120)), sig_alg,
        _der_name(issuer_cn, org), _der_seq(_der_time(now - timedelta(hours=1)), _der_time(now + timedelta(days=days))),
        _der_name(subject_cn, org), _rsa_public_info(subject_key), _der(0xA3, _der_seq(*exts)))
    return _der_seq(tbs, sig_alg, _der(0x03, b"\x00" + _rsa_sign_sha256(issuer_key, tbs)))


def _pem(label: str, der: bytes) -> str:
    b64 = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) + f"\n-----END {label}-----\n"


def _rsa_private_pem(key: dict) -> str:
    n, e, d, p, q = key["n"], key["e"], key["d"], key["p"], key["q"]
    der = _der_seq(*(_der_int(v) for v in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    return _pem("RSA PRIVATE KEY", der)


def _rsa_private_load(path: Path) -> dict:
    """Liest den mit _rsa_private_pem geschriebenen PKCS#1-Schlüssel (nur die eigene CA)."""
    der = base64.b64decode("".join(l for l in path.read_text(encoding="ascii").splitlines() if not l.startswith("-----")))
    def read(pos):
        tag, size = der[pos], der[pos + 1]
        pos += 2
        if size & 0x80:
            cnt = size & 0x7F
            size, pos = int.from_bytes(der[pos:pos + cnt], "big"), pos + cnt
        return tag, der[pos:pos + size], pos + size
    tag, body, _ = read(0)
    if tag != 0x30:
        raise ValueError("CA-Schlüssel ist ungültig.")
    der, pos, vals = body, 0, []
    while pos < len(der):
        tag, value, pos = read(pos)
        vals.append(int.from_bytes(value, "big"))
    _ver, n, e, d, p, q = vals[:6]
    return {"n": n, "e": e, "d": d, "p": p, "q": q}


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def tls_setup(host_ip: str, extra_names: list[str] = ()) -> dict:
    """Firmen-CA (falls fehlend) und neues Serverzertifikat für LAN-IP und PC-Namen anlegen."""
    ip = str(ipaddress.ip_address(host_ip))
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    org = "Produktionsplanung"
    try:
        org = str(mp_config.load_config()[0]["company"].get("name") or org)[:60] or org
    except Exception:
        pass
    if TLS_CA_CERT.exists() and TLS_CA_KEY.exists():
        ca_key, created_ca = _rsa_private_load(TLS_CA_KEY), False
    else:
        ca_key, created_ca = rsa_generate(), True
        _write_private(TLS_CA_KEY, _rsa_private_pem(ca_key))
        TLS_CA_CERT.write_text(_pem("CERTIFICATE", x509_certificate(ca_key, ca_key, "Produktionsplanung Firmen-CA", "Produktionsplanung Firmen-CA", org, TLS_CA_DAYS, True)), encoding="ascii")
    names = sorted({n for n in [*LOCAL_HOST_NAMES, *(x.strip().lower() for x in extra_names)] if n and re.fullmatch(r"[a-z0-9.-]{1,253}", n) and not re.fullmatch(r"[0-9.]+", n)})
    key = rsa_generate()
    cn = next((n for n in names if n != "localhost"), ip)
    cert = x509_certificate(key, ca_key, cn, "Produktionsplanung Firmen-CA", org, TLS_SERVER_DAYS, False, names, [ip])
    _write_private(TLS_KEY, _rsa_private_pem(key))
    TLS_CERT.write_text(_pem("CERTIFICATE", cert), encoding="ascii")
    return {"ca": str(TLS_CA_CERT), "createdCa": created_ca, "names": names, "ip": ip, "days": TLS_SERVER_DAYS}


def tls_renew_reason(host_ip: str) -> str:
    """Grund für eine automatische Erneuerung des eigenen Serverzertifikats, sonst ''.

    Nur wenn die Firmen-CA (mit Schlüssel) vorliegt; ein selbst hinterlegtes Fremdzertifikat bleibt unangetastet.
    """
    if not (TLS_CERT.exists() and TLS_KEY.exists() and TLS_CA_CERT.exists() and TLS_CA_KEY.exists()):
        return ""
    import ssl
    try:
        info = ssl._ssl._test_decode_cert(str(TLS_CERT))
        ips = {v for k, v in info.get("subjectAltName", ()) if k == "IP Address"}
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(info["notAfter"]), timezone.utc)
    except Exception:
        return "Serverzertifikat nicht lesbar"
    if str(ipaddress.ip_address(host_ip)) not in ips:
        return f"LAN-IP {host_ip} fehlt im Zertifikat"
    if expires < datetime.now(timezone.utc) + timedelta(days=30):
        return f"Zertifikat läuft am {expires:%d.%m.%Y} ab"
    return ""


def tls_context():
    """SSL-Kontext, wenn config/tls/server.crt und server.key vorhanden sind; sonst None (HTTP)."""
    if not (TLS_CERT.exists() and TLS_KEY.exists()):
        return None
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(TLS_CERT), str(TLS_KEY))
    return ctx
