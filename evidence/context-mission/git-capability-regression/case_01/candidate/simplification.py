"""Conservative exact simplification over immutable expression tuples."""
from fractions import Fraction
from nodes import number


def _constant(node):
    """Return the exact value of a reduced numeric node, or None."""
    kind=node[0]
    if kind=='number':
        return Fraction(node[1])
    if kind=='div' and node[1][0]=='number' and node[2][0]=='number':
        if node[2][1]==0: raise ValueError('division by zero')
        return Fraction(node[1][1],node[2][1])
    return None


def _literal(value):
    """Encode an exact rational as the required reduced constant node."""
    value=Fraction(value)
    if value.denominator==1: return number(value.numerator)
    return ('div',number(value.numerator),number(value.denominator))


def reduce(node):
    def visit(current):
        kind=current[0]
        if kind=='number':
            return number(current[1])
        if kind=='variable':
            return ('variable',current[1])
        if kind=='neg':
            child=visit(current[1])
            if child[0]=='neg': return child[1]
            constant=_constant(child)
            if constant is not None: return _literal(-constant)
            return ('neg',child)
        if kind=='pow':
            base=visit(current[1])
            exponent=current[2]
            constant=_constant(base)
            if constant is not None:
                if not constant and exponent<0:
                    raise ValueError('zero negative power')
                return _literal(constant**exponent)
            if exponent==1: return base
            return ('pow',base,exponent)
        if kind not in ('add','sub','mul','div'):
            raise ValueError('unknown expression node')

        left,right=visit(current[1]),visit(current[2])
        left_constant,right_constant=_constant(left),_constant(right)
        if left_constant is not None and right_constant is not None:
            if kind=='add': result=left_constant+right_constant
            elif kind=='sub': result=left_constant-right_constant
            elif kind=='mul': result=left_constant*right_constant
            else:
                if not right_constant: raise ValueError('division by zero')
                result=left_constant/right_constant
            return _literal(result)

        if kind=='add':
            if left_constant is not None and left_constant==0: return right
            if right_constant is not None and right_constant==0: return left
        elif kind=='sub':
            if right_constant is not None and right_constant==0: return left
        elif kind=='mul':
            if left_constant is not None and left_constant==1: return right
            if right_constant is not None and right_constant==1: return left
        elif kind=='div':
            if right_constant is not None and right_constant==1: return left
        return (kind,left,right)

    return visit(node)
