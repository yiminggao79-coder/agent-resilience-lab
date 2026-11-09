"""Named-formula workbook evaluation over immutable expression ASTs."""
from fractions import Fraction
from nodes import identifier,variables
from parsing import parse
from evaluation import environment,evaluate,pair
from differentiation import derivative
from simplification import reduce
from rendering import render


def _prepare(formulas,inputs,outputs,wrt):
    if not isinstance(formulas,dict):
        raise ValueError('formulas must be a dictionary')
    env=environment(inputs)
    input_names=set(env)

    trees={}
    for name,text in formulas.items():
        formula_name=identifier(name)
        if formula_name in input_names:
            raise ValueError('input and formula names overlap')
        trees[formula_name]=parse(text)
    formula_names=set(trees)
    known_names=input_names|formula_names

    references={}
    dependencies={}
    for name,tree in trees.items():
        refs=variables(tree)
        if not refs<=known_names:
            raise ValueError('unknown variable')
        references[name]=refs
        dependencies[name]=refs-input_names

    remaining=set(formula_names)
    order=[]
    while remaining:
        ready=sorted(name for name in remaining
                     if not dependencies[name]&remaining)
        if not ready:
            raise ValueError('dependency cycle')
        chosen=ready[0]
        order.append(chosen)
        remaining.remove(chosen)

    if not isinstance(outputs,list):
        raise ValueError('outputs must be a list')
    requested=[]
    seen=set()
    for raw_name in outputs:
        name=identifier(raw_name)
        if name not in known_names or name in seen:
            raise ValueError('outputs must be distinct known names')
        seen.add(name)
        requested.append(name)

    derivative_name=identifier(wrt)
    if derivative_name not in input_names:
        raise ValueError('wrt must name an existing input')

    return env,trees,references,order,requested,derivative_name


def _calculate(formulas,inputs,outputs,wrt):
    (env,trees,references,order,requested,
     derivative_name)=_prepare(formulas,inputs,outputs,wrt)

    values={name:pair(amount) for name,amount in env.items()}
    sensitivities={
        name:Fraction(1 if name==derivative_name else 0)
        for name in env
    }
    formula_values={}

    for name in order:
        tree=trees[name]
        value=evaluate(tree,values)
        total=Fraction(0)
        for reference in sorted(references[name]):
            partial=evaluate(derivative(tree,reference),values)
            total+=partial*sensitivities[reference]
        formula_values[name]=value
        sensitivities[name]=total
        values[name]=pair(value)

    results=[]
    for name in requested:
        if name in env:
            amount=env[name]
        else:
            amount=formula_values[name]
        results.append({
            'name':name,
            'value':pair(amount),
            'derivative':pair(sensitivities[name]),
        })
    return order,results,trees


def run(formulas,inputs,outputs,wrt):
    order,results,_=_calculate(formulas,inputs,outputs,wrt)
    return {'order':order,'results':results}


def workbook_report(formulas,inputs,outputs,wrt):
    order,results,trees=_calculate(formulas,inputs,outputs,wrt)
    expressions={
        name:render(reduce(trees[name]))
        for name in sorted(trees)
    }
    return {'order':order,'results':results,'expressions':expressions}
