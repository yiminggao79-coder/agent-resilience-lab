"""Integrated relational observations, with no SQL compatibility claim."""
from joins import left_join
from grouping import summarize
def combine(left,right,left_keys,right_keys,group_keys,value,prefix='r_'):
    joined=left_join(left,right,left_keys,right_keys,prefix)
    return {'joined':joined,'groups':summarize(joined,group_keys,value)}
