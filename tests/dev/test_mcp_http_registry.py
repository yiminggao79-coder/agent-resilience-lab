"""Real loopback extension + Engine approvals/recovery; scripted model only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store
from tests.support import ScriptedModel,tool,final
from tests.fixtures.mcp_http_server import serving


class MCPHTTPRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'work';self.root.mkdir()
        self.config=self.base/'trusted-mcp.json';self.store=Store(self.base/'tasks.sqlite')
        self.addCleanup(self.store.close)
    def registry(self,server,**extra):
        self.config.write_text(json.dumps({'servers':[{'name':'http_public','transport':'streamable_http',
            'url':server.url,'allow_loopback_http':True,'timeout':1,**extra}]}))
        registry=MCPToolRegistry(CodingTools(self.root),self.config)
        registry.__enter__();self.addCleanup(registry.close);return registry
    @staticmethod
    def wire(registry):return registry.manifest['tools'][0]['name']
    def approve(self,engine,tid):
        request=engine.approval_request(tid)
        engine.decide_approval(tid,request['request_id'],request['digest'],True)
        return request

    def test_remote_authority_requires_exact_approval_then_private_feedback(self):
        with serving() as server:
            registry=self.registry(server)
            model=ScriptedModel([tool(self.wire(registry),{'value':3}),final('scripted finish')])
            engine=Engine(self.store,registry,model);tid=engine.create('Use public HTTP extension')
            self.assertEqual(engine.run(tid)['phase'],'awaiting_approval');self.assertFalse(server.effects)
            request=self.approve(engine,tid)
            self.assertEqual(request['action']['authority'],'external_trusted_http')
            self.assertEqual(request['action']['url'],server.url)
            self.assertNotIn('command',request['action'])
            self.assertEqual(engine.run(tid)['phase'],'completed')
            result=json.loads(model.seen[-1][-1]['content'])
            self.assertEqual(result['structuredContent'],{'value':3})
            self.assertEqual(result['origin']['type'],'mcp_streamable_http')
            events=json.dumps(self.store.events(tid))
            self.assertNotIn(server.url,events);self.assertNotIn('synthetic-session',events)
            self.assertEqual(len(server.effects),1);registry.close()

    def test_unknown_after_remote_effect_never_replays_on_repeated_resume(self):
        with serving() as server:
            registry=self.registry(server,timeout=.2)
            model=ScriptedModel([tool(self.wire(registry),{})])
            engine=Engine(self.store,registry,model)
            tid=engine.create('Effect whose reply times out',Limits(tool_timeout=.2))
            engine.run(tid);self.approve(engine,tid);server.mode='effect_timeout'
            self.assertEqual(engine.run(tid)['phase'],'needs_confirmation')
            self.assertTrue(server.effect_ready.is_set());self.assertEqual(len(server.effects),1)
            for _ in range(2):self.assertEqual(engine.run(tid)['phase'],'needs_confirmation')
            self.assertEqual(len(server.effects),1);registry.close()
            code='''import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.registry import MCPToolRegistry
from agent_lab.store import Store
class NoModel:
 identity='offline_scripted_protocol_double'
 def complete(self,*args,**kwargs):raise AssertionError('unknown outcome must not sample')
with Store(Path(sys.argv[4])) as store,MCPToolRegistry(CodingTools(Path(sys.argv[2])),Path(sys.argv[3])) as registry:
 state=Engine(store,registry,NoModel()).run(sys.argv[5])
 print(json.dumps({'phase':state['phase'],'pid':os.getpid()}))
'''
            child=subprocess.run([sys.executable,'-I','-c',code,str(Path(__file__).resolve().parents[2]),
                str(self.root),str(self.config),str(self.base/'tasks.sqlite'),tid],
                env={'PATH':os.defpath,'LANG':'C.UTF-8'},capture_output=True,text=True,timeout=10)
            self.assertEqual(child.returncode,0,child.stderr)
            report=json.loads(child.stdout)
            self.assertEqual(report['phase'],'needs_confirmation');self.assertNotEqual(report['pid'],os.getpid())
            self.assertEqual(len(server.effects),1)

    def test_changed_host_endpoint_or_options_reject_saved_toolset(self):
        with serving() as first,serving() as second:
            registry=self.registry(first)
            model=ScriptedModel([final()]);engine=Engine(self.store,registry,model)
            tid=engine.create('Saved identity');registry.close()
            changed=self.registry(second)
            with self.assertRaises(ValueError):Engine(self.store,changed,model).run(tid)
            self.assertFalse(first.effects);self.assertFalse(second.effects);self.assertFalse(model.seen)
            changed.close()

    def test_readonly_and_mixed_transport_configuration_refuse_before_dispatch(self):
        with serving() as server:
            spec={'name':'http_public','transport':'streamable_http','url':server.url,'allow_loopback_http':True}
            for extra in ({'command':['/usr/bin/true'],'cwd':str(self.base)},
                          {'headers':{'Authorization':'synthetic-forbidden'}},{'allow_loopback_http':False}):
                self.config.write_text(json.dumps({'servers':[{**spec,**extra}]}))
                with self.subTest(extra=extra),self.assertRaises(ValueError):
                    MCPToolRegistry(CodingTools(self.root),self.config)
            self.config.write_text(json.dumps({'servers':[spec]}))
            with self.assertRaises(ValueError):MCPToolRegistry(CodingTools(self.root,read_only_paths=('.',)),self.config)
            self.assertFalse(server.records)


if __name__=='__main__':unittest.main()
