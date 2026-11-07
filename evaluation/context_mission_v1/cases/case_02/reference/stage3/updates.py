"""Apply edits to an owned corpus, then rebuild all observations."""
from corpus import documents
from searching import search
from phrases import phrase_search

def apply_batch(rows, edits):
    current = documents(rows)
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('edit must be a dictionary')
        operation = edit.get('op')
        if operation == 'upsert':
            try:
                replacement = documents([edit['document']])[0]
            except KeyError:
                raise ValueError('upsert requires a document') from None
            offset = next((i for i, row in enumerate(current) if row['id'] == replacement['id']), None)
            if offset is None:
                current.append(replacement)
            else:
                current[offset] = replacement
        elif operation == 'delete':
            identity = edit.get('id')
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError('delete requires an id')
            offset = next((i for i, row in enumerate(current) if row['id'] == identity), None)
            if offset is None:
                raise ValueError('cannot delete an absent document')
            current.pop(offset)
        else:
            raise ValueError('unknown edit operation')
    return current

def revise_and_search(rows, edits, query, phrase=None, slop=0):
    revised = apply_batch(rows, edits)
    result = {'documents': revised, 'hits': search(revised, query)}
    if phrase is not None:
        result['phrases'] = phrase_search(revised, phrase, slop)
    return result
