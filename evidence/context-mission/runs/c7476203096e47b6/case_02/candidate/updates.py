"""Atomic edit-then-query helpers for the in-memory corpus.

Edits are applied in list order to an owned copy of ``rows``. Nothing in the
caller's data is mutated: the copy is validated up front by
:func:`corpus.documents`, and every edit is validated by the same rules used
elsewhere (``corpus`` documents for upserts). Because the working copy is
local, any failure simply raises before a result is returned, and the caller's
rows and objects are untouched.

Replacement keeps the existing position; new ids append. Because results are
re-indexed from the revised rows on every query, no stale term positions from
replacements or deletions can survive.
"""
from corpus import documents
from phrases import phrase_search
from searching import search


def _validate_edit(edit, corpus, positions):
    if not isinstance(edit, dict):
        raise ValueError('edit must be a dictionary')
    operation = edit.get('op')
    if operation == 'upsert':
        if 'document' not in edit:
            raise ValueError('upsert requires a document')
        canonical = documents([edit['document']])[0]
        identity = canonical['id']
        if identity in positions:
            corpus[positions[identity]] = canonical
        else:
            positions[identity] = len(corpus)
            corpus.append(canonical)
    elif operation == 'delete':
        if 'id' not in edit:
            raise ValueError('delete requires an id')
        identity = edit['id']
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('delete id must be nonempty text')
        if identity not in positions:
            raise ValueError('delete id not found')
        del corpus[positions[identity]]
        positions.clear()
        positions.update({doc['id']: index for index, doc in enumerate(corpus)})
    else:
        raise ValueError('unknown operation')


def apply_batch(rows, edits):
    """Apply ``edits`` sequentially to an owned copy and return canonical rows."""
    corpus = documents(rows)
    if isinstance(edits, (str, bytes, dict)):
        raise ValueError('edits must be a sequence of edits')
    try:
        edit_list = list(edits)
    except TypeError:
        raise ValueError('edits must be a sequence of edits') from None
    positions = {doc['id']: index for index, doc in enumerate(corpus)}
    for edit in edit_list:
        _validate_edit(edit, corpus, positions)
    return corpus


def revise_and_search(rows, edits, query, phrase=None, slop=0):
    """Revise the corpus, then run all-term (and optional phrase) retrieval."""
    revised = apply_batch(rows, edits)
    hits = search(revised, query)
    result = {'documents': revised, 'hits': hits}
    if phrase is not None:
        result['phrases'] = phrase_search(revised, phrase, slop)
    return result
