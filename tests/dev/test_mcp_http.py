"""Actual loopback HTTP/SSE transport contracts, not LLM/remote certification."""
import os
import time
import unittest
from unittest.mock import patch

from agent_lab.mcp import MCPError
try:
    from agent_lab.mcp_http import MCPHTTPClient
except ImportError:
    MCPHTTPClient=None
from tests.fixtures.mcp_http_server import serving


class MCPHTTPTests(unittest.TestCase):
    def client(self,server,**kwargs):
        self.assertIsNotNone(MCPHTTPClient,'HTTP transport not implemented')
        client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=kwargs.pop('timeout',2),**kwargs)
        self.addCleanup(client.close)
        return client
    def ready(self,server,**kwargs):
        client=self.client(server,**kwargs);client.start();client.list_tools();return client

    def test_json_lifecycle_headers_and_session_delete_without_credentials_or_proxy(self):
        with serving() as server:
            with patch.dict(os.environ,{'HTTP_PROXY':'http://127.0.0.1:1','DEEPSEEK_API_KEY':'synthetic-never-forward'}):
                with self.client(server) as client:
                    self.assertEqual(client.list_tools()[0]['name'],'echo')
                    self.assertEqual(client.call_tool('echo',{'value':7})['structuredContent'],{'value':7})
            records=server.records
            self.assertEqual([r.get('body',{}).get('method',r.get('method')) for r in records],
                ['initialize','notifications/initialized','tools/list','tools/call','DELETE'])
            for index,r in enumerate(records):
                headers={k.lower():v for k,v in r['headers'].items()}
                self.assertNotIn('authorization',headers);self.assertNotIn('cookie',headers)
                self.assertNotIn('synthetic-never-forward',str(headers))
                if index:
                    self.assertEqual(headers['mcp-session-id'],'synthetic-session')
                    self.assertEqual(headers['mcp-protocol-version'],'2025-11-25')
                else: self.assertNotIn('mcp-session-id',headers)

    def test_sse_notifications_ping_and_both_line_endings(self):
        for mode in ('sse','sse_cr'):
            with self.subTest(mode=mode),serving() as server:
                client=self.ready(server);server.mode=mode
                self.assertEqual(client.call_tool('echo',{'text':'unicode: 你'})['structuredContent'],{'text':'unicode: 你'})
                replies=[r['body'] for r in server.records if 'body' in r and 'method' not in r['body']]
                self.assertEqual(replies,[{'jsonrpc':'2.0','id':'server-ping','result':{}}])
                client.close()

    def test_negotiated_older_version_pagination_and_optional_sessions(self):
        for stateless in (False,True):
            with self.subTest(stateless=stateless),serving() as server:
                server.version='2025-06-18';server.stateless=stateless;server.paginated=True
                client=self.client(server);client.start()
                self.assertEqual([t['name'] for t in client.list_tools()],['echo','inspect'])
                self.assertEqual(client.call_tool('inspect',{})['structuredContent'],{})
                client.close()
                for record in server.records[1:]:
                    headers={k.lower():v for k,v in record['headers'].items()}
                    self.assertEqual(headers['mcp-protocol-version'],'2025-06-18')
                    self.assertEqual('mcp-session-id' in headers,not stateless)
                self.assertEqual(any(r.get('method')=='DELETE' for r in server.records),not stateless)
        with serving() as server:
            server.version='unsupported-public-fixture'
            client=self.client(server)
            with self.assertRaises(MCPError) as caught:client.start()
            self.assertEqual(caught.exception.code,'unsupported_protocol_version')
            self.assertEqual(len(server.records),1)

    def test_matching_sse_result_finishes_without_waiting_for_stream_disconnect(self):
        with serving() as server:
            client=self.ready(server);server.mode='persistent_sse';started=time.monotonic()
            self.assertTrue(client.call_tool('echo',{})['content'])
            self.assertLess(time.monotonic()-started,1)
            self.assertTrue(server.result_ready.is_set());self.assertFalse(server.release.is_set())
            client.close()

    def test_invalid_final_framing_and_http_status_do_not_replay(self):
        for mode,code in [('wrong_id','unexpected_response'),('duplicate_key','invalid_jsonrpc'),
                          ('partial_sse','incomplete_sse'),('changed_session','invalid_session'),
                          ('redirect','redirect_refused'),('expired','session_expired'),('accepted','unexpected_http_status')]:
            with self.subTest(mode=mode),serving() as server:
                client=self.ready(server);server.mode=mode
                with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
                self.assertEqual(caught.exception.code,code);self.assertTrue(caught.exception.uncertain)
                with self.assertRaises(MCPError):client.call_tool('echo',{})
                self.assertEqual(len(server.effects),1)
                self.assertEqual(sum(r.get('body',{}).get('method')=='initialize' for r in server.records),1)
                self.assertFalse(any(r.get('path','').endswith('/other') for r in server.records))

    def test_output_limit_and_timeout_after_effect_remain_unknown(self):
        for mode,code in [('overflow','output_limit'),('effect_timeout','request_timeout')]:
            with self.subTest(mode=mode),serving() as server:
                client=self.ready(server,timeout=.2,max_bytes=4096);server.mode=mode;started=time.monotonic()
                with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
                self.assertEqual(caught.exception.code,code);self.assertTrue(caught.exception.uncertain)
                self.assertLess(time.monotonic()-started,1)
                with self.assertRaises(MCPError):client.start()
                self.assertEqual(len(server.effects),1)

    def test_url_authority_is_explicit_and_invalid_limits_launch_nothing(self):
        self.assertIsNotNone(MCPHTTPClient)
        for url in ('http://example.com/mcp','http://localhost/mcp','http://127.0.0.1/mcp',
                    'https://user:secret@example.com/mcp','https://example.com/mcp?token=x',
                    'https://example.com/mcp#x','https://example.com/mcp\r\nX:x','file:///tmp/mcp'):
            with self.subTest(url=url),self.assertRaises(ValueError):MCPHTTPClient(url)
        for url in ('http://127.0.0.1/mcp','http://[::1]/mcp','https://example.com/mcp'):
            client=MCPHTTPClient(url,allow_loopback_http=True);client.close()
        for opts in ({'timeout':True},{'timeout':float('inf')},{'max_bytes':True},{'max_pages':0},
                     {'max_tools':0},{'allow_loopback_http':1}):
            with self.subTest(opts=opts),self.assertRaises(ValueError):MCPHTTPClient('https://example.com/mcp',**opts)


if __name__=='__main__':unittest.main()
