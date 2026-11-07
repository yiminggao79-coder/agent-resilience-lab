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
