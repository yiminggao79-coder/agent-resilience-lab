"""Fresh synthetic authoring source. Sealed: do not expose before the acceptance gate."""
from pathlib import Path
import json, textwrap
ROOT = Path(__file__).resolve().parent

def write(path, text):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip('\n'))

def files(case, kind, mapping):
    for name, content in mapping.items():
        write(f'cases/{case}/{kind}/{name}', content)

A_COMMON = {
 'clockwork.py': '''
    """Local wall-clock parsing for a single calendar day."""
    import re
    from datetime import date

    def minute(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
            raise ValueError("time must be HH:MM")
        hour, minute = map(int, value.split(":"))
        if hour == 24 and minute == 0:
            return 1440
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError("time out of range")
        return hour * 60 + minute

    def day_key(value):
        if not isinstance(value, str):
            raise ValueError("day must be an ISO date")
        try:
            parsed = date.fromisoformat(value)
        except (ValueError, TypeError):
            raise ValueError("day must be an ISO date") from None
        if parsed.isoformat() != value:
            raise ValueError("day must be YYYY-MM-DD")
        return value
 ''',
 'records.py': '''
    """Canonical records never share mutable state with callers."""
    from clockwork import day_key, minute

    def positive(value, name):
        if type(value) is not int or value <= 0:
            raise ValueError(name + " must be a positive integer")
        return value

    def room_key(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("room must be nonempty text")
        return value.strip().casefold()

    def booking(row):
        if not isinstance(row, dict):
            raise ValueError("booking must be a dictionary")
        try:
            identity = row['id']
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError("id must be nonempty text")
            result = dict(id=identity, room=room_key(row['room']), day=day_key(row['day']),
                          start=minute(row['start']), end=minute(row['end']),
                          attendees=positive(row.get('attendees', 1), 'attendees'))
        except KeyError as exc:
            raise ValueError("missing booking field: " + str(exc)) from None
        if result['start'] >= result['end']:
            raise ValueError("booking must end after it starts")
        return result

    def canonical(rows):
        result, seen = [], set()
        for row in rows:
            item = booking(row)
            if item['id'] in seen:
                raise ValueError("duplicate booking id")
            seen.add(item['id'])
            result.append(item)
        return result

    def ordered(rows):
        return sorted(rows, key=lambda r: (r['room'], r['day'], r['start'], r['id']))
 ''',
 'schedule.py': '''
    """Conflict analysis; intervals are represented in minutes."""
    from records import canonical

    def overlap(left, right):
        return (left['room'] == right['room'] and left['day'] == right['day']
                and left['start'] <= right['end'] and right['start'] <= left['end'])

    def conflicts(rows):
        entries = canonical(rows)
        pairs = []
        for index, left in enumerate(entries):
            for right in entries[index + 1:]:
                if overlap(left, right):
                    pairs.append(sorted([left['id'], right['id']]))
        return sorted(pairs)
 ''',
 'reporting.py': '''
    """Small JSON-ready daily summaries."""
    from clockwork import day_key
    from records import canonical, ordered

    def day_report(rows, day):
        day_key(day)
        grouped = {}
        for row in ordered(canonical(rows)):
            if row['day'] != day:
                continue
            group = grouped.setdefault(row['room'], {'minutes': 0, 'ids': []})
            group['minutes'] += row['end'] - row['start']
            group['ids'].append(row['id'])
        return {'day': day, 'rooms': grouped}
 ''',
 'README.md': '''
    # Local schedule helpers

    This is a deliberately small pure-Python scheduling library. It stores no files,
    contacts no service, and models one civil day per booking. Run the protected
    suite with `python -m unittest discover -s tests`. Callers provide dictionaries;
    public functions must leave them unchanged. `observe.py` is a protected public
    observation boundary, not an implementation module. You may add your own tests.
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from clockwork import minute
    from records import canonical
    from schedule import conflicts
    from reporting import day_report

    def row(identity, start, end, room='main', day='2027-05-10', attendees=1):
        return dict(id=identity, room=room, day=day, start=start, end=end, attendees=attendees)

    class OriginalContracts(unittest.TestCase):
        def test_midnight_boundary(self):
            self.assertEqual(minute('24:00'), 1440)
            with self.assertRaises(ValueError):
                minute('24:01')
        def test_canonical_copy(self):
            rows = [row('x', '08:00', '08:30', ' MAIN ')]
            before = copy.deepcopy(rows)
            result = canonical(rows)
            self.assertEqual(result[0]['room'], 'main')
            result[0]['room'] = 'elsewhere'
            self.assertEqual(rows, before)
        def test_duplicate_identity(self):
            with self.assertRaises(ValueError):
                canonical([row('x', '08:00', '09:00'), row('x', '10:00', '11:00')])
        def test_adjacent_is_free(self):
            self.assertEqual(conflicts([row('a', '08:00', '09:00'), row('b', '09:00', '10:00')]), [])
        def test_room_and_day_partition(self):
            self.assertEqual(conflicts([row('a', '08:00', '09:00'), row('b', '08:00', '09:00', 'annex'),
                                        row('c', '08:00', '09:00', day='2027-05-11')]), [])
        def test_union_minutes(self):
            rows = [row('b', '08:15', '08:45'), row('a', '08:00', '09:00'), row('c', '09:00', '09:30')]
            self.assertEqual(day_report(rows, '2027-05-10'),
                {'day': '2027-05-10', 'rooms': {'main': {'minutes': 90, 'ids': ['a', 'b', 'c']}}})
        def test_empty_day(self):
            self.assertEqual(day_report([], '2027-05-10'), {'day': '2027-05-10', 'rooms': {}})
        def test_invalid_interval(self):
            for start, end in [('10:00', '09:00'), ('10:00', '10:00')]:
                with self.assertRaises(ValueError):
                    canonical([row('a', start, end)])

    if __name__ == '__main__':
        unittest.main()
 ''',
 'observe.py': '''
    """Protected JSON boundary. Expected results live outside this workspace."""
    import copy

    def _call(payload, operation):
        before = copy.deepcopy(payload)
        try:
            result = operation()
        except (ValueError, KeyError, ImportError, AttributeError) as exc:
            result = {'error': type(exc).__name__}
        return {'result': result, 'preserved': payload == before}

    def stage1(payload):
        def run():
            from schedule import conflicts
            from reporting import day_report
            return {'conflicts': conflicts(payload['rows']),
                    'report': day_report(payload['rows'], payload['day'])}
        return _call(payload, run)

    def stage2(payload):
        def run():
            from requests import allocate
            return allocate(payload['existing'], payload['requests'], payload['capacities'], payload.get('blocks', []))
        return _call(payload, run)

    def stage3(payload):
        def run():
            from changes import apply_groups
            return apply_groups(payload['existing'], payload['groups'], payload['capacities'], payload.get('blocks', []))
        return _call(payload, run)
 ''',
}
A1 = {
 'schedule.py': A_COMMON['schedule.py'].replace("left['start'] <= right['end'] and right['start'] <= left['end']", "left['start'] < right['end'] and right['start'] < left['end']"),
 'reporting.py': '''
    """Daily summaries use union duration, not attendee time."""
    from clockwork import day_key
    from records import canonical, ordered

    def day_report(rows, day):
        day_key(day)
        grouped = {}
        intervals = {}
        for row in ordered(canonical(rows)):
            if row['day'] == day:
                group = grouped.setdefault(row['room'], {'minutes': 0, 'ids': []})
                group['ids'].append(row['id'])
                intervals.setdefault(row['room'], []).append((row['start'], row['end']))
        for room, spans in intervals.items():
            start, end = spans[0]
            duration = 0
            for left, right in spans[1:]:
                if left <= end:
                    end = max(end, right)
                else:
                    duration += end - start
                    start, end = left, right
            grouped[room]['minutes'] = duration + end - start
        return {'day': day, 'rooms': grouped}
 ''',
}
A2 = {
 'requests.py': '''
    """Deterministic admission against rooms and blocked intervals."""
    from records import canonical, booking, ordered, positive, room_key
    from schedule import overlap

    def limits(capacities):
        result = {}
        for room, capacity in capacities.items():
            key = room_key(room)
            if key in result:
                raise ValueError('duplicate normalized room capacity')
            result[key] = positive(capacity, 'capacity')
        return result

    def blackout_rows(blocks):
        return [booking(dict(block, id='@block-' + str(index), attendees=1))
                for index, block in enumerate(blocks)]

    def rejection(added, occupants, capacities, blocks):
        if any(overlap(item, block) for item in added for block in blocks):
            return 'blocked'
        for item in added:
            peers = [peer for peer in occupants if overlap(item, peer)]
            checkpoints = {item['start']}
            checkpoints.update(peer['start'] for peer in peers if item['start'] <= peer['start'] < item['end'])
            for point in checkpoints:
                total = sum(peer['attendees'] for peer in peers if peer['start'] <= point < peer['end'])
                if total > capacities.get(item['room'], 1):
                    return 'capacity'
        return None

    def allocate(existing, requests, capacities, blocks=()):
        occupied = canonical(existing)
        proposed = canonical(requests)
        if {row['id'] for row in occupied} & {row['id'] for row in proposed}:
            raise ValueError('request id already exists')
        priorities = []
        for source, item in zip(requests, proposed):
            value = source.get('priority', 0)
            if type(value) is not int:
                raise ValueError('priority must be an integer')
            priorities.append((value, item))
        room_limits, blocked = limits(capacities), blackout_rows(blocks)
        accepted, rejected = [], []
        for _, item in sorted(priorities, key=lambda pair: -pair[0]):
            reason = rejection([item], occupied + [item], room_limits, blocked)
            if reason:
                rejected.append({'id': item['id'], 'reason': reason})
            else:
                accepted.append(item['id'])
                occupied.append(item)
        return {'accepted': accepted, 'rejected': rejected, 'schedule': ordered(occupied)}
 ''',
}
A3 = {
 'changes.py': '''
    """Atomic edit groups with sequential group visibility."""
    from records import canonical, ordered
    from requests import limits, blackout_rows, rejection

    def apply_groups(existing, groups, capacities, blocks=()):
        current = canonical(existing)
        room_limits, blocked = limits(capacities), blackout_rows(blocks)
        prepared, identities = [], set()
        for group in groups:
            identity = group.get('id')
            if not isinstance(identity, str) or not identity.strip() or identity in identities:
                raise ValueError('group ids must be unique nonempty strings')
            identities.add(identity)
            remove = group.get('remove', [])
            if not isinstance(remove, list) or any(not isinstance(v, str) for v in remove) or len(set(remove)) != len(remove):
                raise ValueError('remove must contain unique ids')
            # Keep duplicate additions visible as a rejected group, not an invalid request.
            added = [canonical([row])[0] for row in group.get('add', [])]
            prepared.append((identity, remove[:], added))
        accepted, rejected = [], []
        for identity, remove, added in prepared:
            old_ids = {row['id'] for row in current}
            missing = next((v for v in remove if v not in old_ids), None)
            reason = 'missing:' + missing if missing is not None else None
            candidate = [row for row in current if row['id'] not in set(remove)]
            seen = {row['id'] for row in candidate}
            for item in added:
                if reason is None and item['id'] in seen:
                    reason = 'duplicate:' + item['id']
                seen.add(item['id'])
            if reason is None:
                reason = rejection(added, candidate + added, room_limits, blocked)
            if reason:
                rejected.append({'id': identity, 'reason': reason})
            else:
                accepted.append(identity)
                current = candidate + added
        return {'accepted': accepted, 'rejected': rejected, 'schedule': ordered(current)}
 ''',
}
files('case_01', 'workspace', A_COMMON)
for stage, overlay in enumerate([A1, A2, A3], 1):
    files('case_01', f'reference/stage{stage}', overlay)

