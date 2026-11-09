# Small scalar-table toolkit

Tables are {columns: [unique names], rows: [scalar lists]}. A scalar is None,
an integer (not bool), or text. There is no SQL parser or external database.
APIs return owned tables and preserve caller objects. All code is standard
library only. Keep the original tests and observe.py byte-identical; add
separate discoverable tests. Run python3 -m unittest discover -s tests.
