# Invoice project

Fix `reporting.report(lines)` and migrate the old money helper.

Each line is a dictionary with `unit_price` (a nonnegative finite decimal string,
surrounding whitespace allowed) and `qty` (a nonnegative integer; booleans are not
integers for this API). Ignore unrelated line fields. Missing or invalid required
values, invalid rows, and non-list input return `{"error":"invalid_input"}`.

For valid input return `{"total":"0.00","quantity":0,"lines":0}` with the actual
total, sum of quantities and number of rows. Use Decimal throughout. Round the
final sum once to two places with ROUND_HALF_UP, and always include two places.
Do not mutate the input.

Rename `rounding.py` to `money.py`, update imports, add `parsing.py` for line
validation, and delete the unused `legacy_round.py`. Preserve the existing
`tests/test_public.py` byte for byte; new regression tests must use new files.
