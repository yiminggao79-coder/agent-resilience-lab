Continue this project by adding an `Inventory(stock)` class exported by
`allocation.py`. `Inventory.allocate(orders)` has the same result schema and
allocation rules as the existing function, but remaining stock and ID replay
memory persist across calls on that object. Rejected IDs also stay rejected.
The existing `allocate(stock, orders)` function must keep all prior behavior
by using a fresh inventory for each call. Do not mutate constructor stock or
caller order inputs.

Each batch call is atomic if an ID conflict raises ValueError: no stock changes
or newly remembered IDs from any earlier order in that failed batch may remain.
Earlier successful batches stay committed. `snapshot()` returns a detached copy
of current stock. Each returned result's `remaining` must also be detached from
internal stock, so changing returned dictionaries cannot affect later calls.
Preserve all original test bytes, keep previous rules, add regression tests, run
the actual tests, and verify the final implementation.
