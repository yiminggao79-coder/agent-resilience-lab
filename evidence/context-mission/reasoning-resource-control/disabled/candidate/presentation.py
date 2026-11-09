"""Small public season observation."""
from results import matches
from scoring import table
from ordering import rank

def season(rows, teams=()):
    canonical = matches(rows)
    standings = rank(table(canonical, teams))
    return {'standings': standings, 'summary': {'games': len(canonical), 'teams': len(standings),
            'goals': sum(row['home_goals'] + row['away_goals'] for row in canonical)}}
