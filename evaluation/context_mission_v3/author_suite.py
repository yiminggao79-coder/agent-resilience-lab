"""Fresh synthetic sealed acceptance authoring source, version 3."""
import json
from pathlib import Path
import textwrap
ROOT = Path(__file__).resolve().parent


def put(name, text):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip('\n'))


def group(case, directory, mapping):
    for name, text in mapping.items():
        put(f'cases/{case}/{directory}/{name}', text)


A = {
 'results.py': '''
    """Canonical score records and participant validation."""
    def name(value):
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError('name must be nonempty trimmed text')
        return value

    def number(value, minimum=0):
        if type(value) is not int or value < minimum:
            raise ValueError('expected integer in range')
        return value

    def matches(rows):
        output, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('match must be a dictionary')
            try:
                identity, home, away = name(row['id']), name(row['home']), name(row['away'])
                hg, ag = number(row['home_goals']), number(row['away_goals'])
            except KeyError:
                raise ValueError('missing match field') from None
            if identity in seen or home == away:
                raise ValueError('duplicate match id or self match')
            seen.add(identity)
            output.append(dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag))
        return output

    def participants(rows, teams=()):
        output = set()
        for team in teams:
            team = name(team)
            if team in output:
                raise ValueError('duplicate registered team')
            output.add(team)
        for row in rows:
            output.update([row['home'], row['away']])
        return sorted(output)
 ''',
 'scoring.py': '''
    """Per-team totals for played matches."""
    from results import matches, participants

    def table(rows, teams=()):
        rows = matches(rows)
        totals = {team: dict(team=team, played=0, won=0, drawn=0, lost=0,
                             goals_for=0, goals_against=0, difference=0, points=0)
                  for team in participants(rows, teams)}
        for row in rows:
            home, away = totals[row['home']], totals[row['away']]
            for own, other, scored, conceded in [(home, away, row['home_goals'], row['away_goals']),
                                                 (away, home, row['away_goals'], row['home_goals'])]:
                own['played'] += 1
                own['goals_for'] += scored
                own['goals_against'] += conceded
                if scored > conceded:
                    own['won'] += 1
                    own['points'] += 3
                elif scored < conceded:
                    own['lost'] += 1
                else:
                    own['drawn'] += 1
                    # Draw points are currently omitted.
            home['difference'] = home['goals_for'] - home['goals_against']
            away['difference'] = away['goals_for'] - away['goals_against']
        return list(totals.values())
 ''',
 'ordering.py': '''
    """Deterministic standings order."""
    def rank(rows):
        return sorted((dict(row) for row in rows),
                      key=lambda row: (-row['points'], -row['goals_for'], -row['difference'], row['team']))
 ''',
 'presentation.py': '''
    """Small public season observation."""
    from results import matches
    from scoring import table
    from ordering import rank

    def season(rows, teams=()):
        canonical = matches(rows)
        standings = rank(table(canonical, teams))
        return {'standings': standings, 'summary': {'games': len(canonical), 'teams': len(standings),
                'goals': sum(row['home_goals'] + row['away_goals'] for row in canonical)}}
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from results import matches
    from scoring import table
    from ordering import rank
    from presentation import season

    def match(identity='m', home='A', away='B', hg=1, ag=0):
        return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

    class OriginalContracts(unittest.TestCase):
        def test_win_loss(self):
            rows = table([match()])
            self.assertEqual([(r['team'],r['points'],r['won'],r['lost']) for r in rows],
                             [('A',3,1,0),('B',0,0,1)])
        def test_draw_credit(self):
            self.assertEqual([r['points'] for r in table([match(hg=2,ag=2)])], [1,1])
        def test_difference_precedes_goals(self):
            rows = [match('x','A','B',2,0),match('y','C','D',4,3)]
            self.assertEqual([r['team'] for r in rank(table(rows))], ['A','C','D','B'])
        def test_goals_then_name(self):
            rows = [match('x','A','B',1,0),match('y','C','D',2,1)]
            self.assertEqual([r['team'] for r in rank(table(rows))], ['C','A','D','B'])
        def test_unplayed_registration(self):
            row = table([],['Z'])[0]
            self.assertEqual((row['team'],row['played'],row['points']), ('Z',0,0))
        def test_summary(self):
            self.assertEqual(season([match(hg=3,ag=1)],['C'])['summary'], {'games':1,'teams':3,'goals':4})
        def test_match_copy(self):
            rows = [match()]
            before = copy.deepcopy(rows)
            matches(rows)[0]['home'] = 'changed'
            self.assertEqual(rows,before)
        def test_rank_copy(self):
            rows = table([match()])
            before = copy.deepcopy(rows)
            rank(rows)[0]['points'] = 999
            self.assertEqual(rows,before)
        def test_invalid_score(self):
            for value in [True,-1,1.2]:
                with self.assertRaises(ValueError):
                    table([match(hg=value)])
        def test_duplicate_and_self(self):
            for rows in [[match(),match()],[match(home='A',away='A')]]:
                with self.assertRaises(ValueError):
                    table(rows)

    if __name__ == '__main__':
        unittest.main()
 ''',
 'observe.py': '''
    """Protected observation adapter; answers remain external."""
    import copy

    def _observe(payload, action):
        before = copy.deepcopy(payload)
        try:
            value = action()
        except (ValueError, KeyError, ImportError, AttributeError) as exc:
            value = {'error': type(exc).__name__}
        return {'value': value, 'input_intact': payload == before}

    def first(payload):
        def action():
            from presentation import season
            return season(payload['matches'], payload.get('teams', []))
        return _observe(payload, action)

    def second(payload):
        def action():
            from discipline import review
            return review(payload['matches'], payload['actions'], payload.get('teams', []))
        return _observe(payload, action)

    def third(payload):
        def action():
            from qualification import select
            return select(payload['matches'], payload['actions'], payload['groups'],
                          payload.get('slots', 1), payload.get('wildcards', 0))
        return _observe(payload, action)
 ''',
 'README.md': '''
    # Community league records

    A small standard-library league-results package. Scores are nonnegative
    integers, matches have stable IDs, and all public observations are JSON-ready.
    No files or external service are used. Public APIs preserve caller objects.
    Run `python3 -m unittest discover -s tests`. Keep the original test file and
    observe.py byte-identical; separate new tests may be added.
 ''',
}
A1 = {
 'scoring.py': A['scoring.py'].replace('                    # Draw points are currently omitted.', "                    own['points'] += 1"),
 'ordering.py': A['ordering.py'].replace("-row['goals_for'], -row['difference']", "-row['difference'], -row['goals_for']"),
}
A2 = {
 'discipline.py': '''
    """Sequential score decisions and cumulative point deductions."""
    from results import matches, participants, name, number
    from scoring import table
    from ordering import rank

    def review(rows, actions, teams=()):
        current = matches(rows)
        universe = participants(current, teams)
        penalties = dict.fromkeys(universe, 0)
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError('action must be a dictionary')
            kind = action.get('kind')
            if kind in ('void', 'replace'):
                identity = name(action.get('match_id'))
                offset = next((i for i,row in enumerate(current) if row['id'] == identity), None)
                if offset is None:
                    raise ValueError('unknown active match')
                if kind == 'void':
                    current.pop(offset)
                else:
                    try:
                        hg, ag = number(action['home_goals']), number(action['away_goals'])
                    except KeyError:
                        raise ValueError('replacement scores missing') from None
                    current[offset]['home_goals'], current[offset]['away_goals'] = hg, ag
            elif kind == 'penalty':
                team = name(action.get('team'))
                if team not in penalties:
                    raise ValueError('unknown penalty team')
                try:
                    points = number(action['points'])
                except KeyError:
                    raise ValueError('penalty points missing') from None
                penalties[team] += points
            else:
                raise ValueError('unknown action kind')
        standings = table(current, universe)
        for row in standings:
            row['points'] -= penalties[row['team']]
        return {'matches': current, 'standings': rank(standings), 'penalties': penalties}
 ''',
}
A3 = {
 'qualification.py': '''
    """Group-local standings and deterministic wildcard selection."""
    from results import name, number
    from discipline import review
    from scoring import table
    from ordering import rank

    def select(rows, actions, groups, slots=1, wildcards=0):
        number(slots)
        number(wildcards)
        if not isinstance(groups, dict) or not groups:
            raise ValueError('groups must be a nonempty dictionary')
        members, canonical = set(), {}
        for group, teams in groups.items():
            name(group)
            if not isinstance(teams, list) or not teams:
                raise ValueError('each group must contain teams')
            copied = []
            for team in teams:
                name(team)
                if team in members:
                    raise ValueError('team appears in multiple slots')
                members.add(team)
                copied.append(team)
            canonical[group] = copied
        reviewed = review(rows, actions, sorted(members))
        if any(row['home'] not in members or row['away'] not in members for row in reviewed['matches']):
            raise ValueError('match team is not assigned to a group')
        # Even voided original matches must name assigned teams.
        from results import matches
        if any(row['home'] not in members or row['away'] not in members for row in matches(rows)):
            raise ValueError('original match team is not assigned to a group')
        standings, automatic, remaining = {}, [], []
        for group in sorted(canonical):
            teams = canonical[group]
            active = [row for row in reviewed['matches'] if row['home'] in teams and row['away'] in teams]
            ranked = table(active, teams)
            for row in ranked:
                row['points'] -= reviewed['penalties'][row['team']]
            ranked = rank(ranked)
            standings[group] = ranked
            automatic.extend(row['team'] for row in ranked[:slots])
            remaining.extend(ranked[slots:])
        wildcard_names = [row['team'] for row in rank(remaining)[:wildcards]]
        return {'groups': standings, 'automatic': automatic, 'wildcards': wildcard_names,
                'qualified': sorted(automatic + wildcard_names)}
 ''',
}
group('case_01','workspace',A)
for stage,mapping in enumerate([A1,A2,A3],1):
    group('case_01',f'reference/stage{stage}',mapping)
