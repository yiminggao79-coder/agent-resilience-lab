"""Named aggregates with first-seen grouping and explicit null behavior."""
from rows import owned,indices,name
def aggregate(table,keys,measures):
    table=owned(table)
    positions=indices(table,keys)
    if not isinstance(measures,list) or not measures: raise ValueError('measures must be nonempty')
    prepared=[]
    aliases=set(keys)
    for measure in measures:
        if not isinstance(measure,dict): raise ValueError('invalid measure')
        alias=name(measure.get('as'))
        operation=measure.get('op')
        if alias in aliases or operation not in ('count_all','count','distinct','sum','min','max'): raise ValueError('invalid aggregate')
        aliases.add(alias)
        if operation=='count_all':
            if 'column' in measure: raise ValueError('count_all has no column')
            position=None
        else:
            column=measure.get('column')
            if column not in table['columns']: raise ValueError('unknown measure column')
            position=table['columns'].index(column)
        prepared.append((alias,operation,position))
    groups={}
    for row in table['rows']:
        key=tuple(row[i] for i in positions)
        groups.setdefault(key,[]).append(row)
    output=[]
    for key,records in groups.items():
        values=[]
        for alias,operation,position in prepared:
            present=[row[position] for row in records if row[position] is not None] if position is not None else []
            if operation=='count_all': result=len(records)
            elif operation=='count': result=len(present)
            elif operation=='distinct': result=len(set(present))
            elif operation=='sum':
                if any(type(value) is not int for value in present): raise ValueError('sum requires integers')
                result=sum(present)
            else:
                if len({type(value) for value in present})>1: raise ValueError('mixed comparison types')
                result=(min(present) if operation=='min' else max(present)) if present else None
            values.append(result)
        output.append(list(key)+values)
    return {'columns':keys[:]+[alias for alias,operation,position in prepared],'rows':output}
