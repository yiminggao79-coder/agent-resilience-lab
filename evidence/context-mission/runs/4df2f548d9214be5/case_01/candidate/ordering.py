"""Deterministic standings order."""
def rank(rows):
    return sorted((dict(row) for row in rows),
                  key=lambda row: (-row['points'], -row['difference'],
                                   -row['goals_for'], row['team']))