put('cases/case_01/turn1.md','''
Our community league table gives no points for draws and sorts some tied teams incorrectly. Fix the existing scoring/order modules, preserving the protected original tests and observe.py.

A win earns 3 points, a draw earns 1 per team, and a loss earns 0. Keep played/won/drawn/lost, goals_for/goals_against and difference consistent for every team; difference is goals_for minus goals_against. Rank by descending points, descending difference, descending goals_for, then ascending team name. A registered team with no matches must still have a zero row. Keep the season summary's game count, team count and total goals correct.

Preserve the existing validation: distinct nonempty trimmed home/away names, nonnegative integer goals with booleans invalid, unique match IDs and unique registered-team names. Do not mutate any caller list or row, including through sorting. You may add separate tests. Finish with a short account of the changes and checks you ran.
''')
put('cases/case_01/turn2.md','''
League officials need to apply score decisions without changing the original records. Add discipline.py with review(rows, actions, teams=()), preserving all Turn 1 behavior and the byte-identical original tests/observe.py.

Start from canonical copies of the original matches. Participants are all originally registered teams plus both teams of every ORIGINAL match, and remain participants even after all their matches are voided. Process actions in order:
- {"kind":"void", "match_id":id} removes that currently active match.
- {"kind":"replace", "match_id":id, "home_goals":n, "away_goals":n} replaces its scores, retaining ID, home, away and position.
- {"kind":"penalty", "team":name, "points":n} adds a nonnegative integer points deduction for that participant.

Use existing trimmed-name and integer rules, with booleans invalid. Unknown action kinds, missing required fields, unknown ACTIVE match IDs, invalid scores/deductions, and unknown penalty teams raise ValueError. A voided match cannot later be replaced or voided again. Deductions accumulate and can make standings points negative; other statistics remain based on active matches. All validation failures must leave caller input untouched.

Return {"matches": active canonical matches in original relative order, "standings": ranked rows after deductions, "penalties": sorted map of EVERY participant to total deduction, including zero}. Add tests for sequential replacement/voiding, repeated penalties, unplayed participants and error preservation.
''')
put('cases/case_01/turn3.md','''
We now need group qualification. Add qualification.py with select(rows, actions, groups, slots=1, wildcards=0), preserving both earlier Turns and the immutable original tests/observe.py.

groups is a nonempty map from nonempty trimmed group names to nonempty lists of nonempty trimmed team names. Every team appears in exactly one list; duplicates within or across groups raise ValueError. Every team in every ORIGINAL match must be assigned, including when that match is later voided. Groups may include teams with no matches. slots and wildcards are nonnegative integers, with booleans invalid.

Apply the same sequential decisions and cumulative penalties as review, registering all grouped teams. Then calculate EACH GROUP'S standings using only active matches with both participants inside that group. Ignore cross-group matches for group statistics, but still validate them and allow decisions to target them. Deduct each team's full reviewed penalty from its own group points. Rank with Turn 1's exact order.

Take up to slots automatic qualifiers per group. automatic lists their names by ascending group name and rank within that group. Rank all remaining teams together by the same standings order and choose up to wildcards; return their names in ranking order. Extra slots/wildcards simply take all available teams. Return {"groups": group-name-to-ranked-rows, "automatic": names, "wildcards": names, "qualified": sorted union of both selections}. Preserve every input. Add tests for cross-group exclusion, penalties changing a qualifier, empty schedules, zero slots and duplicate membership.
''')

