"""Actual venv interpreter launch in the unchanged native sandbox; no API."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv


class VenvNativeTests(unittest.TestCase):
    def test_venv_launcher_symlink_does_not_prevent_sandboxed_tests(self):
        backend=Path('/usr/bin/sandbox-exec')
        if not backend.is_file(): self.skipTest('sandbox-exec unavailable; no bare fallback')
        probe=subprocess.run([str(backend),'-p','(version 1)(allow default)','/usr/bin/true'],
                             capture_output=True,timeout=3)
        if probe.returncode: self.skipTest('host denies sandbox-exec; no bare fallback')
        with tempfile.TemporaryDirectory(prefix='agentlab-venv-native-') as directory:
            base=Path(directory).resolve(); root=base/'work'; root.mkdir()
            (root/'test_public.py').write_text(
                'import unittest, os, socket, subprocess, sys\n'
                'class T(unittest.TestCase):\n'
                ' def test_bounds(self):\n'
                '  self.assertNotIn("DEEPSEEK_API_KEY", os.environ)\n'
                '  with self.assertRaises(OSError): os.fork()\n'
                '  with self.assertRaises(OSError): subprocess.Popen([sys.executable,"-c","pass"])\n'
                '  with self.assertRaises(OSError): socket.socket().connect(("127.0.0.1",9))\n'
                '  self.assertEqual(2+3,5)\n')
            venv.EnvBuilder(with_pip=False,symlinks=True).create(base/'env')
            python=base/'env'/'bin'/'python'
            self.assertTrue(python.is_symlink())
            code=('import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
                  'from agent_lab.sandbox import run_tests; '
                  'print(json.dumps(run_tests(Path(sys.argv[2]),10)))')
            result=subprocess.run([str(python),'-I','-c',code,str(Path(__file__).resolve().parents[2]),str(root)],
                                  cwd=root,env={'PATH':os.defpath,'LANG':'C.UTF-8'},
                                  capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(result.stdout)
            self.assertEqual(report['exit_code'],0,report['stderr'])
            self.assertTrue(report['ok'])
            self.assertIn('Ran 1 test',report['stderr'])


if __name__=='__main__': unittest.main()
