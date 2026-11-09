"""Named aggregation over owned scalar tables."""
from rows import owned,indices,name

_OPERATIONS=('count_all','count','distinct','sum','min','max')

def _measure_specs(table,measures):
    if not isinstance(measures,list) or not measures: raise ValueError('invalid measures')
    aliases=[]
    specs=[]
    seen=set()
    columns=table['columns']
    for measure in measures:
        if not isinstance(measure,dict) or 'as' not in measure or 'op' not in measure:
            raise ValueError('invalid measure')
        alias=name(measure['as'])
        if alias in seen: raise ValueError('duplicate output alias')
        seen.add(alias)
        op=measure['op']
        if not isinstance(op,str) or op not in _OPERATIONS: raise ValueError('invalid aggregation operation')
        if op=='count_all':
            if 'column' in measure: raise ValueError('count_all must not have a column')
            index=None
        else:
            if 'column' not in measure: raise ValueError('missing aggregation column')
            column=measure['column']
            if not isinstance(column,str) or column not in columns: raise ValueError('invalid aggregation column')
            index=columns.index(column)
        aliases.append(alias)
        specs.append((op,index))
    return aliases,specs

def _present(rows,index):
    return [row[index] for row in rows if row[index] is not None]

def _measure(op,index,rows):
    if op=='count_all': return len(rows)
    values=_present(rows,index)
    if op=='count': return len(values)
    if op=='distinct': return len(set(values))
    if op=='sum':
        if any(type(value) is not int for value in values): raise ValueError('sum values must be integers')
        return sum(values)
    if not values: return None
    if len({type(value) for value in values})!=1: raise ValueError('min/max values must have one type')
    return min(values) if op=='min' else max(values)

def aggregate(table,keys,measures):
    table=owned(table)
    positions=indices(table,keys)
    key_columns=[table['columns'][i] for i in positions]
    aliases,specs=_measure_specs(table,measures)
    if set(aliases)&set(key_columns): raise ValueError('output alias collides with grouping key')
    groups={}
    for row in table['rows']:
        key=tuple(row[i] for i in positions)
        groups.setdefault(key,[]).append(row)
    result=[]
    for key,rows in groups.items():
        result.append(list(key)+[_measure(op,index,rows) for op,index in specs])
    return {'columns':key_columns+aliases,'rows':result}
