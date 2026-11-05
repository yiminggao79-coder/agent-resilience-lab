"""Persistent, conservative CNY budget for the explicitly authorized DeepSeek run.

This is a permission ledger, not a supplier invoice. Unknown requests remain
charged at their reservation. No prompt, response body or credential is stored.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import time
import unicodedata
import uuid
from urllib.parse import quote

from .model import ModelError

CAP_MICROS = 30_000_000
# Peak, cache-miss CNY / million tokens == integer micro-CNY / token.
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ checked 2026-11-07.
RATES = {'deepseek-flash': (2, 8), 'deepseek-v4-pro': (9, 27)}
# Reserve published full input window + maximum output, even when a compatibility
# server might disregard a smaller requested output cap. Deliberately pessimistic.
INPUT_CEILING, OUTPUT_CEILING = 1_048_576, 393_216


class BudgetExceeded(ModelError):
    def __init__(self):
        super().__init__('budget_exhausted')


def _inside_budget_workspace(path, workspace):
    key = lambda value: tuple(unicodedata.normalize('NFD', part).casefold() for part in value.parts)
    path, workspace = key(path), key(workspace)
    return path[:len(workspace)] == workspace


def _existing_budget_identity(path, workspace=None):
    lexical = Path(path).absolute()
    info = lexical.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError()
    canonical = lexical.resolve(strict=True)
    current = canonical.lstat()
    if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
        raise ValueError()
    if workspace is not None:
        selected_workspace = Path(workspace).absolute()
        workspaces = (selected_workspace, selected_workspace.resolve())
        # Resolving just the final path can miss a route through a workspace
        # alias and then an outward symlink. Check every selected ancestor too.
        for candidate in (lexical, canonical, *(parent.resolve() for parent in lexical.parents)):
            if any(_inside_budget_workspace(candidate, scope) for scope in workspaces):
                raise ValueError()
    return str(canonical), current.st_dev, current.st_ino


class BudgetLedger:
    @classmethod
    def open_existing(cls, path, *, workspace=None):
        """Admit an existing current ledger, without initialization or repair."""
        db = None
        try:
            identity = _existing_budget_identity(path, workspace)
            db = sqlite3.connect('file:' + quote(identity[0], safe='/') + '?mode=ro', uri=True, timeout=10)
            db.execute('PRAGMA query_only=ON')
            with db:
                db.execute('BEGIN')
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('budget','requests')")}
                if tables != {'budget', 'requests'}:
                    raise ValueError()
                required = {'budget': {'id', 'cap_micros', 'halted'},
                            'requests': {'id', 'at', 'model', 'reserved', 'charged', 'status',
                                         'input_tokens', 'output_tokens', 'output_bound'}}
                for table, columns in required.items():
                    if not columns <= {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}:
                        raise ValueError()
                rows = db.execute('SELECT id,cap_micros,halted FROM budget LIMIT 2').fetchall()
                if (len(rows) != 1 or any(type(value) is not int for value in rows[0])
                        or rows[0][0] != 1 or rows[0][1] != CAP_MICROS or rows[0][2] not in (0, 1)):
                    raise ValueError()
                if _existing_budget_identity(path, workspace) != identity:
                    raise ValueError()
            instance = cls.__new__(cls)
            instance.path = Path(identity[0])
            instance._existing_file_identity = identity
            instance._existing_workspace = Path(workspace).absolute() if workspace is not None else None
            return instance
        except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError):
            raise ValueError('budget_ledger_unavailable') from None
        finally:
            if db is not None:
                db.close()

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), cap_micros INTEGER NOT NULL)')
            if 'halted' not in {row[1] for row in db.execute('PRAGMA table_info(budget)')}:
                db.execute('ALTER TABLE budget ADD COLUMN halted INTEGER NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO budget(id,cap_micros,halted) VALUES (1,?,0)', (CAP_MICROS,))
            if db.execute('SELECT cap_micros FROM budget WHERE id=1').fetchone() != (CAP_MICROS,):
                raise ValueError('budget_configuration_mismatch')
            db.execute('''CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, at REAL NOT NULL, model TEXT NOT NULL,
                reserved INTEGER NOT NULL, charged INTEGER NOT NULL,
                status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER)''')
            if 'output_bound' not in {row[1] for row in db.execute('PRAGMA table_info(requests)')}:
                db.execute('ALTER TABLE requests ADD COLUMN output_bound INTEGER NOT NULL DEFAULT 393216')
        os.chmod(self.path, 0o600)

    def _connect(self):
        if hasattr(self, '_existing_file_identity'):
            db = None
            try:
                if _existing_budget_identity(self.path, self._existing_workspace) != self._existing_file_identity:
                    raise ValueError()
                db = sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=rw', uri=True, timeout=10)
                if _existing_budget_identity(self.path, self._existing_workspace) != self._existing_file_identity:
                    raise ValueError()
                db.execute('PRAGMA synchronous=FULL')
                return db
            except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError):
                if db is not None:
                    db.close()
                raise ValueError('budget_ledger_unavailable') from None
        db = sqlite3.connect(str(self.path), timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def reserve(self, model, output_token_bound=OUTPUT_CEILING, *, enforce_cap=True):
        if type(enforce_cap) is not bool:raise ValueError('invalid_budget_policy')
        if getattr(self,'_provider_exhausted',False):raise ModelError('paid_requests_halted')
        if model not in RATES:
            raise ValueError('unpriced_model')
        if type(output_token_bound) is not int or not 1 <= output_token_bound <= OUTPUT_CEILING:
            raise ValueError('invalid_output_reservation_bound')
        inp, out = RATES[model]
        quote = INPUT_CEILING * inp + output_token_bound * out
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COALESCE(SUM(charged),0) FROM requests').fetchone()[0]
            cap = db.execute('SELECT cap_micros FROM budget WHERE id=1').fetchone()[0]
            if cap != CAP_MICROS:
                raise ValueError('budget_configuration_mismatch')
            if db.execute('SELECT halted FROM budget WHERE id=1').fetchone()[0]:
                if not enforce_cap:raise ModelError('paid_requests_halted')
                raise BudgetExceeded()
            if enforce_cap and used + quote > cap:raise BudgetExceeded()
            rid = uuid.uuid4().hex
            db.execute('INSERT INTO requests(id,at,model,reserved,charged,status,input_tokens,output_tokens,output_bound) VALUES (?,?,?,?,?,?,NULL,NULL,?)',
                       (rid, time.time(), model, quote, quote, 'reserved', output_token_bound))
        return rid

    def halt_paid_requests(self):
        self._provider_exhausted=True
        with closing(self._connect()) as db,db:
            db.execute('UPDATE budget SET halted=1 WHERE id=1')

    def settle(self, rid, usage, error_code=None):
        known = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                               for k in ('input_tokens', 'output_tokens'))
        inp, out = (usage['input_tokens'], usage['output_tokens']) if known else (None, None)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT model,reserved,status,input_tokens,output_tokens,output_bound FROM requests WHERE id=?', (rid,)).fetchone()
            if row is None:
                raise ValueError('unknown_budget_request')
            model, reserved, status, old_in, old_out, output_bound = row
            if status == 'settled':
                if known and (old_in, old_out) == (inp, out):
                    return
                raise ValueError('conflicting_budget_settlement')
            rates = RATES[model]
            charge = inp * rates[0] + out * rates[1] if known else reserved
            if known and (charge > reserved or inp > INPUT_CEILING or out > output_bound):
                db.execute('UPDATE budget SET halted=1 WHERE id=1')
            db.execute('UPDATE requests SET charged=?,status=?,input_tokens=?,output_tokens=? WHERE id=?',
                       (charge, 'settled' if known else 'unknown', inp, out, rid))
        # A provider exceeding its published bounds must never result in another
        # request. The actual count remains recorded even if larger than reserve.
        if known and (charge > reserved or inp > INPUT_CEILING or out > output_bound):
            raise ModelError('provider_usage_exceeded_reservation', uncertain=True)

    def report(self):
        with closing(self._connect()) as db:
            rows = db.execute('SELECT id,at,model,reserved,charged,status,input_tokens,output_tokens,output_bound FROM requests ORDER BY at,id').fetchall()
        with closing(self._connect()) as db:
            halted = bool(db.execute('SELECT halted FROM budget WHERE id=1').fetchone()[0])
        return {'halted': halted, 'currency': 'CNY', 'cap_cny': 30, 'pricing': 'peak_cache_miss_upper_estimate_2026_11_07',
                'committed_upper_cny': sum(r[4] for r in rows) / 1_000_000,
                'known_usage_estimate_cny': sum(r[4] for r in rows if r[5] == 'settled') / 1_000_000,
                'unresolved_requests': sum(r[5] != 'settled' for r in rows),
                'requests': [{'id': r[0], 'at': r[1], 'model': r[2], 'reserved_cny': r[3] / 1_000_000,
                              'charged_upper_cny': r[4] / 1_000_000, 'status': r[5], 'output_token_bound': r[8],
                              'usage': {'input_tokens': r[6], 'output_tokens': r[7]} if r[5] == 'settled' else None}
                             for r in rows]}


class BudgetedModel:
    def __init__(self, delegate, ledger, *, prepaid_account=False):
        if type(prepaid_account) is not bool:raise ValueError('invalid_budget_policy')
        self.prepaid_account=prepaid_account
        if (delegate.base_url not in ('https://api.deepseek.com', 'https://api.deepseek.com/anthropic')
                or delegate.api_key_env != 'DEEPSEEK_API_KEY' or delegate.model not in RATES):
            raise ValueError('unauthorized_paid_endpoint_or_model')
        self.delegate, self.ledger, self.identity = delegate, ledger, delegate.identity

    @property
    def api_key_env(self):
        return self.delegate.api_key_env

    @property
    def last_text_streamed(self):
        return getattr(self.delegate, 'last_text_streamed', False)

    def complete(self, messages, tools, timeout, max_tokens):
        # Revalidate mutable client config before every delegation, not just init.
        if (self.delegate.base_url not in ('https://api.deepseek.com', 'https://api.deepseek.com/anthropic')
                or self.delegate.api_key_env != 'DEEPSEEK_API_KEY' or self.delegate.model not in RATES):
            raise ValueError('unauthorized_paid_endpoint_or_model')
        if getattr(self.delegate, 'allow_api', True) is False:
            raise ModelError('api_not_enabled')
        if not os.environ.get(self.delegate.api_key_env):
            raise ModelError('api_key_missing')
        try:
            # DeepSeek Chat documents max_tokens as a hard generation ceiling.
            # Anthropic compatibility may raise it; retain published max there.
            # Input remains at the entire published window (no tokenizer guess).
            output_bound = max_tokens if (getattr(self.delegate, 'protocol', None) == 'chat'
                            and getattr(self.delegate, 'token_parameter', None) == 'max_tokens') else OUTPUT_CEILING
            rid = (self.ledger.reserve(self.delegate.model,output_bound,enforce_cap=False) if self.prepaid_account
                   else self.ledger.reserve(self.delegate.model,output_bound))
        except ModelError:
            raise
        except Exception:
            raise ModelError('budget_storage_failed')
        try:
            result = self.delegate.complete(messages, tools, timeout, max_tokens)
        except BaseException as error:
            try:
                self.ledger.settle(rid, None)
            except Exception:
                pass  # The durable reservation remains, including on storage loss.
            if self.prepaid_account and isinstance(error,ModelError) and error.http_status==402:
                try:self.ledger.halt_paid_requests()
                except Exception:raise ModelError('budget_storage_failed',uncertain=True) from None
                raise ModelError('account_balance_exhausted',http_status=402) from None
            raise
        try:
            self.ledger.settle(rid, result.get('usage'))
        except ModelError:
            raise
        except Exception:
            raise ModelError('budget_storage_failed', uncertain=True)
        return result


def load_first_line_key(path=None):
    """Read exactly one line. Never serialize the value or include it in errors."""
    source = Path(path) if path is not None else Path.home() / 'Downloads' / 'API.txt'
    try:
        with source.open('r') as stream:
            value = stream.readline().strip()
    except (OSError, UnicodeError):
        raise ModelError('credential_file_unavailable')
    if not value or '\x00' in value or '\r' in value or '\n' in value:
        raise ModelError('credential_first_line_invalid')
    os.environ['DEEPSEEK_API_KEY'] = value
