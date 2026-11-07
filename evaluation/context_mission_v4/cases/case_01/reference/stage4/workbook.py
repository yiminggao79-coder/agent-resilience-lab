"""Formula dependency evaluation and exact chain-rule sensitivities."""
from fractions import Fraction
from nodes import identifier,variables
from parsing import parse
from evaluation import environment,evaluate,pair
from differentiation import derivative
def run(formulas,inputs,outputs,wrt):
    if not isinstance(formulas,dict): raise ValueError('formulas must be a dictionary')
    base=environment(inputs)
    identifier(wrt)
    if wrt not in base: raise ValueError('wrt must name an input')
    trees={identifier(name):parse(text) for name,text in formulas.items()}
    if set(trees)&set(base): raise ValueError('formula shadows an input')
    names=set(trees)|set(base)
    if not isinstance(outputs,list) or any(not isinstance(v,str) or v not in names for v in outputs) or len(set(outputs))!=len(outputs):
        raise ValueError('outputs must be unique known names')
    dependencies={name:variables(tree) for name,tree in trees.items()}
    if any(not deps<=names for deps in dependencies.values()): raise ValueError('unknown formula variable')
    order=[]
    pending=set(trees)
    while pending:
        ready=sorted(name for name in pending if not (dependencies[name]&pending))
        if not ready: raise ValueError('formula cycle')
        chosen=ready[0]
        order.append(chosen)
        pending.remove(chosen)
    values=dict(base)
    sensitivities={name:Fraction(int(name==wrt)) for name in base}
    for name in order:
        env={key:pair(value) for key,value in values.items()}
        tree=trees[name]
        values[name]=evaluate(tree,env)
        sensitivities[name]=sum((evaluate(derivative(tree,dep),env)*sensitivities[dep]
                                 for dep in sorted(dependencies[name])),Fraction(0))
    return {'order':order,'results':[{'name':name,'value':pair(values[name]),
                 'derivative':pair(sensitivities[name])} for name in outputs]}
