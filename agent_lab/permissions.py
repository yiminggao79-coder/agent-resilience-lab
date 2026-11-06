"""Small host-owned read-only carveouts; no model-controlled exceptions."""
import hashlib
import json
import os
from pathlib import Path
import stat
import unicodedata

MAX_SCOPES = 32
MAX_PATH_BYTES = 512
MAX_DEPTH = 32
MAX_ENTRIES = 10000


def _key(value):
    # Conservatively protect aliases on case-insensitive/normalizing filesystems.
    # Case-sensitive filesystems may therefore protect additional spellings.
    return unicodedata.normalize('NFD', value).casefold()


def _parts(value):
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise ValueError('invalid_read_only_path')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise ValueError('invalid_read_only_path') from None
    parts = value.split('/')
    if (size > MAX_PATH_BYTES or len(parts) > MAX_DEPTH or Path(value).is_absolute()
            or any(not part or part in ('.', '..') or part.startswith('.') for part in parts)):
        raise ValueError('invalid_read_only_path')
    return parts


class ReadOnlyPolicy:
    def __init__(self, root, paths=()):
        self.root = Path(root)
        if not isinstance(paths, (tuple, list)) or len(paths) > MAX_SCOPES:
            raise ValueError('invalid_read_only_paths')
        # Validate every spelling before root dominance; canonicalizing ignored
        # descendants would unnecessarily enumerate a fully protected tree.
        parts = [None if isinstance(value, str) and value == '.' else _parts(value) for value in paths]
        chosen = ['.'] if None in parts else []
        if not chosen:
            scopes = sorted({self._canonical(value) for value in parts})
            for scope in sorted(scopes, key=lambda value: (value.count('/'), value)):
                if not any(_key(scope) == _key(parent) or _key(scope).startswith(_key(parent) + '/') for parent in chosen):
                    chosen.append(scope)
        self.paths = tuple(sorted(chosen))
        self.manifest = {'version': 1, 'read_only_paths': list(self.paths)}
        self.identity = hashlib.sha256(json.dumps(self.manifest, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
        self.validate()

    @property
    def whole_workspace(self):
        return self.paths == ('.',)

    def _canonical(self, parts):
        """Use existing native spellings so Seatbelt binds the actual pathname."""
        current, names = self.root, []
        for part in parts:
            if current.is_symlink():
                raise ValueError('invalid_read_only_path')
            if current.is_dir():
                matches, count = [], 0
                with os.scandir(current) as entries:
                    for entry in entries:
                        count += 1
                        if count > MAX_ENTRIES:
                            raise ValueError('read_only_scope_too_large')
                        if _key(entry.name) == _key(part):
                            matches.append(entry.name)
                if len(matches) > 1:
                    raise ValueError('ambiguous_read_only_path')
                if matches:
                    part = matches[0]
            names.append(part)
            current = current / part
        return '/'.join(names)

    def validate(self):
        """Reject aliases/special files in protected scopes before every boundary."""
        if self.whole_workspace:
            try:
                if not stat.S_ISDIR(self.root.lstat().st_mode):
                    raise ValueError('invalid_read_only_path')
            except OSError:
                raise ValueError('invalid_read_only_path') from None
            return
        count = 0
        def inspect(path, depth=0):
            nonlocal count
            count += 1
            if count > MAX_ENTRIES or depth > MAX_DEPTH:
                raise ValueError('read_only_scope_too_large')
            try:
                info = path.lstat()
            except FileNotFoundError:
                return
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise ValueError('invalid_read_only_path')
            elif stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    for entry in entries:
                        inspect(Path(entry.path), depth + 1)
            else:
                raise ValueError('invalid_read_only_path')
        try:
            for scope in self.paths:
                current = self.root
                for part in scope.split('/')[:-1]:
                    current = current / part
                    if current.is_symlink() or (current.exists() and not current.is_dir()):
                        raise ValueError('invalid_read_only_path')
                inspect(self.root / scope)
        except OSError:
            raise ValueError('invalid_read_only_path') from None

    def check_mutation(self, value):
        if self.whole_workspace:
            raise ValueError('read_only_path')
        key = _key('/'.join(_parts(value)))
        for scope in self.paths:
            protected = _key(scope)
            if key == protected or key.startswith(protected + '/') or protected.startswith(key + '/'):
                raise ValueError('read_only_path')

    def check_scratch(self, scratch):
        if not self.whole_workspace:
            return
        try:
            root_parts = tuple(_key(part) for part in self.root.resolve().parts)
            scratch_parts = tuple(_key(part) for part in Path(scratch).resolve().parts)
            if (root_parts == scratch_parts[:len(root_parts)]
                    or scratch_parts == root_parts[:len(scratch_parts)]):
                raise ValueError('read_only_scratch_overlap')
        except (OSError, ValueError, TypeError, RuntimeError):
            raise ValueError('read_only_scratch_overlap') from None

    def check_native(self):
        # Native Unicode normalization aliases are not safely represented by
        # the current Seatbelt literal rules. Withhold execution authority.
        if self.paths and (not str(self.root.absolute()).isascii()
                           or any(not scope.isascii() for scope in self.paths)):
            raise ValueError('read_only_native_path_unsupported')

    def protected_ancestors(self):
        if self.whole_workspace:
            return [self.root]
        ancestors = {self.root}
        for scope in self.paths:
            path = (self.root / scope).parent
            while path != self.root:
                ancestors.add(path)
                path = path.parent
        return sorted(ancestors)
