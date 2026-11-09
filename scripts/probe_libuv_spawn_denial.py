"""One fixed Node spawn reaction probe; NoFork retained, not npm validation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab import sandbox
from agent_lab.command_runtime import runtime_policy

BODY="""const cp=require('node:child_process');
console.log(JSON.stringify({node:process.version,libuv:process.versions.uv}));
const r=cp.spawnSync('/usr/bin/true',[],{timeout:500,stdio:'pipe'});
console.log(JSON.stringify({node:process.version,libuv:process.versions.uv,
status:r.status,signal:r.signal,error:r.error?{code:r.error.code,errno:r.error.errno}:null}));
"""


def probe(node):
    with tempfile.TemporaryDirectory(prefix='agentlab-libuv-denial-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir();scratch=base/'scratch';scratch.mkdir()
        authority=runtime_policy(root,node)
        selected=authority['node']['path']
        profile=sandbox._command_profile(root,scratch,node_executable=selected)
        profile+='\n(deny syscall-unix (syscall-number 82 147))\n(deny syscall-unix (syscall-number 244) (with errno ENOSYS))'
        result=sandbox._collect([sandbox.BACKEND_PATH,'-p',profile,selected,'-e',BODY],root,
            {'PATH':os.defpath,'LANG':'C.UTF-8','HOME':str(scratch),'TMPDIR':str(scratch)},4)
        lines=result['stdout'].splitlines()
        return {'kind':'research_libuv_spawn_denial_nofork','node_sha256':authority['node']['sha256'],
            'exit_code':result['exit_code'],'startup':json.loads(lines[0]) if lines else None,
            'body':json.loads(lines[1]) if result['exit_code']==0 and len(lines)==2 else None,
            'libuv_enosys_assertion_observed':('err != ENOSYS' in result['stderr']
                and 'uv__spawn_and_init_child_posix_spawn' in result['stderr']),
            'stderr_bytes':len(result['stderr'].encode()),'stderr_sha256':hashlib.sha256(result['stderr'].encode()).hexdigest(),
            'stdout_truncated':result['stdout_truncated'],'profile_sha256':hashlib.sha256(profile.encode()).hexdigest(),
            'stdio_configuration':'pipe','production_policy_changed':False,'fork_enabled':False,
            'provider_requests':0,'credential_file_read':False,'real_budget_opened':False,
            'stock_npm_compatibility_proven':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node-executable',required=True)
    print(json.dumps(probe(parser.parse_args().node_executable),indent=2))
