"""Regression for real JSON1 escapes, not special task/runtime branches."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_lab.session_listing import list_sessions
from agent_lab.store import Store


class SessionListingEscapeTests(unittest.TestCase):
    def test_literal_backslash_u0000_workspace_is_distinct_from_actual_nul(self):
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'state.sqlite'
            workspace=str((Path(directory)/r'work\u0000name').resolve())
            tid='1'*32
            with Store(database) as writer:
                writer.save({'id':tid,'session_id':'2'*32,'turn_id':'3'*32,'phase':'ready',
                    'profile':'coding','created_at':10,'workspace':workspace},'created')
            with Store.open_readonly(database) as reader:
                page=list_sessions(reader,workspace=workspace)
                self.assertEqual([item['task_id'] for item in page['items']],[tid])
                self.assertIsNone(page['items'][0]['metadata_error'])
            # The syntactically similar actual NUL must still never match.
            with Store(database) as writer:
                record=writer.load(tid);record['workspace']=workspace+'\0private'
                writer.save(record,'changed')
            with Store.open_readonly(database) as reader:
                self.assertEqual(list_sessions(reader,workspace=workspace)['items'],[])
