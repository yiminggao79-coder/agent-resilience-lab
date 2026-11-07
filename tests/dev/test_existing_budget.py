"""Existing-only budget admission over owned synthetic SQLite; no real ledger."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import unicodedata
from unittest.mock import patch

from agent_lab import budget
from agent_lab.model import ModelError


class ExistingBudgetTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(budget.BudgetLedger, 'open_existing'), 'existing-only budget admission is missing')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / 'Workspace'; self.workspace.mkdir()
        self.path = self.root / 'private' / 'ledger.sqlite'
        self.original = budget.BudgetLedger(self.path)

    def open(self, path=None, **kwargs):
        return budget.BudgetLedger.open_existing(self.path if path is None else path, **kwargs)

    def denied(self, path, **kwargs):
        with self.assertRaisesRegex(ValueError, '^budget_ledger_unavailable$'): self.open(path, **kwargs)

    def test_existing_admission_does_not_create_chmod_migrate_or_change_unknown_reservations(self):
        rid = self.original.reserve('deepseek-flash', 100)
        self.original.settle(rid, None)
        self.path.chmod(0o640)
        before = (self.path.read_bytes(), self.path.stat().st_mode,
                  sorted(p.name for p in self.path.parent.iterdir()))
        opened = self.open(workspace=self.workspace)
        after = (self.path.read_bytes(), self.path.stat().st_mode,
                 sorted(p.name for p in self.path.parent.iterdir()))
        self.assertEqual(before, after)
        self.assertEqual(opened.path, self.path)
        self.assertEqual(opened.report(), self.original.report())
        self.assertEqual(opened.report()['unresolved_requests'], 1)
        missing = self.root / 'missing-parent' / 'ledger.sqlite'
        self.denied(missing)
        self.assertFalse(missing.parent.exists())

    def test_bad_schema_cap_types_halted_and_non_sqlite_fail_fixed_without_migration(self):
        legacy = self.root / 'legacy.sqlite'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE budget(id INTEGER PRIMARY KEY,cap_micros INTEGER NOT NULL)')
            db.execute('INSERT INTO budget VALUES(1,?)', (budget.CAP_MICROS,))
        before = legacy.read_bytes(); self.denied(legacy); self.assertEqual(legacy.read_bytes(), before)
        for index, (cap, halted) in enumerate(((budget.CAP_MICROS + 1, 0), (float(budget.CAP_MICROS), 0),
                                               (str(budget.CAP_MICROS), 0), (budget.CAP_MICROS, 2), (budget.CAP_MICROS, 1.0))):
            path = self.root / ('bad-' + str(index) + '.sqlite'); path.write_bytes(self.path.read_bytes())
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE budget'); db.execute('CREATE TABLE budget(id,cap_micros,halted)')
                db.execute('INSERT INTO budget VALUES(1,?,?)', (cap, halted))
            original = path.read_bytes(); self.denied(path); self.assertEqual(path.read_bytes(), original)
        old_requests = self.root / 'old-requests.sqlite'; old_requests.write_bytes(self.path.read_bytes())
        with sqlite3.connect(old_requests) as db:
            db.execute('DROP TABLE requests')
            db.execute('CREATE TABLE requests(id,at,model,reserved,charged,status,input_tokens,output_tokens)')
        before = old_requests.read_bytes(); self.denied(old_requests); self.assertEqual(old_requests.read_bytes(), before)
        bad = self.root / 'not-sqlite'; bad.write_text('synthetic non database')
        self.denied(bad)

    def test_directory_fifo_symlink_and_hardlinked_ledger_rejected_before_sqlite_open(self):
        directory = self.root / 'directory'; directory.mkdir()
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        symlink = self.root / 'ledger-link'; symlink.symlink_to(self.path)
        for path in (directory, fifo, symlink):
            with self.subTest(path=path), patch.object(budget.sqlite3, 'connect', side_effect=AssertionError('unsafe file reached SQLite')):
                self.denied(path)
        alias = self.root / 'ledger-hardlink'; os.link(self.path, alias)
        with patch.object(budget.sqlite3, 'connect', side_effect=AssertionError('hardlink reached SQLite')):
            self.denied(self.path)

    def test_workspace_lexical_canonical_and_ancestor_aliases_are_rejected(self):
        inside = self.workspace / 'ledger.sqlite'; inside.write_bytes(self.path.read_bytes())
        outside_alias = self.root / 'outside-file-alias'; outside_alias.symlink_to(inside)
        workspace_alias = self.root / 'workspace-alias'; workspace_alias.symlink_to(self.workspace)
        (self.workspace / 'escape').symlink_to(self.path.parent)
        paths = (inside, outside_alias, workspace_alias / 'ledger.sqlite',
                 workspace_alias / 'escape' / self.path.name,
                 self.workspace / '..' / 'private' / self.path.name,
                 self.workspace.with_name('WORKSPACE') / 'ledger.sqlite')
        for path in paths:
            with self.subTest(path=path): self.denied(path, workspace=self.workspace)
        unicode_workspace = self.root / 'Café'; unicode_workspace.mkdir()
        unicode_inside = unicode_workspace / 'ledger.sqlite'; unicode_inside.write_bytes(self.path.read_bytes())
        unicode_alias_scope = unicode_workspace.with_name(unicodedata.normalize('NFD', 'CAFÉ'))
        self.denied(unicode_inside, workspace=unicode_alias_scope)
        self.assertEqual(self.open(workspace=self.workspace).path, self.path)

    def test_unlink_and_replacement_of_live_ledger_never_create_or_reserve_in_new_file(self):
        opened = self.open()
        saved = self.path.with_suffix('.old'); self.path.rename(saved)
        with self.assertRaisesRegex(ValueError, '^budget_ledger_unavailable$'):
            opened.reserve('deepseek-flash', 10)
        self.assertFalse(self.path.exists())
        self.path.write_bytes(saved.read_bytes()); before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '^budget_ledger_unavailable$'):
            opened.reserve('deepseek-flash', 10)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.open().report()['requests'], [])

    def test_replace_between_rw_open_checks_closes_connection_and_keeps_old_data(self):
        opened = self.open(); actual = sqlite3.connect; connections = []
        saved = self.path.with_suffix('.old')
        def connect(*args, **kwargs):
            db = actual(*args, **kwargs); connections.append(db)
            self.path.rename(saved); self.path.write_bytes(saved.read_bytes())
            return db
        with patch.object(budget.sqlite3, 'connect', side_effect=connect):
            with self.assertRaisesRegex(ValueError, '^budget_ledger_unavailable$'):
                opened.reserve('deepseek-flash', 10)
        self.assertEqual(len(connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError): connections[0].execute('SELECT 1')
        self.assertEqual(self.open(saved).report()['requests'], [])
        self.assertEqual(self.open().report()['requests'], [])

    def test_existing_reserve_and_settle_keep_normal_known_and_unknown_accounting(self):
        opened = self.open()
        unknown = opened.reserve('deepseek-flash', 100)
        opened.settle(unknown, None)
        known = opened.reserve('deepseek-flash', 100)
        opened.settle(known, {'input_tokens': 100, 'output_tokens': 20})
        report = self.open().report()
        self.assertEqual(len(report['requests']), 2)
        self.assertEqual(report['unresolved_requests'], 1)
        self.assertAlmostEqual(report['known_usage_estimate_cny'], .00036)
        self.assertEqual(report['requests'][0]['status'], 'unknown')

    def test_settlement_file_loss_remains_uncertain_without_recreation(self):
        opened = self.open(); path = self.path; saved = path.with_suffix('.saved')
        class Model:
            base_url = 'https://api.deepseek.com'
            api_key_env = 'DEEPSEEK_API_KEY'
            model = 'deepseek-flash'
            identity = 'synthetic-no-network'
            protocol = 'chat'
            token_parameter = 'max_tokens'
            def complete(self, *args):
                path.rename(saved)
                return {'usage': {'input_tokens': 10, 'output_tokens': 1}}
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'synthetic-test-value'}):
            with self.assertRaises(ModelError) as caught:
                budget.BudgetedModel(Model(), opened).complete([], [], 1, 100)
        self.assertEqual(caught.exception.code, 'budget_storage_failed')
        self.assertTrue(caught.exception.uncertain)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.open(saved).report()['requests'][0]['status'], 'reserved')

    def test_ordinary_constructor_still_explicitly_creates_and_migrates(self):
        target = self.root / 'new-parent' / 'new.sqlite'
        self.denied(target); self.assertFalse(target.parent.exists())
        created = budget.BudgetLedger(target)
        self.assertEqual(created.report()['requests'], [])
        legacy = self.root / 'old.sqlite'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE budget(id INTEGER PRIMARY KEY,cap_micros INTEGER NOT NULL)')
            db.execute('INSERT INTO budget VALUES(1,?)', (budget.CAP_MICROS,))
        self.denied(legacy)
        migrated = budget.BudgetLedger(legacy)
        self.assertEqual(migrated.report()['requests'], [])


if __name__ == '__main__': unittest.main()
