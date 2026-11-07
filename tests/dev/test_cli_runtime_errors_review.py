"""One independent CLI boundary case; private state is synthetic only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import __main__ as cli
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import ToolError


class CLIRuntimeErrorsReviewTests(unittest.TestCase):
    def test_hostile_error_values_are_not_rendered_or_used_to_change_unknown_state(self):
        touched = []

        def forbidden(name):
            touched.append(name)
            raise AssertionError('arbitrary exception rendering must not occur')

        class HostileValue:
            def __str__(self): return forbidden('value_str')
            def __repr__(self): return forbidden('value_repr')

        class HostileString(str):
            def __str__(self): return forbidden('string_str')
            def __repr__(self): return forbidden('string_repr')
            def __len__(self): return forbidden('string_len')
            def __eq__(self,other): return forbidden('string_eq')

        class UnprintableError(ToolError):
            def __str__(self): return forbidden('exception_str')
            def __repr__(self): return forbidden('exception_repr')

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base/'work'; root.mkdir()
            keep = root/'keep.txt'; keep.write_text('preserve unknown partial effect')
            database = base/'state.sqlite'; task = 'a'*32
            state = {'id':task,'turn_id':'b'*32,'phase':'needs_confirmation','created_at':1,
                     'workspace':str(root.resolve()),'outcome_unresolved':True,'limits':{},'metrics':{},
                     'pending':{'status':'unknown','intent':{'name':'run_command','policy':'unsafe'}},
                     'messages':[{'role':'user','content':'PRIVATE_SYNTHETIC_HISTORY'}]}
            with Store(database) as store:
                store.save(state,'created')

            def rows():
                with Store.open_readonly(database) as store:
                    return {table:store.db.execute('SELECT * FROM '+table).fetchall()
                            for table in ('tasks','turns','events','stop_requests')}

            before = rows()
            cases = [('known','command_runtime_not_allowed','command_runtime_not_allowed'),
                     ('object',HostileValue(),'tool_error'),
                     ('string_subclass',HostileString('command_runtime_changed'),'tool_error')]
            for command in ('resume','approval'):
                target = 'agent_lab.coding_tools.CodingTools' if command=='resume' else 'agent_lab.__main__.Tools'
                for label,value,expected in cases:
                    out,err = io.StringIO(),io.StringIO()
                    with self.subTest(command=command,label=label), \
                         patch(target,side_effect=UnprintableError(value)), \
                         patch.object(cli,'OpenAIModel',side_effect=AssertionError('no model')), \
                         patch.object(Store,'save',side_effect=AssertionError('no new checkpoint')), \
                         patch.object(Engine,'run',side_effect=AssertionError('no resume')), \
                         patch.object(Engine,'abort',side_effect=AssertionError('no effect decision')), \
                         redirect_stdout(out),redirect_stderr(err):
                        code = cli.main(['--db',str(database),command,task,'--workspace',str(root)])
                    self.assertEqual(code,2)
                    self.assertEqual(out.getvalue(),'')
                    diagnostic = json.loads(err.getvalue())
                    self.assertEqual(set(diagnostic),{'error','hint'})
                    self.assertEqual(diagnostic['error'],expected)
                    self.assertLess(len(err.getvalue()),512)
                    self.assertNotIn('PRIVATE',err.getvalue())
                    self.assertNotIn(str(base),err.getvalue())
                    self.assertNotIn('Traceback',err.getvalue())
                    self.assertEqual(rows(),before)
                    self.assertEqual(keep.read_text(),'preserve unknown partial effect')
            self.assertEqual(touched,[])
