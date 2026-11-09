# Small exact expression engine

Stdlib-only expression tuples, rational calculations and canonical rendering.
Numbers are integer literals; environment values are integers or [numerator,
nonzero denominator] pairs. The supported operators are + - * / unary signs,
parentheses and a single signed integer literal power suffix per atom, with
exponent magnitude at most 12. Fully parenthesized output is intentional.
No service or file storage is used. Preserve caller inputs and the original
tests/observe.py bytes; separate added tests are welcome. Run python3 -m
unittest discover -s tests.
