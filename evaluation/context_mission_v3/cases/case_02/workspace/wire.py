"""Three fields separated by pipes, terminated by one newline."""
from fields import record

def encode_record(value):
    value = record(value)
    return '|'.join(value[key] for key in ('tag','value','unit')) + '\n'

def decode_line(line):
    if not isinstance(line, str) or not line.endswith('\n') or '\n' in line[:-1]:
        raise ValueError('expected one terminated physical line')
    parts = [part for part in line[:-1].split('|') if part]
    if len(parts) != 3:
        raise ValueError('expected three fields')
    return record(dict(zip(('tag','value','unit'),parts)))