write('cases/case_01/turn1.md', '''
We use this library to review room reservations. Please fix the two reported schedule errors without changing the protected original tests or observe.py. Adjacent reservations are allowed: all time ranges are half-open [start, end). Conflicts must remain sorted unique pairs of booking IDs for the same normalized room and date. Daily occupied minutes must be the union of reservation intervals, including nested/overlapping reservations; do not sum overlapping minutes. Report IDs stay ordered by start time then ID within each normalized room. Retain the existing validation and caller-input preservation. Inspect the existing modules and add any tests you need. Please finish with a short account of the change and checks run.
''')
write('cases/case_01/turn2.md', '''
The corrected daily report is now used by reception. Add requests.py with allocate(existing, requests, capacities, blocks=()). Keep all earlier behavior and the protected original tests/observe.py unchanged.

Both existing and requests are lists of the original booking dictionaries. A request can also have an integer priority (default 0; booleans invalid). Process higher priorities first, preserving request input order for ties. Normalize every booking and validate all inputs before allocating; any duplicate ID anywhere among existing and requests is ValueError. capacities maps room names to positive integer attendee capacities; normalize names like bookings and reject duplicate normalized keys. Unspecified rooms have capacity 1. blocks contains dictionaries with room/day/start/end, using the existing validation but no ID or attendees required. Neither a block nor a booking touching only an endpoint overlaps.

A request is rejected as "blocked" if it overlaps any block in its room/date; otherwise reject as "capacity" if adding it would exceed simultaneous attendee capacity at any time during that request. Include existing reservations and earlier accepted requests. Existing reservations may already violate a limit; an unrelated request can still succeed. Prefer blocked over capacity if both apply. Accepted reservations are not displaced.

Return {"accepted": [IDs in processing order], "rejected": [{"id": ID, "reason": reason} in processing order], "schedule": [all existing and accepted canonical bookings sorted by room, day, start, ID]}. Canonical bookings have exactly id/room/day/start/end/attendees, with times as integer minutes. Leave all caller objects unchanged. Reuse the existing parsing and interval rules.
''')
write('cases/case_01/turn3.md', '''
Reception needs transactional edits for moving several reservations together. Add changes.py with apply_groups(existing, groups, capacities, blocks=()), preserving both earlier Turns and the immutable original tests/observe.py.

Each group is {"id": nonempty string, "remove": [booking IDs, default []], "add": [booking dictionaries, default []]}. Group IDs must be unique. remove must be a list of distinct strings; malformed groups, malformed bookings, capacities, or blocks raise ValueError before any groups are processed. Reuse the existing canonical booking rules; priority is irrelevant here. Duplicate IDs within a group's additions are a group rejection, not malformed input.

Process groups in input order. A group removes its specified IDs and adds all new bookings atomically. It can re-add an ID it removes. The next group sees only accepted changes. If any removal is missing, reject with "missing:<id>" using the first missing ID in remove order. Otherwise detect duplicate addition IDs against the retained schedule and prior additions in this group, rejecting with "duplicate:<id>" for the first duplicate in add order. Otherwise reject the whole group as "blocked" if any addition overlaps a block, then as "capacity" if the resulting simultaneous attendee totals exceed capacity during any addition. Cancellations alone may succeed even when an unrelated existing reservation violates capacity. A rejected group leaves the schedule unchanged.

Return the same accepted/rejected/schedule structure as allocate, except accepted/rejected IDs identify groups. Sort canonical schedule exactly as in Turn 2. Leave every caller object unchanged, including when validation fails. Add your own tests for atomic rollback and sequential visibility.
''')

