"""Exact integer-tick scaling, without rounding."""
from fractions import Fraction
from score import integer
def ratio(value):
    if value is None: return Fraction(1)
    if not isinstance(value,list) or len(value)!=2: raise ValueError('stretch must be a positive pair')
    return Fraction(integer(value[0],1),integer(value[1],1))
def scale(value,stretch):
    result=integer(value)*ratio(stretch)
    if result.denominator!=1: raise ValueError('nonintegral transformed tick')
    return result.numerator
