"""Explicit private terminal presentation; no state/trace/execution authority."""
import codecs
import sys
import unicodedata


def _visible(text):
    result = []
    for char in text:
        if char in ('\n', '\t'):
            result.append(char)
        elif unicodedata.category(char) in ('Cc', 'Cf'):
            point = ord(char)
            if char == '\r':
                result.append('\\r')
            elif point < 256:
                result.append('\\x%02x' % point)
            elif point <= 65535:
                result.append('\\u%04x' % point)
            else:
                result.append('\\U%08x' % point)
        else:
            result.append(char)
    return ''.join(result)


class ToolOutputDisplay:
    """Callbacks must return promptly; a blocking host stream is not preempted."""
    def __init__(self, stream=None):
        self.stream = stream
        self.output_id = None
        self.decoders = {}
        self.tool = None
        self.last_label = None
        self.line_start = True

    def _write(self, label, text):
        if text:
            target = self.stream if self.stream is not None else sys.stderr
            if label != self.last_label and not self.line_start:
                target.write('\n')
                self.line_start = True
            for part in _visible(text).splitlines(keepends=True):
                if self.line_start:
                    target.write('[%s %s] ' % (self.tool, label))
                target.write(part)
                self.line_start = part.endswith('\n')
            self.last_label = label
            target.flush()

    def __call__(self, event):
        if event['output_id'] != self.output_id:
            # A failed producer may never emit end. Do not carry UTF-8 state
            # from that operation into an unrelated next execution.
            if self.decoders:
                self._flush()
                self._write('display', 'incomplete\n')
            self.output_id, self.tool = event['output_id'], event['tool']
            self.last_label = None
            if not self.line_start:
                (self.stream if self.stream is not None else sys.stderr).write('\n')
                self.line_start = True
            self.decoders = {label:codecs.getincrementaldecoder('utf-8')(errors='replace')
                             for label in ('stdout','stderr')}
        if event['type'] == 'tool_output_delta':
            label = event['stream']
            self._write(label, self.decoders[label].decode(event['data'], final=False))
        elif event['type'] == 'tool_output_end':
            self._flush()
            if event['incomplete']:
                self._write('display', 'incomplete (output omitted or producer interrupted)\n')
            if not self.line_start:
                (self.stream if self.stream is not None else sys.stderr).write('\n')
                (self.stream if self.stream is not None else sys.stderr).flush()
                self.line_start = True

    def _flush(self):
        for label, decoder in self.decoders.items():
            self._write(label, decoder.decode(b'', final=True))
        self.decoders = {}