B_COMMON = {
 'lexing.py': '''
    """Body text tokenization."""
    def tokens(text):
        if not isinstance(text, str):
            raise ValueError('text must be a string')
        return text.lower().split()
 ''',
 'corpus.py': '''
    """Validation and owned copies of document records."""
    def documents(rows):
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('document must be a dictionary')
            try:
                identity, title, body = row['id'], row['title'], row['body']
            except KeyError:
                raise ValueError('missing document field') from None
            if not isinstance(identity, str) or not identity.strip() or identity in seen:
                raise ValueError('document id must be unique nonempty text')
            if not isinstance(title, str) or not isinstance(body, str):
                raise ValueError('title and body must be strings')
            seen.add(identity)
            result.append({'id': identity, 'title': title, 'body': body})
        return result
 ''',
 'inverted.py': '''
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
 ''',
 'searching.py': '''
    """All-term retrieval with stable scoring."""
    from inverted import build_index
    from lexing import tokens

    def search(rows, query):
        index = build_index(rows)
        terms = tokens(query)
        if not terms:
            return []
        hits = []
        for identity in index['documents']:
            counts = {term: len(index['postings'].get(term, {}).get(identity, [])) for term in terms}
            if all(counts.values()):
                # Original implementation mistakenly weights repeated query terms.
                hits.append({'id': identity, 'score': sum(counts[term] for term in terms), 'terms': counts})
        return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))
 ''',
 'reporting.py': '''
    """Corpus metrics, independent of retrieval ordering."""
    from inverted import build_index
    from lexing import tokens

    def describe(rows):
        index = build_index(rows)
        return {'documents': len(index['documents']), 'terms': len(index['postings']),
                'tokens': sum(len(tokens(row['body'])) for row in index['documents'].values())}
 ''',
 'README.md': '''
    # Small local search library

    Pure Python with an in-memory positional index. Only body text is indexed;
    titles are metadata. No service, files, or installation is required. Run
    `python -m unittest discover -s tests`. Public functions own their result
    dictionaries and must preserve all caller input. The original tests and
    observe.py are protected; add new tests in separate files when useful.
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from corpus import documents
    from lexing import tokens
    from inverted import build_index
    from searching import search
    from reporting import describe

    class OriginalContracts(unittest.TestCase):
        def test_tokens_punctuation(self):
            self.assertEqual(tokens("Red, RED-blue_7!"), ['red', 'red', 'blue', '7'])
        def test_apostrophe_and_unicode(self):
            self.assertEqual(tokens("L’été isn't STRASSE Straße"), ["l'été", "isn't", 'strasse', 'strasse'])
        def test_positions(self):
            rows = [{'id': 'b', 'title': 'not indexed', 'body': 'red blue red'}]
            self.assertEqual(build_index(rows)['postings']['red']['b'], [0, 2])
            self.assertNotIn('indexed', build_index(rows)['postings'])
        def test_repeated_query_is_not_weighted(self):
            rows = [{'id': 'x', 'title': '', 'body': 'red red blue'}]
            self.assertEqual(search(rows, 'red red blue'), [{'id': 'x', 'score': 3, 'terms': {'red': 2, 'blue': 1}}])
        def test_all_terms_required(self):
            rows = [{'id': 'x', 'title': '', 'body': 'red'}]
            self.assertEqual(search(rows, 'red blue'), [])
        def test_stable_tie(self):
            rows = [{'id': name, 'title': '', 'body': 'green'} for name in ['z', 'a']]
            self.assertEqual([hit['id'] for hit in search(rows, 'green')], ['a', 'z'])
        def test_blank_query(self):
            self.assertEqual(search([], ''), [])
        def test_metrics(self):
            self.assertEqual(describe([{'id': 'a', 'title': '', 'body': 'hi hi there'}]),
                             {'documents': 1, 'terms': 2, 'tokens': 3})
        def test_owned_copy(self):
            rows = [{'id': 'a', 'title': '', 'body': 'hi'}]
            before = copy.deepcopy(rows)
            documents(rows)[0]['body'] = 'changed'
            self.assertEqual(rows, before)
        def test_validation(self):
            with self.assertRaises(ValueError):
                documents([{'id': 'a', 'title': '', 'body': 4}])
            with self.assertRaises(ValueError):
                documents([{'id': 'a', 'title': '', 'body': ''}] * 2)

    if __name__ == '__main__':
        unittest.main()
 ''',
 'observe.py': '''
    """Protected observation adapter. It contains no oracle answers."""
    import copy

    def _call(payload, operation):
        before = copy.deepcopy(payload)
        try:
            result = operation()
        except (ValueError, KeyError, ImportError, AttributeError) as exc:
            result = {'error': type(exc).__name__}
        return {'result': result, 'preserved': payload == before}

    def stage1(payload):
        def run():
            from searching import search
            from reporting import describe
            return {'hits': search(payload['documents'], payload['query']), 'metrics': describe(payload['documents'])}
        return _call(payload, run)

    def stage2(payload):
        def run():
            from phrases import phrase_search
            return phrase_search(payload['documents'], payload['phrase'], payload.get('slop', 0))
        return _call(payload, run)

    def stage3(payload):
        def run():
            from updates import revise_and_search
            return revise_and_search(payload['documents'], payload['edits'], payload['query'],
                                     payload.get('phrase'), payload.get('slop', 0))
        return _call(payload, run)
 ''',
}
B1 = {
 'lexing.py': '''
    """Unicode alphanumeric tokens with internal apostrophes."""
    import re

    def tokens(text):
        if not isinstance(text, str):
            raise ValueError('text must be a string')
        return re.findall(r"[^\\W_]+(?:'[^\\W_]+)*", text.casefold().replace('’', "'"))
 ''',
 'searching.py': B_COMMON['searching.py'].replace('terms = tokens(query)', 'terms = sorted(set(tokens(query)))').replace('                # Original implementation mistakenly weights repeated query terms.\n', ''),
}
B2 = {
 'phrases.py': '''
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
 ''',
}
B3 = {
 'updates.py': '''
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
 ''',
}
files('case_02', 'workspace', B_COMMON)
for stage, overlay in enumerate([B1, B2, B3], 1):
    files('case_02', f'reference/stage{stage}', overlay)

