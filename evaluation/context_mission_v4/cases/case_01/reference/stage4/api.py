"""Public JSON-ready expression calculations."""
from parsing import parse
from evaluation import evaluate,pair
from rendering import render
def calculate(text,values):
    tree=parse(text)
    return {'canonical':render(tree),'value':pair(evaluate(tree,values))}

from differentiation import derivative
def slope(text,values,variable):
    tree=parse(text)
    original=evaluate(tree,values)
    return {'value':pair(original),'slope':pair(evaluate(derivative(tree,variable),values))}

from simplification import reduce
from nodes import variables
def reduced(text,values,variable):
    original=parse(text)
    evaluate(original,values)
    tree=reduce(original)
    return {'canonical':render(tree),'variables':sorted(variables(tree)),
            'value':pair(evaluate(tree,values)),'slope':pair(evaluate(derivative(tree,variable),values))}

def workbook_report(formulas,inputs,outputs,wrt):
    from workbook import run
    report=run(formulas,inputs,outputs,wrt)
    report['expressions']={name:render(reduce(parse(formulas[name]))) for name in sorted(formulas)}
    return report
