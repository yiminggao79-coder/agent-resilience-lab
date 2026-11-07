"""Deterministic standings order."""
def rank(rows):
    return sorted((dict(row) for row in rows),
                  key=lambda row: (-row['points'], -row['goals_for'], -row['difference'], row['team']))
