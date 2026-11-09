"""Fixed owned toolchain feasibility, not a production grant or model score."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_lab import sandbox
from agent_lab.confined_collector import collect


def probe():
    def find(name):
        result=subprocess.run(['/usr/bin/xcrun','--find',name],env={'PATH':os.defpath},capture_output=True,text=True,timeout=4)
        if result.returncode:raise RuntimeError('tool_inventory_failed')
        return Path(result.stdout.strip()).resolve()
    cc,linker=find('clang'),find('ld')
    result=subprocess.run(['/usr/bin/xcrun','--show-sdk-path'],env={'PATH':os.defpath},capture_output=True,text=True,timeout=4)
    if result.returncode:raise RuntimeError('sdk_inventory_failed')
    sdk=Path(result.stdout.strip()).resolve()
    result=subprocess.run([str(cc),'-print-resource-dir'],env={'PATH':os.defpath},capture_output=True,text=True,timeout=4)
    if result.returncode:raise RuntimeError('resource_inventory_failed')
    resource=Path(result.stdout.strip()).resolve();version=json.loads((sdk/'SDKSettings.json').read_text())['Version']
    source='#include <stdio.h>\nint main(void){puts("sandbox built program");return 0;}\n'
    with tempfile.TemporaryDirectory(prefix='agentlab-compiler-probe-',dir='/private/tmp') as directory:
        base=Path(directory);root=base/'work';root.mkdir();scratch=base/'scratch';scratch.mkdir()
        (root/'main.c').write_text(source)
        mapping={'cc':str(cc),'linker':str(linker)}
        profile=sandbox._command_profile(root,scratch,tool_executables=mapping,command_backend='confined-fork',allow_workspace_executables=True)
        parents={parent for p in (sdk,resource) for parent in p.parents}
        resources='\n'.join([
            '(allow file-read* (subpath %s) (subpath %s))'%(sandbox._quote(sdk),sandbox._quote(resource)),
            '(allow file-read* %s)'%' '.join('(literal %s)'%sandbox._quote(linker.parent.parent/'lib'/name)
                for name in ('libtapi.dylib','libLTO.dylib','libswiftDemangle.dylib')),
            '(allow file-read-metadata %s)'%' '.join('(literal %s)'%sandbox._quote(p) for p in sorted(parents)),
            '(deny file-write* (subpath %s) (subpath %s))'%(sandbox._quote(sdk),sandbox._quote(resource)),
            '(allow sysctl-read (sysctl-name "hw.pagesize_compat"))',
            '(deny file-link)','(deny system-fcntl (fcntl-command 80 110))'])
        env={'PATH':os.defpath,'LANG':'C','HOME':str(scratch),'TMPDIR':str(scratch),'PYTHONDONTWRITEBYTECODE':'1'}
        front=[str(cc),'-cc1','-triple','x86_64-apple-macosx14.0.0','-fgnuc-version=4.2.1','-emit-obj','-x','c',
               '-isysroot',str(sdk),'-internal-isystem',str(resource/'include'),
               '-internal-isystem',str(sdk/'usr/include'),'main.c','-o','main.o']
        def execute(argv,extra=True):
            return collect([sandbox.BACKEND_PATH,'-p',profile+('\n'+resources if extra else ''),*argv],root,env,12,output_limit=8192)
        report={'kind':'fixed_toolchain_resource_feasibility','model_kind':None,'production_resource_capability':False,
                'clang':str(cc),'linker':str(linker),'sdk':str(sdk),'sdk_version':version,'clang_resources':str(resource),
                'sysctl_read':['hw.pagesize_compat'],
                'source_sha256':hashlib.sha256(source.encode()).hexdigest()}
        report['frontend_without_resources']=execute(front,False)
        report['frontend_with_readonly_resources']=execute(front)
        if report['frontend_with_readonly_resources']['exit_code']==0:
            report['link']=execute([str(linker),'-arch','x86_64','-platform_version','macos','14.0.0',version,
                                   '-syslibroot',str(sdk),'-lSystem','main.o','-o','program'])
            if report['link']['exit_code']==0:
                report['run']=execute([str(root/'program')])
                report['binary_sha256']=hashlib.sha256((root/'program').read_bytes()).hexdigest()
        report['experiment_passed']=report.get('run',{}).get('stdout')=='sandbox built program\n'
        return report


if __name__=='__main__':
    report=probe()
    target=Path(__file__).resolve().parents[1]/'evidence/v2/compiler-resource-probe.json'
    target.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['experiment_passed'] else 1)