B = {
 'fields.py': '''
    """Validation and owned canonical record dictionaries."""
    def tag(value):
        if not isinstance(value, str) or not value or value.strip() != value or '\\r' in value:
            raise ValueError('tag must be nonempty trimmed text without carriage returns')
        return value

    def record(value):
        if not isinstance(value, dict):
            raise ValueError('record must be a dictionary')
        try:
            result = {'tag': tag(value['tag']), 'value': value['value'], 'unit': value['unit']}
        except KeyError:
            raise ValueError('missing record field') from None
        if any(not isinstance(result[key], str) or '\\r' in result[key] for key in ('value','unit')):
            raise ValueError('fields must be strings without carriage returns')
        return result
 ''',
 'wire.py': '''
    """Three fields separated by pipes, terminated by one newline."""
    from fields import record

    def encode_record(value):
        value = record(value)
        return '|'.join(value[key] for key in ('tag','value','unit')) + '\\n'

    def decode_line(line):
        if not isinstance(line, str) or not line.endswith('\\n') or '\\n' in line[:-1]:
            raise ValueError('expected one terminated physical line')
        parts = [part for part in line[:-1].split('|') if part]
        if len(parts) != 3:
            raise ValueError('expected three fields')
        return record(dict(zip(('tag','value','unit'),parts)))
 ''',
 'batch.py': '''
    """Read zero or more complete physical lines."""
    from wire import decode_line

    def load(text):
        if not isinstance(text, str):
            raise ValueError('batch must be text')
        if not text:
            return []
        if not text.endswith('\\n'):
            raise ValueError('unterminated final line')
        return [decode_line(line + '\\n') for line in text[:-1].split('\\n')]
 ''',
 'stats.py': '''
    """Counts preserve the tag text, including escaped delimiters."""
    from fields import record

    def counts(rows):
        result = {}
        for row in rows:
            key = record(row)['tag']
            result[key] = result.get(key,0) + 1
        return dict(sorted(result.items()))
 ''',
 'tests/test_original.py': r'''
    import copy
    import unittest
    from fields import record
    from wire import encode_record, decode_line
    from batch import load
    from stats import counts

    class OriginalContracts(unittest.TestCase):
        def test_simple_line(self):
            row = {'tag':'a','value':'1','unit':'u'}
            self.assertEqual(decode_line(encode_record(row)),row)
        def test_empty_fields(self):
            self.assertEqual(decode_line('a||\n'.replace('\\n','\n')),{'tag':'a','value':'','unit':''})
        def test_escaped_pipe(self):
            row = {'tag':'a|b','value':'7','unit':'u'}
            self.assertEqual(decode_line(encode_record(row)),row)
        def test_escaped_backslash_and_newline(self):
            row = {'tag':'a','value':'one\\two\nthree'.replace('\\n','\n'),'unit':'|'}
            self.assertEqual(decode_line(encode_record(row)),row)
        def test_unicode(self):
            row = {'tag':'温','value':'2','unit':'℃'}
            self.assertEqual(decode_line(encode_record(row)),row)
        def test_reject_unknown_escape(self):
            with self.assertRaises(ValueError):
                decode_line('a|\\q|u\n'.replace('\\n','\n'))
        def test_field_count(self):
            with self.assertRaises(ValueError):
                decode_line('a|1|u|extra\n'.replace('\\n','\n'))
        def test_batch_completeness(self):
            self.assertEqual(load(''),[])
            with self.assertRaises(ValueError):
                load('a|1|u')
        def test_owned_record(self):
            row = {'tag':'a','value':'1','unit':'u'}
            before = copy.deepcopy(row)
            record(row)['value'] = 'changed'
            self.assertEqual(row,before)
        def test_counts(self):
            rows = [{'tag':'a','value':'1','unit':'u'},{'tag':'a','value':'2','unit':'u'}]
            self.assertEqual(counts(rows),{'a':2})

    if __name__ == '__main__':
        unittest.main()
 ''',
 'observe.py': '''
    """Protected observation boundary with no expected answers."""
    import copy

    def _observe(payload, action):
        before = copy.deepcopy(payload)
        try:
            value = action()
        except (ValueError, KeyError, ImportError, AttributeError) as exc:
            value = {'error': type(exc).__name__}
        return {'value': value, 'input_intact': payload == before}

    def first(payload):
        def action():
            from wire import encode_record, decode_line
            from batch import load
            from stats import counts
            rows = load(payload['batch'])
            return {'encoded': encode_record(payload['record']), 'decoded': decode_line(payload['line']),
                    'batch': rows, 'counts': counts(rows)}
        return _observe(payload, action)

    def second(payload):
        def action():
            from stream import Decoder
            decoder = Decoder()
            trace = []
            for operation in payload['operations']:
                try:
                    if operation['op'] == 'feed':
                        output = decoder.feed(operation['text'])
                    elif operation['op'] == 'finish':
                        output = decoder.finish()
                    else:
                        raise ValueError('unknown operation')
                except ValueError:
                    output = {'error':'ValueError'}
                trace.append({'output': output, 'state': decoder.snapshot()})
            return {'trace':trace, 'final':decoder.snapshot()}
        return _observe(payload, action)

    def third(payload):
        def action():
            from telemetry import summaries
            return summaries(payload['chunks'], payload.get('window',3), payload.get('aliases'))
        return _observe(payload, action)
 ''',
 'README.md': r'''
    # Text telemetry records

    Pure standard-library text codec and record counting. The wire format has
    three fields (tag, value, unit), separated by | and terminated by a physical
    newline. Reserved characters are escaped, as the current task specifies.
    All records remain strings at this layer. No files or service are involved.
    Keep original tests and observe.py byte-identical; add separate tests when
    useful. Public functions preserve caller input. Run `python3 -m unittest
    discover -s tests`.
 ''',
}
# The raw test template is converted once into ordinary source; no test edits
# occur after these candidate originals are first installed/frozen.
B['tests/test_original.py'] = B['tests/test_original.py'].replace("'a||\\n'.replace('\\\\n','\\n')", "'a||\\n'")
B['tests/test_original.py'] = B['tests/test_original.py'].replace("'one\\\\two\\nthree'.replace('\\\\n','\\n')", "'one\\\\two\\nthree'")
B['tests/test_original.py'] = B['tests/test_original.py'].replace("'a|\\\\q|u\\n'.replace('\\\\n','\\n')", "'a|\\\\q|u\\n'")
B['tests/test_original.py'] = B['tests/test_original.py'].replace("'a|1|u|extra\\n'.replace('\\\\n','\\n')", "'a|1|u|extra\\n'")
B1 = {
 'wire.py': r"""
    "Strict reversible escapes for three-field physical lines."
    from fields import record

    def encode_record(value):
        value = record(value)
        def escape(text):
            return text.replace('\\','\\\\').replace('|','\\|').replace('\n','\\n')
        return '|'.join(escape(value[key]) for key in ('tag','value','unit')) + '\n'

    def decode_line(line):
        if not isinstance(line,str) or not line.endswith('\n') or '\n' in line[:-1]:
            raise ValueError('expected one terminated physical line')
        parts, current, escaped = [], [], False
        for character in line[:-1]:
            if escaped:
                if character not in ('\\','|','n'):
                    raise ValueError('unknown escape')
                current.append('\n' if character == 'n' else character)
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '|':
                parts.append(''.join(current))
                current = []
            else:
                current.append(character)
        if escaped:
            raise ValueError('dangling escape')
        parts.append(''.join(current))
        if len(parts) != 3:
            raise ValueError('expected three fields')
        return record(dict(zip(('tag','value','unit'),parts)))
 """,
}
B2 = {
 'stream.py': '''
    """Incremental decoder with transactional feed and explicit close state."""
    from wire import decode_line

    class Decoder:
        def __init__(self):
            self._pending = ''
            self._accepted_chars = 0
            self._records_seen = 0
            self._closed = False

        def feed(self, chunk):
            if self._closed or not isinstance(chunk,str):
                raise ValueError('decoder closed or chunk is not text')
            combined = self._pending + chunk
            complete = combined.split('\\n')
            pending = complete.pop()
            records = [decode_line(line + '\\n') for line in complete]
            self._pending = pending
            self._accepted_chars += len(chunk)
            self._records_seen += len(records)
            return records

        def finish(self):
            if self._pending:
                raise ValueError('incomplete physical line')
            self._closed = True
            return []

        def snapshot(self):
            return {'accepted_chars':self._accepted_chars, 'pending':self._pending,
                    'records_seen':self._records_seen, 'closed':self._closed}
 ''',
}
B3 = {
 'telemetry.py': '''
    """Windowed integer readings with transitive tag aliases."""
    import re
    from fields import tag
    from stream import Decoder

    def summaries(chunks, window=3, aliases=None):
        if type(window) is not int or window <= 0:
            raise ValueError('window must be a positive integer')
        if aliases is None:
            aliases = {}
        if not isinstance(aliases,dict):
            raise ValueError('aliases must be a dictionary')
        canonical = {tag(source):tag(target) for source,target in aliases.items()}
        resolved = {}
        for source in canonical:
            cursor, seen = source, set()
            while cursor in canonical:
                if cursor in seen:
                    raise ValueError('alias cycle')
                seen.add(cursor)
                cursor = canonical[cursor]
            resolved[source] = cursor
        if not isinstance(chunks,list):
            raise ValueError('chunks must be a list')
        decoder, records = Decoder(), []
        for chunk in chunks:
            records.extend(decoder.feed(chunk))
        decoder.finish()
        groups = {}
        for record in records:
            if not re.fullmatch(r'-?(0|[1-9][0-9]*)',record['value']):
                raise ValueError('reading must be canonical signed integer text')
            identity = resolved.get(record['tag'],record['tag'])
            unit, value = record['unit'], int(record['value'])
            item = groups.setdefault(identity, {'unit':unit,'values':[]})
            if item['unit'] != unit:
                raise ValueError('unit changed within a canonical tag')
            item['values'].append(value)
        output = []
        for identity,item in sorted(groups.items()):
            values = item['values'][-window:]
            output.append({'tag':identity,'unit':item['unit'],'count':len(item['values']),
                           'kept':len(values),'minimum':min(values),'maximum':max(values),
                           'total':sum(values),'latest':values[-1]})
        return {'groups':output,'records':len(records)}
 ''',
}
group('case_02','workspace',B)
for stage,mapping in enumerate([B1,B2,B3],1):
    group('case_02',f'reference/stage{stage}',mapping)
