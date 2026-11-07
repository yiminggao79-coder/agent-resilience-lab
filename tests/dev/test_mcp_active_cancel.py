"""Actual external transport waits; cancellation never attests remote quiescence."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.mcp import MCPClient,MCPError
from agent_lab.mcp_http import MCPHTTPClient
from agent_lab.registry import MCPToolRegistry
from agent_lab.tool_control import ToolCancelled
from tests.fixtures.mcp_http_server import serving
from tests.fixtures import mcp_http_server as http_fixture

SERVER=Path(__file__).resolve().parents[1]/'fixtures/mcp_server.py'


class MCPActiveCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-mcp-cancel-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'work';self.root.mkdir();self.config=self.base/'mcp.json'

    def registry(self,spec):
        self.config.write_text(json.dumps({'servers':[{'name':'owned',**spec}]}))
        registry=MCPToolRegistry(CodingTools(self.root),self.config);registry.__enter__();self.addCleanup(registry.close)
        return registry

    def test_existing_base_scope_cancels_dispatched_stdio_effect_and_preserves_unknown(self):
        marker=self.base/'effect';log=self.base/'requests'
        registry=self.registry({'command':[sys.executable,'-I','-S',str(SERVER),'effect_timeout',str(log),str(marker)],'cwd':str(self.base),'timeout':4})
        wire=next(item['name'] for item in registry.manifest['tools'] if item['tool']=='mutate')
        started=time.monotonic()
        with registry.base.cancellation_scope(marker.exists):
            with self.assertRaises(ToolCancelled) as caught:registry.execute(registry.prepare(wire,{}),4)
        self.assertFalse(caught.exception.local_quiescent)
        self.assertLess(time.monotonic()-started,1.5)
        self.assertEqual(marker.read_text(),'effect\n')
        records=[json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(item.get('method')=='tools/call' for item in records),1)

    def test_existing_base_scope_cancels_dispatched_http_without_cross_thread_callback(self):
        with serving() as server:
            registry=self.registry({'transport':'streamable_http','url':server.url,'allow_loopback_http':True,'timeout':4})
            wire=next(item['name'] for item in registry.manifest['tools'] if item['tool']=='echo')
            server.mode='effect_timeout';owner=threading.get_ident();checks=[]
            def stop():
                checks.append(threading.get_ident());self.assertEqual(checks[-1],owner)
                return server.effect_ready.is_set()
            started=time.monotonic()
            with registry.base.cancellation_scope(stop):
                with self.assertRaises(ToolCancelled) as caught:registry.execute(registry.prepare(wire,{}),4)
            self.assertFalse(caught.exception.local_quiescent)
            self.assertLess(time.monotonic()-started,1.5)
            self.assertTrue(checks);self.assertEqual(len(server.effects),1)
            self.assertFalse(any(r.get('method')=='DELETE' for r in server.records))

    def test_direct_client_predispatch_stop_does_not_send_or_report_success(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=3);self.addCleanup(client.close)
            client.start();client.list_tools()
            with client.cancellation_scope(lambda:True):
                with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
            self.assertEqual(caught.exception.code,'request_cancelled');self.assertFalse(caught.exception.uncertain)
            self.assertEqual(server.effects,[])
            with self.assertRaises(MCPError):client.call_tool('echo',{})

    def test_callback_failure_closes_without_echoing_arbitrary_exception(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=3);self.addCleanup(client.close)
            client.start();client.list_tools()
            def fail():raise RuntimeError('PRIVATE_SYNTHETIC_SENTINEL')
            with client.cancellation_scope(fail):
                with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
            self.assertEqual(caught.exception.code,'request_cancel_check_failed');self.assertNotIn('PRIVATE',str(caught.exception))
            self.assertFalse(server.effects)

    def test_interrupt_closes_client_without_a_new_http_delete(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=3);self.addCleanup(client.close)
            client.start();client.list_tools()
            def interrupt():raise KeyboardInterrupt()
            with client.cancellation_scope(interrupt):
                with self.assertRaises(KeyboardInterrupt):client.call_tool('echo',{})
            self.assertEqual(client._state,'closed')
            client.close()
            self.assertFalse(server.effects)
            self.assertFalse(any(r.get('method')=='DELETE' for r in server.records))

    def test_connected_slow_headers_are_cancelled_without_waiting_for_header_or_deadline(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=4);self.addCleanup(client.close)
            client.start();client.list_tools();entered=threading.Event();original=http_fixture.Handler.send
            def slow(handler,status,body=b'',kind='application/json',headers=None):
                if handler.server is not server or server.mode!='slow_headers':return original(handler,status,body,kind,headers)
                try:
                    handler.connection.sendall(b'HTTP/1.1 200 OK\r\nX-Slow: ');entered.set()
                    for _ in range(30):handler.connection.sendall(b'x');time.sleep(.05)
                except OSError:pass
            server.mode='slow_headers';started=time.monotonic()
            with patch.object(http_fixture.Handler,'send',slow),client.cancellation_scope(entered.is_set):
                with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
            self.assertEqual(caught.exception.code,'request_cancelled');self.assertTrue(caught.exception.uncertain)
            self.assertLess(time.monotonic()-started,1)
            self.assertEqual(len(server.effects),1)

    def test_detached_response_is_closed_before_cancel_returns_even_when_retained(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=4);self.addCleanup(client.close)
            client.start();client.list_tools();entered=threading.Event();status_read=threading.Event();captured=[]
            original_send=http_fixture.Handler.send
            from agent_lab import mcp_http
            original_status=mcp_http.http.client.HTTPResponse._read_status
            original_response=mcp_http.http.client.HTTPConnection.getresponse
            def partial(handler,status,body=b'',kind='application/json',headers=None):
                if handler.server is not server or server.mode!='partial_headers':return original_send(handler,status,body,kind,headers)
                handler.connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Stall: ')
                entered.set();server.release.wait(3)
            def status(response):
                result=original_status(response);status_read.set();return result
            def response(connection):
                result=original_response(connection);captured.append(result);return result
            server.mode='partial_headers'
            try:
                with patch.object(http_fixture.Handler,'send',partial),patch.object(mcp_http.http.client.HTTPResponse,'_read_status',status),patch.object(mcp_http.http.client.HTTPConnection,'getresponse',response),client.cancellation_scope(lambda:entered.is_set() and status_read.is_set()):
                    with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
                self.assertEqual(caught.exception.code,'request_cancelled')
                self.assertEqual(len(captured),1)
                self.assertTrue(captured[0].will_close)
                self.assertTrue(captured[0].isclosed(),'detached response still owns its socket file until GC')
            finally:
                for item in captured:item.close()

    def test_late_connect_worker_retains_abort_gate_after_controller_returns(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=4);self.addCleanup(client.close)
            client.start();client.list_tools()
            entered,release,closed=threading.Event(),threading.Event(),threading.Event();sends=[]
            class Socket:
                def shutdown(self,*_):pass
                def settimeout(self,*_):pass
            class LateConnection:
                def __init__(self,*_,**__):self.sock=None
                def connect(self):entered.set();release.wait(3);self.sock=Socket()
                def request(self,*args,**kwargs):sends.append(args);raise AssertionError('late request sent')
                def close(self):closed.set()
            with patch('agent_lab.mcp_http.http.client.HTTPConnection',LateConnection):
                try:
                    with client.cancellation_scope(entered.is_set):
                        with self.assertRaises(MCPError) as caught:client.call_tool('echo',{})
                    self.assertEqual(caught.exception.code,'request_cancelled');self.assertFalse(caught.exception.uncertain)
                    self.assertFalse(closed.is_set(),'test did not retain a live late-connect worker')
                finally:release.set()
                self.assertTrue(closed.wait(1))
            self.assertEqual(sends,[])
            self.assertEqual(server.effects,[])

    def test_cancellable_nested_sse_ping_preserves_normal_result_and_controller_callback_thread(self):
        with serving() as server:
            client=MCPHTTPClient(server.url,allow_loopback_http=True,timeout=3);self.addCleanup(client.close)
            client.start();client.list_tools();server.mode='sse';owner=threading.get_ident();checks=[]
            def keep():
                self.assertEqual(threading.get_ident(),owner);checks.append(owner);return False
            with client.cancellation_scope(keep):result=client.call_tool('echo',{'owned':7})
            self.assertEqual(result['structuredContent'],{'owned':7})
            self.assertTrue(checks)
            self.assertTrue(any(r.get('body',{}).get('id')=='server-ping' for r in server.records))


if __name__=='__main__':unittest.main()
