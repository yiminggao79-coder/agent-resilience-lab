"""Counts preserve the tag text, including escaped delimiters."""
from fields import record

def counts(rows):
    result = {}
    for row in rows:
        key = record(row)['tag']
        result[key] = result.get(key,0) + 1
    return dict(sorted(result.items()))
