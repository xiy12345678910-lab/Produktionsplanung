"""Demo-Server für die Anleitungen: leere Datenbank im angegebenen Ordner, Admin admin/adminpass1.
Aufruf: python demo_server.py <ordner> [port]   -- nie mit dem Live-Datenordner verwenden."""
import ipaddress
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server  # noqa: E402

data = Path(sys.argv[1]).resolve()
if (data / "maschinenplanung.sqlite3").exists():
    raise SystemExit("Ordner enthält bereits eine Datenbank – bitte einen leeren Ordner angeben.")
server.DATA_DIR = data
server.DB_PATH = data / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db()
server.create_or_reset_admin("admin", "adminpass1")
httpd = server.MPHTTPServer(("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 18999), server.Handler)
print("ready", flush=True)
httpd.serve_forever()