write('cases/case_02/turn1.md', '''
This small search library has inconsistent word handling and query scoring. Fix it while preserving the protected original tests and observe.py. Only bodies are indexed; titles remain metadata. A token is a maximal run of Unicode alphanumeric characters, optionally joined by internal straight apostrophes between such runs. Underscores, hyphens and other punctuation separate tokens. Convert curly apostrophe U+2019 to straight apostrophe and casefold the text before extracting tokens. Leading/trailing apostrophes are separators. Token positions are zero-based in this normalized token sequence.

All-term search must require every DISTINCT normalized query token and score a hit as the sum of their body occurrence counts. Repeated query words must not boost scores. Empty normalized queries return no hits. Return hits sorted by descending score, then ascending document ID; terms maps each distinct query token to its positive count. Keep validation, positional indexing, corpus metrics and caller-input preservation. Add your own regression tests and summarize what you checked.
''')
write('cases/case_02/turn2.md', '''
The repaired index now needs phrase retrieval. Add phrases.py with phrase_search(rows, phrase, slop=0). Preserve all Turn 1 behavior and the protected original tests/observe.py.

Use exactly the existing tokenization and body-only index. A match chooses strictly increasing token positions for the phrase's tokens in order, including repeated tokens. Its gap cost is last_position - first_position - (number_of_phrase_tokens - 1). Accept costs at most slop; slop is a nonnegative integer, booleans invalid. A single word costs zero. Return [] for an empty normalized phrase, but still validate the documents and slop.

Return [{"id": document_id, "starts": [matching starting token positions]}], sorted by document ID and then increasing starts. Count each starting position once even if several later choices form matches. Do not treat term presence alone as a phrase, and do not bridge across documents or index title text. Validate duplicate document IDs and malformed rows as before. Preserve all inputs. Add focused tests for order, repeated terms, gap budgets and multiple possible continuations.
''')
write('cases/case_02/turn3.md', '''
We need an atomic edit-and-query API for the in-memory corpus. Add updates.py with apply_batch(rows, edits) and revise_and_search(rows, edits, query, phrase=None, slop=0), keeping both earlier Turns and the protected original tests/observe.py.

Apply edits in list order to an owned copy. An edit is {"op": "upsert", "document": {"id", "title", "body"}} or {"op": "delete", "id": id}. Upsert uses the existing document validation; a replacement stays in the existing position, while a new ID appends at the end. Delete removes the current document; deleting an absent ID raises ValueError. Empty/invalid delete IDs, missing upsert documents, unknown operations and malformed records also raise ValueError. Multiple edits to one ID are legal and see the preceding edits. If any edit fails, no caller object may change. Return canonical document dictionaries containing only id/title/body, in current corpus order. Do not retain stale term positions from replacements or deletions.

revise_and_search returns {"documents": revised_rows, "hits": search(revised_rows, query)}; when phrase is not None, also include "phrases": phrase_search(revised_rows, phrase, slop). Omit phrases otherwise. It must preserve all inputs even when an edit or query fails. Build on the earlier modules rather than implementing different token rules. Add tests for replacement, removal, append order, sequential edits and failure rollback.
''')