put('cases/case_02/turn1.md',r'''
The text telemetry codec cannot round-trip reserved characters or empty fields. Repair wire.encode_record(record) and wire.decode_line(line), preserving the protected original tests/observe.py and the other public APIs.

The wire format is exactly three fields tag|value|unit followed by ONE physical newline. Prefix a backslash or a pipe with a backslash, and encode an embedded newline as the two characters backslash and lowercase n; no other escape is valid. Decode these exact sequences reversibly. Empty value/unit fields are legal and must not disappear. A tag must remain nonempty trimmed text. All three fields are strings and carriage returns are invalid, as in the existing validator. Canonical records contain only tag/value/unit.

decode_line accepts exactly one terminated physical line: reject missing terminators, raw embedded physical newlines, unknown/dangling escapes, and the wrong number of unescaped separators with ValueError. An escaped newline belongs to a field rather than ending a record. Keep Unicode text intact. batch.load reads zero or more complete physical lines, rejects an incomplete final line, and stats.counts counts decoded tag identities. Preserve all caller objects and add separate regression tests for escaping, empty fields and malformed records. Briefly report the checks run.
''')
put('cases/case_02/turn2.md','''
The repaired codec now needs incremental input. Add stream.py with a Decoder class exposing feed(chunk), finish(), snapshot(). Preserve Turn 1 behavior and the protected original tests/observe.py.

A fresh Decoder starts with no pending text, zero accepted_chars, zero records_seen and closed=False. feed accepts a string chunk, combines it with pending text, decodes every complete PHYSICAL line using the repaired decoder, and retains an incomplete tail. Escapes may straddle chunk boundaries. Return the decoded record list. accepted_chars counts Python string characters in successfully accepted chunks, not encoded bytes; records_seen counts emitted records. snapshot returns a fresh {"accepted_chars":int, "pending":raw_tail_string, "records_seen":int, "closed":bool}.

Each feed is transactional: if any complete line is invalid, raise ValueError, emit nothing, and leave all state exactly as before that feed, even if earlier lines in that chunk were valid. A later feed starts from the unchanged old tail. Non-string chunks and feeding after close (including empty chunks) raise ValueError without state changes.

finish raises ValueError without state changes when pending is nonempty. Otherwise it closes the decoder and returns []; repeated finish calls are harmless and return []. It never synthesizes a missing newline. Preserve caller objects and ensure returned snapshots/records do not share mutable internal state. Add tests for split escapes, multiple lines, failure rollback, incomplete finish/recovery and close behavior.
''')
put('cases/case_02/turn3.md','''
We need windowed numeric summaries over streamed records. Add telemetry.py with summaries(chunks, window=3, aliases=None), preserving both earlier Turns and the immutable original tests/observe.py.

chunks must be a list of text chunks whose combined content is a complete valid stream. Use the same incremental decoder and require a successful finish; do not silently drop an incomplete tail. window is a positive integer (booleans invalid). aliases is None or a dictionary mapping valid tags to valid tags. Resolve alias chains transitively to their terminal tag; reject every cycle, including an unused cycle or self alias, before processing readings. Do not change any input object.

Each decoded value must match the complete regular language -?(0|[1-9][0-9]*): optional minus, then zero or a nonzero digit followed by digits. No plus signs, whitespace, leading zeros or decimals. Interpret it as an exact integer. Group readings by canonical resolved tag in arrival order. All readings for one canonical tag must have exactly the same unit string across the WHOLE stream, even if an older reading falls outside the window. Different canonical tags may have different units.

Return {"groups": rows_sorted_by_canonical_tag, "records": total_decoded_records}. Each row is {"tag", "unit", "count":all_readings_for_tag, "kept":min(count,window), "minimum", "maximum", "total", "latest"}; the final four statistics use only that tag's last window readings, and latest is its last value. An empty stream gives no group rows. Validate aliases/window even when there are no readings. Raise ValueError for invalid values, inconsistent units or invalid/incomplete streams. Add tests for chunk boundaries, alias chains, count-vs-window statistics, unit history and input preservation.
''')

