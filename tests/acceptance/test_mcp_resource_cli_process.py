"""Two new CLI processes retain unknown external read without automatic replay."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from .test_confined_backend_cli_process import HARNESS,REPO
from agent_lab.store import Store
from tests.support import tool


class MCPResourceCLIProcessTests(unittest.TestCase):
    def test_external_effect_before_response_remains_unknown_across_new_cli_processes(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-resource-cli-',dir='/private/tmp') as directory:
            base=Path(directory);root=base/'work';root.mkdir();db=base/'state.sqlite'
            trusted=base/'trusted';trusted.mkdir();server=trusted/'server.py'
            shutil.copyfile(REPO/'tests/fixtures/mcp_resource_server.py',server)
            log=trusted/'requests';marker=trusted/'marker';config=trusted/'mcp.json'
            config.write_text(json.dumps({'servers':[{'name':'owned','command':[sys.executable,'-I','-S',str(server),'die',str(log),str(marker)],'cwd':str(trusted)}]}))
            env={'PATH':os.defpath,'LANG':'C','PYTHONDONTWRITEBYTECODE':'1','AGENT_LAB_FIXTURE_KEY':'synthetic-no-network'}
            def invoke(args,responses=()):
                return subprocess.run([sys.executable,'-I','-c',HARNESS,str(REPO),'--db',str(db),*args],
                    env={**env,'AGENT_LAB_FIXTURE_RESPONSES':json.dumps(responses)},capture_output=True,text=True,timeout=8)
            workspace=['--workspace',str(root)]
            extension=['--mcp-config',str(config)]
            flags=['--profile','coding','--model','synthetic','--base-url','https://example.invalid/v1','--api-key-env','AGENT_LAB_FIXTURE_KEY','--allow-api']
            started=invoke(['run','Read trusted context',*workspace,*flags,*extension],
                [tool('read_mcp_resource',{'server':'owned','uri':'owned:///reference'},'resource')])
            self.assertEqual(started.returncode,3,started.stderr);task=json.loads(started.stdout.splitlines()[0])['task_id']
            approval=invoke(['approval',task,*workspace]);self.assertEqual(approval.returncode,0,approval.stderr);request=json.loads(approval.stdout)
            approved=invoke(['approve',task,request['request_id'],*workspace,'--digest',request['digest']]);self.assertEqual(approved.returncode,0,approved.stderr)
            failed=invoke(['resume',task,*workspace,*flags,*extension]);self.assertEqual(failed.returncode,3,failed.stderr)
            with Store(db) as store:
                before=store.load(task);self.assertEqual(before['phase'],'needs_confirmation');pending=before['pending']
            resumed=invoke(['resume',task,*workspace,*flags,*extension]);self.assertEqual(resumed.returncode,3,resumed.stderr)
            with Store(db) as store:
                after=store.load(task);self.assertEqual(after['phase'],'needs_confirmation');self.assertEqual(after['pending'],pending)
            self.assertEqual(marker.read_text(),'resource dispatched\n')
            self.assertEqual(sum(json.loads(line).get('method')=='resources/read' for line in log.read_text().splitlines()),1)


if __name__=='__main__':unittest.main()
