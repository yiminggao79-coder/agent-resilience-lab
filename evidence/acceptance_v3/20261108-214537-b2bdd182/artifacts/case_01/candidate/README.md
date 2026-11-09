# Occupancy report
This stdlib-only Python library reports room occupancy over integer-minute windows.
Public API: report.summarize(records, start, stop).
Each record has a room string and integer start/end; an optional cancelled boolean defaults to false.
All inputs are valid, end >= start, and query stop >= start.
Run the existing unittest suite from tests.