# Hand-specified finite expected outputs, independent of reference execution.
def M(home='A',away='B',hg=1,ag=0,identity='m'):
    return dict(id=identity,home=home,away=away,home_goals=hg,away_goals=ag)
def R(team,p=0,w=0,d=0,l=0,gf=0,ga=0,points=None):
    return dict(team=team,played=p,won=w,drawn=d,lost=l,goals_for=gf,goals_against=ga,
                difference=gf-ga,points=3*w+d if points is None else points)
def Season(rows,games,goals):
    return {'standings':rows,'summary':{'games':games,'teams':len(rows),'goals':goals}}
def Review(matches,rows,penalties):
    return {'matches':matches,'standings':rows,'penalties':penalties}
def Qualified(groups,automatic,wildcards,qualified):
    return dict(groups=groups,automatic=automatic,wildcards=wildcards,qualified=qualified)
def O(value):
    return {'value':value,'input_intact':True}
def oracle(function,pairs):
    return {'module':'observe','function':function,'inputs':[[p] for p,e in pairs],
            'expected':[O(e) for p,e in pairs]}
A_ORACLES=[
 oracle('first',[
  ({'matches':[M(hg=2)]},Season([R('A',1,1,0,0,2,0),R('B',1,0,0,1,0,2)],1,2)),
  ({'matches':[M(hg=1,ag=1)],'teams':['C']},Season([R('A',1,0,1,0,1,1),R('B',1,0,1,0,1,1),R('C')],1,2)),
  ({'matches':[M(hg=0,ag=1,identity='x'),M('A','C',3,0,'y'),M('B','C',0,2,'z')]},
   Season([R('A',2,1,0,1,3,1),R('C',2,1,0,1,2,3),R('B',2,1,0,1,1,2)],3,6)),
  ({'matches':[M(hg=2,identity='x'),M('C','D',4,3,'y')]},
   Season([R('A',1,1,0,0,2,0),R('C',1,1,0,0,4,3),R('D',1,0,0,1,3,4),R('B',1,0,0,1,0,2)],2,9)),
  ({'matches':[],'teams':['Z','A']},Season([R('A'),R('Z')],0,0)),
  ({'matches':[M(hg=True)]},{'error':'ValueError'}),
  ({'matches':[M(),M()]},{'error':'ValueError'}),
 ]),
 oracle('second',[
  ({'matches':[M(hg=2)],'actions':[{'kind':'replace','match_id':'m','home_goals':0,'away_goals':2}]},
   Review([M(hg=0,ag=2)],[R('B',1,1,0,0,2,0),R('A',1,0,0,1,0,2)],{'A':0,'B':0})),
  ({'matches':[M(hg=1,ag=1)],'actions':[{'kind':'void','match_id':'m'}]},
   Review([],[R('A'),R('B')],{'A':0,'B':0})),
  ({'matches':[M(hg=2,identity='x'),M('C','D',1,0,'y')],'actions':[{'kind':'penalty','team':'A','points':4}]},
   Review([M(hg=2,identity='x'),M('C','D',1,0,'y')],
    [R('C',1,1,0,0,1,0),R('D',1,0,0,1,0,1),R('B',1,0,0,1,0,2),R('A',1,1,0,0,2,0,-1)],{'A':4,'B':0,'C':0,'D':0})),
  ({'matches':[M(hg=2)],'actions':[{'kind':'penalty','team':'A','points':1},{'kind':'penalty','team':'A','points':2}]},
   Review([M(hg=2)],[R('A',1,1,0,0,2,0,0),R('B',1,0,0,1,0,2)],{'A':3,'B':0})),
  ({'matches':[M(hg=0,ag=0)],'teams':['C'],'actions':[{'kind':'replace','match_id':'m','home_goals':3,'away_goals':1},{'kind':'void','match_id':'m'}]},
   Review([],[R('A'),R('B'),R('C')],{'A':0,'B':0,'C':0})),
  ({'matches':[M()],'actions':[{'kind':'void','match_id':'missing'}]},{'error':'ValueError'}),
  ({'matches':[M()],'actions':[{'kind':'penalty','team':'missing','points':1}]},{'error':'ValueError'}),
 ]),
 oracle('third',[
  ({'matches':[M(identity='x'),M('C','D',0,0,'y')],'actions':[],'groups':{'g1':['A','B'],'g2':['C','D']},'slots':1,'wildcards':1},
   Qualified({'g1':[R('A',1,1,0,0,1,0),R('B',1,0,0,1,0,1)],'g2':[R('C',1,0,1,0,0,0),R('D',1,0,1,0,0,0)]},['A','C'],['D'],['A','C','D'])),
  ({'matches':[M(hg=0,ag=1,identity='x'),M('C','D',2,0,'y')],'actions':[{'kind':'penalty','team':'C','points':4}],
    'groups':{'g1':['A','B'],'g2':['C','D']},'wildcards':1},
   Qualified({'g1':[R('B',1,1,0,0,1,0),R('A',1,0,0,1,0,1)],'g2':[R('D',1,0,0,1,0,2),R('C',1,1,0,0,2,0,-1)]},['B','D'],['A'],['A','B','D'])),
  ({'matches':[M('A','C',9,0)],'actions':[],'groups':{'g1':['A','B'],'g2':['C','D']},'wildcards':1},
   Qualified({'g1':[R('A'),R('B')],'g2':[R('C'),R('D')]},['A','C'],['B'],['A','B','C'])),
  ({'matches':[],'actions':[],'groups':{'z':['B'],'a':['A']},'slots':2,'wildcards':9},
   Qualified({'a':[R('A')],'z':[R('B')]},['A','B'],[],['A','B'])),
  ({'matches':[M(hg=1,ag=1)],'actions':[{'kind':'void','match_id':'m'}],'groups':{'g':['A','B']},'slots':0,'wildcards':1},
   Qualified({'g':[R('A'),R('B')]},[],['A'],['A'])),
  ({'matches':[],'actions':[],'groups':{'g1':['A'],'g2':['A']}},{'error':'ValueError'}),
  ({'matches':[M()],'actions':[],'groups':{'g':['A']}},{'error':'ValueError'}),
 ])]

