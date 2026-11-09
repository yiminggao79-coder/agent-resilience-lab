"""Group qualification from reviewed league decisions."""
from collections.abc import Mapping

import results
import scoring
import ordering
import discipline


def _groups(groups):
    if not isinstance(groups, Mapping) or not groups:
        raise ValueError('groups must be a nonempty mapping')
    output, seen = {}, set()
    for group_name, members in groups.items():
        group_name = results.name(group_name)
        if not isinstance(members, list) or not members:
            raise ValueError('group members must be a nonempty list')
        cleaned = []
        for team in members:
            team = results.name(team)
            if team in seen:
                raise ValueError('duplicate team membership')
            seen.add(team)
            cleaned.append(team)
        output[group_name] = cleaned
    return output, seen


def select(rows, actions, groups, slots=1, wildcards=0):
    """Apply decisions, then select group qualifiers and wildcards."""
    groups, assigned = _groups(groups)
    slots = results.number(slots)
    wildcards = results.number(wildcards)

    originals = results.matches(rows)
    for row in originals:
        if row['home'] not in assigned or row['away'] not in assigned:
            raise ValueError('every match team must be assigned to a group')

    grouped_teams = sorted(assigned)
    reviewed = discipline.review(rows, actions, grouped_teams)
    active = reviewed['matches']
    deductions = reviewed['penalties']

    group_rows = {}
    for group_name in sorted(groups):
        members = groups[group_name]
        inside = set(members)
        group_matches = [row for row in active
                         if row['home'] in inside and row['away'] in inside]
        standings = scoring.table(group_matches, members)
        for row in standings:
            row['points'] -= deductions[row['team']]
        group_rows[group_name] = ordering.rank(standings)

    automatic = []
    for group_name in sorted(group_rows):
        automatic.extend(row['team'] for row in group_rows[group_name][:slots])

    automatic_set = set(automatic)
    remaining = [row for group_name in sorted(group_rows)
                 for row in group_rows[group_name]
                 if row['team'] not in automatic_set]
    wildcards_selected = [row['team'] for row in ordering.rank(remaining)[:wildcards]]

    return {
        'groups': group_rows,
        'automatic': automatic,
        'wildcards': wildcards_selected,
        'qualified': sorted(set(automatic) | set(wildcards_selected)),
    }
