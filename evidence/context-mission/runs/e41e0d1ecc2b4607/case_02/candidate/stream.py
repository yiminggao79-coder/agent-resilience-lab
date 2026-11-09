"""Incremental decoder for the three-field wire format.

Chunks are accumulated until a physical newline arrives; each complete
physical line (terminator included) is handed to ``wire.decode_line``.
"""
from wire import decode_line


class Decoder:
    """Transactional incremental decoder for terminated wire lines."""

    def __init__(self):
        self._pending = ''
        self._accepted_chars = 0
        self._records_seen = 0
        self._closed = False

    def feed(self, chunk):
        if not isinstance(chunk, str):
            raise ValueError('chunk must be a string')
        if self._closed:
            raise ValueError('decoder is closed')
        text = self._pending + chunk
        boundary = text.rfind('\n')
        if boundary < 0:
            head, tail = '', text
        else:
            head, tail = text[:boundary + 1], text[boundary + 1:]
        # Decode everything before committing any state: all or nothing.
        records = [decode_line(piece) for piece in head.splitlines(keepends=True)]
        self._pending = tail
        self._accepted_chars += len(chunk)
        self._records_seen += len(records)
        return records

    def finish(self):
        if self._pending:
            raise ValueError('incomplete trailing line')
        self._closed = True
        return []

    def snapshot(self):
        return {
            'accepted_chars': self._accepted_chars,
            'pending': self._pending,
            'records_seen': self._records_seen,
            'closed': self._closed,
        }