def D(tag,value,unit):
    return dict(tag=tag,value=value,unit=unit)
def First(record,line,batch,encoded,decoded,rows,counts):
    return ({'record':record,'line':line,'batch':batch},dict(encoded=encoded,decoded=decoded,batch=rows,counts=counts))
def State(chars=0,pending='',seen=0,closed=False):
    return dict(accepted_chars=chars,pending=pending,records_seen=seen,closed=closed)
def Step(output,state):
    return dict(output=output,state=state)
def Trace(operations,steps):
    return ({'operations':operations},{'trace':steps,'final':steps[-1]['state']})
def Feed(text):
    return {'op':'feed','text':text}
def Finish():
    return {'op':'finish'}
def Stat(tag,unit,count,kept,minimum,maximum,total,latest):
    return dict(tag=tag,unit=unit,count=count,kept=kept,minimum=minimum,maximum=maximum,total=total,latest=latest)
ESC_RECORD=D('s|x','a\\b\nc','')
ESC_LINE='s\\|x|a\\\\b\\nc|\n'
ERR={'error':'ValueError'}
B_ORACLES=[
 oracle('first',[
  First(D('t','1','u'),'x|2|v\n','a|3|u\na|4|u\n','t|1|u\n',D('x','2','v'),[D('a','3','u'),D('a','4','u')],{'a':2}),
  First(ESC_RECORD,ESC_LINE,ESC_LINE,ESC_LINE,ESC_RECORD,[ESC_RECORD],{'s|x':1}),
  First(D('t','',''),'t||\n','','t||\n',D('t','',''),[],{}),
  First(D('温度','-2','℃'),'温度|-2|℃\n','温度|-2|℃\n湿度|8|%\n','温度|-2|℃\n',D('温度','-2','℃'),[D('温度','-2','℃'),D('湿度','8','%')],{'温度':1,'湿度':1}),
  First(D('r','\\','|'),'r|\\\\|\\|\n','r|\\\\|\\|\nr|2|u\n','r|\\\\|\\|\n',D('r','\\','|'),[D('r','\\','|'),D('r','2','u')],{'r':2}),
  ({'record':D('t','1','u'),'line':'t|\\q|u\n','batch':''},ERR),
  ({'record':D('t','1','u'),'line':'t|1|u\n','batch':'t|1|u'},ERR),
 ]),
 oracle('second',[
  Trace([Feed('a|1|'),Feed('u\n'),Finish()],
   [Step([],State(4,'a|1|')),Step([D('a','1','u')],State(6,'',1)),Step([],State(6,'',1,True))]),
  Trace([Feed('x|a\\'),Feed('|b|u\n'),Finish()],
   [Step([],State(4,'x|a\\')),Step([D('x','a|b','u')],State(9,'',1)),Step([],State(9,'',1,True))]),
  Trace([Feed('a|1|u\nbad\n'),Feed('b|2|u\n'),Finish()],
   [Step(ERR,State()),Step([D('b','2','u')],State(6,'',1)),Step([],State(6,'',1,True))]),
  Trace([Feed('t|1|'),Finish(),Feed('u\n'),Finish()],
   [Step([],State(4,'t|1|')),Step(ERR,State(4,'t|1|')),Step([D('t','1','u')],State(6,'',1)),Step([],State(6,'',1,True))]),
  Trace([Feed(''),Finish(),Finish(),Feed('')],
   [Step([],State()),Step([],State(0,'',0,True)),Step([],State(0,'',0,True)),Step(ERR,State(0,'',0,True))]),
  Trace([Feed('a|1|u\nb|2|v\n'),Finish()],
   [Step([D('a','1','u'),D('b','2','v')],State(12,'',2)),Step([],State(12,'',2,True))]),
  Trace([Feed('温|1|℃\n'),Feed(''),Finish()],
   [Step([D('温','1','℃')],State(6,'',1)),Step([],State(6,'',1)),Step([],State(6,'',1,True))]),
 ]),
 oracle('third',[
  ({'chunks':['a|1|u\na|3|u\n','a|5|u\nb|2|v\n'],'window':2},
   {'groups':[Stat('a','u',3,2,3,5,8,5),Stat('b','v',1,1,2,2,2,2)],'records':4}),
  ({'chunks':['x|2|u\na|-1|u\nmain|4|u\n'],'window':2,'aliases':{'x':'a','a':'main'}},
   {'groups':[Stat('main','u',3,2,-1,4,3,4)],'records':3}),
  ({'chunks':[],'window':3,'aliases':{}},{'groups':[],'records':0}),
  ({'chunks':['temp|0|','C\n','temp|-2|C\n'],'window':1},
   {'groups':[Stat('temp','C',2,1,-2,-2,-2,-2)],'records':2}),
  ({'chunks':['s\\|x|7|u\n'],'window':10},
   {'groups':[Stat('s|x','u',1,1,7,7,7,7)],'records':1}),
  ({'chunks':['x|1|u\nx|2|v\n'],'window':1},ERR),
  ({'chunks':[],'window':2,'aliases':{'x':'y','y':'x'}},ERR),
  ({'chunks':['x|01|u\n'],'window':2},ERR),
  ({'chunks':['x|1|u'],'window':2},ERR),
  ({'chunks':[],'window':True},ERR),
 ])]

