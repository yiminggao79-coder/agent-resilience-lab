"""Actual local MCP resource RPCs with scripted model decisions, no API calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest

from agent_lab.mcp import MCPClient,MCPError
from agent_lab.mcp_http import MCPHTTPClient
from agent_lab.coding_tools import CodingTools
from agent_lab.registry import MCPToolRegistry
from agent_lab.engine import Engine,Limits
from agent_lab.store import Store
from agent_lab.tools import ToolError
from tests.support import ScriptedModel,tool,final
from tests.fixtures.mcp_http_server import serving

SERVER=Path(__file__).resolve().parents[1]/'fixtures/mcp_resource_server.py'


class MCPResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-mcp-resources-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir();self.trusted=self.base/'trusted';self.trusted.mkdir()
        self.server=self.trusted/'server.py';shutil.copyfile(SERVER,self.server)
        self.log=self.trusted/'requests';self.marker=self.trusted/'marker';self.config=self.trusted/'mcp.json'

    def client(self,mode='normal',**options):
        client=MCPClient((sys.executable,'-I','-S',str(self.server),mode,str(self.log),str(self.marker)),self.trusted,**options)
        self.addCleanup(client.close);client.start();return client

    def registry(self,mode='normal',**options):
        self.config.write_text(json.dumps({'servers':[{'name':'owned','command':[sys.executable,'-I','-S',str(self.server),mode,str(self.log),str(self.marker)],'cwd':str(self.trusted),**options}]}))
        registry=MCPToolRegistry(CodingTools(self.root),self.config);registry.__enter__();self.addCleanup(registry.close);return registry

    def requests(self):return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_actual_resource_only_pagination_templates_and_multiple_contents(self):
        client=self.client('multi')
        page=client.list_resources();self.assertEqual(page['nextCursor'],'page2')
        self.assertEqual(client.list_resources(page['nextCursor'])['resources'][0]['uri'],'owned:///binary')
        self.assertEqual(client.list_resource_templates()['resourceTemplates'][0]['uriTemplate'],'owned:///reference/{name}')
        result=client.read_resource('owned:///reference')
        self.assertEqual(len(result['contents']),2);self.assertEqual(result['contents'][1]['uri'],'owned:///related')
        self.assertFalse(any(r.get('method')=='tools/list' for r in self.requests()))

    def test_missing_and_malformed_capabilities_are_not_resource_support(self):
        client=self.client('no_resources')
        with self.assertRaisesRegex(MCPError,'resources_not_supported'):client.list_resources()
        self.assertFalse(any(r.get('method','').startswith('resources/') for r in self.requests()))
        with self.assertRaisesRegex(MCPError,'invalid_initialize_result'):self.client('bad_capability')

    def test_bad_catalog_cursor_and_entry_limit_close_without_truncated_success(self):
        for mode in ('bad_page','bad_cursor','many'):
            with self.subTest(mode=mode):
                client=self.client(mode,max_tools=2)
                with self.assertRaises(MCPError):client.list_resources()
                self.assertEqual(client._state,'closed')

    def test_invalid_resource_bodies_and_binary_encoding_are_rejected(self):
        for mode in ('bad_blob','ambiguous','bad_text','bad_uri'):
            with self.subTest(mode=mode):
                client=self.client(mode)
                with self.assertRaisesRegex(MCPError,'invalid_resource_result'):client.read_resource('owned:///reference')
                self.assertEqual(client._state,'closed')
        self.assertEqual(self.client('binary').read_resource('owned:///binary')['contents'][0]['blob'],'YWJjAA==')

    def test_local_bad_uri_and_cursor_fail_before_dispatch(self):
        client=self.client();before=len(self.requests())
        for value in ('','relative','owned:///newline\n',False,'x'*4097):
            with self.subTest(uri=value),self.assertRaises(MCPError):client.read_resource(value)
        for value in ('',False,1,'x'*4097):
            with self.subTest(cursor=value),self.assertRaises(MCPError):client.list_resources(value)
        self.assertEqual(len(self.requests()),before);self.assertEqual(client._state,'ready')

    def test_local_wire_size_rejection_does_not_claim_dispatched_uncertainty(self):
        client=self.client(max_bytes=512);before=len(self.requests())
        with self.assertRaisesRegex(MCPError,'request_limit') as caught:client.read_resource('owned:///'+('x'*450))
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(len(self.requests()),before)
        self.assertIn('contents',client.read_resource('owned:///reference'))

    def test_registry_known_oversize_before_dispatch_is_correctable_feedback(self):
        registry=self.registry(max_bytes=512)
        with Store(self.base/'state.sqlite') as store:
            model=ScriptedModel([tool('read_mcp_resource',{'server':'owned','uri':'owned:///'+('x'*450)},'oversize'),
                tool('read_mcp_resource',{'server':'owned','uri':'owned:///reference'},'corrected'),final()])
            engine=Engine(store,registry,model);tid=engine.create('Read context');engine.run(tid)
            approval=engine.approval_request(tid);engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            state=engine.run(tid)
            self.assertEqual(state['phase'],'awaiting_approval')
            feedback=json.loads(model.seen[-1][-1]['content'])
            self.assertFalse(feedback['ok']);self.assertEqual(feedback['error'],'mcp_request_limit')
            self.assertFalse(any(r.get('method')=='resources/read' for r in self.requests()))
            approval=engine.approval_request(tid);engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            state=engine.run(tid);self.assertEqual(state['phase'],'completed')
            self.assertTrue(json.loads(model.seen[-1][-1]['content'])['ok'])
            self.assertEqual(sum(r.get('method')=='resources/read' for r in self.requests()),1)

    def test_received_page_canonical_expansion_is_not_classified_as_unsent(self):
        from tests.fixtures.mcp_resource_server import resource_result
        packet={'jsonrpc':'2.0','id':2,'result':resource_result('resources/list',{},'expanded_page')}
        raw=json.dumps(packet,separators=(',',':')).replace('1e-07','1e-7')
        maximum=len(raw.encode())+2
        client=self.client('expanded_page',max_bytes=maximum)
        with self.assertRaisesRegex(MCPError,'request_limit') as caught:client.list_resources()
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(sum(r.get('method')=='resources/list' for r in self.requests()),1)

    def test_engine_resource_approval_feedback_binary_projection_and_privacy(self):
        registry=self.registry('binary')
        with Store(self.base/'state.sqlite') as store:
            model=ScriptedModel([tool('read_mcp_resource',{'server':'owned','uri':'owned:///binary'},'resource'),final()])
            engine=Engine(store,registry,model);tid=engine.create('Read explicitly trusted context')
            state=engine.run(tid);self.assertEqual(state['phase'],'awaiting_approval')
            self.assertFalse(any(r.get('method')=='resources/read' for r in self.requests()))
            approval=engine.approval_request(tid);self.assertEqual(approval['action']['method'],'resources/read')
            engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            state=engine.run(tid);self.assertEqual(state['phase'],'completed')
            feedback=json.loads(model.seen[-1][-1]['content']);block=feedback['contents'][0]
            self.assertEqual(block['blob_bytes'],4);self.assertEqual(block['blob_sha256'],hashlib.sha256(b'abc\0').hexdigest())
            self.assertNotIn('blob',block);self.assertNotIn('YWJjAA==',json.dumps(store.events(tid)))
            changed=copy.deepcopy(registry.prepare('read_mcp_resource',{'server':'owned','uri':'owned:///binary'}));changed['args']['uri']='owned:///other'
            with self.assertRaises(ToolError):registry.execute(changed,2)
            with self.assertRaises(ToolError):registry.prepare('list_mcp_resources',{'server':'unknown'})

    def test_tools_only_manifest_and_schemas_do_not_gain_resource_authority(self):
        registry=self.registry('no_resources')
        self.assertEqual(set(registry.manifest),{'version','config_path','config_sha256','builtin_schema_sha256','servers','tools'})
        self.assertFalse(any(schema['function']['name'] in ('list_mcp_resources','read_mcp_resource','list_mcp_resource_templates') for schema in registry.schemas()))
        with self.assertRaises(ToolError):registry.prepare('read_mcp_resource',{'server':'owned','uri':'owned:///reference'})
        self.assertFalse(any(r.get('method','').startswith('resources/') for r in self.requests()))

    def test_external_result_cannot_override_host_envelope(self):
        registry=self.registry('envelope_override')
        intent=registry.prepare('read_mcp_resource',{'server':'owned','uri':'owned:///reference'})
        result=registry.execute(intent,2)
        self.assertTrue(result['ok']);self.assertEqual(result['server'],'owned')
        self.assertEqual(result['origin']['type'],'mcp_stdio')

    def test_read_timeout_and_new_registry_resume_do_not_repeat_external_request(self):
        registry=self.registry('die')
        with Store(self.base/'state.sqlite') as store:
            engine=Engine(store,registry,ScriptedModel([tool('read_mcp_resource',{'server':'owned','uri':'owned:///reference'})]))
            tid=engine.create('Read context',Limits(tool_timeout=.3));engine.run(tid);approval=engine.approval_request(tid)
            engine.decide_approval(tid,approval['request_id'],approval['digest'],True)
            state=engine.run(tid);self.assertEqual(state['phase'],'needs_confirmation');self.assertIsNotNone(state['pending'])
            registry.close();replacement=self.registry('die')
            state=Engine(store,replacement,ScriptedModel([])).run(tid)
            self.assertEqual(state['phase'],'needs_confirmation');self.assertEqual(self.marker.read_text(),'resource dispatched\n')
            self.assertEqual(sum(r.get('method')=='resources/read' for r in self.requests()),1)

    def test_resource_wait_honors_active_cancel_and_never_claims_quiescence(self):
        client=self.client('slow',timeout=3);stop=threading.Event()
        thread=threading.Thread(target=lambda:(time.sleep(.15),stop.set()));thread.start()
        try:
            with client.cancellation_scope(stop.is_set),self.assertRaises(MCPError) as caught:client.read_resource('owned:///reference')
            self.assertEqual(caught.exception.code,'request_cancelled');self.assertTrue(caught.exception.uncertain)
            self.assertEqual(client._state,'closed');self.assertEqual(self.marker.read_text(),'resource dispatched\n')
        finally:thread.join(1)

    def test_http_transport_reuses_resource_protocol_and_sse_without_new_credentials(self):
        with serving() as server:
            server.resources=True;server.mode='sse'
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=2);self.addCleanup(client.close);client.start()
            page=client.list_resources();self.assertEqual(page['nextCursor'],'page2')
            self.assertEqual(client.list_resources(page['nextCursor'])['resources'][0]['uri'],'owned:///binary')
            self.assertIn('resourceTemplates',client.list_resource_templates())
            self.assertIn('offset is 7',client.read_resource('owned:///reference')['contents'][0]['text'])
            client.close()
            self.assertFalse(any('authorization' in {k.lower() for k in r['headers']} for r in server.records))


if __name__=='__main__':unittest.main()
