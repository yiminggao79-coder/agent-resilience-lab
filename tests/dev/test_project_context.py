import json
from pathlib import Path
import tempfile
import unittest
from agent_lab.context import build_context, ContextLimit
from agent_lab.instructions import load_project_context


class ContextTests(unittest.TestCase):
    def test_large_tool_results_are_archived_without_changing_full_history(self):
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'Keep the public API unchanged'}]
        for i in range(8):
            messages += [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(i), 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
                         {'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({'ok': True, 'content': 'x'*65536})}]
        view, stats = build_context(messages, [], 32000)
        self.assertGreater(stats['original_bytes'], 500000)
        self.assertLessEqual(stats['retained_bytes'], 32000)
        self.assertEqual(messages[-1]['content'], json.dumps({'ok': True, 'content': 'x'*65536}))
        self.assertIn('Keep the public API unchanged', json.dumps(view))
        self.assertEqual(view[-1]['tool_call_id'], '7')
        # Every retained result still has a preceding provider tool call.
        calls = {c['id'] for m in view for c in m.get('tool_calls', [])}
        self.assertTrue(all(m['tool_call_id'] in calls for m in view if m['role']=='tool'))

    def test_task_and_system_are_never_silently_trimmed(self):
        with self.assertRaises(ContextLimit):
            build_context([{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'task'*1000}], [], 100)


class InstructionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()

    def test_root_agents_and_explicit_skill_are_loaded_with_hashes(self):
        (self.root/'AGENTS.md').write_text('Use unittest. Preserve public API.')
        skill=self.root/'.agents'/'skills'/'testing'
        skill.mkdir(parents=True)
        (skill/'SKILL.md').write_text('---\nname: testing\ndescription: Test edits\n---\nRun relevant tests.')
        text, manifest = load_project_context(self.root, ['testing'])
        self.assertIn('Preserve public API', text)
        self.assertIn('Run relevant tests', text)
        self.assertEqual(len(manifest),2)
        self.assertTrue(all(len(m['sha256'])==64 for m in manifest))

    def test_symlinked_instructions_and_skill_names_cannot_escape(self):
        outside=Path(self.tmp.name)/'outside';outside.write_text('private')
        (self.root/'AGENTS.md').symlink_to(outside)
        with self.assertRaises(ValueError):load_project_context(self.root)
        (self.root/'AGENTS.md').unlink()
        with self.assertRaises(ValueError):load_project_context(self.root,['../escape'])
