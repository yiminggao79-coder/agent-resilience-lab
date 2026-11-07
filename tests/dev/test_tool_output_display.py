"""Private terminal rendering and explicit CLI opt-in, no paid provider."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab import __main__ as cli
from tests.support import ScriptedModel,tool,final


class ToolOutputDisplayTests(unittest.TestCase):
    def renderer(self, stream):
        from agent_lab.tool_display import ToolOutputDisplay
        return ToolOutputDisplay(stream)

    def delta(self, data, stream='stdout', output_id='owned-output'):
        return {'type':'tool_output_delta','output_id':output_id,'tool':'run_tests','stream':stream,'data':data}

    def end(self, incomplete=False, output_id='owned-output'):
        return {'type':'tool_output_end','output_id':output_id,'tool':'run_tests','incomplete':incomplete}

    def test_incremental_unicode_and_terminal_controls_are_rendered_visibly(self):
        target=io.StringIO();render=self.renderer(target)
        raw='你好🙂'.encode()
        for value in raw:render(self.delta(bytes([value])))
        render(self.delta(b'\x1b[2J\r\x07\x7f'))
        render(self.delta('\u202ehidden'.encode(),'stderr'))
        render(self.end())
        text=target.getvalue()
        self.assertIn('你好🙂',text)
        self.assertNotIn('\ufffd',text)
        for control in ('\x1b','\r','\x07','\x7f','\u202e'):self.assertNotIn(control,text)
        self.assertIn('\\x1b[2J\\r\\x07\\x7f',text)
        self.assertIn('\\u202e',text)
        self.assertIn('run_tests stderr',text)
        self.assertNotIn('success',text.lower())

    def test_end_flushes_invalid_bytes_and_reports_incomplete_not_completion(self):
        target=io.StringIO();render=self.renderer(target)
        render(self.delta(b'\xf0\x9f'));render(self.end(True))
        self.assertIn('\ufffd',target.getvalue())
        self.assertIn('incomplete',target.getvalue())
        self.assertNotIn('completed',target.getvalue())
        render(self.delta('新'.encode(),output_id='next'));render(self.end(output_id='next'))
        self.assertIn('新',target.getvalue())

    def test_complete_display_without_final_newline_leaves_next_prompt_on_new_line(self):
        target=io.StringIO();render=self.renderer(target)
        render(self.delta(b'no trailing newline'));render(self.end())
        self.assertTrue(target.getvalue().endswith('\n'))
        self.assertIn('no trailing newline',target.getvalue())

    def test_cli_opt_in_stderr_metadata_stdout_remain_separate(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)/'work';root.mkdir()
                (root/'test_print.py').write_text("import unittest\nclass T(unittest.TestCase):\n def test_it(self):\n  print('PRIVATE_CLI_TOOL_TEXT')\n")
                out,err=io.StringIO(),io.StringIO()
                argv=['--db',str(Path(directory)/'state.sqlite'),'run','--workspace',str(root),
                      '--allow-api','--api-key-env','LAB_TOOL_DISPLAY_TEST_KEY','--events','--max-seconds','10','Run tests']
                if enabled:argv.insert(-1,'--stream-tools')
                model=ScriptedModel([tool('run_tests',{},'c1'),final('done')])
                model.api_key_env='LAB_TOOL_DISPLAY_TEST_KEY'
                with patch.dict(os.environ,{'LAB_TOOL_DISPLAY_TEST_KEY':'synthetic-local-only'}),patch.object(cli,'OpenAIModel',return_value=model),redirect_stdout(out),redirect_stderr(err):
                    code=cli.main(argv)
                self.assertEqual(code,0,err.getvalue())
                self.assertNotIn('PRIVATE_CLI_TOOL_TEXT',out.getvalue())
                self.assertEqual('PRIVATE_CLI_TOOL_TEXT' in err.getvalue(),enabled)
                # Existing CLI emits JSONL events followed by one pretty JSON
                # status document; output streaming must preserve that format.
                remaining=out.getvalue().lstrip();values=[]
                while remaining:
                    value,end=json.JSONDecoder().raw_decode(remaining)
                    values.append(value);remaining=remaining[end:].lstrip()
                self.assertTrue(any('event' in value for value in values))
                self.assertEqual(values[-1]['phase'],'completed')
