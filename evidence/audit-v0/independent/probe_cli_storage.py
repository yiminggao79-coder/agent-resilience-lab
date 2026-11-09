"""Fault-inject storage failure into real CLI; only synthetic model, no network."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from probe_state import Fixture, call, finish
import agent_lab.__main__ as cli
from agent_lab.store import Store


class FaultStore(Store):
    def save(self, state, kind, meta=None):
        if kind == 'tool_finished':
            self.db.execute('PRAGMA query_only=ON')
        return super().save(state, kind, meta)


with tempfile.TemporaryDirectory(prefix='arl-audit-cli-') as base:
    root = Path(base) / 'work'
    root.mkdir()
    fixture = Fixture([call('append_file', {'path': 'effect.txt', 'content': 'once\n'}), finish()])
    with patch.object(cli, 'Store', FaultStore), patch.object(cli, 'OpenAIModel', lambda *args, **kwargs: fixture), patch.dict(os.environ, {'AUDIT_FAKE_KEY': 'synthetic-no-network'}):
        sys.exit(cli.main(['--db', str(Path(base) / 'private' / 'tasks.sqlite'), 'run',
                           'Append one line', '--workspace', str(root), '--model', 'offline',
                           '--api-key-env', 'AUDIT_FAKE_KEY', '--allow-api']))
