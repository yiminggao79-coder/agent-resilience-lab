"""Three fields separated by pipes, terminated by one newline.

Wire grammar (canonical, exactly three fields ``tag|value|unit`` followed by
one physical newline):

* a literal backslash is written as ``\\\\``
* a literal pipe is written as ``\\|``
* an embedded newline is written as ``\\n`` (backslash + lowercase ``n``)

No other escape is valid. Empty value/unit fields are legal and are emitted
as empty between separators rather than being dropped. Only the three
unescaped pipes separate fields; escaped pipes/newlines belong to a field.
"""
from fields import tag as _tag
from fields import record

_SPECIAL = {'\\': '\\\\', '|': '\\|', '\n': '\\n'}
_UNESCAPE = {'\\': '\\', '|': '|', 'n': '\n'}


def _escape_field(text):
    return ''.join(_SPECIAL.get(char, char) for char in text)


def encode_record(value):
    value = record(value)
    encoded = '|'.join(_escape_field(value[key]) for key in ('tag', 'value', 'unit'))
    if '\r' in encoded:
        raise ValueError('carriage returns are invalid')
    return encoded + '\n'


def _split_fields(body):
    """Split an unterminated line body on unescaped pipes, unescaping fields."""
    parts = [[]]
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char == '\\':
            if index + 1 >= length:
                raise ValueError('dangling escape')
            following = body[index + 1]
            if following not in _UNESCAPE:
                raise ValueError('unknown escape')
            parts[-1].append(_UNESCAPE[following])
            index += 2
        elif char == '|':
            parts.append([])
            index += 1
        else:
            parts[-1].append(char)
            index += 1
    return [''.join(part) for part in parts]


def decode_line(line):
    if not isinstance(line, str) or not line.endswith('\n'):
        raise ValueError('expected one terminated physical line')
    body = line[:-1]
    if '\n' in body or '\r' in body:
        raise ValueError('expected one terminated physical line')
    parts = _split_fields(body)
    if len(parts) != 3:
        raise ValueError('expected three fields')
    _tag(parts[0])
    return record(dict(zip(('tag','value','unit'), parts)))
