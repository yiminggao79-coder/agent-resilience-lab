"""Score decisions applied on top of canonical match records.

``review`` takes the original match rows plus an ordered list of actions and
returns the still-active matches, the resulting standings and the total
deduction for every participant.  Every validation happens on copies, so a
failure never touches caller objects.
"""
from results import matches, name, number, participants
from scoring import table
from ordering import rank

KINDS = ('void', 'replace', 'penalty')


def _field(action, key):
    if not isinstance(action, dict):
        raise ValueError('action must be a dictionary')
    if key not in action:
        raise ValueError('missing action field')
    return action[key]


def _actions(actions):
    """Validate and normalise the action list without touching the caller."""
    try:
        items = list(actions)
    except TypeError:
        raise ValueError('actions must be iterable') from None
    output = []
    for action in items:
        kind = _field(action, 'kind')
        if kind not in KINDS:
            raise ValueError('unknown action kind')
        if kind == 'penalty':
            output.append(dict(kind=kind, team=name(_field(action, 'team')),
                               points=number(_field(action, 'points'))))
        else:
            identity = name(_field(action, 'match_id'))
            if kind == 'void':
                output.append(dict(kind=kind, match_id=identity))
            else:
                output.append(dict(kind=kind, match_id=identity,
                                   home_goals=number(_field(action, 'home_goals')),
                                   away_goals=number(_field(action, 'away_goals'))))
    return output


def review(rows, actions, teams=()):
    original = matches(rows)
    planned = _actions(actions)
    people = participants(original, teams)

    active = {row['id']: dict(row) for row in original}
    order = [row['id'] for row in original]
    deductions = {team: 0 for team in people}

    for action in planned:
        kind = action['kind']
        if kind == 'penalty':
            team = action['team']
            if team not in deductions:
                raise ValueError('unknown penalty team')
            deductions[team] += action['points']
            continue
        identity = action['match_id']
        if identity not in active:
            raise ValueError('unknown active match id')
        if kind == 'void':
            del active[identity]
        else:
            match = active[identity]
            match['home_goals'] = action['home_goals']
            match['away_goals'] = action['away_goals']

    remaining = [dict(active[identity]) for identity in order if identity in active]
    standings = table(remaining, people)
    for row in standings:
        row['points'] -= deductions[row['team']]
    standings = rank(standings)
    return {'matches': remaining, 'standings': standings,
            'penalties': {team: deductions[team] for team in sorted(deductions)}}
