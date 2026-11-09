"""Owned scalar tables with explicit column order."""
def name(value):
    if not isinstance(value,str) or not value or value.strip()!=value: raise ValueError('invalid name')
    return value
def cell(value):
    if value is not None and type(value) not in (int,str): raise ValueError('invalid scalar cell')
    return value
def owned(table):
    if not isinstance(table,dict): raise ValueError('table must be a dictionary')
    columns=table.get('columns')
    data=table.get('rows')
    if not isinstance(columns,list) or not columns or any(not isinstance(c,str) for c in columns): raise ValueError('invalid columns')
    columns=[name(c) for c in columns]
    if len(set(columns))!=len(columns) or not isinstance(data,list): raise ValueError('duplicate columns or invalid rows')
    result=[]
    for row in data:
        if not isinstance(row,list) or len(row)!=len(columns): raise ValueError('row width mismatch')
        result.append([cell(value) for value in row])
    return {'columns':columns,'rows':result}
def indices(table,columns,allow_empty=True):
    if not isinstance(columns,list) or (not columns and not allow_empty): raise ValueError('invalid key list')
    if any(not isinstance(c,str) or c not in table['columns'] for c in columns) or len(set(columns))!=len(columns): raise ValueError('invalid key column')
    return [table['columns'].index(c) for c in columns]
