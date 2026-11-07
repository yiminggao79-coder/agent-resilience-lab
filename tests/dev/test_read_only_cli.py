import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class Model(ScriptedModel):
    api_key_env='SYNTHETIC_KEY'


class ReadOnlyCLITests(unittest.TestCase):
    def test_host_scope_blocks_protected_write_and_cannot_disappear_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();(root/'locked').mkdir()
            (root/'locked/source.py').write_text('original\n');db=Path(temp)/'state.sqlite'
            model=Model([tool('write_file',{'path':'locked/source.py','content':'wrong\n'}),
                         tool('write_file',{'path':'allowed.py','content':'allowed\n'}),final('Finished')])
            base=['--db',str(db)];common=['--workspace',str(root),'--model','test','--allow-api']
            with patch('agent_lab.__main__.OpenAIModel',return_value=model),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()):
                code=main(base+['run','Protect locked sources']+common+['--read-only-path','locked'])
            self.assertEqual(code,0)
            self.assertEqual((root/'locked/source.py').read_text(),'original\n')
            self.assertEqual((root/'allowed.py').read_text(),'allowed\n')
            with Store(db) as store:
                tid=store.db.execute('SELECT id FROM tasks').fetchone()[0]
                self.assertEqual(store.load(tid)['read_only_policy']['read_only_paths'],['locked'])
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(main(base+['policy',tid]),0)
            self.assertEqual(json.loads(output.getvalue())['read_only_policy']['read_only_paths'],['locked'])
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(main(base+['status',tid]),0)
            status=json.loads(output.getvalue())
            self.assertEqual(status['read_only']['scope_count'],1)
            self.assertEqual(len(status['read_only']['identity']),64)
            self.assertNotIn('locked',output.getvalue())
            with patch('agent_lab.__main__.OpenAIModel',return_value=Model([])),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(base+['resume',tid]+common),2)
                self.assertEqual(main(base+['resume',tid]+common+['--read-only-path','locked']),0)
