"""Immutable expression tuples and names."""
import re
def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value):
        raise ValueError('invalid identifier')
    return value
def number(value): return ('number',value)
def variable(value): return ('variable',identifier(value))
def variables(node):
    kind=node[0]
    if kind=='number': return set()
    if kind=='variable': return {node[1]}
    if kind in ('neg','pow'): return variables(node[1])
    return variables(node[1])|variables(node[2])
