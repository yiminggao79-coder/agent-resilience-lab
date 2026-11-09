"""Installed Codex on an isolated synthetic Responses peer, never LLM quality."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.reference_cli import REFERENCE_BINARY,reference_environment,reference_command,managed_preferences_status
from scripts.reference_protocol import serving


def request_summary(body):
    return {'model':body.get('model'),'stream':body.get('stream'),
        'tool_names':[tool.get('name') for tool in body.get('tools',[])],
        'tool_types':[tool.get('type') for tool in body.get('tools',[])],
        'input_types':[item.get('type',item.get('role')) for item in body.get('input',[])]}


def probe():
    base=Path(tempfile.mkdtemp(prefix='agentlab-reference-final-',dir='/private/tmp'))
    (base/'work').mkdir();(base/'private').write_text('OWNED_PRIVATE_REFERENCE_SENTINEL')
    started=time.monotonic()
    report={'passed':False,'phase':'running','experiment':'isolated_installed_codex_synthetic_final',
        'fixture_directory':str(base),'binary_path':str(REFERENCE_BINARY),'binary_sha256':hashlib.sha256(REFERENCE_BINARY.read_bytes()).hexdigest(),
        'provider_requests':0,'real_llm_task_success_rate':None,'real_tokens':None,'cost_estimate_cny':None,
        'same_as_pinned_source_commit':None,'protocol_stimulus':'offline_synthetic_responses','raw_requests_exported':False}
    with serving(max_requests=2) as server:
        status=managed_preferences_status(base,server.server_port);report['managed_preferences_presence']=status
        if status['forced_config'] or status['forced_requirements']:
            return {**report,'phase':'blocked','error':'forced_managed_preferences_present','requests':0}
        command=reference_command(base,server.server_port,'Reply with the fixture result. Do not call tools.')
        try:
            result=subprocess.run(command,cwd=base/'work',env=reference_environment(base),stdin=subprocess.DEVNULL,
                capture_output=True,text=True,timeout=25)
            report.update(exit_code=result.returncode,stdout=result.stdout[:262144],stderr=result.stderr[:65536],timed_out=False,
                          stdout_truncated=len(result.stdout)>262144,stderr_truncated=len(result.stderr)>65536)
        except subprocess.TimeoutExpired:
            report.update(exit_code=None,timed_out=True)
        report.update(requests=len(server.requests),request_summaries=[request_summary(body) for body in server.requests])
    report.update(elapsed_seconds=round(time.monotonic()-started,3))
    report['passed']=report['exit_code']==0 and report['requests']==1 and 'owned reference final' in report.get('stdout','')
    report['phase']='completed' if report['passed'] else 'failed'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',required=True);args=parser.parse_args()
    target=Path(args.report);target.write_text(json.dumps({'passed':False,'phase':'running','real_llm_task_success_rate':None})+'\n')
    try:result=probe()
    except Exception:result={'passed':False,'phase':'failed','error':'reference_probe_failed','real_llm_task_success_rate':None}
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key not in ('stdout','stderr','request_summaries')},indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
