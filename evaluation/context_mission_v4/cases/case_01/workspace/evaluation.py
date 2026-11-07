"""Exact rational expression evaluation."""
from fractions import Fraction
from nodes import identifier
def value(raw):
    if type(raw) is int: return Fraction(raw)
    if isinstance(raw,list) and len(raw)==2 and all(type(v) is int for v in raw) and raw[1]!=0:
        return Fraction(raw[0],raw[1])
    raise ValueError('value must be integer or rational pair')
def environment(values):
    if not isinstance(values,dict): raise ValueError('values must be a dictionary')
    return {identifier(name):value(raw) for name,raw in values.items()}
def evaluate(node,values):
    env=environment(values)
    def run(node):
        kind=node[0]
        if kind=='number': return Fraction(node[1])
        if kind=='variable':
            if node[1] not in env: raise ValueError('unbound variable')
            return env[node[1]]
        if kind=='neg': return -run(node[1])
        if kind=='pow':
            base=run(node[1])
            if not base and node[2]<0: raise ValueError('zero negative power')
            return base**node[2]
        left,right=run(node[1]),run(node[2])
        if kind=='add': return left+right
        if kind=='sub': return left-right
        if kind=='mul': return left*right
        if kind=='div':
            if not right: raise ValueError('division by zero')
            # Current quotient discards fractional results, despite the exact API.
            return Fraction(left//right)
        raise ValueError('unknown expression node')
    return run(node)
def pair(value): return [value.numerator,value.denominator]
