"""An in-memory positional body index."""
from corpus import documents
from lexing import tokens

def build_index(rows):
    records = documents(rows)
    postings = {}
    for document in records:
        for position, term in enumerate(tokens(document['body'])):
            postings.setdefault(term, {}).setdefault(document['id'], []).append(position)
    return {'documents': {doc['id']: doc for doc in records}, 'postings': postings}
