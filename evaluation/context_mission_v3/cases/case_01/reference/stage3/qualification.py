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
