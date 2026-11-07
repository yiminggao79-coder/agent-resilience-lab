"""Stable multi-key sorting with null placement independent of direction."""
from functools import cmp_to_key
from rows import owned
def _indices(table,order):
    table=owned(table)
    if not isinstance(order,list): raise ValueError('order must be a list')
    prepared=[]
    seen=set()
    for key in order:
        if not isinstance(key,dict): raise ValueError('invalid sort key')
        column=key.get('column')
        descending=key.get('descending',False)
        nulls=key.get('nulls','last')
        if column not in table['columns'] or column in seen or type(descending) is not bool or nulls not in ('first','last'): raise ValueError('invalid sort key')
        seen.add(column)
        position=table['columns'].index(column)
        if len({type(row[position]) for row in table['rows'] if row[position] is not None})>1: raise ValueError('mixed sort types')
        prepared.append((position,descending,nulls))
    def compare(left,right):
        for position,descending,nulls in prepared:
            a,b=table['rows'][left][position],table['rows'][right][position]
            if a is None and b is None: difference=0
            elif a is None: difference=-1 if nulls=='first' else 1
            elif b is None: difference=1 if nulls=='first' else -1
            else:
                difference=(a>b)-(a<b)
                if descending: difference=-difference
            if difference: return difference
        return 0
    return sorted(range(len(table['rows'])),key=cmp_to_key(compare))
def sort_table(table,order):
    table=owned(table)
    return {'columns':table['columns'][:],'rows':[table['rows'][index][:] for index in _indices(table,order)]}
