"""Group qualification from reviewed match decisions.

Extends ``discipline.review`` with group tables: only active matches whose two
participants belong to the same group count towards that group's standings,
while every team still carries its full cumulative penalty deduction.
"""
import results
import scoring
import ordering
import discipline


def _count(value):
    return results.number(value)


def _group_table(groups):
    """Validate the group map and return (ordered mapping, team -> group)."""
    if not isinstance(groups, dict) or not groups:
        raise ValueError('groups must be a nonempty mapping')
    membership = {}
    ordered = {}
    for group in groups:
        key = results.name(group)
        if key in ordered:
            raise ValueError('duplicate group name')
        teams = groups[group]
        if not isinstance(teams, list):
            raise ValueError('group members must be a list of team names')
        if not teams:
            raise ValueError('group must contain at least one team')
        members = []
        for team in teams:
            team = results.name(team)
            if team in membership:
                raise ValueError('team appears in more than one position')
            membership[team] = key
            members.append(team)
        ordered[key] = members
    return dict(sorted(ordered.items())), membership


def select(rows, actions, groups, slots=1, wildcards=0):
    slots = _count(slots)
    wildcards = _count(wildcards)
    grouped, membership = _group_table(groups)

    originals = results.matches(rows)
    for row in originals:
        for team in (row['home'], row['away']):
            if team not in membership:
                raise ValueError('unassigned team: %s' % team)

    participants = sorted(set(membership) | set(results.participants(originals)))
    reviewed = discipline.review(rows, actions, participants)
    active = reviewed['matches']
    deductions = reviewed['penalties']

    group_ranked = {}
    for group, members in grouped.items():
        inside = set(members)
        local = [row for row in active if row['home'] in inside and row['away'] in inside]
        table = scoring.table(local, members)
        for row in table:
            row['points'] -= deductions.get(row['team'], 0)
        group_ranked[group] = ordering.rank(table)

    automatic_rows = []
    leftovers = []
    for group in sorted(group_ranked):
        ranked = group_ranked[group]
        automatic_rows.extend(ranked[:slots])
        leftovers.extend(ranked[slots:])
    automatic = [row['team'] for row in automatic_rows]
    wildcard_names = [row['team'] for row in ordering.rank(leftovers)][:wildcards]

    return {
        'groups': group_ranked,
        'automatic': automatic,
        'wildcards': wildcard_names,
        'qualified': sorted(set(automatic) | set(wildcard_names)),
    }
