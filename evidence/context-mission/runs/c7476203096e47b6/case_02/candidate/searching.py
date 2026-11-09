"""All-term retrieval with stable scoring."""
from inverted import build_index
from lexing import tokens

def search(rows, query):
    index = build_index(rows)
    normalized = tokens(query)
    if not normalized:
        return []
    # Deduplicate while keeping deterministic (first-seen) order; repeated
    # query words must require the term once and count its body occurrences
    # only once.
    distinct = list(dict.fromkeys(normalized))
    hits = []
    for identity in index['documents']:
        counts = {term: len(index['postings'].get(term, {}).get(identity, []))
                  for term in distinct}
        if all(counts.values()):
            hits.append({'id': identity, 'score': sum(counts.values()), 'terms': counts})
    return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))
