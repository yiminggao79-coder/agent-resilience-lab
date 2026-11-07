"""Legacy integer group summary, in first-seen group order."""
from rows import owned,indices
def summarize(table,keys,value):
    table=owned(table)
    positions=indices(table,keys)
    if value not in table['columns'] or set(keys)&{'count','present','total'}: raise ValueError('invalid summary columns')
    vi=table['columns'].index(value)
    groups={}
    for row in table['rows']:
        key=tuple(row[i] for i in positions)
        groups.setdefault(key,[]).append(row[vi])
    result=[]
    for key,values in groups.items():
        if any(v is not None and type(v) is not int for v in values): raise ValueError('summary values must be integers')
        result.append(list(key)+[len(values),len(values),sum(v for v in values if v is not None)])
    return {'columns':keys[:]+['count','present','total'],'rows':result}
