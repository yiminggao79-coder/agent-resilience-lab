"""Incremental decoder for the ``tag|value|unit`` wire format.

``Decoder.feed`` accepts arbitrary string chunks, combines them with any
retained incomplete tail and decodes only complete *physical* lines.  Each
feed is transactional: if any complete line in the chunk is invalid, the
decoder's state is restored exactly and ``ValueError`` is raised.
"""
from wire import decode_line


class Decoder(object):
    def __init__(self):
        self._pending = ''
        self._accepted_chars = 0
        self._records_seen = 0
        self._closed = False

    def feed(self, chunk):
        if not isinstance(chunk, str):
            raise ValueError('chunk must be a str')
        if self._closed:
            raise ValueError('decoder is closed')

        combined = self._pending + chunk
        cut = combined.rfind('\n')
        if cut < 0:
            complete_text = ''
            tail = combined
        else:
            complete_text = combined[:cut + 1]
            tail = combined[cut + 1:]

        # Build everything before committing any state so a failure leaves
        # the decoder exactly as it was before this feed.
        decoded = []
        if complete_text:
            lines = complete_text[:-1].split('\n')
            decoded = [decode_line(line + '\n') for line in lines]

        self._pending = tail
        self._accepted_chars += len(chunk)
        self._records_seen += len(decoded)
        return decoded

    def finish(self):
        if self._pending:
            raise ValueError('incomplete line pending')
        self._closed = True
        return []

    def snapshot(self):
        return {
            'accepted_chars': self._accepted_chars,
            'pending': self._pending,
            'records_seen': self._records_seen,
            'closed': self._closed,
        }
