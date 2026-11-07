"""Protected observation boundary with no expected answers."""
import copy

def _observe(payload, action):
    before = copy.deepcopy(payload)
    try:
        value = action()
    except (ValueError, KeyError, ImportError, AttributeError) as exc:
        value = {'error': type(exc).__name__}
    return {'value': value, 'input_intact': payload == before}

def first(payload):
    def action():
        from wire import encode_record, decode_line
        from batch import load
        from stats import counts
        rows = load(payload['batch'])
        return {'encoded': encode_record(payload['record']), 'decoded': decode_line(payload['line']),
                'batch': rows, 'counts': counts(rows)}
    return _observe(payload, action)

def second(payload):
    def action():
        from stream import Decoder
        decoder = Decoder()
        trace = []
        for operation in payload['operations']:
            try:
                if operation['op'] == 'feed':
                    output = decoder.feed(operation['text'])
                elif operation['op'] == 'finish':
                    output = decoder.finish()
                else:
                    raise ValueError('unknown operation')
            except ValueError:
                output = {'error':'ValueError'}
            trace.append({'output': output, 'state': decoder.snapshot()})
        return {'trace':trace, 'final':decoder.snapshot()}
    return _observe(payload, action)

def third(payload):
    def action():
        from telemetry import summaries
        return summaries(payload['chunks'], payload.get('window',3), payload.get('aliases'))
    return _observe(payload, action)