# The finite expectations below are authored directly, independently of overlays.
def R(i, start, end, room='main', day='2027-05-10', attendees=1, **extra):
    return dict(id=i, room=room, day=day, start=start, end=end, attendees=attendees, **extra)
def C(i, start, end, room='main', day='2027-05-10', attendees=1):
    return dict(id=i, room=room, day=day, start=start, end=end, attendees=attendees)
def D(i, body, title=''):
    return dict(id=i, title=title, body=body)
def observed(result):
    return dict(result=result, preserved=True)
def oracle(stage, pairs):
    return dict(module='observe', function=f'stage{stage}', inputs=[[p] for p, _ in pairs], expected=[observed(e) for _, e in pairs])
def a1(rows, rooms, pairs=None, day='2027-05-10'):
    return ({'rows': rows, 'day': day}, {'conflicts': pairs or [], 'report': {'day': day, 'rooms': rooms}})
A_ORACLES = [
 oracle(1, [
  a1([R('b','09:00','10:00'),R('a','08:00','09:00')], {'main': {'minutes':120,'ids':['a','b']}}),
  a1([R('z','08:15','08:45'),R('a','08:00','09:00'),R('c','08:30','09:30')], {'main': {'minutes':90,'ids':['a','z','c']}}, [['a','c'],['a','z'],['c','z']]),
  a1([R('x','23:00','24:00',' SIDE '),R('b','08:00','08:10','main'),R('q','08:00','08:10','side','2027-05-11')], {'main':{'minutes':10,'ids':['b']},'side':{'minutes':60,'ids':['x']}}),
  ({'rows':[R('bad','09:00','09:00')],'day':'2027-05-10'}, {'error':'ValueError'}),
  ({'rows':[R('x','08:00','08:30'),R('x','10:00','11:00')],'day':'2027-05-10'}, {'error':'ValueError'}),
  a1([], {}),
 ]),
 oracle(2, [
  ({'existing':[R('e','09:00','10:00',attendees=2)],'requests':[R('low','09:30','10:30',attendees=2),R('high','09:30','10:30',priority=5)],'capacities':{'MAIN':3}},
   {'accepted':['high'],'rejected':[{'id':'low','reason':'capacity'}],'schedule':[C('e',540,600,attendees=2),C('high',570,630)]}),
  ({'existing':[],'requests':[R('a','08:00','09:00'),R('b','09:00','10:00'),R('c','08:30','09:30')],'capacities':{}},
   {'accepted':['a','b'],'rejected':[{'id':'c','reason':'capacity'}],'schedule':[C('a',480,540),C('b',540,600)]}),
  ({'existing':[],'requests':[R('a','08:00','09:00'),R('b','09:00','10:00')],'capacities':{'main':1},'blocks':[{'room':' Main ','day':'2027-05-10','start':'08:30','end':'09:00'}]},
   {'accepted':['b'],'rejected':[{'id':'a','reason':'blocked'}],'schedule':[C('b',540,600)]}),
  ({'existing':[R('e','08:00','09:00')],'requests':[R('e','10:00','11:00')],'capacities':{}}, {'error':'ValueError'}),
  ({'existing':[R('a','08:00','09:00',attendees=2),R('b','09:00','10:00',attendees=2)],'requests':[R('c','08:30','09:30')],'capacities':{'main':3}},
   {'accepted':['c'],'rejected':[],'schedule':[C('a',480,540,attendees=2),C('c',510,570),C('b',540,600,attendees=2)]}),
  ({'existing':[],'requests':[R('a','08:00','09:00',priority=True)],'capacities':{}}, {'error':'ValueError'}),
 ]),
 oracle(3, [
  ({'existing':[R('a','08:00','09:00'),R('b','09:00','10:00')], 'groups':[{'id':'move','remove':['a'],'add':[R('a','09:30','10:30')]},{'id':'cancel','remove':['a']},{'id':'reuse','add':[R('a','08:30','09:00')]}],'capacities':{}},
   {'accepted':['cancel','reuse'],'rejected':[{'id':'move','reason':'capacity'}],'schedule':[C('a',510,540),C('b',540,600)]}),
  ({'existing':[R('a','08:00','09:00')], 'groups':[{'id':'swap','remove':['a'],'add':[R('a','08:00','09:00','annex'),R('b','08:00','09:00')]}],'capacities':{}},
   {'accepted':['swap'],'rejected':[],'schedule':[C('a',480,540,'annex'),C('b',480,540)]}),
  ({'existing':[R('a','08:00','09:00')], 'groups':[{'id':'missing','remove':['z','q'],'add':[R('a','08:00','09:00')]},{'id':'duplicate','add':[R('b','10:00','11:00'),R('b','11:00','12:00')]}],'capacities':{}},
   {'accepted':[],'rejected':[{'id':'missing','reason':'missing:z'},{'id':'duplicate','reason':'duplicate:b'}],'schedule':[C('a',480,540)]}),
  ({'existing':[R('a','08:00','09:00')], 'groups':[{'id':'change','remove':['a'],'add':[R('a','10:00','11:00'),R('b','10:30','11:30')]}],'capacities':{},'blocks':[{'room':'main','day':'2027-05-10','start':'10:15','end':'10:20'}]},
   {'accepted':[],'rejected':[{'id':'change','reason':'blocked'}],'schedule':[C('a',480,540)]}),
  ({'existing':[R('a','08:00','09:00')], 'groups':[{'id':'first','remove':['a']},{'id':'bad','add':[R('b','12:00','11:00')]}],'capacities':{}}, {'error':'ValueError'}),
  ({'existing':[R('a','08:00','09:00',attendees=3),R('b','08:00','09:00')], 'groups':[{'id':'cancel','remove':['b']},{'id':'other','add':[R('c','10:00','11:00')]}],'capacities':{}},
   {'accepted':['cancel','other'],'rejected':[],'schedule':[C('a',480,540,attendees=3),C('c',600,660)]}),
 ])]

