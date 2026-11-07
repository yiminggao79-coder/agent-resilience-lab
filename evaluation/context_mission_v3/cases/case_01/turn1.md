Our community league table gives no points for draws and sorts some tied teams incorrectly. Fix the existing scoring/order modules, preserving the protected original tests and observe.py.

A win earns 3 points, a draw earns 1 per team, and a loss earns 0. Keep played/won/drawn/lost, goals_for/goals_against and difference consistent for every team; difference is goals_for minus goals_against. Rank by descending points, descending difference, descending goals_for, then ascending team name. A registered team with no matches must still have a zero row. Keep the season summary's game count, team count and total goals correct.

Preserve the existing validation: distinct nonempty trimmed home/away names, nonnegative integer goals with booleans invalid, unique match IDs and unique registered-team names. Do not mutate any caller list or row, including through sorting. You may add separate tests. Finish with a short account of the changes and checks you ran.
