import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from scripts.evaluate_acceptance import run_case
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final


class ProtectedRunnerTests(unittest.TestCase):
    def test_patch_task_cannot_claim_interface_coverage_from_failed_tool_or_text(self):
        from scripts.evaluate_patch import grade_patch_trial
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('money.py','parsing.py'): (root/name).write_text('# fixture\n')
            result={'task_success':True,'trace':[]}
            self.assertFalse(grade_patch_trial(result,root)['task_success'])
            result['trace']=[{'kind':'plan_updated','ok':True},{'kind':'tool_finished','tool':'apply_patch_text','ok':False}]
            self.assertFalse(grade_patch_trial(result,root)['task_success'])
            result['trace'][-1]['ok']=True
            self.assertTrue(grade_patch_trial(result,root)['task_success'])
            (root/'legacy_round.py').write_text('still present')
            self.assertFalse(grade_patch_trial(result,root)['task_success'])

    def test_generic_runner_enforces_host_read_only_scope_before_candidate_can_edit_tests(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);suite=base/'suite';root=suite/'project';(root/'tests').mkdir(parents=True)
            (root/'calc.py').write_text('def score():\n return 0\n')
            text='import unittest\nfrom calc import score\nclass T(unittest.TestCase):\n def test_score(self): self.assertEqual(score(),1)\n'
            (root/'tests/test_public.py').write_text(text)
            case={'id':'protected','workspace_dir':'project','task':'Fix calc without editing protected files.',
                  'immutable_files':['tests/test_public.py'],'oracle':{'module':'calc','function':'score','inputs':[[]],'expected':[1]}}
            model=ScriptedModel([tool('write_file',{'path':'tests/test_public.py','content':''}),final('All fixed')])
            with contextlib.redirect_stdout(io.StringIO()):
                result=run_case(suite,case,base/'trial',model,read_only_paths=['tests/test_public.py'])
            self.assertFalse(result['task_success'])
            self.assertTrue(result['immutable_files_unchanged'])
            self.assertEqual((base/'trial/project/tests/test_public.py').read_text(),text)
            with Store(base/'trial/tasks.sqlite') as store:
                state=store.load(result['id'])
                self.assertEqual(state['read_only_policy']['read_only_paths'],['tests/test_public.py'])