B_ORACLES = [
 oracle(1, [
  ({'documents':[D('z','Red, blue red!'),D('a','red blue'),D('b','red')],'query':'RED red, blue'},
   {'hits':[{'id':'z','score':3,'terms':{'red':2,'blue':1}},{'id':'a','score':2,'terms':{'red':1,'blue':1}}],'metrics':{'documents':3,'terms':2,'tokens':6}}),
  ({'documents':[D('x',"L’été l'été Straße STRASSE, can't can’t"),D('y','strasse',"l'été")],'query':"l’été STRASSE"},
   {'hits':[{'id':'x','score':4,'terms':{"l'été":2,'strasse':2}}],'metrics':{'documents':2,'terms':3,'tokens':7}}),
  ({'documents':[D('x',"'red' blue_green 42")],'query':'_---_'},
   {'hits':[],'metrics':{'documents':1,'terms':4,'tokens':4}}),
  ({'documents':[D('z','alpha'),D('a','alpha')],'query':'alpha'},
   {'hits':[{'id':'a','score':1,'terms':{'alpha':1}},{'id':'z','score':1,'terms':{'alpha':1}}],'metrics':{'documents':2,'terms':1,'tokens':2}}),
  ({'documents':[D('x','a'),D('x','b')],'query':'a'}, {'error':'ValueError'}),
  ({'documents':[],'query':'missing'}, {'hits':[],'metrics':{'documents':0,'terms':0,'tokens':0}}),
 ]),
 oracle(2, [
  ({'documents':[D('z','red x blue red blue'),D('a','blue red'),D('b','red blue blue')],'phrase':'red blue','slop':0},
   [{'id':'b','starts':[0]},{'id':'z','starts':[3]}]),
  ({'documents':[D('x','a x b y c a b c')],'phrase':'a b c','slop':1}, [{'id':'x','starts':[5]}]),
  ({'documents':[D('x','a a a a')],'phrase':'a a','slop':2}, [{'id':'x','starts':[0,1,2]}]),
  ({'documents':[D('x','a x b y c a b c')],'phrase':'a b c','slop':2}, [{'id':'x','starts':[0,5]}]),
  ({'documents':[D('x','a')],'phrase':'a','slop':True}, {'error':'ValueError'}),
  ({'documents':[D('x',"Can't! can’t?"),D('y','x',"can't")],'phrase':"CAN’T"}, [{'id':'x','starts':[0,1]}]),
  ({'documents':[D('x','a')],'phrase':'---','slop':0}, []),
 ]),
 oracle(3, [
  ({'documents':[D('a','old red'),D('b','blue red')],'edits':[{'op':'upsert','document':D('a','new blue blue')},{'op':'delete','id':'b'},{'op':'upsert','document':D('c','blue green')}],'query':'blue','phrase':'blue blue'},
   {'documents':[D('a','new blue blue'),D('c','blue green')],'hits':[{'id':'a','score':2,'terms':{'blue':2}},{'id':'c','score':1,'terms':{'blue':1}}],'phrases':[{'id':'a','starts':[1]}]}),
  ({'documents':[D('a','red'),D('b','blue')],'edits':[{'op':'delete','id':'a'},{'op':'upsert','document':D('a','green')},{'op':'upsert','document':D('b','green green')}],'query':'green'},
   {'documents':[D('b','green green'),D('a','green')],'hits':[{'id':'b','score':2,'terms':{'green':2}},{'id':'a','score':1,'terms':{'green':1}}]}),
  ({'documents':[D('a','red')],'edits':[{'op':'upsert','document':D('a','blue')},{'op':'delete','id':'absent'}],'query':'blue'}, {'error':'ValueError'}),
  ({'documents':[D('a','red')],'edits':[{'op':'upsert','document':D('a','blue')},{'op':'upsert','document':D('a','green green')}],'query':'red','phrase':'green green'},
   {'documents':[D('a','green green')],'hits':[],'phrases':[{'id':'a','starts':[0]}]}),
  ({'documents':[D('a','red')],'edits':[{'op':'upsert','document':{'id':'b','title':'bad','body':8}}],'query':'red'}, {'error':'ValueError'}),
  ({'documents':[D('a','red')],'edits':[],'query':'red','phrase':'red','slop':-1}, {'error':'ValueError'}),
 ])]

