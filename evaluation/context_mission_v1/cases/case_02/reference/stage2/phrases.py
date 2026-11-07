"""Ordered phrase matches count starts, not alternative paths."""
from bisect import bisect_right
from inverted import build_index
from lexing import tokens

def phrase_search(rows, phrase, slop=0):
    if type(slop) is not int or slop < 0:
        raise ValueError('slop must be a nonnegative integer')
    index = build_index(rows)
    terms = tokens(phrase)
    if not terms:
        return []
    hits = []
    for identity in sorted(index['documents']):
        positions = [index['postings'].get(term, {}).get(identity, []) for term in terms]
        if not all(positions):
            continue
        starts = []
        for start in positions[0]:
            end = start
            for following in positions[1:]:
                offset = bisect_right(following, end)
                if offset == len(following):
                    break
                end = following[offset]
            else:
                if end - start - (len(terms) - 1) <= slop:
                    starts.append(start)
        if starts:
            hits.append({'id': identity, 'starts': starts})
    return hits
