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
                own['points'] += 1
    # Difference is always recomputed from the final goals so it stays
    # consistent, including for teams with no matches (a zero row).
    for own in totals.values():
        own['difference'] = own['goals_for'] - own['goals_against']
    return [dict(totals[team]) for team in sorted(totals)]
