# Media transfer ranges

This tiny media service computes the inclusive byte spans to send for an asset.
Repair its range selection and preserve the public `select_ranges(header, size,
max_ranges)` function in `ranges.py`. All arguments and results are JSON values.
Use Python 3.9 and the standard library only.

`size` is a nonnegative integer and `max_ranges` is a positive integer. `header`
is either null or a string. Inputs obey those types. Do not mutate inputs.

The result always has exactly these keys:

* `status`: 200, 206, or 416.
* `ranges`: ascending nonoverlapping inclusive `[start, end]` pairs.
* `selected_bytes`: the sum of `end - start + 1` over those pairs.

The application's contract is:

1. A null header means the full asset: status 200 and `[[0, size-1]]`, or `[]`
   when size is zero. An invalid header has that same full-asset result.
2. Strip surrounding whitespace from the header. It must then begin with the
   exact case-sensitive prefix `bytes=`. The rest is a nonempty comma-separated
   list of members. Strip surrounding whitespace from each member. Each member
   has exactly one hyphen, at least one side present, and every present side
   consists only of ASCII decimal digits. Leading zeroes are allowed. Whitespace
   within a member, signs, empty members, other units, and any other characters
   make the entire header invalid. A reversed explicit range is syntactically
   valid but unsatisfiable, as described next.
3. `a-b` selects from a through min(b, size-1), `a-` selects from a through
   size-1, and `-n` selects the last min(n, size) bytes. Discard a member if the
   asset is empty, its explicit start is outside the asset, its explicit end
   precedes its start, or its suffix length is zero. Other unsatisfiable members
   do not invalidate satisfiable members.
4. Sort the remaining spans by start and merge every overlap or adjacency,
   including transitive merges. If no spans remain, return status 416 with empty
   ranges and zero selected bytes.
5. If the number of merged spans exceeds `max_ranges`, return the full-asset
   status-200 result. Otherwise return status 206 with the merged spans, even
   when they cover the entire asset. Apply the limit after merging.

The supplied files README.md and test_ranges.py are immutable byte-for-byte.
You may edit the implementation files and add files. Add any new tests in new
files; do not edit or replace the supplied tests or README. Run the public tests
with `python3 -m unittest discover -v` from this directory. No network, fork,
external dependencies, symlinks, or files outside this project are needed.
