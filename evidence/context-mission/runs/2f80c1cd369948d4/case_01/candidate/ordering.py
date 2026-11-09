"""Stable multi-key ordering over owned scalar tables."""
from functools import cmp_to_key
from rows import owned

def _sort_specs(table,keys):
    if not isinstance(keys,(list,tuple)): raise ValueError('invalid sort keys')
    columns=table['columns']
    specs=[]
    seen=set()
    for spec in keys:
        if not isinstance(spec,dict) or 'column' not in spec: raise ValueError('invalid sort key')
        column=spec['column']
        if not isinstance(column,str) or column not in columns: raise ValueError('invalid sort column')
        if column in seen: raise ValueError('duplicate sort column')
        seen.add(column)
        descending=spec.get('descending',False)
        if type(descending) is not bool: raise ValueError('invalid descending flag')
        nulls=spec.get('nulls','last')
        if nulls not in ('first','last'): raise ValueError('invalid null placement')
        index=columns.index(column)
        types={type(row[index]) for row in table['rows'] if row[index] is not None}
        if len(types)>1: raise ValueError('mixed sort column types')
        specs.append((index,descending,nulls))
    return specs

def _compare(left,right,specs):
    for index,descending,nulls in specs:
        a,b=left[index],right[index]
        if a is None or b is None:
            if a is None and b is None: continue
            if a is None: return -1 if nulls=='first' else 1
            return 1 if nulls=='first' else -1
        if a<b: result=-1
        elif a>b: result=1
        else: continue
        return -result if descending else result
    return 0

def permutation(table,order=()):
    table=owned(table)
    specs=_sort_specs(table,order)
    result=list(range(len(table['rows'])))
    if specs:
        result.sort(key=cmp_to_key(
            lambda left,right:_compare(table['rows'][left],table['rows'][right],specs)))
    return result

def sort_table(table,keys=()):
    table=owned(table)
    specs=_sort_specs(table,keys)
    if specs:
        table['rows'].sort(key=cmp_to_key(lambda left,right:_compare(left,right,specs)))
    return table
