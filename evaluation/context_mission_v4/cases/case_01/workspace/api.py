"""Public JSON-ready expression calculations."""
from parsing import parse
from evaluation import evaluate,pair
from rendering import render
def calculate(text,values):
    tree=parse(text)
    return {'canonical':render(tree),'value':pair(evaluate(tree,values))}
