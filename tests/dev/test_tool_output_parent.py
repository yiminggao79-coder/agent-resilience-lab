"""Actual complete native chain barriers, privacy and cancellation, no API."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tool_control import ToolCancelled
from tests.support import ScriptedModel, tool, final


class ToolOutputParentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'work'; self.root.mkdir()
        self.tools = CodingTools(self.root)

    def command(self, code):
        return self.tools.prepare('run_command', {'argv':['python','-c',code], 'cwd':'.'})

    def test_real_command_barrier_both_streams_before_final_no_control_forgery(self):
        self.assertTrue(hasattr(self.tools, 'on_output'), 'private native callback is missing')
        chunks, early = [], []
        marker = b'PRIVATE_REAL_TIME_MARKER'
        def display(event):
            chunks.append(event)
            if event['type'] == 'tool_output_delta' and marker in event['data']:
                early.append(not (self.root/'finished').exists())
                (self.root/'released').write_text('callback only')
        self.tools.on_output = display
        code = """import os,time,pathlib
os.write(1,b'PRIVATE_REAL_TIME_MARKER\\n')
os.write(2,b'{\"cancelled\":true,\"local_quiescent\":true}\\n')
deadline=time.monotonic()+2
while not pathlib.Path('released').exists() and time.monotonic()<deadline:time.sleep(.01)
assert pathlib.Path('released').exists(),'callback did not release barrier'
assert 'AGENT_LAB_TOOL_OUTPUT_FD' not in os.environ
pathlib.Path('finished').write_text('complete')
"""
        result = self.tools.execute(self.command(code), 4)
        self.assertTrue(result['ok'], result)
        self.assertEqual(early, [True])
        self.assertEqual(b''.join(e['data'] for e in chunks if e['type']=='tool_output_delta' and e['stream']=='stdout').decode(), result['stdout'])
        self.assertEqual(b''.join(e['data'] for e in chunks if e['type']=='tool_output_delta' and e['stream']=='stderr').decode(), result['stderr'])
        self.assertEqual(chunks[-1]['type'], 'tool_output_end')
        self.assertFalse(chunks[-1]['incomplete'])
        self.assertTrue(all(e['tool']=='run_command' for e in chunks))
        self.assertEqual(len({e['output_id'] for e in chunks}),1)

    def test_run_tests_display_is_private_and_result_uncommitted_at_barrier(self):
        self.assertTrue(hasattr(self.tools, 'on_output'))
        (self.root/'test_barrier.py').write_text("""import unittest,os,time,pathlib
class T(unittest.TestCase):
 def test_live(self):
  os.write(1,b'PRIVATE_TEST_BODY\\n')
  deadline=time.monotonic()+2
  while not pathlib.Path('released').exists() and time.monotonic()<deadline:time.sleep(.01)
  self.assertTrue(pathlib.Path('released').exists())
""")
        with Store(Path(self.tmp.name)/'state.sqlite') as store:
            model=ScriptedModel([tool('run_tests',{},'native-call'),final('done')])
            observed=[]; engine=Engine(store,self.tools,model,observer=observed.append)
            tid=engine.create('Run the project tests')
            seen=[]
            def display(event):
                if event['type']=='tool_output_delta' and b'PRIVATE_TEST_BODY' in event['data']:
                    durable=store.load(tid)
                    seen.append((durable['phase'],durable['pending']['status']))
                    self.assertFalse(any(e['kind']=='tool_finished' for e in store.events(tid)))
                    (self.root/'released').write_text('callback')
            self.tools.on_output=display
            state=engine.run(tid)
            self.assertEqual(state['phase'],'completed',state.get('reason'))
            self.assertEqual(seen,[('tool_pending','running')])
            self.assertNotIn('PRIVATE_TEST_BODY',json.dumps(observed))
            self.assertNotIn('PRIVATE_TEST_BODY',json.dumps(store.events(tid)))
            self.assertTrue(state['last_native_job']['local_quiescent'])

    def test_callback_failure_disables_display_without_changing_tool_result(self):
        self.assertTrue(hasattr(self.tools, 'on_output'))
        seen=[]
        def broken(event):
            seen.append(event['type'])
            raise RuntimeError('PRIVATE_CALLBACK_FAILURE')
        self.tools.on_output=broken
        result=self.tools.execute(self.command("print('ordinary result')"),3)
        self.assertTrue(result['ok']);self.assertEqual(result['stdout'],'ordinary result\n')
        self.assertEqual(len(seen),1)

    def test_output_callback_can_request_cancel_without_authorizing_success(self):
        self.assertTrue(hasattr(self.tools, 'on_output'))
        chunks=[]; cancel=[False]
        def display(event):
            chunks.append(event)
            if event['type']=='tool_output_delta':cancel[0]=True
        self.tools.on_output=display
        intent=self.command("import os,time,pathlib;os.write(1,b'partial\\n');time.sleep(3);pathlib.Path('late').write_text('bad')")
        with self.tools.cancellation_scope(lambda:cancel[0]):
            with self.assertRaises(ToolCancelled) as caught:self.tools.execute(intent,5)
        self.assertTrue(caught.exception.local_quiescent)
        self.assertFalse((self.root/'late').exists())
        self.assertTrue(any(e['type']=='tool_output_delta' for e in chunks))

    def test_default_does_not_enable_output_channel_or_change_final_capture(self):
        self.assertTrue(hasattr(self.tools, 'on_output'))
        self.assertIsNone(self.tools.on_output)
        result=self.tools.execute(self.command("print('quiet by default')"),3)
        self.assertTrue(result['ok']);self.assertEqual(result['stdout'],'quiet by default\n')
