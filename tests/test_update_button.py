#!/usr/bin/env python3
"""#84: Admin-Button "Update installieren" mit echtem server.py-Prozess und Testquelle (alle Plattformen).

Was hier laeuft (ohne Windows): echter Server mit kuenstlich niedrigerer Version, gestartet mit
MP_UPDATE_TEST_API; Release-Paket des aktuellen Stands mit tools/build_release.py; GitHub-Nachbildung aus
tests/update_button_lib.py; Admin-Login, GET /api/updates findet die Version, POST /api/updates/install.
Den Hintergrundprozess startet der Server nur unter Windows (sonst 409 mit Hinweis); hier laeuft run_job daher
im Testprozess gegen dieselbe Quelle, mit einem Installer, der wie UPDATE_LIVE.ps1 die Dateien uebernimmt und den
Server neu startet. Danach dieselben Pruefungen wie in der Windows-CI (tests/ci_windows_update_button.ps1).
Ausserdem: Die Testquelle ist nur in der engen Loopback-Form zulaessig und bekommt nie den GitHub-Token.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import app_updates as updates  # noqa: E402
import deploy_lib as L  # noqa: E402
import update_button_lib as B  # noqa: E402

PW = "Admin-Knopf-Test-1"


def app_files() -> list[str]:
    return L.package_lists((ROOT / "MP_Common.ps1").read_text(encoding="utf-8-sig"))[0]


def copy_app(dst: Path) -> None:
    for name in app_files():
        target = dst.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT.joinpath(*name.split("/")), target)


def clean_env() -> dict:
    """Wie tests/test_deploy_upgrade.py: keine MP_-/PYTHON-Variablen des Aufrufers an den Server weitergeben."""
    return {k: v for k, v in os.environ.items() if not k.startswith("MP_") and not k.startswith("PYTHON")}


def head_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        if len(sha) == 40:
            return sha
    except (OSError, subprocess.CalledProcessError):
        pass
    return "c" * 40


class TestSourceRules(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in (updates.TEST_API_ENV, "MP_UPDATE_GITHUB_TOKEN")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_is_github(self):
        os.environ.pop(updates.TEST_API_ENV, None)
        self.assertEqual(updates.api_root(), "https://api.github.com/repos/")
        with self.assertRaises(ValueError):
            updates.request_bytes("http://127.0.0.1:8080/repos/a/b/releases/latest")

    def test_only_loopback_form_is_accepted(self):
        for bad in ["https://evil.example/repos/", "http://10.0.0.5:80/repos/", "http://localhost:80/repos/", "http://127.0.0.1/repos/",
                    "http://127.0.0.1:80/repos", "http://127.0.0.1:80/repos/x/", "http://127.0.0.1:0/repos/", "http://127.0.0.1:80/repos/\n",
                    "http://127.0.0.2:80/repos/", "http://user@127.0.0.1:80/repos/", "https://api.github.com/repos/../"]:
            os.environ[updates.TEST_API_ENV] = bad
            with self.assertRaises(ValueError, msg=bad):
                updates.api_root()
            with self.assertRaises(ValueError, msg=bad):
                updates.release_info("owner/repo", "12.19.0")
        os.environ[updates.TEST_API_ENV] = "http://127.0.0.1:8123/repos/"
        self.assertEqual(updates.api_root(), "http://127.0.0.1:8123/repos/")
        # Auch mit Testquelle: nichts ausserhalb dieser Wurzel, insbesondere nicht GitHub.
        with self.assertRaises(ValueError):
            updates.request_bytes("https://api.github.com/repos/a/b/releases/latest")

    def test_browser_cannot_select_source(self):
        # Die Quelle ist nur Prozessumgebung; die Install-Route verweigert jeden Inhalt (unveraendert).
        text = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertNotIn(updates.TEST_API_ENV, text)
        self.assertIn('"Updatequelle und Paket werden ausschließlich vom Server gewählt."', text)


@unittest.skipIf(not L.find_lan_ip(), "keine LAN-IPv4-Adresse")
# Unter Windows wuerde der Button einen echten Worker samt UPDATE_LIVE.ps1 starten: dort nur per CI-Skript.
@unittest.skipIf(os.name == "nt", "Windows: tests/ci_windows_update_button.ps1")
class ButtonFlow(unittest.TestCase):
    """Ein durchgehender Ablauf; die Schritte bauen aufeinander auf."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-knopf-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ip = L.find_lan_ip()
        self.port = L.free_port(self.ip)
        self.url = f"http://{self.ip}:{self.port}"
        self.live = self.tmp / "live"
        self.live.mkdir()
        copy_app(self.live)
        self.proc = None
        self.saved_env = {k: os.environ.get(k) for k in (updates.TEST_API_ENV, "MP_UPDATE_GITHUB_TOKEN")}
        self.addCleanup(self.restore_env)
        self.addCleanup(self.stop_server)

    def restore_env(self):
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def env(self, api: str) -> dict:
        e = clean_env()
        e[updates.TEST_API_ENV] = api
        e["MP_UPDATE_GITHUB_TOKEN"] = "geheimer-test-token"   # darf nie an die Testquelle gehen
        return e

    def start_server(self, api: str, expect: str) -> None:
        log = open(self.tmp / "server.log", "ab")
        self.addCleanup(log.close)
        self.proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-I", str(self.live / "server.py"), "--host", self.ip, "--port", str(self.port),
                                      "--allowed-subnet", f"{self.ip}/32"], cwd=self.live, stdout=log, stderr=subprocess.STDOUT, env=self.env(api))
        for _ in range(160):
            if self.proc.poll() is not None:
                break
            try:
                if B.Client(self.url).health(timeout=2) == expect:
                    return
            except OSError:
                pass
            time.sleep(0.25)
        self.fail("Server startet nicht: " + (self.tmp / "server.log").read_text(errors="replace")[-3000:])

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def test_button_flow(self):
        check = B.Checks()
        new = self.read_version()          # aktueller Stand = Version des Testreleases
        low = B.lower_of(new)              # installiert wird derselbe Code mit kuenstlich niedrigerer Version
        self.assertEqual(B.lower_version(self.live, low), new)
        # 1. Admin anlegen wie Setup_Windows.ps1 (--init-admin)
        e = clean_env(); e["MP_ADMIN_PASSWORD"] = PW
        r = subprocess.run([sys.executable, "-I", str(self.live / "server.py"), "--init-admin", "admin"], cwd=self.live, env=e, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 2. Release-Paket des aktuellen Stands (tools/build_release.py) und Nachbildung der GitHub-API
        pkgsrc = self.tmp / "pkgsrc"; pkgsrc.mkdir(); copy_app(pkgsrc)
        release = self.tmp / "release"
        manifest = B.build_package(pkgsrc, release, head_commit())
        self.assertEqual(manifest["version"], new)
        mock = B.MockGitHub(release, self.tmp / "mock.log")
        mock.start(0)
        self.addCleanup(mock.stop)
        # 3. Server (alte Version) mit Testquelle starten, Aenderung anlegen, Button ausloesen
        self.start_server(mock.api, low)
        B.seed(self.url, PW, None, check)
        # Ohne Windows startet der Server keinen Worker: 409 "Installation benötigt Windows ...".
        B.trigger(self.url, PW, new, None, None, check, expect_install=409)
        self.assertEqual(check.failed, 0)
        # 4. Ohne Windows: Auftrag wie der Server anlegen und run_job mit Installer-Ersatz ausfuehren
        os.environ[updates.TEST_API_ENV] = mock.api
        os.environ["MP_UPDATE_GITHUB_TOKEN"] = "geheimer-test-token"
        m = updates.UpdateManager(self.live, low, lambda: {}, launch=lambda _id: None)
        m.check()
        self.assertEqual(m.error, "")
        job, started = m.start("admin")
        self.assertTrue(started)
        seen = {}

        def installer(package, status):
            # Wie UPDATE_LIVE.ps1 (Kurzform): stoppen, $MP_AppFiles uebernehmen, neu starten, Status 'complete'.
            seen["files"] = sorted(p.name for p in package.iterdir())
            self.assertTrue((package / "UPDATE_LIVE.ps1").is_file())
            self.stop_server()
            for name in app_files():
                shutil.copy2(package.joinpath(*name.split("/")), self.live.joinpath(*name.split("/")))
            self.start_server(mock.api, new)   # dieselbe Quelle: kein Netz, kein neueres Release
            j = updates.read_json(status)
            j["stage"] = "complete"
            updates.atomic_json(status, j)

        updates.run_job(self.live, job["jobId"], installer=installer)
        self.assertIn("server.py", seen.get("files", []))
        check2 = B.Checks()
        B.wait(self.url, PW, new, job["jobId"], 60, None, check2)
        B.verify(self.url, PW, new, job["jobId"], self.tmp / "mock.log", None, check2)
        self.assertEqual(check2.failed, 0)
        self.assertFalse((self.live / "updates" / "update.lock").exists())

    def read_version(self) -> str:
        return re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', (self.live / "server.py").read_text(encoding="utf-8"), re.M).group(1)


class LibUnits(unittest.TestCase):
    def test_lower_of(self):
        self.assertEqual(B.lower_of("12.28.0"), "12.27.99")
        self.assertEqual(B.lower_of("12.28.3"), "12.28.2")

    def test_lower_version_rejects_higher(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "server.py").write_text('APP_VERSION = "12.28.0"\n', encoding="utf-8")
            (d / "index.html").write_text("const CLIENT_VERSION='12.28.0';\r\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                B.lower_version(d, "12.28.1")
            B.lower_version(d, "12.27.99")
            self.assertIn('"12.27.99"', (d / "server.py").read_text(encoding="utf-8"))
            self.assertEqual((d / "index.html").read_bytes(), b"const CLIENT_VERSION='12.27.99';\r\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
