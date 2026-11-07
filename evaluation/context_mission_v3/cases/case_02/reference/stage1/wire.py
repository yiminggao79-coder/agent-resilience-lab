"Strict reversible escapes for three-field physical lines."
from fields import record

def encode_record(value):
    value = record(value)
    def escape(text):
        return text.replace('\\','\\\\').replace('|','\\|').replace('\n','\\n')
    return '|'.join(escape(value[key]) for key in ('tag','value','unit')) + '\n'

def decode_line(line):
    if not isinstance(line,str) or not line.endswith('\n') or '\n' in line[:-1]:
        raise ValueError('expected one terminated physical line')
    parts, current, escaped = [], [], False
    for character in line[:-1]:
        if escaped:
            if character not in ('\\','|','n'):
                raise ValueError('unknown escape')
            current.append('\n' if character == 'n' else character)
            escaped = False
        elif character == '\\':
            escaped = True
        elif character == '|':
            parts.append(''.join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        raise ValueError('dangling escape')
    parts.append(''.join(current))
    if len(parts) != 3:
        raise ValueError('expected three fields')
    return record(dict(zip(('tag','value','unit'),parts)))
