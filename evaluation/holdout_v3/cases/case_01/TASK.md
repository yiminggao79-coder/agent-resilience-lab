Fix the occupancy report implementation. The API report.summarize(records, start, stop) must report each room's busy_minutes and peak concurrent reservations within the query window.

Both reservations and query windows are half-open integer-minute intervals [start, end): an interval ending exactly when another starts does not overlap it. Ignore cancelled reservations and zero-length intervals. Clip every reservation to the query window; omit rooms with no positive overlap. busy_minutes is the duration of the union of the clipped reservations, counting any minute only once. peak is the largest number of concurrent clipped reservations, including duplicate reservations as separate reservations. Return a list of {"room": string, "busy_minutes": integer, "peak": integer} sorted by room name ascending.

Inputs are valid: room names are strings, times are integers, end >= start, and stop >= start. cancelled is an optional boolean, false by default. Leave the caller's records and their dictionaries unchanged. Preserve the public API and original tests byte-for-byte. You may add new tests and change implementation files. Use only the Python standard library.
Keep acceptance_probe.py byte-for-byte unchanged; it is a supplied test observation adapter, not implementation code.

