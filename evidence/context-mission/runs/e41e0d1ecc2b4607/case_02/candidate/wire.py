"""Three fields separated by pipes, terminated by one newline.

Escaping rules (exactly these, nothing else):
    ``\\`` -> ``\\\\``   (backslash is written as a literal backslash escape)
    ``|``  -> ``\\|``    (pipe is written as a literal pipe escape)
    newline -> ``\\n``   (two characters: backslash followed by lowercase n)

Only the two character sequence backslash + ``n`` stands for an embedded
newline; a raw backslash before anything else is an unknown escape.
"""
from fields import record

_KEYS = ('tag', 'value', 'unit')
_ESCAPES = {'\\': '\\\\', '|': '\\|', '\n': '\\n'}
_UNESCAPES = {'\\': '\\', '|': '|', 'n': '\n'}


def _encode_field(text):
    return ''.join(_ESCAPES.get(char, char) for char in text)


def _decode_field(text):
    if '\\' not in text:
        return text
    pieces = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != '\\':
            pieces.append(char)
            index += 1
            continue
        if index + 1 >= length:
            raise ValueError('dangling escape')
        escaped = _UNESCAPES.get(text[index + 1])
        if escaped is None:
            raise ValueError('unknown escape')
        pieces.append(escaped)
        index += 2
    return ''.join(pieces)


def encode_record(value):
    value = record(value)
    return '|'.join(_encode_field(value[key]) for key in _KEYS) + '\n'


def decode_line(line):
    if not isinstance(line, str) or not line.endswith('\n'):
        raise ValueError('expected one terminated physical line')
    if '\n' in line[:-1]:
        raise ValueError('raw embedded newline')
    # Split on unescaped separators only; an escaped pipe belongs to a field.
    fields = []
    current = []
    index = 0
    length = len(line) - 1  # drop the terminator
    while index < length:
        char = line[index]
        if char == '\\':
            if index + 1 >= length:
                raise ValueError('dangling escape')
            if line[index + 1] not in _UNESCAPES:
                raise ValueError('unknown escape')
            current.append(char)
            current.append(line[index + 1])
            index += 2
            continue
        if char == '|':
            fields.append(''.join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    fields.append(''.join(current))
    if len(fields) != 3:
        raise ValueError('expected three fields')
    decoded = {key: _decode_field(item) for key, item in zip(_KEYS, fields)}
    return record(decoded)
