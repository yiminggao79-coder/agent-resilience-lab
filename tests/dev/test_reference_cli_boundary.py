"""Owned reference experiment isolation, no real credentials or model endpoints."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


class ReferenceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentlab-reference-',dir='/private/tmp');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.work=self.base/'work';self.work.mkdir()
        self.private=self.base/'private';self.private.write_text('OWNED_PRIVATE_REFERENCE_SENTINEL')

    def test_environment_is_explicit_empty_of_ambient_auth_and_preserves_real_home(self):
        from scripts.reference_cli import reference_environment
        original=os.environ.get('HOME')
        environment=reference_environment(self.base)
        self.assertEqual(set(environment),{'PATH','LANG','HOME','TMPDIR','CODEX_HOME','AGENTLAB_REFERENCE_FIXTURE_KEY'})
        self.assertEqual(environment['HOME'],str(self.base/'home'))
        self.assertEqual(environment['CODEX_HOME'],str(self.base/'codex-home'))
        self.assertEqual(environment['AGENTLAB_REFERENCE_FIXTURE_KEY'],'owned-synthetic-reference-token')
        self.assertEqual(os.environ.get('HOME'),original)
        self.assertNotIn('DEEPSEEK_API_KEY',environment)

    def test_only_numeric_valid_port_and_owned_private_scope_are_accepted(self):
        from scripts.reference_cli import reference_profile
        for port in (None,True,0,65536,'1234'):
            with self.subTest(port=port),self.assertRaises(ValueError):reference_profile(self.base,port,self.private)
        with self.assertRaises(ValueError):reference_profile(self.base,1234,self.base/'outside'/'..'/'private')

    def test_managed_preferences_probe_exports_only_presence_booleans_not_values(self):
        from scripts.reference_cli import managed_preferences_status
        result=managed_preferences_status(self.base,12345)
        self.assertEqual(set(result),{'synchronized','forced_config','forced_requirements'})
        self.assertTrue(all(type(value) is bool for value in result.values()))
        self.assertTrue(result['synchronized'])

    def test_only_preferences_broker_is_allowed_and_other_mach_services_stay_denied(self):
        from scripts.reference_cli import reference_profile,reference_environment
        body="""import ctypes,json
lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib');bootstrap=ctypes.c_uint.in_dll(lib,'bootstrap_port').value
lookup=lib.bootstrap_look_up;lookup.argtypes=[ctypes.c_uint,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint)];lookup.restype=ctypes.c_int
result={}
for name in ('com.apple.cfprefsd.agent','com.apple.cfprefsd.daemon','com.apple.securityd.xpc','com.apple.system.logger'):
 port=ctypes.c_uint();result[name]=lookup(bootstrap,name.encode(),ctypes.byref(port))
print(json.dumps(result))
"""
        result=subprocess.run(['/usr/bin/sandbox-exec','-p',reference_profile(self.base,12345,self.private),sys.executable,'-I','-S','-c',body],
            env=reference_environment(self.base),capture_output=True,text=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr);codes=json.loads(result.stdout)
        self.assertEqual(codes['com.apple.cfprefsd.agent'],0);self.assertEqual(codes['com.apple.cfprefsd.daemon'],0)
        self.assertNotEqual(codes['com.apple.securityd.xpc'],0);self.assertNotEqual(codes['com.apple.system.logger'],0)

    def test_actual_wrapper_denies_private_reads_and_second_endpoint_but_preserves_fixture_access(self):
        from scripts.reference_cli import reference_profile,reference_environment
        allowed=socket.socket();allowed.bind(('127.0.0.1',0));allowed.listen();self.addCleanup(allowed.close)
        denied=socket.socket();denied.bind(('127.0.0.1',0));denied.listen();self.addCleanup(denied.close)
        (self.work/'input').write_text('owned public context')
        body='''import json,pathlib,socket,sys
root=pathlib.Path(sys.argv[1]);private=pathlib.Path(sys.argv[2]);result={}
result['read']= (root/'input').read_text()
(root/'output').write_text('owned output');result['write']=(root/'output').read_text()
for name,path in [('private',private),('real_home',pathlib.Path('/Users'))]:
 try:path.read_bytes() if name=='private' else list(path.iterdir());result[name]='unexpected'
 except PermissionError:result[name]='denied'
for name,port in [('allowed',int(sys.argv[3])),('other',int(sys.argv[4]))]:
 sock=socket.socket();sock.settimeout(1)
 try:sock.connect(('127.0.0.1',port));result[name]='connected'
 except PermissionError:result[name]='denied'
 finally:sock.close()
print(json.dumps(result))
'''
        port=allowed.getsockname()[1]
        command=['/usr/bin/sandbox-exec','-p',reference_profile(self.base,port,self.private),sys.executable,'-I','-S','-c',body,
                 str(self.work),str(self.private),str(port),str(denied.getsockname()[1])]
        result=subprocess.run(command,cwd=self.work,env=reference_environment(self.base),capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'read':'owned public context','write':'owned output',
            'private':'denied','real_home':'denied','allowed':'connected','other':'denied'})
        self.assertEqual(self.private.read_text(),'OWNED_PRIVATE_REFERENCE_SENTINEL')


if __name__=='__main__':unittest.main()
