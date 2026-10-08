#!/usr/bin/env python3
"""Build the immutable release package and schema-1 update manifest (flat names or one subfolder level, e.g. core/x.py)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_updates import member_name  # noqa: E402


def build(root, output, commit):
    root, output = Path(root), Path(output)
    common = (root/'MP_Common.ps1').read_text(encoding='utf-8-sig')
    block = re.search(r'\$MP_AppFiles\s*=\s*@\((.*?)\n\)', common, re.S).group(1)
    names = re.findall(r"'([^']+)'", block)
    version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', (root/'server.py').read_text(), re.M).group(1)
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Full release commit required.')
    output.mkdir(parents=True, exist_ok=True)
    archive = output/('produktionsplanung-v'+version+'.zip')
    files = {}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in names:
            member_name(name)  # same rule as the updater: '/' separator, at most one folder level
            content = (root/name).read_bytes()
            z.writestr(name, content)
            files[name] = hashlib.sha256(content).hexdigest()
    manifest = {'schema': 1, 'version': version, 'minVersion': '12.19.0', 'commit': commit, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'files': files}
    (output/'update-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    build(root,args.output,commit)
