"""Integrated relational observations, with no SQL compatibility claim."""
from joins import left_join
from grouping import summarize
def combine(left,right,left_keys,right_keys,group_keys,value,prefix='r_'):
    joined=left_join(left,right,left_keys,right_keys,prefix)
    return {'joined':joined,'groups':summarize(joined,group_keys,value)}

def analyze(table,keys,measures,order=()):
    from analytics import aggregate
    from ordering import sort_table
    return sort_table(aggregate(table,keys,measures),[] if order==() else order)

def enrich(table,partition_by,order_by,calculations,output_order=()):
    from windows import compute
    from ordering import sort_table
    result=compute(table,partition_by,order_by,calculations)
    return {'table':result,'ordered':sort_table(result,[] if output_order==() else output_order)}
