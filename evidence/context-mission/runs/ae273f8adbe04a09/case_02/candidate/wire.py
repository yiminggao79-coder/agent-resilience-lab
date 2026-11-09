"""Three fields separated by pipes, terminated by one newline.

Escaping rules:
  * a backslash is written as two backslashes,
  * a pipe is written as a backslash followed by a pipe,
  * an embedded newline is written as a backslash followed by lowercase ``n``.

No other escape sequence is valid. An empty value or unit field is legal and
must be preserved. Decoding accepts exactly one terminated physical line: it
must end with one newline and contain no other raw physical newline.
"""
from fields import record

_KEYS = ('tag', 'value', 'unit')
_ESCAPES = {'\\': '\\', '|': '|', 'n': '\n'}


def _encode_field(text):
    out = []
    for char in text:
        if char == '\\':
            out.append('\\\\')
        elif char == '|':
            out.append('\\|')
        elif char == '\n':
            out.append('\\n')
        else:
            out.append(char)
    return ''.join(out)


def _decode_field(text):
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '\\':
            index += 1
            if index >= length:
                raise ValueError('dangling escape')
            escape = text[index]
            if escape not in _ESCAPES:
                raise ValueError('unknown escape')
            out.append(_ESCAPES[escape])
        elif char == '|':
            raise ValueError('unescaped separator')
        else:
            out.append(char)
        index += 1
    return ''.join(out)


def encode_record(value):
    value = record(value)
    return '|'.join(_encode_field(value[key]) for key in _KEYS) + '\n'


def decode_line(line):
    if not isinstance(line, str) or not line.endswith('\n') or '\n' in line[:-1]:
        raise ValueError('expected one terminated physical line')
    body = line[:-1]
    parts = []
    current = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char == '\\':
            if index + 1 >= length:
                raise ValueError('dangling escape')
            current.append(char)
            current.append(body[index + 1])
            index += 2
            continue
        if char == '|':
            parts.append(''.join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append(''.join(current))
    if len(parts) != 3:
        raise ValueError('expected three fields')
    decoded = [_decode_field(part) for part in parts]
    return record(dict(zip(_KEYS, decoded)))
