Add reports.py with build_report(stock, orders), preserving all existing allocation rules.
It returns exactly: remaining (stock after allocation), accepted_unique and rejected_unique
(count distinct order IDs by their first accepted/rejected decision), units_reserved (total
units actually consumed across stock keys), reserved_by_sku (each original stock key mapped
to units consumed; include zeros, inserted in sorted key order), and rejected_ids (distinct
rejected IDs in first appearance order). Equivalent replays are counted once and consume
nothing. Conflicting ID payloads raise ValueError; never mutate caller inputs, even on error.
Use the current allocation API rather than creating another independent allocation engine.
Keep every prior behavior and protected original test/observation file byte unchanged.
Add useful tests, run old and new tests, and verify the saved implementation.
