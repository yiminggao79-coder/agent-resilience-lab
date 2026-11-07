"""Read zero or more complete physical lines."""
from wire import decode_line

def load(text):
    if not isinstance(text, str):
        raise ValueError('batch must be text')
    if not text:
        return []
    if not text.endswith('\n'):
        raise ValueError('unterminated final line')
    return [decode_line(line + '\n') for line in text[:-1].split('\n')]
