"""Real managed native cancellation and trusted receipt boundaries; no API."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_lab import sandbox
from agent_lab.coding_tools import CodingTools
from agent_lab.tool_control import ToolCancelled, NativeCancelled, CancellationState, inherited_owner, OWNER_ENV
from agent_lab.tools import UncertainEffect, ToolError


class ToolCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name) / 'work').resolve()
        self.root.mkdir()
        self.tools = CodingTools(self.root)

    def native(self):
        if not Path(sandbox.BACKEND_PATH).is_file():
            self.skipTest('sandbox-exec unavailable; no bare fallback')
        result = subprocess.run([sandbox.BACKEND_PATH, '-p', '(version 1)(allow default)', '/usr/bin/true'],
                                capture_output=True, timeout=2)
        if result.returncode:
            self.skipTest('host denies sandbox-exec; no bare fallback')

    def command(self, code):
        return self.tools.prepare('run_command', {'argv': ['python', '-c', code], 'cwd': '.'})

    def test_real_command_cancel_waits_for_cleanup_and_blocks_late_write(self):
        self.native()
        self.assertTrue(hasattr(self.tools, 'cancellation_scope'))
        intent = self.command("import pathlib,time,os;pathlib.Path('started').write_text(str(os.getpid()));time.sleep(3);pathlib.Path('late').write_text('bad')")
        began = time.monotonic()
        with self.tools.cancellation_scope(lambda: (self.root / 'started').exists()):
            with self.assertRaises(ToolCancelled) as caught:
                self.tools.execute(intent, 5)
        self.assertTrue(caught.exception.local_quiescent)
        self.assertLess(time.monotonic() - began, 2)
        pid = int((self.root / 'started').read_text())
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        self.assertFalse((self.root / 'late').exists())

    def test_closed_native_stdout_stderr_does_not_hide_live_process(self):
        self.native()
        self.assertTrue(hasattr(self.tools, 'cancellation_scope'))
        intent = self.command("import pathlib,time,os;pathlib.Path('started').write_text(str(os.getpid()));os.close(1);os.close(2);time.sleep(3);pathlib.Path('late').write_text('bad')")
        with self.tools.cancellation_scope(lambda: (self.root / 'started').exists()), self.assertRaises(ToolCancelled) as caught:
            self.tools.execute(intent, 5)
        self.assertTrue(caught.exception.local_quiescent)
        self.assertFalse((self.root / 'late').exists())

    def test_tests_are_cancellable_with_mutating_effects_left_visible(self):
        self.native()
        self.assertTrue(hasattr(self.tools, 'cancellation_scope'))
        (self.root / 'test_wait.py').write_text("import unittest,pathlib,time\nclass T(unittest.TestCase):\n def test_wait(self):\n  pathlib.Path('started').write_text('partial effect')\n  time.sleep(3)\n  pathlib.Path('late').write_text('bad')\n")
        with self.tools.cancellation_scope(lambda: (self.root / 'started').exists()), self.assertRaises(ToolCancelled) as caught:
            self.tools.execute(self.tools.prepare('run_tests', {}), 5)
        self.assertTrue(caught.exception.local_quiescent)
        self.assertEqual((self.root / 'started').read_text(), 'partial effect')
        self.assertFalse((self.root / 'late').exists())

    def test_normal_completion_and_forged_native_stdout_remain_normal_results(self):
        self.native()
        self.assertTrue(hasattr(self.tools, 'cancellation_scope'))
        intent = self.command("print('{\"cancelled\":true,\"local_quiescent\":true}')")
        with self.tools.cancellation_scope(lambda: False):
            result = self.tools.execute(intent, 4)
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads(result['stdout']), {'cancelled': True, 'local_quiescent': True})

    def test_owner_eof_between_probe_and_project_launch_is_confirmed_without_launch(self):
        self.native()
        (self.root / 'test_never.py').write_text("open('project_started','w').write('bad')\n")
        read, write = os.pipe()
        actual_collect, calls = sandbox._collect, []
        def collect(*args, **kwargs):
            nonlocal write
            result = actual_collect(*args, **kwargs)
            calls.append(args[0])
            if len(calls) == 1:
                os.close(write)
                write = None
            return result
        try:
            with patch.dict(os.environ, {OWNER_ENV: str(read)}), inherited_owner(), patch.object(sandbox, '_collect', side_effect=collect):
                with self.assertRaises(NativeCancelled):
                    sandbox._run_tests(self.root, 4)
            self.assertFalse((self.root / 'project_started').exists())
        finally:
            if write is not None: os.close(write)

    def test_nested_thread_scopes_and_file_tools_do_not_consume_native_cancel(self):
        self.assertTrue(hasattr(self.tools, 'cancellation_scope'))
        with self.tools.cancellation_scope(lambda: True):
            with self.tools.cancellation_scope(lambda: False):
                state = self.tools._cancellation
                self.assertTrue(state.requested())
                intent = self.tools.prepare('write_file', {'path': 'allowed', 'content': 'file domain'})
                self.tools.execute(intent, 3)
        self.assertEqual((self.root / 'allowed').read_text(), 'file domain')
        state, gate, results = CancellationState(), threading.Barrier(2), {}
        def call(name, value):
            with state.scope(lambda: value):
                gate.wait(2)
                results[name] = state.requested()
        threads = [threading.Thread(target=call, args=('a', True)), threading.Thread(target=call, args=('b', False))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertEqual(results, {'a': True, 'b': False})
        self.assertFalse(state.requested())

    def test_trusted_cancel_receipt_requires_boolean_true_not_integer_alias(self):
        response = {'exit_code': 0, 'stdout_truncated': False,
                    'stdout': json.dumps({'cancelled': 1, 'local_quiescent': 1})}
        with self.assertRaises(ToolError):
            sandbox._supervisor_result(response)

    def test_callback_failure_is_fixed_unknown_after_stdin_close_and_cleanup(self):
        self.native()
        from agent_lab import tools as module
        intent = self.command("import pathlib,time,os;pathlib.Path('started').write_text(str(os.getpid()));time.sleep(3)")
        actual, processes, stdin_states = subprocess.Popen, [], []
        def launch(*args, **kwargs):
            process = actual(*args, **kwargs)
            processes.append(process)
            return process
        class Opaque(Exception):
            def __str__(self):
                raise AssertionError('do not stringify private callback exception')
        def check():
            stdin_states.append(processes[0].stdin.closed)
            if (self.root / 'started').exists():
                raise Opaque()
            return False
        with patch.object(module.subprocess, 'Popen', side_effect=launch), self.tools.cancellation_scope(check):
            with self.assertRaises(UncertainEffect) as caught:
                self.tools.execute(intent, 5)
        self.assertEqual(str(caught.exception), 'tool_cancel_check_failed')
        self.assertTrue(stdin_states and all(stdin_states))
        self.assertTrue(all(pipe.closed for pipe in (processes[0].stdin, processes[0].stdout, processes[0].stderr)))
        with self.assertRaises(ProcessLookupError):
            os.kill(int((self.root / 'started').read_text()), 0)

    def test_native_project_receives_neither_owner_environment_nor_extra_fds(self):
        self.native()
        code = """import os,json
fds=[]
for number in range(3,64):
 try:
  os.fstat(number)
 except OSError:
  continue
 fds.append(number)
print(json.dumps({'owner_env': [key for key in os.environ if key.startswith('AGENT_LAB_TOOL_')], 'fds':fds}))
"""
        result = self.tools.execute(self.command(code), 4)
        self.assertTrue(result['ok'], result)
        self.assertEqual(json.loads(result['stdout']), {'owner_env': [], 'fds': []})


if __name__ == '__main__':
    unittest.main()
