"""Integrated relational observations, with no SQL compatibility claim."""
from joins import left_join
from grouping import summarize
from analytics import aggregate
from ordering import sort_table
from windows import compute
from query import execute
def combine(left,right,left_keys,right_keys,group_keys,value,prefix='r_'):
    joined=left_join(left,right,left_keys,right_keys,prefix)
    return {'joined':joined,'groups':summarize(joined,group_keys,value)}
def analyze(table,keys,measures,order=()):
    return sort_table(aggregate(table,keys,measures),order)
def enrich(table,partition_by,order_by,calculations,output_order=()):
    computed=compute(table,partition_by,order_by,calculations)
    return {'table':computed,'ordered':sort_table(computed,output_order)}
def pipeline(sources,steps,outputs):
    return execute(sources,steps,outputs)
