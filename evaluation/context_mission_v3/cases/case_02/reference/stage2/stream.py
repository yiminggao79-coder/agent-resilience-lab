"""Incremental decoder with transactional feed and explicit close state."""
from wire import decode_line

class Decoder:
    def __init__(self):
        self._pending = ''
        self._accepted_chars = 0
        self._records_seen = 0
        self._closed = False

    def feed(self, chunk):
        if self._closed or not isinstance(chunk,str):
            raise ValueError('decoder closed or chunk is not text')
        combined = self._pending + chunk
        complete = combined.split('\n')
        pending = complete.pop()
        records = [decode_line(line + '\n') for line in complete]
        self._pending = pending
        self._accepted_chars += len(chunk)
        self._records_seen += len(records)
        return records

    def finish(self):
        if self._pending:
            raise ValueError('incomplete physical line')
        self._closed = True
        return []

    def snapshot(self):
        return {'accepted_chars':self._accepted_chars, 'pending':self._pending,
                'records_seen':self._records_seen, 'closed':self._closed}
