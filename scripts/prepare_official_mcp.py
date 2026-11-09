"""Install only the committed MCP interop lock into a fresh owned temporary tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',required=True,help='trusted absolute Node>=24 binary')
    parser.add_argument('--npm-cli',required=True,help='trusted absolute npm-cli.js; no global install/update')
    args=parser.parse_args()
    node=Path(args.node);npm=Path(args.npm_cli)
    if not node.is_absolute() or not npm.is_absolute() or not node.is_file() or not npm.is_file():raise ValueError('explicit_installed_node_and_npm_required')
    evidence=Path(__file__).resolve().parents[1]/'evidence/interop/filesystem-2.2.0'
    base=Path(tempfile.mkdtemp(prefix='agentlab-official-mcp-',dir='/private/tmp'))
    for name in ('install','home','cache','scratch'):(base/name).mkdir()
    for name in ('user-empty.npmrc','global-empty.npmrc'):(base/name).write_text('')
    for name in ('package.json','package-lock.json'):shutil.copyfile(evidence/name,base/'install'/name)
    command=[str(node),str(npm),'--prefix',str(base/'install'),'--cache',str(base/'cache'),
        '--userconfig',str(base/'user-empty.npmrc'),'--globalconfig',str(base/'global-empty.npmrc'),
        '--ignore-scripts','--no-audit','--no-fund','--registry','https://registry.npmjs.org','ci']
    started=time.monotonic()
    result=subprocess.run(command,env={'PATH':str(node.parent)+':'+os.defpath,'HOME':str(base/'home'),
        'TMPDIR':str(base/'scratch'),'LANG':'C.UTF-8'},capture_output=True,text=True,timeout=60)
    report={'fixture_directory':str(base),'install':str(base/'install'),'node':str(node.resolve()),'exit_code':result.returncode,
        'elapsed_seconds':round(time.monotonic()-started,3),'stdout':result.stdout,'stderr':result.stderr,
        'lifecycle_scripts_disabled':True,'audit_disabled':True,'user_global_config_empty_distinct':True,
        'provider_requests':0,'package_lock_sha256':hashlib.sha256((base/'install'/'package-lock.json').read_bytes()).hexdigest()}
    (base/'installation-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if result.returncode==0 else 1


if __name__=='__main__':raise SystemExit(main())
