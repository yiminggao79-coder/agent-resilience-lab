"""Bounded, lossy private display bytes, separate from native result authority."""
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import os
import stat

OUTPUT_ENV = 'AGENT_LAB_TOOL_OUTPUT_FD'
MAX_DISPLAY_BYTES = 65536
MAX_CHUNK_BYTES = 192
MAX_FRAME_BYTES = 512
MAX_DATA_FRAMES = 1024
MAX_WIRE_BYTES = (MAX_DATA_FRAMES + 1) * MAX_FRAME_BYTES
MAX_DRAIN_READS = 128
_MAX_COUNTER = (1 << 63) - 1
_STREAMS = ('stdout', 'stderr')
_OUTPUT = ContextVar('native_private_output', default=None)


def _encoded(value):
    return (json.dumps(value, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


class _Sender:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.sequence = 0
        self.seen = dict.fromkeys(_STREAMS, 0)
        self.sent = dict.fromkeys(_STREAMS, 0)
        self.broken = False

    def write(self, value):
        data = _encoded(value)
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError('invalid_tool_output')
        if self.broken:
            return False
        try:
            # <= POSIX's minimum PIPE_BUF: all bytes or no bytes, never blocking.
            return os.write(self.descriptor, data) == len(data)
        except BlockingIOError:
            return False
        except OSError:
            self.broken = True
            return False

    def emit(self, stream, data):
        before = self.seen[stream]
        self.seen[stream] = min(_MAX_COUNTER, before + len(data))
        eligible = min(len(data), max(0, MAX_DISPLAY_BYTES - before))
        for offset in range(0, eligible, MAX_CHUNK_BYTES):
            if self.sequence >= MAX_DATA_FRAMES:
                break
            chunk = data[offset:min(offset + MAX_CHUNK_BYTES, eligible)]
            frame = {'v': 1, 't': 'd', 's': stream, 'q': self.sequence,
                     'd': base64.b64encode(chunk).decode('ascii')}
            self.sequence += 1  # Failed write attempts leave detectable gaps.
            if self.write(frame):
                self.sent[stream] += len(chunk)

    def finish(self):
        counts = lambda stream: [self.seen[stream], self.sent[stream], self.seen[stream] - self.sent[stream]]
        self.write({'v': 1, 't': 'e', 'q': self.sequence,
                    'o': counts('stdout'), 'e': counts('stderr')})


@contextmanager
def inherited_output(producer=False):
    """Consume trusted environment once; only a supervisor produces frames."""
    value = os.environ.pop(OUTPUT_ENV, None)
    descriptor = None
    if value is not None:
        try:
            descriptor = int(value)
            if descriptor < 3 or str(descriptor) != value:
                descriptor = None
                raise ValueError()
            if (not stat.S_ISFIFO(os.fstat(descriptor).st_mode)
                    or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY):
                raise ValueError()
            os.set_inheritable(descriptor, False)
            os.set_blocking(descriptor, False)
        except (ValueError, OSError):
            if descriptor is not None:
                os.close(descriptor)
            raise ValueError('invalid_tool_output') from None
    sender = _Sender(descriptor) if descriptor is not None and producer else None
    token = _OUTPUT.set((descriptor, sender))
    try:
        yield
    finally:
        try:
            if sender is not None:
                sender.finish()
        finally:
            _OUTPUT.reset(token)
            if descriptor is not None:
                os.close(descriptor)


def forwarded_output():
    state = _OUTPUT.get()
    descriptor = state[0] if state else None
    return ({OUTPUT_ENV: str(descriptor)}, (descriptor,)) if descriptor is not None else ({}, ())


def emit_output(stream, data):
    state = _OUTPUT.get()
    if state is None or state[1] is None:
        return
    if stream not in _STREAMS or not isinstance(data, bytes):
        raise ValueError('invalid_tool_output')
    state[1].emit(stream, data)


class NativeOutputPipe:
    """Parent-owned bounded reader; finish never waits for producer EOF."""
    def __init__(self, callback):
        if callback is not None and not callable(callback):
            raise ValueError('invalid_tool_output_callback')
        self.callback = callback
        self.environment = {}
        self.pass_fds = ()
        self.read = self.write = None
        self.buffer = bytearray()
        self.wire_bytes = 0
        self.sequence = 0
        self.received = dict.fromkeys(_STREAMS, 0)
        self.frames = 0
        self.bad = self.gap = self.finished = False
        self.summary = None

    def __enter__(self):
        if self.callback is not None:
            self.read, self.write = os.pipe()
            os.set_blocking(self.read, False)
            os.set_blocking(self.write, False)
            self.environment = {OUTPUT_ENV: str(self.write)}
            self.pass_fds = (self.write,)
        return self

    def _deliver(self, event):
        if self.callback is not None:
            try:
                self.callback(event)
            except KeyboardInterrupt:
                self.callback = None
                raise
            except Exception:
                self.callback = None

    def _line(self, raw):
        if self.bad:
            return
        try:
            if len(raw) + 1 > MAX_FRAME_BYTES or self.summary is not None:
                raise ValueError()
            value = json.loads(raw.decode('ascii'), object_pairs_hook=_unique)
            if not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1:
                raise ValueError()
            sequence = value.get('q')
            if type(sequence) is not int or not self.sequence <= sequence <= MAX_DATA_FRAMES:
                raise ValueError()
            if sequence != self.sequence:
                self.gap = True
            if value.get('t') == 'd':
                if set(value) != {'v', 't', 's', 'q', 'd'} or value['s'] not in _STREAMS or sequence >= MAX_DATA_FRAMES:
                    raise ValueError()
                data = base64.b64decode(value['d'], validate=True)
                stream = value['s']
                if not 1 <= len(data) <= MAX_CHUNK_BYTES or self.received[stream] + len(data) > MAX_DISPLAY_BYTES:
                    raise ValueError()
                self.sequence = sequence + 1
                self.received[stream] += len(data)
                self.frames += 1
                self._deliver({'type': 'tool_output_delta', 'stream': stream, 'data': data})
            elif value.get('t') == 'e':
                if set(value) != {'v', 't', 'q', 'o', 'e'}:
                    raise ValueError()
                summary = {}
                for stream, key in zip(_STREAMS, ('o', 'e')):
                    counts = value[key]
                    if (not isinstance(counts, list) or len(counts) != 3
                            or any(type(n) is not int or not 0 <= n <= _MAX_COUNTER for n in counts)):
                        raise ValueError()
                    seen, sent, dropped = counts
                    if (sent > MAX_DISPLAY_BYTES or seen != sent + dropped
                            or sent < self.received[stream]):
                        raise ValueError()
                    summary[stream] = counts
                self.summary = summary
            else:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self.bad = True

    def drain(self):
        if self.read is None or self.finished:
            return
        for _ in range(MAX_DRAIN_READS):
            try:
                data = os.read(self.read, 8192)
            except BlockingIOError:
                return
            if not data:
                return
            self.wire_bytes = min(MAX_WIRE_BYTES + 1, self.wire_bytes + len(data))
            if self.wire_bytes > MAX_WIRE_BYTES:
                self.bad = True
            if self.bad:
                self.buffer.clear()
                continue
            self.buffer.extend(data)
            while b'\n' in self.buffer:
                raw, _, remaining = self.buffer.partition(b'\n')
                self.buffer = bytearray(remaining)
                self._line(raw)
            if len(self.buffer) >= MAX_FRAME_BYTES:
                self.bad = True
                self.buffer.clear()
        self.bad = True  # Bound a hostile continuously writable descriptor too.

    def finish(self):
        if self.finished:
            return
        self.drain()
        self.finished = True
        if self.read is None:
            return
        incomplete = self.bad or self.gap or bool(self.buffer) or self.summary is None
        dropped = dict.fromkeys(_STREAMS, 0)
        if self.summary is not None:
            for stream in _STREAMS:
                _, sent, dropped[stream] = self.summary[stream]
                incomplete |= sent != self.received[stream] or dropped[stream] != 0
        self._deliver({'type': 'tool_output_end', 'incomplete': bool(incomplete),
                       'stdout_bytes': self.received['stdout'], 'stderr_bytes': self.received['stderr'],
                       'dropped_stdout_bytes': dropped['stdout'], 'dropped_stderr_bytes': dropped['stderr'],
                       'received_frames': self.frames, 'summary_received': self.summary is not None})

    def __exit__(self, *exc):
        try:
            self.finish()
        finally:
            for descriptor in (self.read, self.write):
                if descriptor is not None:
                    os.close(descriptor)
            self.read = self.write = None
            self.environment = {}
            self.pass_fds = ()
