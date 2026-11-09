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
