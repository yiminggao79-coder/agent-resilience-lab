"""Public JSON-ready expression calculations."""
from parsing import parse
from evaluation import evaluate,pair
from rendering import render
from differentiation import derivative
from simplification import reduce
from nodes import variables
from workbook import workbook_report as _workbook_report
def calculate(text,values):
    tree=parse(text)
    return {'canonical':render(tree),'value':pair(evaluate(tree,values))}
def slope(text,values,variable):
    tree=parse(text)
    original=evaluate(tree,values)
    sensitivity=evaluate(derivative(tree,variable),values)
    return {'value':pair(original),'slope':pair(sensitivity)}
def reduced(text,values,variable):
    tree=parse(text)
    original=evaluate(tree,values)
    simple=reduce(tree)
    value=evaluate(simple,values)
    sensitivity=evaluate(derivative(simple,variable),values)
    return {'canonical':render(simple),'variables':sorted(variables(simple)),
            'value':pair(value),'slope':pair(sensitivity)}
def workbook_report(formulas,inputs,outputs,wrt):
    return _workbook_report(formulas,inputs,outputs,wrt)
