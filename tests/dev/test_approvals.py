"""Approval tests exercise durable engine state, not just policy helper returns."""
import json
from pathlib import Path
import tempfile
import unittest
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool as call, final


class CommandTools(Tools):
    def prepare(self, name, args, timeout=None):
        return {'name':'run_command','args':args,'policy':'unsafe','command_digest':'a'*64}
    def execute(self, intent, timeout):
        (self.root/'effect.txt').write_text('executed')
        return {'ok':True,'result':{'exit_code':0}}


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'project'; self.root.mkdir()
        self.db = Path(self.tmp.name)/'tasks.sqlite'
        self.model = ScriptedModel([call('run_command', {'argv':['echo','ok'],'cwd':'.'}), final('done')])
        self.store = Store(self.db); self.addCleanup(self.store.close)
        self.engine = Engine(self.store, CommandTools(self.root), self.model)

    def test_approval_waits_without_effect_or_extra_model_call_and_survives_restart(self):
        tid = self.engine.create('Run requested command', Limits(max_seconds=60))
        state = self.engine.run(tid)
        self.assertEqual(state['phase'], 'awaiting_approval')
        self.assertFalse((self.root/'effect.txt').exists())
        self.assertEqual(state['metrics']['model_calls'], 1)
        approval = state['pending']['approval']
        with Store(self.db) as reopened:
            approver = Engine(reopened, CommandTools(self.root), None)
            approver.decide_approval(tid, approval['request_id'], approval['digest'], True)
        finished = self.engine.run(tid)
        self.assertEqual(finished['phase'], 'completed')
        self.assertEqual((self.root/'effect.txt').read_text(), 'executed')
        self.assertEqual(finished['metrics']['tool_attempts'], 1)

    def test_wrong_identity_cannot_approve_and_denial_becomes_tool_feedback(self):
        tid = self.engine.create('Run requested command')
        state = self.engine.run(tid)
        self.assertEqual(state['phase'], 'awaiting_approval')
        approval = state['pending']['approval']
        with self.assertRaises(ValueError):
            self.engine.decide_approval(tid, approval['request_id'], 'b'*64, True)
        self.assertFalse((self.root/'effect.txt').exists())
        self.engine.decide_approval(tid, approval['request_id'], approval['digest'], False)
        state = self.store.load(tid)
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(json.loads(state['messages'][-1]['content'])['error'], 'approval_denied')
        self.assertFalse((self.root/'effect.txt').exists())
        self.assertEqual(self.engine.run(tid)['phase'], 'completed')

    def test_changed_command_after_approval_is_not_executed(self):
        tid = self.engine.create('Run requested command')
        state = self.engine.run(tid)
        self.assertEqual(state['phase'], 'awaiting_approval')
        approval = state['pending']['approval']
        self.engine.decide_approval(tid, approval['request_id'], approval['digest'], True)
        state = self.store.load(tid)
        state['pending']['intent']['command_digest'] = 'b'*64
        self.store.save(state, 'test_corruption')
        state = self.engine.run(tid)
        self.assertFalse((self.root/'effect.txt').exists())
        self.assertNotEqual(state['phase'], 'completed')

    def test_cli_inspection_and_decision_need_no_model_credentials(self):
        import contextlib, io
        from agent_lab.__main__ import main
        tid = self.engine.create('Run requested command')
        state = self.engine.run(tid)
        approval = state['pending']['approval']
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--db', str(self.db), 'approval', tid, '--workspace', str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['action']['argv'], ['echo','ok'])
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['--db', str(self.db), 'approve', tid, approval['request_id'], '--digest', approval['digest'], '--workspace', str(self.root)])
        self.assertEqual(code, 0)
        self.assertFalse((self.root/'effect.txt').exists())
        self.assertEqual(self.store.load(tid)['phase'], 'tool_pending')

    def test_crash_after_durable_denial_leaves_runnable_tool_feedback(self):
        from unittest.mock import patch
        tid = self.engine.create('Run requested command')
        state = self.engine.run(tid); approval = state['pending']['approval']
        save = self.store.save
        def crash_after_commit(snapshot, kind, meta=None, **kwargs):
            save(snapshot,kind,meta,**kwargs)
            if kind == 'approval_decided':
                raise RuntimeError('simulated process exit after commit')
        with patch.object(self.store,'save',side_effect=crash_after_commit):
            with self.assertRaises(RuntimeError):
                self.engine.decide_approval(tid,approval['request_id'],approval['digest'],False)
        recovered = self.engine.run(tid)
        self.assertEqual(recovered['phase'],'completed')
        self.assertFalse((self.root/'effect.txt').exists())
        self.assertEqual(json.loads([m for m in recovered['messages'] if m['role']=='tool'][-1]['content'])['error'],'approval_denied')

    def test_identical_decision_replay_is_idempotent_conflict_rejected(self):
        tid = self.engine.create('Run requested command')
        state = self.engine.run(tid); approval = state['pending']['approval']
        first = self.engine.decide_approval(tid,approval['request_id'],approval['digest'],False)
        again = self.engine.decide_approval(tid,approval['request_id'],approval['digest'],False)
        self.assertEqual(first,again)
        with self.assertRaises(ValueError):
            self.engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
        self.assertEqual(len([m for m in again['messages'] if m['role']=='tool']),1)
