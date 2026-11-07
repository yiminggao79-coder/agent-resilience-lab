"""Actual SIGKILL/new-PID context continuity; no provider or paid ledger."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_lab.store import Store


class FileContextRestartTests(unittest.TestCase):
    def test_read_and_notice_commits_survive_two_sigkills_without_duplicate_or_fake_read(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); root = base / 'work'; root.mkdir(); database = base / 'state.sqlite'
            path = root / 'source.txt'; path.write_bytes(b'OLD_PRIVATE_VALUE\n')
            repo = Path(__file__).resolve().parents[2]
            common = """import sys,os,signal,json
from pathlib import Path
sys.path.insert(0,%r)
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final
root=Path(%r);database=Path(%r)
""" % (str(repo), str(root), str(database))
            def run(body):
                return subprocess.run([sys.executable, '-I', '-c', common + body],
                    capture_output=True, text=True, timeout=8, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
            first = run("""def observer(event):
 if event['kind']=='tool_finished':os.kill(os.getpid(),signal.SIGKILL)
with Store(database) as store:
 engine=Engine(store,CodingTools(root),ScriptedModel([tool('read_file',{'path':'source.txt'})]),observer=observer)
 tid=engine.create('Read source',Limits(max_seconds=30),track_file_context=True)
 engine.run(tid)
""")
            self.assertEqual(first.returncode, -9, first.stderr)
            with Store.open_readonly(database) as store:
                tid = store.db.execute('SELECT id FROM tasks').fetchone()[0]; read_state = store.load(tid)
            prefix = copy.deepcopy(read_state['messages'])
            self.assertEqual(read_state['phase'], 'ready')
            self.assertEqual(len(read_state['file_context']['reads']), 1)
            before = path.stat(); path.write_bytes(b'NEW_PRIVATE_VALUE\n')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            second = run("""def observer(event):
 if event['kind']=='file_context_refreshed':os.kill(os.getpid(),signal.SIGKILL)
with Store(database) as store:
 engine=Engine(store,CodingTools(root),ScriptedModel([final()]),observer=observer)
 engine.run(%r)
""" % tid)
            self.assertEqual(second.returncode, -9, second.stderr)
            with Store.open_readonly(database) as store:
                checkpoint = store.load(tid)
                self.assertEqual(checkpoint['phase'], 'ready')
                self.assertEqual(checkpoint['metrics']['model_calls'], 1)
                self.assertEqual(len([m for m in checkpoint['messages'] if '_file_context' in m]), 1)
            third = run("""with Store(database) as store:
 model=ScriptedModel([final()]);engine=Engine(store,CodingTools(root),model)
 state=engine.run(%r)
 print(json.dumps({'phase':state['phase'],'samples':len(model.seen),'seen':model.seen[0]}))
""" % tid)
            self.assertEqual(third.returncode, 0, third.stderr)
            result = json.loads(third.stdout); self.assertEqual(result['phase'], 'completed'); self.assertEqual(result['samples'], 1)
            with Store.open_readonly(database) as store:
                state = store.load(tid); events = store.events(tid)
            self.assertEqual(state['messages'][:len(prefix)], prefix)
            notes = [m for m in state['messages'] if '_file_context' in m]; self.assertEqual(len(notes), 1)
            self.assertIn({'role': 'user', 'content': notes[0]['content']}, result['seen'])
            payload = json.loads(notes[0]['content'].split('\n', 1)[1])
            self.assertEqual(payload['files'][0]['current_sha256'], hashlib.sha256(b'NEW_PRIVATE_VALUE\n').hexdigest())
            self.assertEqual(state['file_context']['reads'][0]['sha256'], hashlib.sha256(b'OLD_PRIVATE_VALUE\n').hexdigest())
            self.assertEqual(len([e for e in events if e['kind']=='file_context_refreshed']), 1)
            self.assertNotIn('source.txt', json.dumps(events)); self.assertNotIn('PRIVATE_VALUE', json.dumps(events))


if __name__ == '__main__': unittest.main()
