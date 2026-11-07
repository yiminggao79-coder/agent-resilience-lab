"""Independent admission regression for Host executable configuration."""
from pathlib import Path
import tempfile
import unittest

from agent_lab.executables import executable_descriptors
from agent_lab.tools import ToolError


class ExtraExecutableReviewTests(unittest.TestCase):
    def test_external_entry_cannot_route_through_workspace_intermediate_link(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-extra-review-', dir='/private/tmp') as tmp:
            base = Path(tmp)
            root = base / 'work'
            root.mkdir()
            binary = base / 'checker'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            intermediate = root / 'model-controlled-link'
            intermediate.symlink_to(binary)
            selected = base / 'host-selected-entry'
            selected.symlink_to(intermediate)

            with self.assertRaisesRegex(ToolError, '^command_runtime_not_allowed$'):
                executable_descriptors(root, {'checker': str(selected)})


if __name__ == '__main__':
    unittest.main()
