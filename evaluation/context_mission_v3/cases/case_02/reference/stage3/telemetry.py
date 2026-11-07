"""Windowed integer readings with transitive tag aliases."""
import re
from fields import tag
from stream import Decoder

def summaries(chunks, window=3, aliases=None):
    if type(window) is not int or window <= 0:
        raise ValueError('window must be a positive integer')
    if aliases is None:
        aliases = {}
    if not isinstance(aliases,dict):
        raise ValueError('aliases must be a dictionary')
    canonical = {tag(source):tag(target) for source,target in aliases.items()}
    resolved = {}
    for source in canonical:
        cursor, seen = source, set()
        while cursor in canonical:
            if cursor in seen:
                raise ValueError('alias cycle')
            seen.add(cursor)
            cursor = canonical[cursor]
        resolved[source] = cursor
    if not isinstance(chunks,list):
        raise ValueError('chunks must be a list')
    decoder, records = Decoder(), []
    for chunk in chunks:
        records.extend(decoder.feed(chunk))
    decoder.finish()
    groups = {}
    for record in records:
        if not re.fullmatch(r'-?(0|[1-9][0-9]*)',record['value']):
            raise ValueError('reading must be canonical signed integer text')
        identity = resolved.get(record['tag'],record['tag'])
        unit, value = record['unit'], int(record['value'])
        item = groups.setdefault(identity, {'unit':unit,'values':[]})
        if item['unit'] != unit:
            raise ValueError('unit changed within a canonical tag')
        item['values'].append(value)
    output = []
    for identity,item in sorted(groups.items()):
        values = item['values'][-window:]
        output.append({'tag':identity,'unit':item['unit'],'count':len(item['values']),
                       'kept':len(values),'minimum':min(values),'maximum':max(values),
                       'total':sum(values),'latest':values[-1]})
    return {'groups':output,'records':len(records)}
