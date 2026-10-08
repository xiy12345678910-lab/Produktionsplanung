"""Generic release updates. Standard library; browser input never selects files or commands."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.parse import urlparse
import zipfile

REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
ACTIVE = {'checking', 'download', 'preflight', 'backup', 'installation', 'migration', 'restart', 'healthcheck', 'rollback'}
MAX_PACKAGE = 64 * 1024 * 1024
# Program files: a flat name or exactly one subfolder level, always with '/' (e.g. core/config.py).
MEMBER = re.compile(r'[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?')
RESERVED_DIRS = {'config', 'data', 'backups', 'update_backups', 'updates', '__pycache__'}
WINDOWS_RESERVED = re.compile(r'(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?')


def member_name(name):
    """Validate one package member name. Rejects '..', absolute paths, backslashes, drive letters, empty
    segments, hidden/dot segments, trailing dots, Windows device names and more than one folder level."""
    if not isinstance(name, str) or not MEMBER.fullmatch(name):
        raise ValueError('Ungültige Datei im Updatepaket.')
    for part in name.split('/'):
        if part.startswith('.') or part.endswith('.') or WINDOWS_RESERVED.fullmatch(part):
            raise ValueError('Ungültige Datei im Updatepaket.')
    if '/' in name and name.split('/')[0].lower() in RESERVED_DIRS:
        raise ValueError('Ungültige Datei im Updatepaket.')
    return name


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != 'https' or target.hostname not in {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}:
            raise ValueError('Unzulässige Weiterleitung der Updatequelle.')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if target.hostname != urlparse(req.full_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


def version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError('Ungültige Release-Version.')
    return tuple(map(int, value.split('.')))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp-'+str(os.getpid()))
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def request_bytes(url, limit=1024*1024):
    # Every URL is constructed from a validated repository or an exact release asset ID.
    if not url.startswith('https://api.github.com/repos/'):
        raise ValueError('Unzulässige Updatequelle.')
    headers = {'User-Agent': 'Produktionsplanung-Update', 'Accept': 'application/vnd.github+json'}
    if '/releases/assets/' in url:
        headers['Accept'] = 'application/octet-stream'
    token = os.environ.get('MP_UPDATE_GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    with build_opener(ReleaseRedirect()).open(Request(url, headers=headers), timeout=30) as response:
        data = response.read(limit+1)
    if len(data) > limit:
        raise ValueError('Updatepaket überschreitet die Größenbegrenzung.')
    return data


def release_info(repo, current, fetch=request_bytes):
    if not REPO.fullmatch(repo):
        raise ValueError('Updatequelle muss GitHub konto/repository sein.')
    api = 'https://api.github.com/repos/'+repo+'/'
    release = json.loads(fetch(api+'releases/latest'))
    tag = release.get('tag_name', '')
    latest = tag.removeprefix('v')
    if release.get('draft') or release.get('prerelease') or version(latest) <= version(current):
        return None
    commit = json.loads(fetch(api+'commits/'+tag))['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Release-Commit ist ungültig.')
    compare = json.loads(fetch(api+'compare/main...'+commit))
    if compare.get('status') not in {'identical', 'behind'}:
        raise ValueError('Release liegt außerhalb von main.')
    assets = {a.get('name'): a for a in release.get('assets') or []}
    manifest_asset = assets.get('update-manifest.json') or {}
    package = assets.get('produktionsplanung-v'+latest+'.zip') or {}
    if not isinstance(manifest_asset.get('id'), int) or not isinstance(package.get('id'), int):
        raise ValueError('Release enthält kein geprüftes Updatepaket.')
    manifest = json.loads(fetch(api+'releases/assets/'+str(manifest_asset['id'])))
    validate_manifest(manifest, latest, current)
    if manifest.get('commit') != commit:
        raise ValueError('Paket und Release-Commit stimmen nicht überein.')
    # Reserved for signed distribution. An unsupported signature is never silently ignored.
    if manifest.get('signature'):
        raise ValueError('Signierte Distribution benötigt einen konfigurierten Signaturprüfer.')
    return {'version': latest, 'currentVersion': current, 'notes': str(release.get('body') or '')[:20000], 'manifest': manifest, 'packageUrl': api+'releases/assets/'+str(package['id']), 'repository': repo, 'commit': commit}


def validate_manifest(manifest, expected, current):
    if not isinstance(manifest, dict) or manifest.get('schema') != 1 or manifest.get('version') != expected:
        raise ValueError('Paketversion/Manifest ist ungültig.')
    if version(current) < version(manifest.get('minVersion', '12.19.0')) or version(expected) <= version(current):
        raise ValueError('Update ist mit der installierten Version nicht kompatibel.')
    if not re.fullmatch('[0-9a-f]{64}', str(manifest.get('sha256') or '')):
        raise ValueError('SHA256 des Updatepakets fehlt.')
    files = manifest.get('files')
    if not isinstance(files, dict) or not {'server.py', 'index.html', 'release_gates.py', 'MP_Common.ps1', 'UPDATE_LIVE.ps1', 'app_updates.py'} <= set(files):
        raise ValueError('Updatepaket ist unvollständig.')
    for name, digest in files.items():
        member_name(name)
        if not re.fullmatch('[0-9a-f]{64}', str(digest)):
            raise ValueError('Ungültige Datei im Updatepaket.')
    # Windows paths are case-insensitive; a file must never share its name with a folder (core vs. core/x.py).
    folded = [n.lower() for n in files]
    folders = {n.split('/')[0] for n in folded if '/' in n}
    if len(set(folded)) != len(folded) or folders & set(folded):
        raise ValueError('Ungültige Datei im Updatepaket.')
    if manifest.get('signature'):
        raise ValueError('Signaturprüfung ist für diese Distribution noch nicht konfiguriert.')


def unpack_package(blob, manifest, target):
    if len(blob) > MAX_PACKAGE or hashlib.sha256(blob).hexdigest() != manifest['sha256']:
        raise ValueError('Updatepaket ist beschädigt (SHA256).')
    target.mkdir(parents=True, exist_ok=False)
    root = target.resolve()
    archive = target.parent / 'package.zip'
    archive.write_bytes(blob)
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if len(set(names)) != len(names) or set(names) != set(manifest['files']) or sum(x.file_size for x in z.infolist()) > MAX_PACKAGE:
                raise ValueError('Updatepaket enthält unerwartete Dateien.')
            for entry in z.infolist():
                member_name(entry.filename)
                if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Links im Updatepaket sind unzulässig.')
                content = z.read(entry)
                if hashlib.sha256(content).hexdigest() != manifest['files'][entry.filename]:
                    raise ValueError('Dateiprüfsumme stimmt nicht: '+entry.filename)
                dest = target.joinpath(*entry.filename.split('/'))
                # Zip-slip guard: the resolved destination must stay inside the fresh staging folder.
                if dest.parent != target:
                    dest.parent.mkdir(exist_ok=True)
                if dest.resolve().parent not in (root, root/entry.filename.split('/')[0]):
                    raise ValueError('Ungültige Datei im Updatepaket.')
                with open(dest, 'xb') as out:
                    out.write(content)
        py = (target/'server.py').read_text(encoding='utf-8-sig')
        html = (target/'index.html').read_text(encoding='utf-8-sig')
        if not re.search(r'^APP_VERSION\s*=\s*"'+re.escape(manifest['version'])+'"', py, re.M) or not re.search(r"CLIENT_VERSION\s*=\s*'"+re.escape(manifest['version'])+"'", html):
            raise ValueError('Client-/Serverversion passt nicht zum Paket.')
    finally:
        archive.unlink(missing_ok=True)
    return target


def audit(base, job, action):
    db = base/'data'/'maschinenplanung.sqlite3'
    with sqlite3.connect(db, timeout=30) as con:
        con.execute('INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)', (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), job.get('actor', 'System'), action, json.dumps({'role': 'admin', 'jobId': job['jobId'], 'version': job.get('version'), 'stage': job.get('stage'), 'error': job.get('error', '')}, ensure_ascii=False)))


class UpdateManager:
    def __init__(self, base, current, config, launch=None, fetch=request_bytes):
        self.base, self.current, self.config = Path(base), current, config
        self.fetch, self.launch = fetch, launch or self.launch_worker
        self.guard = threading.Lock()
        self.available = None
        self.checked_at = 0
        self.error = ''
        self.checking = False

    @property
    def folder(self):
        return self.base/'updates'

    def repository(self):
        source = (self.config().get('update') or {}).get('source') or ''
        if source.startswith('https://github.com/'):
            source = source[len('https://github.com/'):].rstrip('/')
        if not source:
            source = (read_json(self.base/'LAN_CONFIG.json', {}) or {}).get('UpdateRepo') or ''
        if not source:
            # Keep the distribution default in its existing single authoritative file.
            defaults = (self.base/'Update_von_GitHub.ps1').read_text(encoding='utf-8-sig')
            match = re.search(r"\$Repo\s*=\s*'([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)'", defaults)
            source = match.group(1) if match else ''
        if not REPO.fullmatch(source):
            raise ValueError('Ungültige zentrale Updatequelle (konto/repository).')
        return source

    def check(self):
        with self.guard:
            if self.checking:
                return
            self.checking = True
        try:
            result = release_info(self.repository(), self.current, self.fetch)
            with self.guard:
                self.available, self.error = result, ''
        except Exception as e:
            # GitHub 404 means no release. Other failures never advertise a stale package.
            with self.guard:
                self.available = None
                self.error = '' if getattr(e, 'code', None) == 404 else str(e)[:500]
        finally:
            with self.guard:
                self.checked_at, self.checking = time.time(), False

    def poll(self):
        with self.guard:
            due = time.time()-self.checked_at > 3600 and not self.checking
            # Reserve the interval before dispatch so rapid GETs do not spawn many checks.
            if due:
                self.checked_at = time.time()
        if due:
            threading.Thread(target=self.check, daemon=True).start()
        job = read_json(self.folder/'status.json', {}) or {}
        return {'currentVersion': self.current, 'available': bool(self.available), 'version': (self.available or {}).get('version'), 'notes': (self.available or {}).get('notes', ''), 'job': {k: job.get(k) for k in ('jobId', 'version', 'stage', 'error', 'rolledBack')} if job else None, 'checkError': self.error, 'installSupported': os.name == 'nt'}

    def start(self, actor):
        with self.guard:
            existing = read_json(self.folder/'status.json', {}) or {}
            if (self.folder/'update.lock').exists():
                return existing, False
            if not self.available:
                raise ValueError('Kein freigegebenes Update verfügbar.')
            self.folder.mkdir(parents=True, exist_ok=True)
            job_id = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
            try:
                fd = os.open(self.folder/'update.lock', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            except FileExistsError:
                return read_json(self.folder/'status.json', {}), False
            with os.fdopen(fd, 'w') as f:
                f.write(job_id)
            job = {'jobId': job_id, 'version': self.available['version'], 'actor': actor, 'stage': 'checking', 'currentVersion': self.current, 'repository': self.available['repository']}
            atomic_json(self.folder/'status.json', job)
            try:
                audit(self.base, job, 'Update gestartet')
                self.launch(job_id)
            except Exception as e:
                job.update(stage='failed', error=str(e)[:500])
                atomic_json(self.folder/'status.json', job)
                (self.folder/'update.lock').unlink(missing_ok=True)
                raise
            return job, True

    def launch_worker(self, job_id):
        if os.name != 'nt':
            raise ValueError('Installation benötigt Windows Server 2019 oder neuer.')
        if sys.getwindowsversion().build < 17763:
            raise ValueError('Windows Build 17763 oder neuer ist erforderlich.')
        subprocess.Popen([sys.executable, '-I', str(self.base/'app_updates.py'), '--run-job', job_id, '--base', str(self.base)], cwd=self.base, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)


def run_job(base, job_id, fetch=request_bytes, installer=None):
    base = Path(base)
    folder = base/'updates'
    job = read_json(folder/'status.json')
    if not job or job.get('jobId') != job_id or (folder/'update.lock').read_text() != job_id:
        raise ValueError('Updateauftrag ist ungültig.')
    def stage(value):
        job['stage'] = value
        atomic_json(folder/'status.json', job)
        audit(base, job, 'Update: '+value)
    try:
        stage('checking')
        info = release_info(job['repository'], job['currentVersion'], fetch)
        if not info or info['version'] != job['version']:
            raise ValueError('Freigegebenes Release hat sich geändert; Update erneut starten.')
        stage('download')
        package = unpack_package(fetch(info['packageUrl'], MAX_PACKAGE), info['manifest'], folder/'staging'/job_id/'package')
        stage('preflight')
        if shutil.disk_usage(base).free < 3*MAX_PACKAGE+3*(base/'data'/'maschinenplanung.sqlite3').stat().st_size:
            raise ValueError('Zu wenig freier Speicher für Backup und Rollback.')
        if installer:
            installer(package, folder/'status.json')
        else:
            if os.name != 'nt':
                raise ValueError('Installation benötigt Windows Server 2019 oder neuer.')
            log = folder/('install-'+job_id+'.log')
            with log.open('w', encoding='utf-8') as output:
                result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(package/'UPDATE_LIVE.ps1'), '-UpdateJobPath', str(folder/'status.json')], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, timeout=1800)
            if result.returncode:
                raise ValueError('Installation abgebrochen. Details im serverseitigen Updateprotokoll.')
        job = read_json(folder/'status.json', job)
        stage('complete')
    except Exception as e:
        job = read_json(folder/'status.json', job)
        job.update(stage='failed', error=str(job.get('error') or e)[:500])
        atomic_json(folder/'status.json', job)
        audit(base, job, 'Update fehlgeschlagen')
    finally:
        (folder/'update.lock').unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-job', required=True)
    parser.add_argument('--base', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{24}', args.run_job):
        raise SystemExit('Ungültige Update-ID.')
    run_job(Path(args.base), args.run_job)