cases = []
for identity, oracles in [('case_01', A_ORACLES), ('case_02', B_ORACLES)]:
    cases.append({'id': identity, 'workspace_dir': f'cases/{identity}/workspace',
                  'immutable_files': ['tests/test_original.py', 'observe.py'],
                  'turns': [{'task': f'cases/{identity}/turn{stage}.md', 'oracle': check}
                            for stage, check in enumerate(oracles, 1)]})
manifest = {'version': 1, 'cases': cases}
write('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
write('PROVENANCE.md', '''
# Sealed synthetic provenance

Two projects, all source, prompts, protected original tests, observation adapters,
finite oracle inputs and hand-authored expected outputs were authored anew for
this mission. They contain no owner files, production data, external solutions,
previous acceptance answers, previous transcripts, API keys or provider data.
Only each candidate workspace and its current natural user task are model payload.
Reference overlays and the oracle manifest are outside candidate workspaces.

Authorship is a separate author role in the same AI environment, not external
statistical independence. The runtime implementer must not inspect semantic suite
files before runtime freeze and the first acceptance attempt. The public interface
file discloses only schema/counts/protocol. No model or harness is taught case
recognition, prescribed tool sequences, hidden expected answers or reference code.

Freeze this suite before exposure. Do not revise a failed task, expected output,
protected test, reference or original grade after an attempt. Any subsequent use
for development consumes the holdout and requires a new version and fresh cases.
All stages' legacy finite oracles must be rerun against every later stage, and all
protected original tests and bytes must pass at each stage. Runtime requirements
(real automatic summary before final coding, process separation and evidence)
are additional independent gates rather than hidden task instructions.
''')
