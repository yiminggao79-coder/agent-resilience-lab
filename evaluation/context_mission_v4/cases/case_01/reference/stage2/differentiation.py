"""Structural exact symbolic differentiation over immutable tuples."""
from nodes import number,identifier
def derivative(node,variable):
    identifier(variable)
    def diff(node):
        kind=node[0]
        if kind=='number': return number(0)
        if kind=='variable': return number(int(node[1]==variable))
        if kind=='neg': return ('neg',diff(node[1]))
        if kind in ('add','sub'): return (kind,diff(node[1]),diff(node[2]))
        if kind=='mul':
            left,right=node[1:]
            return ('add',('mul',diff(left),right),('mul',left,diff(right)))
        if kind=='div':
            left,right=node[1:]
            return ('div',('sub',('mul',diff(left),right),('mul',left,diff(right))),('pow',right,2))
        if kind=='pow':
            base,exponent=node[1:]
            if exponent==0: return number(0)
            return ('mul',('mul',number(exponent),('pow',base,exponent-1)),diff(base))
        raise ValueError('unknown expression node')
    return diff(node)
