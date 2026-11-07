League officials need to apply score decisions without changing the original records. Add discipline.py with review(rows, actions, teams=()), preserving all Turn 1 behavior and the byte-identical original tests/observe.py.

Start from canonical copies of the original matches. Participants are all originally registered teams plus both teams of every ORIGINAL match, and remain participants even after all their matches are voided. Process actions in order:
- {"kind":"void", "match_id":id} removes that currently active match.
- {"kind":"replace", "match_id":id, "home_goals":n, "away_goals":n} replaces its scores, retaining ID, home, away and position.
- {"kind":"penalty", "team":name, "points":n} adds a nonnegative integer points deduction for that participant.

Use existing trimmed-name and integer rules, with booleans invalid. Unknown action kinds, missing required fields, unknown ACTIVE match IDs, invalid scores/deductions, and unknown penalty teams raise ValueError. A voided match cannot later be replaced or voided again. Deductions accumulate and can make standings points negative; other statistics remain based on active matches. All validation failures must leave caller input untouched.

Return {"matches": active canonical matches in original relative order, "standings": ranked rows after deductions, "penalties": sorted map of EVERY participant to total deduction, including zero}. Add tests for sequential replacement/voiding, repeated penalties, unplayed participants and error preservation.
