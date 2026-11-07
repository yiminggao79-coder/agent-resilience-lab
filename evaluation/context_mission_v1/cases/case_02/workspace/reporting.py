"""Corpus metrics, independent of retrieval ordering."""
from inverted import build_index
from lexing import tokens

def describe(rows):
    index = build_index(rows)
    return {'documents': len(index['documents']), 'terms': len(index['postings']),
            'tokens': sum(len(tokens(row['body'])) for row in index['documents'].values())}
