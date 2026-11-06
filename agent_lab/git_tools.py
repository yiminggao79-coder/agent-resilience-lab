"""Fixed, read-only Git capabilities; no user-selected argv or executable."""
import json
import os
from pathlib import Path
import stat

from .tools import MAX_BYTES, Tools, ToolError, ToolUnavailable

GIT_BINARY = '/Library/Developer/CommandLineTools/usr/bin/git'
MAX_STATUS_ENTRIES = 200
MAX_METADATA_ENTRIES = 20000
MAX_GIT_OUTPUT = 32768


def trusted_git():
    path = Path(GIT_BINARY)
    try:
        info = path.stat()
        if (str(path) != '/Library/Developer/CommandLineTools/usr/bin/git' or path.is_symlink()
                or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or not os.access(path, os.X_OK)):
            raise ToolUnavailable('git_unavailable')
    except OSError:
        raise ToolUnavailable('git_unavailable') from None
    return str(path)


def validate_repository(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ToolError('permission_denied')
    root = root.resolve()
    metadata = root/'.git'
    if metadata.is_symlink() or not metadata.is_dir():
        raise ToolUnavailable('git_repository_not_supported')
    for relative in ('commondir', 'gitdir', 'objects/info/alternates', 'objects/info/http-alternates'):
        if (metadata/relative).exists() or (metadata/relative).is_symlink():
            raise ToolUnavailable('git_repository_not_supported')
    count = 0
    def visit(directory, depth):
        nonlocal count
        if depth > 32:
            raise ToolError('git_metadata_too_large')
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > MAX_METADATA_ENTRIES:
                    raise ToolError('git_metadata_too_large')
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    visit(Path(entry.path), depth+1)
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ToolUnavailable('git_repository_not_supported')
    try:
        visit(metadata, 0)
        if not (metadata/'HEAD').is_file():
            raise ToolUnavailable('git_repository_not_supported')
    except OSError:
        raise ToolUnavailable('git_repository_not_supported') from None
    return root


def prepare_git(root, name, args):
    fields = {'git_status': set(), 'git_diff': {'path'}}
    if name not in fields or not isinstance(args, dict) or set(args) != fields[name]:
        raise ToolError('invalid_arguments')
    if name == 'git_diff':
        # Invalid/forbidden arguments remain invocation errors even when the
        # optional repository capability itself is unavailable.
        try:
            path = Tools(root).path(args['path'])
        except ValueError:
            raise ToolError('permission_denied') from None
        if path.is_dir():
            raise ToolError('not_a_file')
    root = validate_repository(root)
    trusted_git()
    return {'name': name, 'args': dict(args), 'policy': 'read'}


def validate_git(root, intent):
    if not isinstance(intent, dict) or set(intent) != {'name', 'args', 'policy'} or intent['policy'] != 'read':
        raise ToolError('invalid_arguments')
    if prepare_git(root, intent['name'], intent['args']) != intent:
        raise ToolError('invalid_arguments')
    return intent


def git_argv(root, intent):
    validate_git(root, intent)
    root = Path(root).resolve()
    argv = [trusted_git(), '--no-pager', '--no-optional-locks', '--literal-pathspecs',
            '--git-dir=' + str(root/'.git'), '--work-tree=' + str(root)]
    config = ('core.fsmonitor=false', 'core.hooksPath=/dev/null', 'core.pager=', 'core.bare=false',
              'core.attributesFile=/dev/null', 'core.excludesFile=/dev/null',
              'submodule.recurse=false', 'status.renames=false', 'diff.renames=false',
              'core.untrackedCache=false', 'core.worktree=' + str(root))
    for option in config:
        argv.extend(['-c', option])
    if intent['name'] == 'git_status':
        argv.extend(['status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=all', '--no-renames'])
    else:
        argv.extend(['diff', '--no-ext-diff', '--no-textconv', '--no-color', '--no-renames',
                     '--ignore-submodules=all', '--src-prefix=a/', '--dst-prefix=b/', '--', intent['args']['path']])
    return argv


def git_environment(scratch):
    return {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'LC_ALL': 'C', 'HOME': str(scratch), 'TMPDIR': str(scratch),
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': '',
            'GIT_LITERAL_PATHSPECS': '1', 'GIT_NO_LAZY_FETCH': '1'}


def _size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def execute_git(root, intent, timeout):
    from . import sandbox
    validate_git(root, intent)
    response = sandbox.run_git(root, intent, timeout)
    result = {'ok': response['exit_code'] == 0, 'exit_code': response['exit_code'],
              'truncated': response['stdout_truncated'] or response['stderr_truncated']}
    if not result['ok']:
        result['error'] = 'git_failed'
        return result  # Never disclose repository-selected paths/config via stderr.
    if intent['name'] == 'git_diff':
        result.update(path=intent['args']['path'], diff=response['stdout'])
        if _size(result) > MAX_BYTES:
            low, high, text = 0, len(result['diff']), result['diff']
            result['truncated'] = True
            while low < high:
                middle = (low+high+1)//2
                result['diff'] = text[:middle]
                if _size(result) <= MAX_BYTES:
                    low = middle
                else:
                    high = middle-1
            result['diff'] = text[:low]
        return result
    result['entries'] = []
    records = response['stdout'].split('\x00')
    if records[-1]:
        if not response['stdout_truncated']:
            raise ToolError('invalid_git_output')
        result['truncated'] = True
    records.pop()  # Remove empty terminator or incomplete truncated record.
    tools = Tools(root)
    for record in records:
        if len(record) < 4 or record[2] != ' ' or any(c not in ' MADRCUT?!' for c in record[:2]):
            raise ToolError('invalid_git_output')
        relative = record[3:]
        try:
            path = tools.path(relative)
            if path.is_dir():
                continue
        except ToolError:
            continue  # Hidden, escaping, linked, and special paths are not disclosed.
        result['entries'].append({'path': relative, 'index': record[0], 'worktree': record[1]})
        if len(result['entries']) > MAX_STATUS_ENTRIES or _size(result) > MAX_BYTES:
            result['entries'].pop()
            result['truncated'] = True
            break
    return result
