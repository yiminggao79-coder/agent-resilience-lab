"""Opt-in coding tools; patches reuse the existing verified-write protocol.

apply_patch is a single-file exact-substring API, not the Codex patch grammar.
"""
import json
import os
import stat
import time
from contextlib import contextmanager

from .tools import MAX_BYTES, Tools, ToolError, UncertainEffect, digest
from . import sandbox

MAX_FILES = 200
MAX_ENTRIES = 10000
MAX_DEPTH = 32
MAX_SEARCH_BYTES = 1024 * 1024
CODING_FIELDS = {'search_files': ('query',), 'apply_patch': ('path', 'old', 'new'), 'run_tests': ()}
FILE_FIELDS = {'create_directory': {'path', 'parents'}, 'delete_file': {'path'},
               'read_lines': {'path', 'start_line', 'end_line'}}
MAX_LINE_FILE_BYTES = 2 * 1024 * 1024
MAX_DELETE_FILE_BYTES = 2 * 1024 * 1024
MAX_PAGE_LINES = 200


class CodingTools(Tools):
    profile = 'coding'

    def schemas(self=None):
        # Preserve historical class-level default calls while reporting an
        # enabled instance's host-admitted capability to the model/registry.
        schemas = Tools.schemas()
        for schema in schemas:
            if schema['function']['name'] == 'list_files':
                schema['function']['description'] = 'Recursively list up to 200 visible regular workspace files; no links or hidden paths.'
        descriptions = {
            'search_files': 'Search visible UTF-8 files for a literal query, returning bounded matching lines with 1-based line numbers. Binary and oversized files are reported as skipped.',
            'apply_patch': 'Replace one unique exact nonempty substring old with new in an existing UTF-8 file. This is not unified diff or Codex patch syntax. Conflicts fail without changes.',
            'run_tests': 'Run fixed Python unittest discovery in an OS sandbox: tests/ when present, otherwise workspace root (test*.py). Scope is frozen when prepared; tests/ takes priority in mixed layouts. Zero discovered tests and discovery errors fail. Tests may modify workspace files; uncertain execution requires review. No network or general shell.',
        }
        for name, fields in CODING_FIELDS.items():
            schemas.append({'type': 'function', 'function': {
                'name': name, 'description': descriptions[name],
                'parameters': {'type': 'object', 'properties': {field: {'type': 'string'} for field in fields},
                               'required': list(fields), 'additionalProperties': False}}})
        schemas.append({'type': 'function', 'function': {
            'name': 'run_command',
            'description': 'Request an exact argv command in a visible workspace directory. Requires user approval. Fixed trusted executable aliases: python, sh, echo, cat, true, false. OS sandbox denies network, private paths and fork; shell pipelines/background children are unsupported.',
            'parameters': {'type': 'object', 'properties': {'argv': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 128}, 'cwd': {'type': 'string'}},
                           'required': ['argv','cwd'], 'additionalProperties': False}}})
        descriptions = {
            'create_directory': 'Create a visible workspace directory idempotently. parents=false requires an existing parent; parents=true explicitly permits missing ancestors. Never replaces files or links.',
            'delete_file': 'Delete one visible regular single-link file up to 2 MiB after checking its prepared content hash and ordinary mode. Changed files are preserved and require review. No recursive deletion.',
            'read_lines': 'Read an inclusive 1-based UTF-8 line range with numbered lines, next_line, eof and sha256 of the exact whole-file bytes. Maximum 200 requested lines, 2 MiB file, 64 KiB response. Oversized lines/pages fail explicitly; request fewer lines.',
        }
        for name, fields in FILE_FIELDS.items():
            properties = {'path': {'type': 'string'}}
            if name == 'create_directory':
                properties['parents'] = {'type': 'boolean'}
            elif name == 'read_lines':
                properties.update({field: {'type': 'integer', 'minimum': 1}
                                   for field in ('start_line', 'end_line')})
            schemas.append({'type': 'function', 'function': {
                'name': name, 'description': descriptions[name],
                'parameters': {'type': 'object', 'properties': properties,
                               'required': sorted(fields), 'additionalProperties': False}}})
        for name, fields, description in (
            ('git_status', (), 'Read bounded visible-file Git status, including untracked names. No Git writes, helpers, network or arbitrary argv.'),
            ('git_diff', ('path',), 'Read bounded unstaged Git diff for one visible file against the index. Untracked contents and hidden paths are excluded. No external diff/textconv.')):
            schemas.append({'type': 'function', 'function': {
                'name': name, 'description': description,
                'parameters': {'type': 'object', 'properties': {field: {'type': 'string'} for field in fields},
                               'required': list(fields), 'additionalProperties': False}}})
        schemas.append({'type': 'function', 'function': {
            'name': 'list_files_page',
            'description': 'Page through visible regular single-link workspace files in canonical path order. Optional prefix is a visible directory subtree; limit defaults to 100 (1–200). Follow next_cursor using identical prefix/limit until eof. Tree changes reject the cursor; restart without it. Explicit scan and response caps; no links or hidden paths.',
            'parameters': {'type': 'object', 'properties': {
                'prefix': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                'cursor': {'type': 'string', 'maxLength': 1024}},
                'required': [], 'additionalProperties': False}}})
        from .patching import schema
        schemas.append(schema())
        from .searching import schema as search_schema
        schemas.append(search_schema())
        from .editing import schema as edit_schema
        schemas.append(edit_schema())
        if getattr(self, 'node_executable', None) is not None:
            command = next(item['function'] for item in schemas if item['function']['name'] == 'run_command')
            command['description'] = (
                'Request an exact argv command in a visible workspace directory. Requires user approval. '
                'Host-admitted aliases: python, sh, echo, cat, true, false, node. '
                'For node:test use --test --test-isolation=none; npm is not enabled. '
                'OS sandbox denies network, private paths and fork; shell pipelines/background children are unsupported.')
        if getattr(self,'command_backend','nofork') == 'confined-fork':
            function=next(item['function'] for item in schemas if item['function']['name']=='run_command')
            aliases='python, sh, echo, cat, true, false'+(', node' if self.node_executable else '')
            function['description']=(
                'Request an exact argv command; requires approval. Host-selected confined-fork backend. '
                'Fixed aliases: '+aliases+'. Python subprocess and shell pipelines may run; '
                'network, private files and process-group escape remain denied. Background descendants '
                'are killed when the command parent exits. posix_spawn is denied; no npm or PTY guarantee.')
        if getattr(self,'tool_read_paths',None) or getattr(self,'tool_sysctl_read',None):
            function=next(item['function'] for item in schemas if item['function']['name']=='run_command')
            if self.tool_read_paths:function['description']+=' Host readonly tool resources: '+json.dumps(self.tool_read_paths)+'. Directory scopes are not frozen content snapshots; no write/exec/network grant.'
            if self.tool_sysctl_read:function['description']+=' Host named sysctl reads: '+', '.join(self.tool_sysctl_read)+'.'
        if getattr(self,'allow_workspace_executables',False):
            function=next(item['function'] for item in schemas if item['function']['name']=='run_command')
            function['description']+=' Host also permits explicit ./visible workspace programs or exact workspace absolute paths. Primary bytes/mode are approval-bound; secondary visible workspace images share this command scope, not per-image approval. No hidden/link/traversal primary paths.'
        if getattr(self,'tool_executables',None):
            function=next(item['function'] for item in schemas if item['function']['name']=='run_command')
            function['description']+=' Additional Host-registered executable aliases: '+', '.join(sorted(self.tool_executables))+'. No additional runtime resource directories or network are granted.'
        if getattr(self,'python_environment',None) is not None:
            for item in schemas:
                function=item['function']
                if function['name']=='run_command':
                    function['description']+=(' The python alias uses a Host-selected dependency venv. '
                        'For preinstalled pytest use -p no:cacheprovider and explicit --ignore-glob=.* '
                        'and --ignore-glob=**/.* to avoid probing protected hidden metadata during collection. '
                        'No dependency installation tool is provided; entry-point plugin autoload is disabled.')
                elif function['name']=='run_tests':
                    function['description']+=' Uses the Host-selected venv dependencies; still unittest discovery, not pytest.'
        return schemas

    def _prepare(self, name, args):
        if name == 'edit_file':
            from .editing import prepare_edit
            return prepare_edit(self, args)
        if name == 'search_files_page':
            from .searching import prepare_search
            return prepare_search(args)
        if name == 'apply_patch_text':
            from .patching import prepare
            return prepare(self, args)
        if name == 'list_files_page':
            from .discovery import prepare_discovery
            return prepare_discovery(args)
        if name in ('git_status', 'git_diff'):
            from .git_tools import prepare_git
            return prepare_git(self.root, name, args)
        if name in FILE_FIELDS:
            return self._prepare_file_operation(name, args)
        if name == 'run_command':
            if not isinstance(args, dict) or set(args) != {'argv', 'cwd'}:
                raise ToolError('invalid_arguments')
            from .commands import prepare_command
            return prepare_command(self.root, args['argv'], args['cwd'], read_only_paths=self.read_only_policy.paths,
                                   node_executable=self.node_executable,python_environment=self.python_environment,command_backend=self.command_backend,tool_executables=self.tool_executables,allow_workspace_executables=self.allow_workspace_executables,tool_read_paths=self.tool_read_paths,tool_sysctl_read=self.tool_sysctl_read)
        if name not in CODING_FIELDS:
            return super()._prepare(name, args)
        fields = CODING_FIELDS[name]
        if not isinstance(args, dict) or set(args) != set(fields) or any(not isinstance(args[field], str) for field in fields):
            raise ToolError('invalid_arguments')
        try:
            if any(len(value.encode('utf-8')) > MAX_BYTES for value in args.values()):
                raise ToolError('input_too_large')
        except UnicodeError:
            raise ToolError('invalid_data')
        if name == 'apply_patch':
            self._check_read_only({'name': name, 'args': args})
            path = self.path(args['path'])
            if not args['old']:
                raise ToolError('invalid_arguments')
            try:
                original, mode = self._snapshot(path)
                self._ordinary_mode(mode)
                content = original.decode('utf-8')
            except UnicodeError:
                raise ToolError('invalid_data')
            first = content.find(args['old'])
            if first < 0 or content.find(args['old'], first + 1) >= 0:
                raise ToolError('patch_conflict')
            desired = content[:first] + args['new'] + content[first + len(args['old']):]
            encoded = desired.encode('utf-8')
            if len(encoded) > MAX_BYTES:
                raise ToolError('file_too_large')
            # Hash the exact bytes used to derive the replacement, never a later read.
            return {'name': 'write_file', 'requested_tool': 'apply_patch',
                    'args': {'path': args['path'], 'content': desired},
                    'policy': 'verify', 'before': digest(original), 'desired': digest(encoded),
                    'before_mode': mode, 'desired_mode': mode}
        if name == 'search_files' and not args['query']:
            raise ToolError('invalid_arguments')
        if name == 'run_tests':
            self._check_read_only({'name': name, 'args': args})
            from .commands import _check_workspace
            _check_workspace(self.root)
            intent={'name':name,'args':args,'policy':'unsafe','test_start_dir':sandbox.test_start_dir(self.root)}
            if self.command_backend != 'nofork' or self.tool_executables or self.allow_workspace_executables or self.tool_read_paths or self.tool_sysctl_read:
                intent['command_runtime_policy'] = self.validate_command_runtime()
            if self.python_environment is not None:
                intent['python_environment_policy']=self.validate_command_runtime()['python_environment']
            return intent
        return {'name': name, 'args': args, 'policy': 'unsafe' if name == 'run_tests' else 'read'}

    def _file_parts(self, value):
        self.path(value)  # Shared visible-path, link, and special-file rules.
        parts = value.split('/')
        if len(parts) > MAX_DEPTH:
            raise ToolError('invalid_path')
        return parts

    @contextmanager
    def _parent_directory(self, value):
        parts = self._file_parts(value)
        directory = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            yield directory, parts[-1]
        finally:
            os.close(directory)

    @staticmethod
    def _file_identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @classmethod
    def _read_at(cls, directory, name, limit):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ToolError('permission_denied')
            if info.st_size > limit:
                raise ToolError('file_too_large')
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ToolError('file_too_large')
            after = os.fstat(stream.fileno())
            if cls._file_identity(info) != cls._file_identity(after):
                raise ToolError('file_changed')
            return data, after

    def _prepare_file_operation(self, name, args):
        if not isinstance(args, dict) or set(args) != FILE_FIELDS[name] or not isinstance(args['path'], str):
            raise ToolError('invalid_arguments')
        if name == 'create_directory' and type(args['parents']) is not bool:
            raise ToolError('invalid_arguments')
        if name == 'read_lines' and (any(type(args[key]) is not int for key in ('start_line', 'end_line'))
                                     or not 1 <= args['start_line'] <= args['end_line']
                                     or args['end_line'] - args['start_line'] >= MAX_PAGE_LINES):
            raise ToolError('invalid_arguments')
        try:
            self._check_read_only({'name': name, 'args': args})
            args['path'].encode('utf-8')
            self._file_parts(args['path'])
            path = self.path(args['path'])
            intent = {'name': name, 'args': args, 'policy': 'read'}
            if name == 'create_directory':
                for ancestor in (path, *path.parents):
                    if ancestor == self.root:
                        break
                    if ancestor.exists() and not ancestor.is_dir():
                        raise ToolError('not_a_directory')
                if not args['parents'] and not path.parent.is_dir():
                    raise ToolError('parent_not_found')
                intent['policy'] = 'idempotent_directory'
            elif name == 'delete_file':
                with self._parent_directory(args['path']) as (directory, filename):
                    data, info = self._read_at(directory, filename, MAX_DELETE_FILE_BYTES)
                mode = self._ordinary_mode(stat.S_IMODE(info.st_mode))
                intent.update(policy='verify_delete', before=digest(data), before_mode=mode)
            elif path.is_dir():
                raise ToolError('not_a_file')
            return intent
        except UnicodeError:
            raise ToolError('invalid_data')
        except FileNotFoundError:
            raise ToolError('not_found')
        except OSError:
            raise ToolError('file_io_error')

    def _create_directory(self, intent, timeout):
        parts = self._file_parts(intent['args']['path'])
        deadline, created = time.monotonic() + max(0, timeout), False
        directory = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                except FileNotFoundError:
                    if index < len(parts) - 1 and not intent['args']['parents']:
                        raise UncertainEffect('parent_changed')
                    if time.monotonic() >= deadline:
                        raise ToolError('tool_timeout')
                    try:
                        os.mkdir(part, 0o700, dir_fd=directory)
                        os.fsync(directory)
                        created = index == len(parts) - 1
                    except FileExistsError:
                        pass  # A raced real directory is safe; open below rejects links/files.
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            return {'ok': True, 'created': created, 'reconciled': not created}
        finally:
            os.close(directory)

    def _delete_file(self, intent, timeout):
        try:
            with self._parent_directory(intent['args']['path']) as (directory, filename):
                try:
                    data, inspected = self._read_at(directory, filename, MAX_DELETE_FILE_BYTES)
                except FileNotFoundError:
                    return {'ok': True, 'deleted': True, 'reconciled': True}
                if (digest(data) != intent['before']
                        or ('before_mode' in intent and stat.S_IMODE(inspected.st_mode) != intent['before_mode'])):
                    raise UncertainEffect('target_changed')
                if timeout <= 0:
                    raise ToolError('tool_timeout')
                current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                if self._file_identity(current) != self._file_identity(inspected):
                    raise UncertainEffect('target_changed')
                os.unlink(filename, dir_fd=directory)
                os.fsync(directory)
                return {'ok': True, 'deleted': True, 'reconciled': False}
        except FileNotFoundError:
            return {'ok': True, 'deleted': True, 'reconciled': True}

    def _read_lines(self, args):
        with self._parent_directory(args['path']) as (directory, filename):
            data, _ = self._read_at(directory, filename, MAX_LINE_FILE_BYTES)
        content = data.decode('utf-8').splitlines()
        first, end = args['start_line'], args['end_line']
        lines = []
        for number in range(first, min(end, len(content)) + 1):
            text = content[number - 1]
            if len(text.encode('utf-8')) > MAX_BYTES:
                raise ToolError('line_too_large')
            lines.append({'line': number, 'text': text})
        result = {'ok': True, 'path': args['path'], 'sha256': digest(data), 'lines': lines,
                  'next_line': end + 1 if end < len(content) else None, 'eof': end >= len(content)}
        if len(json.dumps({'result': result}, ensure_ascii=False).encode('utf-8')) + 1 > MAX_BYTES:
            raise ToolError('response_too_large')
        return result

    def _execute_file_operation(self, intent, timeout):
        self._check_read_only(intent)
        try:
            if intent['name'] == 'create_directory':
                return self._create_directory(intent, timeout)
            if intent['name'] == 'delete_file':
                return self._delete_file(intent, timeout)
            return self._read_lines(intent['args'])
        except UnicodeError:
            raise ToolError('invalid_data')
        except FileNotFoundError:
            raise ToolError('not_found')
        except (OSError, ToolError) as error:
            if intent['name'] != 'read_lines' and not (isinstance(error, ToolError) and error.code == 'tool_timeout'):
                raise UncertainEffect('target_unverifiable') from None
            if isinstance(error, ToolError):
                raise
            raise ToolError('file_io_error') from None

    def _recover(self, intent, timeout):
        if intent['name'] == 'edit_file':
            from .editing import execute_edit
            return execute_edit(self, intent, timeout, recovering=True)
        if intent['name'] == 'patch_step':
            from .patching import execute_step
            return execute_step(self, intent, timeout, recovering=True)
        if intent['name'] in ('create_directory', 'delete_file'):
            return self._execute_file_operation(intent, timeout)
        return super()._recover(intent, timeout)

    def _visible_files(self):
        files, entries, size = [], 0, 0

        def visit(directory, depth):
            nonlocal entries, size
            if depth > MAX_DEPTH:
                raise ToolError('listing_too_large')
            with os.scandir(directory) as items:
                for item in items:
                    entries += 1
                    if entries > MAX_ENTRIES:
                        raise ToolError('listing_too_large')
                    if item.name.startswith('.') or item.is_symlink():
                        continue
                    relative = str(os.path.relpath(item.path, self.root))
                    # Apply the same special-file/path rules as legacy file tools.
                    try:
                        self.path(relative)
                    except ToolError:
                        continue
                    info = item.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        visit(item.path, depth + 1)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        files.append(relative)
                        size += len(relative.encode('utf-8')) + 8
                        if len(files) > MAX_FILES or size > MAX_BYTES:
                            raise ToolError('listing_too_large')
        try:
            visit(self.root, 0)
        except OSError:
            raise ToolError('file_io_error')
        return sorted(files)

    def _execute(self, intent, timeout):
        if intent['name'] == 'edit_file':
            from .editing import execute_edit
            return execute_edit(self, intent, timeout)
        if intent['name'] == 'patch_step':
            from .patching import execute_step
            return execute_step(self, intent, timeout)
        self._check_read_only(intent)
        if timeout <= 0:
            raise ToolError('tool_timeout')
        name = intent['name']
        if name == 'search_files_page':
            from .searching import execute_search
            return execute_search(self, intent['args'], timeout)
        if name == 'list_files_page':
            from .discovery import execute_discovery
            return execute_discovery(self.root, intent['args'], timeout)
        if name in ('git_status', 'git_diff'):
            from .git_tools import execute_git
            return execute_git(self.root, intent, timeout)
        if name in FILE_FIELDS:
            return self._execute_file_operation(intent, timeout)
        if name == 'list_files':
            return {'ok': True, 'files': self._visible_files()}
        if name == 'search_files':
            matches, skipped, scanned = [], [], 0
            for relative in self._visible_files():
                try:
                    data = self._bytes(self.path(relative))
                    scanned += len(data)
                    if scanned > MAX_SEARCH_BYTES:
                        raise ToolError('search_too_large')
                    content = data.decode('utf-8')
                except UnicodeError:
                    skipped.append(relative)
                    continue
                except ToolError as error:
                    if error.code == 'file_too_large':
                        skipped.append(relative)
                        continue
                    raise
                for number, line in enumerate(content.splitlines(), 1):
                    if intent['args']['query'] in line:
                        matches.append({'path': relative, 'line': number, 'text': line})
                        if len(matches) > MAX_FILES or len(json.dumps(matches, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
                            raise ToolError('search_too_large')
            result = {'ok': True, 'matches': matches, 'skipped_files': skipped}
            if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
                raise ToolError('search_too_large')
            return result
        if name == 'run_command':
            return sandbox.run_command(self.root, intent, timeout, read_only_paths=self.read_only_policy.paths,
                                       **({'node_executable': self.node_executable} if self.node_executable else {}),
                                       **({'python_environment':self.python_environment} if self.python_environment else {}),
                                       **({'command_backend':self.command_backend} if self.command_backend!='nofork' else {}),
                                       **({'tool_executables':self.tool_executables} if self.tool_executables else {}),
                                       **({'allow_workspace_executables':True} if self.allow_workspace_executables else {}),
                                       **({'tool_read_paths':self.tool_read_paths} if self.tool_read_paths else {}),
                                       **({'tool_sysctl_read':self.tool_sysctl_read} if self.tool_sysctl_read else {}))
        if name == 'run_tests':
            if self.command_backend != 'nofork' or self.tool_executables or self.allow_workspace_executables or self.tool_read_paths or self.tool_sysctl_read or 'command_runtime_policy' in intent:
                current_policy=self.validate_command_runtime()
                expected_policy=current_policy if self.command_backend!='nofork' or self.tool_executables or self.allow_workspace_executables or self.tool_read_paths or self.tool_sysctl_read else None
                if json.dumps(intent.get('command_runtime_policy'),sort_keys=True)!=json.dumps(expected_policy,sort_keys=True):
                    raise ToolError('command_runtime_changed')
            current=self.validate_command_runtime() if self.python_environment is not None else None
            expected=current['python_environment'] if current and 'python_environment' in current else None
            if json.dumps(intent.get('python_environment_policy'),sort_keys=True)!=json.dumps(expected,sort_keys=True):
                raise ToolError('command_runtime_changed')
            return sandbox.run_tests(self.root, timeout, read_only_paths=self.read_only_policy.paths,
                                     start_dir=intent.get('test_start_dir'),
                                     **({'node_executable':self.node_executable,'command_runtime_policy':current_policy}
                                        if self.tool_executables or self.allow_workspace_executables or self.tool_read_paths or self.tool_sysctl_read else {}),
                                     **({'python_environment':self.python_environment,'python_environment_policy':expected}
                                        if self.python_environment is not None else {}),
                                     **({'command_backend':self.command_backend} if self.command_backend!='nofork' else {}),
                                       **({'tool_executables':self.tool_executables} if self.tool_executables else {}),
                                       **({'allow_workspace_executables':True} if self.allow_workspace_executables else {}),
                                       **({'tool_read_paths':self.tool_read_paths} if self.tool_read_paths else {}),
                                       **({'tool_sysctl_read':self.tool_sysctl_read} if self.tool_sysctl_read else {}))
        return super()._execute(intent, timeout)
