"""Apply score decisions to canonical match copies without touching caller data."""
from results import matches, name, number, participants
from scoring import table
from ordering import rank

KINDS = ('void', 'replace', 'penalty')


def _field(action, key):
    if key not in action:
        raise ValueError('missing action field')
    return action[key]


def review(rows, actions, teams=()):
    canonical = matches(rows)
    people = participants(canonical, teams)
    active = list(canonical)
    voided = set()
    penalties = {team: 0 for team in people}
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError('action must be a dictionary')
        kind = _field(action, 'kind')
        if kind == 'void':
            identity = name(_field(action, 'match_id'))
            index = _locate(active, identity, voided)
            del active[index]
            voided.add(identity)
        elif kind == 'replace':
            identity = name(_field(action, 'match_id'))
            index = _locate(active, identity, voided)
            home_goals = number(_field(action, 'home_goals'))
            away_goals = number(_field(action, 'away_goals'))
            row = active[index]
            active[index] = dict(row, home_goals=home_goals, away_goals=away_goals)
        elif kind == 'penalty':
            team = name(_field(action, 'team'))
            points = number(_field(action, 'points'))
            if team not in penalties:
                raise ValueError('unknown penalty team')
            penalties[team] += points
        else:
            raise ValueError('unknown action kind')
    standings = table(active, people)
    for row in standings:
        row['points'] -= penalties[row['team']]
    return {'matches': active,
            'standings': rank(standings),
            'penalties': {team: penalties[team] for team in sorted(penalties)}}


def _locate(active, identity, voided):
    for index, row in enumerate(active):
        if row['id'] == identity:
            return index
    if identity in voided:
        raise ValueError('match already voided')
    raise ValueError('unknown match id')
