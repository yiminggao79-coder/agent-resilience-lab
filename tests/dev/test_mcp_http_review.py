"""Independent HTTP MCP completion/deadline checks; loopback only, no auth."""
import json
import time
import unittest
from unittest.mock import patch

from agent_lab.mcp import MCPError
from agent_lab.mcp_http import MCPHTTPClient
from tests.fixtures import mcp_http_server as fixture


class MCPHTTPReviewTests(unittest.TestCase):
    def test_matching_final_sse_event_returns_before_later_event_in_same_chunk(self):
        client=MCPHTTPClient('http://127.0.0.1:1/mcp',allow_loopback_http=True)
        self.addCleanup(client.close)
        result={'content':[{'type':'text','text':'complete synthetic result'}]}
        packets=({'jsonrpc':'2.0','id':1,'result':result},
                 {'jsonrpc':'2.0','id':'after-final','method':'ping'})
        chunk=b''.join(b'data: '+json.dumps(packet).encode()+b'\n\n' for packet in packets)
        with patch.object(client,'_io',side_effect=MCPError('request_timeout')) as later_post:
            self.assertEqual(client._sse(iter([chunk]),1,time.monotonic()+1),result)
        later_post.assert_not_called()

    def test_slow_http_headers_do_not_extend_total_tool_deadline(self):
        with fixture.serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=2)
            self.addCleanup(client.close)
            client.start(); client.list_tools(); client.timeout=.12
            original=fixture.Handler.send

            def slow(handler,status,body=b'',kind='application/json',headers=None):
                if handler.server is not server or server.mode!='slow_headers':
                    return original(handler,status,body,kind,headers)
                # Every byte arrives before the socket inactivity timeout, but
                # the unfinished header exceeds the complete operation budget.
                try:
                    handler.connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: ')
                    for _ in range(20):
                        handler.connection.sendall(b'x'); time.sleep(.04)
                    handler.connection.sendall(b'\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                except OSError:
                    pass  # Timely local cancellation may close our owned peer.

            server.mode='slow_headers'
            started=time.monotonic()
            with patch.object(fixture.Handler,'send',slow), self.assertRaises(MCPError) as caught:
                client.call_tool('echo',{})
            elapsed=time.monotonic()-started
            self.assertEqual(caught.exception.code,'request_timeout')
            self.assertTrue(caught.exception.uncertain)
            self.assertEqual(len(server.effects),1)
            with self.assertRaises(MCPError): client.call_tool('echo',{})
            self.assertEqual(len(server.effects),1)
            self.assertLess(elapsed,.5,'header drip replaced the total deadline with inactivity timeouts')
