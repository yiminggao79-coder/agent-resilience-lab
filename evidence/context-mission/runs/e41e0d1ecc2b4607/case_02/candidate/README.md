# Text telemetry records

Pure standard-library text codec and record counting. The wire format has
three fields (tag, value, unit), separated by | and terminated by a physical
newline. Reserved characters are escaped, as the current task specifies.
All records remain strings at this layer. No files or service are involved.
Keep original tests and observe.py byte-identical; add separate tests when
useful. Public functions preserve caller input. Run `python3 -m unittest
discover -s tests`.
