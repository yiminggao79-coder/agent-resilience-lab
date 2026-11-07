"""Supervisor SIGKILL frees flock; missing durable proof must still fence effects."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from agent_lab.native_jobs import JobBlocked,inspect_workspace
from agent_lab.store import Store
from agent_lab.tools import Tools,ToolError,UncertainEffect

REPO=Path(__file__).resolve().parents[2]


class NativeJobQuarantineProcessTests(unittest.TestCase):
    def test_killed_supervisor_does_not_let_another_database_or_direct_tool_start_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'work';root.mkdir();db=base/'state.sqlite';marker=base/'task.json'
            child=subprocess.Popen([sys.executable,'-m','tests.acceptance.native_job_child',str(root),str(db),str(marker)],
                cwd=str(REPO),env={'PATH':os.defpath,'LANG':'C'},text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            native=None
            try:
                end=time.monotonic()+6
                while time.monotonic()<end:
                    if marker.exists() and (root/'ready.json').exists():
                        try:
                            task=json.loads(marker.read_text());native=json.loads((root/'ready.json').read_text());break
                        except json.JSONDecodeError:pass
                    if child.poll() is not None:self.fail('fixture ended before barrier: '+child.communicate()[1])
                    time.sleep(.01)
                self.assertIsNotNone(native,'native barrier missing')
                os.kill(native['supervisor_pid'],signal.SIGKILL)
                out,err=child.communicate(timeout=5);self.assertEqual(child.returncode,0,err)
                self.assertEqual(json.loads(out)['phase'],'needs_confirmation')
                # Prove the lease owner died but its native process is still live.
                os.kill(native['native_pid'],0)
                admitted=False
                try:
                    with Store(base/'other.sqlite') as other,other.lock(root):
                        admitted=True
                        tools=Tools(root)
                        tools.execute(tools.prepare('write_file',{'path':'new-effect','content':'must be fenced'}),2)
                except JobBlocked:pass
                if admitted:
                    (root/'trigger-old-effect').write_text('controlled observation')
                    end=time.monotonic()+.5
                    while not (root/'old-effect').exists() and time.monotonic()<end:time.sleep(.01)
                print(json.dumps({'kind':'native_job_supervisor_death_contract','model_kind':'offline_protocol_double',
                    **task,**native,'new_runner_admitted':admitted,'old_effect_observed':(root/'old-effect').exists(),
                    'new_effect_observed':(root/'new-effect').exists()}),flush=True)
                self.assertFalse(admitted,'different DB bypassed an unconfirmed native job')
                tools=Tools(root);intent=tools.prepare('write_file',{'path':'direct-effect','content':'no'})
                with self.assertRaises((ToolError,UncertainEffect)):tools.execute(intent,2)
                self.assertFalse((root/'direct-effect').exists());self.assertFalse((root/'new-effect').exists())
                summary=inspect_workspace(root,db)
                self.assertTrue(summary['workspace_quarantined']);self.assertEqual(summary['job_id'],json.loads((root/'.agent-lab.native-job').read_text())['job_id'])
            finally:
                (root/'release').write_text('cooperative fixture cleanup')
                if child.poll() is None:child.kill()
                child.communicate(timeout=5)
                # The native is deliberately self-bounded; no arbitrary PID kill.
                if native:
                    end=time.monotonic()+4
                    while time.monotonic()<end:
                        status=subprocess.run(['/bin/ps','-o','stat=','-p',str(native['native_pid'])],capture_output=True,text=True).stdout.strip()
                        if not status or status.startswith('Z'):break
                        time.sleep(.02)
                    else:self.fail('owned native fixture did not exit')
