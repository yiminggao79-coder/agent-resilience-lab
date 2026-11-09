"""Apply score decisions to copies of the original league records."""
import results
import scoring
import ordering

_REQUIRED = {
    'void': ('match_id',),
    'replace': ('match_id', 'home_goals', 'away_goals'),
    'penalty': ('team', 'points'),
}


def _field(action, key):
    if key not in action:
        raise ValueError('missing action field: %s' % key)
    return action[key]


def _active_match(active, identity):
    for index, row in enumerate(active):
        if row['id'] == identity:
            return index
    raise ValueError('unknown active match id')


def review(rows, actions, teams=()):
    originals = results.matches(rows)
    people = results.participants(originals, teams)
    deductions = {team: 0 for team in people}
    active = [dict(row) for row in originals]
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError('action must be a dictionary')
        kind = action.get('kind')
        if kind not in _REQUIRED:
            raise ValueError('unknown action kind')
        if kind == 'void':
            index = _active_match(active, results.name(_field(action, 'match_id')))
            active.pop(index)
        elif kind == 'replace':
            index = _active_match(active, results.name(_field(action, 'match_id')))
            home_goals = results.number(_field(action, 'home_goals'))
            away_goals = results.number(_field(action, 'away_goals'))
            active[index]['home_goals'] = home_goals
            active[index]['away_goals'] = away_goals
        else:
            team = results.name(_field(action, 'team'))
            points = results.number(_field(action, 'points'))
            if team not in deductions:
                raise ValueError('unknown penalty team')
            deductions[team] += points
    standings = scoring.table(active, people)
    for row in standings:
        row['points'] -= deductions[row['team']]
    return {
        'matches': active,
        'standings': ordering.rank(standings),
        'penalties': dict(sorted(deductions.items())),
    }
