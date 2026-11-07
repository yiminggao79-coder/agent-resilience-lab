"""Owned projection and conjunctive scalar predicates."""
from rows import owned,indices,cell
def project(table,columns):
    table=owned(table)
    positions=indices(table,columns,False)
    return {'columns':columns[:],'rows':[[row[i] for i in positions] for row in table['rows']]}
def filter_rows(table,conditions):
    table=owned(table)
    if not isinstance(conditions,list): raise ValueError('conditions must be a list')
    prepared=[]
    for condition in conditions:
        if not isinstance(condition,dict): raise ValueError('invalid condition')
        column=condition.get('column')
        operation=condition.get('op')
        if column not in table['columns'] or operation not in ('eq','ne','lt','le','gt','ge','is_null','not_null'): raise ValueError('invalid predicate')
        if operation in ('is_null','not_null'):
            if 'value' in condition: raise ValueError('null predicate has no value')
            value=None
        else:
            if 'value' not in condition: raise ValueError('predicate value missing')
            value=cell(condition['value'])
            if operation in ('lt','le','gt','ge') and value is None: raise ValueError('ordered comparison needs a value')
        prepared.append((table['columns'].index(column),operation,value))
    result=[]
    for row in table['rows']:
        checks=[]
        for position,operation,value in prepared:
            actual=row[position]
            if operation=='is_null': answer=actual is None
            elif operation=='not_null': answer=actual is not None
            elif operation=='eq': answer=actual==value
            elif operation=='ne': answer=actual!=value
            elif actual is None: answer=False
            else:
                if type(actual) is not type(value): raise ValueError('incompatible comparison types')
                answer={'lt':lambda:actual<value,'le':lambda:actual<=value,'gt':lambda:actual>value,'ge':lambda:actual>=value}[operation]()
            checks.append(answer)
        if all(checks): result.append(row[:])
    return {'columns':table['columns'][:],'rows':result}
