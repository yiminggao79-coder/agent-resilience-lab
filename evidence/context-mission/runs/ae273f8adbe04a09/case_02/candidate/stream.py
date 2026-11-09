"""Incremental decoder over the terminated physical-line wire format."""
from wire import decode_line


class Decoder:
    def __init__(self):
        self._accepted_chars = 0
        self._pending = ''
        self._records_seen = 0
        self._closed = False

    def feed(self, chunk):
        if not isinstance(chunk, str):
            raise ValueError('chunk must be text')
        if self._closed:
            raise ValueError('decoder is closed')

        # Work on a stage so a failure leaves every state field untouched.
        combined = self._pending + chunk
        pieces = combined.split('\n')
        tail = pieces.pop()
        records = [decode_line(piece + '\n') for piece in pieces]

        self._accepted_chars += len(chunk)
        self._pending = tail
        self._records_seen += len(records)
        return records

    def finish(self):
        if self._pending:
            raise ValueError('unterminated pending line')
        self._closed = True
        return []

    def snapshot(self):
        return {
            'accepted_chars': self._accepted_chars,
            'pending': self._pending,
            'records_seen': self._records_seen,
            'closed': self._closed,
        }
