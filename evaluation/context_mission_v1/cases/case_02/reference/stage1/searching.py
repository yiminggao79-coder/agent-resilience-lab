"""All-term retrieval with stable scoring."""
from inverted import build_index
from lexing import tokens

def search(rows, query):
    index = build_index(rows)
    terms = sorted(set(tokens(query)))
    if not terms:
        return []
    hits = []
    for identity in index['documents']:
        counts = {term: len(index['postings'].get(term, {}).get(identity, [])) for term in terms}
        if all(counts.values()):
            hits.append({'id': identity, 'score': sum(counts[term] for term in terms), 'terms': counts})
    return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))
