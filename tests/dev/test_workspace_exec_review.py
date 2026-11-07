"""Independent owned native regression for secondary hidden-image aliases."""
import os
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError
from tests.native_fixture import compile_c


class WorkspaceExecReviewTests(unittest.TestCase):
    def test_tests_scope_cannot_execute_hidden_image_through_visible_hardlink(self):
        with tempfile.TemporaryDirectory(prefix='agentlab-review-prelink-',dir='/private/tmp') as directory:
            root=Path(directory)/'work';root.mkdir()
            hidden,_=compile_c(root,'.hidden-image',
                '#include <stdio.h>\nint main(void){puts("HIDDEN_IMAGE_EXECUTED");return 91;}\n')
            os.link(hidden,root/'visible-image')
            (root/'test_image.py').write_text(
                'import os,unittest\nclass Check(unittest.TestCase):\n'
                ' def test_image(self):\n'
                '  os.execv("./visible-image",["./visible-image"])\n')
            tools=CodingTools(root,allow_workspace_executables=True)
            try:
                result=tools.execute(tools.prepare('run_tests',{}),6)
            except ToolError as error:
                self.assertEqual(error.code,'permission_denied')
            else:
                self.assertNotIn('HIDDEN_IMAGE_EXECUTED',result['stdout'])
                self.assertNotEqual(result['exit_code'],91)


if __name__=='__main__':unittest.main()
