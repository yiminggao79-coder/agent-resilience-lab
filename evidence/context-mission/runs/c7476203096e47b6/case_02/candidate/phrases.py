"""Positional phrase retrieval with an optional gap (slop) budget.

The matching rules are deliberately narrow:

* Tokens come from the shared body-only tokenizer (:mod:`lexing`), so phrase
  text is normalized exactly like indexed text, and titles are never matched.
* A match selects strictly increasing token positions for the phrase tokens in
  order. Repeated phrase tokens therefore need distinct, increasing positions.
* The gap cost of a match is ``last_position - first_position - (k - 1)`` for a
  phrase of ``k`` tokens; a single-token phrase always costs zero. A match is
  accepted when its cost is at most ``slop``.
* Only *starting* positions are reported, once each, even when several later
  continuations form matches.

Inputs are validated through :func:`inverted.build_index` (which delegates to
:func:`corpus.documents`), so malformed rows and duplicate ids fail exactly as
they do elsewhere, and nothing in the caller's data is mutated.
"""
from bisect import bisect_right

from inverted import build_index
from lexing import tokens


def _validated_slop(slop):
    # bool is an int subclass, so it must be rejected explicitly.
    if isinstance(slop, bool) or not isinstance(slop, int):
        raise ValueError('slop must be a nonnegative integer')
    if slop < 0:
        raise ValueError('slop must be a nonnegative integer')
    return slop


def _matching_starts(phrase_positions, slop):
    """Starting positions that admit at least one strictly increasing match.

    For a fixed start, the cheapest completion takes the earliest available
    later position at every step, so greedily walking forward is exact: if the
    greedy completion exceeds the budget, no other completion can fit.
    """
    width = len(phrase_positions) - 1
    starts = []
    for start in phrase_positions[0]:
        previous = start
        for later_positions in phrase_positions[1:]:
            index = bisect_right(later_positions, previous)
            if index == len(later_positions):
                break
            previous = later_positions[index]
        else:
            if previous - start - width <= slop:
                starts.append(start)
    return starts


def phrase_search(rows, phrase, slop=0):
    """Return phrase hits as ``{'id': ..., 'starts': [...]}``, sorted by id/start."""
    slop = _validated_slop(slop)
    index = build_index(rows)
    phrase_tokens = tokens(phrase)
    if not phrase_tokens:
        return []
    postings = index['postings']
    hits = []
    for identity in index['documents']:
        phrase_positions = []
        for term in phrase_tokens:
            term_positions = postings.get(term, {}).get(identity, [])
            if not term_positions:
                break
            phrase_positions.append(term_positions)
        else:
            starts = _matching_starts(phrase_positions, slop)
            if starts:
                hits.append({'id': identity, 'starts': starts})
    return sorted(hits, key=lambda hit: hit['id'])
