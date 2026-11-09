"""Exact symbolic differentiation over immutable expression tuples."""
from nodes import identifier,number


def derivative(node,variable):
    name=identifier(variable)
    def diff(current):
        kind=current[0]
        if kind=='number': return number(0)
        if kind=='variable': return number(1 if current[1]==name else 0)
        if kind=='neg': return ('neg',diff(current[1]))
        if kind=='add': return ('add',diff(current[1]),diff(current[2]))
        if kind=='sub': return ('sub',diff(current[1]),diff(current[2]))
        if kind=='mul':
            left,right=current[1],current[2]
            return ('add',('mul',diff(left),right),('mul',left,diff(right)))
        if kind=='div':
            numerator,denominator=current[1],current[2]
            return ('div',
                    ('sub',('mul',diff(numerator),denominator),
                           ('mul',numerator,diff(denominator))),
                    ('pow',denominator,2))
        if kind=='pow':
            base,exponent=current[1],current[2]
            if exponent==0: return number(0)
            return ('mul',
                    ('mul',number(exponent),('pow',base,exponent-1)),
                    diff(base))
        raise ValueError('unknown expression node')
    return diff(node)
