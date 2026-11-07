"""A project cannot be allowed to rewrite the harness that executes its tools."""
from pathlib import Path
import tempfile
import unittest
from agent_lab import tools as tools_module
from agent_lab.tools import Tools


class TrustedRuntimeBoundaryTests(unittest.TestCase):
    def test_runtime_package_and_ancestor_workspace_are_rejected_before_any_controls(self):
        package=Path(tools_module.__file__).resolve().parent
        before={p.name for p in package.iterdir()}
        for root in (package,package.parent):
            with self.subTest(root=root),self.assertRaisesRegex(ValueError,'workspace_overlaps_trusted_runtime'):
                Tools(root)
        self.assertEqual({p.name for p in package.iterdir()},before)

    def test_separate_workspace_remains_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'work';root.mkdir()
            tools=Tools(root)
            result=tools.execute(tools.prepare('write_file',{'path':'safe','content':'actual'}),3)
            self.assertTrue(result['ok']);self.assertEqual((root/'safe').read_text(),'actual')
