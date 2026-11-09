"""Stable left joins over scalar keys."""
from rows import owned,indices,name
def left_join(left,right,left_keys,right_keys,prefix='r_'):
    left,right=owned(left),owned(right)
    li,ri=indices(left,left_keys,False),indices(right,right_keys,False)
    if len(li)!=len(ri) or not isinstance(prefix,str): raise ValueError('invalid join keys or prefix')
    columns=left['columns']+[prefix+c for c in right['columns']]
    if any(not c or c.strip()!=c for c in columns) or len(set(columns))!=len(columns): raise ValueError('join column collision')
    right_rows=right['rows']
    right_keys=[tuple(row[i] for i in ri) for row in right_rows]
    output=[]
    for lrow in left['rows']:
        key=tuple(lrow[i] for i in li)
        if any(value is None for value in key):
            matches=[]
        else:
            matches=[rrow for rkey,rrow in zip(right_keys,right_rows)
                     if not any(value is None for value in rkey) and rkey==key]
        if matches:
            output.extend(lrow+match for match in matches)
        else:
            output.append(lrow+[None]*len(right['columns']))
    return {'columns':columns,'rows':output}
