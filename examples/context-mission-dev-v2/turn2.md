Continue the same project with idempotent order IDs. Keep all previous behavior.
Within a single allocate call, the same ID with the same combined SKU quantities
replays the original accepted/rejected result without consuming stock again.
Line order and splitting the same SKU over multiple lines do not change identity.
A previously rejected ID stays rejected even if a replay occurs later. Reusing
an ID with different combined quantities raises ValueError. Caller inputs must
remain unchanged even on this error. Empty orders follow the same identity rule.
Add regression tests and run both old and new tests; do not change original tests.
