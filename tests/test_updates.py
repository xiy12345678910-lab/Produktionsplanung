#!/usr/bin/env python3
"""7a: trusted packages, generic consecutive releases, durable jobs and recovery."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_updates as updates

COMMIT='a'*40


def package(v='12.20.0'):
    files={'server.py':f'APP_VERSION = "{v}"\n', 'index.html':f"const CLIENT_VERSION='{v}';", 'release_gates.py':'', 'MP_Common.ps1':'', 'UPDATE_LIVE.ps1':'', 'app_updates.py':''}
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for k,value in files.items():z.writestr(k,value)
    blob=buf.getvalue()
    manifest={'schema':1,'version':v,'minVersion':'12.19.0','commit':COMMIT,'sha256':hashlib.sha256(blob).hexdigest(),'files':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}}
    return blob,manifest


def source(v='12.20.0', **attrs):
    blob,manifest=package(v)
    release={'tag_name':'v'+v,'draft':False,'prerelease':False,'body':'Changes','assets':[{'name':'update-manifest.json','id':1},{'name':'produktionsplanung-v'+v+'.zip','id':2}],**attrs}
    def fetch(url,limit=1024*1024):
        if url.endswith('releases/latest'):return json.dumps(release).encode()
        if '/commits/' in url:return json.dumps({'sha':COMMIT}).encode()
        if '/compare/' in url:return b'{"status":"behind"}'
        if url.endswith('/assets/1'):return json.dumps(manifest).encode()
        if url.endswith('/assets/2'):return blob
        raise AssertionError(url)
    return fetch,manifest


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mp-updates-')
        self.base=Path(self.tmp.name)
        (self.base/'data').mkdir()
        with sqlite3.connect(self.base/'data'/'maschinenplanung.sqlite3') as con:
            con.execute('CREATE TABLE server_audit(ts,username,action,detail,revision)')
        self.addCleanup(self.tmp.cleanup)

    def manager(self,v='12.19.0',fetch=None,launch=None):
        return updates.UpdateManager(self.base,v,lambda:{'update':{'source':'owner/repo'}},launch or (lambda _:None),fetch or source()[0])

    def test_no_new_version_or_unapproved_release(self):
        for attrs in [{},{'draft':True},{'prerelease':True}]:
            fetch,_=source('12.19.0' if not attrs else '12.20.0',**attrs)
            self.assertIsNone(updates.release_info('owner/repo','12.19.0',fetch))

    def test_two_generic_updates_and_hide_after_success(self):
        for current,new in [('12.19.0','12.20.0'),('12.20.0','12.21.0')]:
            fetch,_=source(new);m=self.manager(current,fetch);m.check()
            self.assertEqual(m.poll()['version'],new)
            job,started=m.start('admin');self.assertTrue(started)
            updates.run_job(self.base,job['jobId'],fetch,lambda pkg,status:None)
            self.assertEqual(updates.read_json(m.folder/'status.json')['stage'],'complete')
            m2=self.manager(new,fetch);m2.check();self.assertFalse(m2.poll()['available'])
            self.assertFalse((m.folder/'update.lock').exists())

    def test_concurrent_clicks_launch_exactly_once_and_survive_reload(self):
        calls=[];m=self.manager(launch=lambda jid:calls.append(jid));m.check()
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:m.start('admin'),range(4)))
        self.assertEqual(len(calls),1)
        self.assertEqual(sum(x[1] for x in results),1)
        reloaded=self.manager();self.assertEqual(reloaded.poll()['job']['jobId'],calls[0])
        self.assertEqual(len({x[0]['jobId'] for x in results}),1)

    def test_wrong_hash_is_rejected_before_installer(self):
        fetch,manifest=source();manifest['sha256']='0'*64;m=self.manager(fetch=fetch);m.check()
        job,_=m.start('admin');calls=[]
        updates.run_job(self.base,job['jobId'],fetch,lambda *args:calls.append(args))
        self.assertFalse(calls)
        self.assertEqual(updates.read_json(m.folder/'status.json')['stage'],'failed')
        self.assertFalse((m.folder/'update.lock').exists())

    def test_bad_version_or_missing_integrity_or_incompatibility(self):
        _,original=package()
        for change in [{'version':'12.99.0'},{'sha256':''},{'minVersion':'13.0.0'},{'signature':'unknown'}]:
            with self.assertRaises(ValueError):updates.validate_manifest({**original,**change},'12.20.0','12.19.0')

    def test_client_server_version_mismatch(self):
        blob,manifest=package()
        buf=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as z,zipfile.ZipFile(buf,'w') as out:
            for name in z.namelist():out.writestr(name,b"const CLIENT_VERSION='12.22.0';" if name=='index.html' else z.read(name))
        bad=buf.getvalue();manifest['sha256']=hashlib.sha256(bad).hexdigest();manifest['files']['index.html']=hashlib.sha256(b"const CLIENT_VERSION='12.22.0';").hexdigest()
        with self.assertRaises(ValueError):updates.unpack_package(bad,manifest,self.base/'package')

    def test_zip_path_traversal_and_extra_files(self):
        blob,manifest=package();buf=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as z,zipfile.ZipFile(buf,'w') as out:
            for name in z.namelist():out.writestr(name,z.read(name))
            out.writestr('../server.py','bad')
        bad=buf.getvalue();manifest['sha256']=hashlib.sha256(bad).hexdigest()
        with self.assertRaises(ValueError):updates.unpack_package(bad,manifest,self.base/'package')
        self.assertFalse((self.base/'server.py').exists())

    def test_install_failure_preserves_recovery_result(self):
        fetch,_=source();m=self.manager(fetch=fetch);m.check();job,_=m.start('admin')
        def installer(pkg,status):
            data=updates.read_json(status);data.update(stage='rollback',rolledBack=True,error='Migration fehlgeschlagen; vorherige Version wiederhergestellt.');updates.atomic_json(status,data)
            raise ValueError('Simulated installation failure')
        updates.run_job(self.base,job['jobId'],fetch,installer)
        result=updates.read_json(m.folder/'status.json')
        self.assertTrue(result['rolledBack']);self.assertEqual(result['stage'],'failed')
        self.assertIn('Migration fehlgeschlagen',result['error'])
        with sqlite3.connect(self.base/'data'/'maschinenplanung.sqlite3') as con:self.assertTrue(con.execute("SELECT 1 FROM server_audit WHERE action='Update fehlgeschlagen'").fetchone())

    def test_arbitrary_source_is_rejected(self):
        for repo in ['../../file','https://evil.example/path','owner/repo;run']:
            with self.assertRaises(ValueError):updates.release_info(repo,'12.19.0',lambda *a:self.fail('Network must not run'))

    def test_release_must_be_in_main_and_manifest_matches_commit(self):
        fetch,manifest=source();manifest['commit']='b'*40
        with self.assertRaises(ValueError):updates.release_info('owner/repo','12.19.0',fetch)


if __name__=='__main__':unittest.main(verbosity=2)