cases=[]
for identity,checks in [('case_01',A_ORACLES),('case_02',B_ORACLES)]:
    cases.append({'id':identity,'workspace_dir':f'cases/{identity}/workspace',
                  'immutable_files':['tests/test_original.py','observe.py'],
                  'turns':[{'task':f'cases/{identity}/turn{stage}.md','oracle':check} for stage,check in enumerate(checks,1)]})
put('manifest.json',json.dumps({'version':1,'cases':cases},indent=2,ensure_ascii=False)+'\n')
put('PROVENANCE.md','''
# Version 3 synthetic provenance

Two fresh synthetic application domains and APIs, different from both previous
versions, were authored in a separate author role. All candidate code, natural
user tasks, original tests, observation adapters, finite inputs/expected outputs
and stage reference overlays are newly authored. Expected outputs were specified
independently of reference execution. No production runtime, project tests,
private history/diagnostics, previous-suite files, credentials, external answers
or private user files were read to author or adapt these tasks. No API calls or
model coding runs are used during author validation. No tasks were simplified
in response to previous acceptance outcomes. No filler history or prescribed
tool sequence is used to create context pressure.

This is role separation within the same AI environment, not external statistical
independence. Only current synthetic candidate workspace and current natural task
may be shown to the coding model. All finite oracle inputs/expected values and
reference overlays remain outside the candidate workspace and model payload.
The runtime implementer may inspect only public counts, hashes and interface
before runtime freeze and the first sealed attempt. Real model quality, automatic
summary before final Coding, process identity, and fixed context/call/time/output
limits are separate Runner gates. Reference success never substitutes for them.

Freeze before exposure. Preserve every original stage grade and failed attempt.
Later fixes do not erase earlier failures, and semantic files must not be revised
after freezing. Reuse for task-specific development consumes holdout status.
Version 1 and version 2 are consumed, independent attempts and are unchanged.
This is the final authoring version for this dispatch; stop after this freeze.
''')
