"""Public local-only protocol stimulus with strict auth/path/request limits."""
import http.client
import json
import tempfile
import threading
from pathlib import Path
import unittest

from scripts.reference_cli import FIXTURE_TOKEN


class ReferenceFixtureTests(unittest.TestCase):
    def test_request_admission_rejects_wrong_auth_path_and_excess_without_recording_body(self):
        from scripts.reference_protocol import serving
        with serving(max_requests=1) as server:
            def post(path='/v1/responses',token=FIXTURE_TOKEN):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                try:
                    c.request('POST',path,json.dumps({'input':[]}),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    response=c.getresponse();return response.status,response.read()
                finally:c.close()
            self.assertEqual(post(token='wrong-synthetic-token')[0],401)
            self.assertEqual(post('/wrong')[0],404);self.assertEqual(server.requests,[])
            self.assertEqual(post()[0],200);self.assertEqual(len(server.requests),1)
            self.assertEqual(post()[0],429);self.assertEqual(len(server.requests),1)

    def test_complete_frames_normalize_in_our_existing_responses_adapter(self):
        from scripts.reference_protocol import response_events
        from agent_lab.protocols import consume_sse
        events=response_events([{'id':'msg_fixture','type':'message','role':'assistant','status':'completed',
            'content':[{'type':'output_text','text':'owned final','annotations':[]}]}])
        result=consume_sse('responses',b''.join(events).decode().splitlines(keepends=True))
        self.assertEqual(result['message']['content'],'owned final')
        self.assertEqual(result['usage'],{'input_tokens':12,'output_tokens':3})

    def test_completion_gate_delivers_item_done_before_completion_boundary(self):
        from scripts.reference_protocol import serving
        with serving(completion_gate=True) as server:
            received=[]
            def request():
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
                try:
                    c.request('POST','/v1/responses','{}',{'Authorization':'Bearer '+FIXTURE_TOKEN})
                    response=c.getresponse();received.append(response.read())
                finally:c.close()
            thread=threading.Thread(target=request);thread.start()
            try:
                self.assertTrue(server.item_done.wait(2));self.assertTrue(thread.is_alive());self.assertEqual(received,[])
            finally:server.release.set();thread.join(4)
            self.assertEqual(len(received),1);self.assertIn(b'response.output_item.done',received[0]);self.assertIn(b'response.completed',received[0])

    def test_reference_command_keeps_normal_sandbox_and_uses_only_loopback_synthetic_provider(self):
        from scripts.reference_cli import reference_command
        with tempfile.TemporaryDirectory(prefix='agentlab-reference-',dir='/private/tmp') as directory:
            base=Path(directory);(base/'work').mkdir();(base/'private').write_text('owned')
            command=reference_command(base,12345,'owned protocol stimulus')
            self.assertEqual(command[:2],['/usr/bin/sandbox-exec','-p'])
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
            self.assertIn('workspace-write',command)
            self.assertIn('--ignore-user-config',command);self.assertIn('--ephemeral',command)
            self.assertIn('model_providers.reference_fixture.base_url="http://127.0.0.1:12345/v1"',command)
            self.assertIn('cli_auth_credentials_store="ephemeral"',command)
            self.assertIn('model_providers.reference_fixture.requires_openai_auth=false',command)
            self.assertNotIn(FIXTURE_TOKEN,' '.join(command))


if __name__=='__main__':unittest.main()
