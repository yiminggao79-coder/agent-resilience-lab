"""Conservative identities preserve evaluation-domain requirements."""
from evaluation import evaluate
from nodes import variables
def reduce(node):
    kind=node[0]
    if kind in ('number','variable'): return node
    if kind=='neg':
        child=reduce(node[1])
        if not variables(child):
            folded=-evaluate(child,{})
            if folded.denominator==1: return ('number',folded.numerator)
            return ('div',('number',folded.numerator),('number',folded.denominator))
        if child[0]=='neg': return child[1]
        return ('neg',child)
    if kind=='pow':
        base=reduce(node[1])
        result=('pow',base,node[2])
        if not variables(base):
            folded=evaluate(result,{})
            if folded.denominator==1: return ('number',folded.numerator)
            return ('div',('number',folded.numerator),('number',folded.denominator))
        return base if node[2]==1 else result
    left,right=reduce(node[1]),reduce(node[2])
    result=(kind,left,right)
    if not variables(result):
        folded=evaluate(result,{})
        if folded.denominator==1: return ('number',folded.numerator)
        return ('div',('number',folded.numerator),('number',folded.denominator))
    zero=('number',0)
    one=('number',1)
    if kind=='add':
        if left==zero: return right
        if right==zero: return left
    if kind=='sub' and right==zero: return left
    if kind=='mul':
        if left==one: return right
        if right==one: return left
    if kind=='div' and right==one: return left
    return result
