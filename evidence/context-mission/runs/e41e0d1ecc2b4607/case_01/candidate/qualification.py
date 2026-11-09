"""Group qualification with automatic slots and wildcards."""
from collections.abc import Mapping

import results
import scoring
import ordering
import discipline


def _groups(groups):
    if not isinstance(groups, Mapping) or not groups:
        raise ValueError('groups must be a nonempty map')
    mapping = {}
    seen = set()
    for key, teams in groups.items():
        group = results.name(key)
        if group in mapping:
            raise ValueError('duplicate group name')
        if not isinstance(teams, list) or not teams:
            raise ValueError('group must be a nonempty list of teams')
        listed = []
        for team in teams:
            team = results.name(team)
            if team in seen:
                raise ValueError('duplicate team membership')
            seen.add(team)
            listed.append(team)
        mapping[group] = listed
    return mapping


def select(rows, actions, groups, slots=1, wildcards=0):
    originals = results.matches(rows)
    mapping = _groups(groups)
    slots = results.number(slots, minimum=0)
    wildcards = results.number(wildcards, minimum=0)

    members = [team for teams in mapping.values() for team in teams]
    needed = set(members)
    for row in originals:
        for team in (row['home'], row['away']):
            if team not in needed:
                raise ValueError('original match team is unassigned')

    review = discipline.review(originals, actions, members)
    active = review['matches']
    deductions = review['penalties']

    standings = {}
    for group, teams in mapping.items():
        inside = set(teams)
        local = [row for row in active
                 if row['home'] in inside and row['away'] in inside]
        rows_out = scoring.table(local, teams)
        for row in rows_out:
            row['points'] -= deductions[row['team']]
        standings[group] = ordering.rank(rows_out)

    automatic, remaining = [], set(members)
    for group in sorted(standings):
        for row in standings[group][:slots]:
            automatic.append(row['team'])
            remaining.discard(row['team'])

    local = [row for row in active
             if row['home'] in remaining and row['away'] in remaining]
    pool = scoring.table(local, sorted(remaining))
    for row in pool:
        row['points'] -= deductions[row['team']]
    chosen = ordering.rank(pool)[:wildcards]
    wild = [row['team'] for row in chosen]
    for row in chosen:
        remaining.discard(row['team'])

    return {
        'groups': standings,
        'automatic': automatic,
        'wildcards': wild,
        'qualified': sorted(set(automatic) | set(wild)),
    }
