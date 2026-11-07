# Tick-score composition helpers

Synthetic musical note events use integer ticks, pitches 0..127 and velocity
1..127. Spelling maps C/D/E/F/G/A/B to 0/2/4/5/7/9/11 with C0=12. This is an
in-memory toolkit, not an audio engine or hardware protocol. Standard library
only, no devices/services/files. Preserve inputs and returned-container
isolation. Keep original tests/observe.py byte-identical; add separate tests.
Run python3 -m unittest discover -s tests.
