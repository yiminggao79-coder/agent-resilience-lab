"""Independent parent boundary checks; synthetic worker, real anonymous pipes."""
from contextvars import ContextVar
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from agent_lab import tools as runtime
from agent_lab.tool_control import OWNER_ENV
from agent_lab.tool_output import OUTPUT_ENV


WIRE = (b'{"v":1,"t":"d","s":"stdout","q":0,"d":"cHJpdmF0ZQ=="}\n'
        b'{"v":1,"t":"e","q":1,"o":[7,7,0],"e":[0,0,0]}\n')


class ToolOutputReviewTests(unittest.TestCase):
    def worker(self, callback, *, running=False):
        tools = runtime.Tools.__new__(runtime.Tools)
        tools.on_output = callback
        tools._native_job_context = ContextVar('review_job', default={})
        history = []

        class Worker:
            def __init__(self, *args, **kwargs):
                self.stdin, self.stdout, self.stderr = (io.StringIO() for _ in range(3))
                self.output_fd = int(kwargs['env'][OUTPUT_ENV])
                self.owner_fd = int(kwargs['env'][OWNER_ENV])
                self.alive = running
                self.sent = False

            def communicate(self, **kwargs):
                if not self.sent:
                    os.write(self.output_fd, WIRE)
                    self.sent = True
                if self.alive:
                    raise subprocess.TimeoutExpired('synthetic-worker', .05)
                return json.dumps({'result': {'ok': True}}), ''

            def poll(self):
                return None if self.alive else 0

            def kill(self):
                # Owner EOF must precede terminating the trusted worker.
                self_outer.assertEqual(os.read(self.owner_fd, 1), b'')
                history.append('kill_after_owner_eof')
                self.alive = False

            def wait(self):
                history.append('join')
                return 0

        self_outer = self
        proc = Worker.__new__(Worker)

        def spawn(*args, **kwargs):
            Worker.__init__(proc, *args, **kwargs)
            return proc

        return tools, proc, history, patch.object(runtime.subprocess, 'Popen', side_effect=spawn)

    def assert_closed(self, proc):
        self.assertTrue(all(stream.closed for stream in (proc.stdin, proc.stdout, proc.stderr)))
        for descriptor in (proc.output_fd, proc.owner_fd):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_interrupt_in_delta_or_end_closes_owner_and_joins_without_attesting(self):
        for interrupt_at in ('tool_output_delta', 'tool_output_end'):
            with self.subTest(interrupt_at=interrupt_at):
                events = []

                def callback(event):
                    events.append(event['type'])
                    if event['type'] == interrupt_at:
                        raise KeyboardInterrupt()

                tools, proc, history, spawn = self.worker(callback, running=interrupt_at.endswith('delta'))
                with spawn, patch.object(runtime.native_jobs, 'require_quiescent') as proof:
                    with self.assertRaises(KeyboardInterrupt):
                        tools._native_worker('{}', 2, reference={'synthetic': True}, output_tool='run_tests')
                proof.assert_not_called()
                self.assertNotIn('local_quiescent', tools._native_job_context.get())
                self.assertEqual(events.count(interrupt_at), 1)
                self.assertIn('join', history)
                if interrupt_at.endswith('delta'):
                    self.assertEqual(history[0], 'kill_after_owner_eof')
                self.assert_closed(proc)

    def test_callback_exception_disables_display_but_keeps_final_proof_gate(self):
        for proof_fails in (False, True):
            with self.subTest(proof_fails=proof_fails):
                events = []

                def callback(event):
                    events.append(event)
                    raise RuntimeError('private callback failure')

                tools, proc, history, spawn = self.worker(callback)
                failure = runtime.native_jobs.JobStateError('synthetic_unconfirmed') if proof_fails else None
                with spawn, patch.object(runtime.native_jobs, 'require_quiescent', side_effect=failure) as proof:
                    if proof_fails:
                        with self.assertRaises(runtime.native_jobs.JobStateError):
                            tools._native_worker('{}', 2, reference={'synthetic': True}, output_tool='run_tests')
                    else:
                        self.assertEqual(json.loads(tools._native_worker('{}', 2, reference={'synthetic': True},
                                                                        output_tool='run_tests')),
                                         {'result': {'ok': True}})
                proof.assert_called_once()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['data'], b'private')
                self.assertEqual(tools._native_job_context.get().get('local_quiescent'), None if proof_fails else True)
                self.assertIn('join', history)
                self.assert_closed(proc)
