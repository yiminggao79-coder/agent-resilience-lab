"""Stable left joins over scalar keys."""
from rows import owned,indices,name
def left_join(left,right,left_keys,right_keys,prefix='r_'):
    left,right=owned(left),owned(right)
    li,ri=indices(left,left_keys,False),indices(right,right_keys,False)
    if len(li)!=len(ri) or not isinstance(prefix,str): raise ValueError('invalid join keys or prefix')
    columns=left['columns']+[prefix+c for c in right['columns']]
    if any(not c or c.strip()!=c for c in columns) or len(set(columns))!=len(columns): raise ValueError('join column collision')
    output=[]
    for lrow in left['rows']:
        key=tuple(lrow[i] for i in li)
        matches=[] if any(value is None for value in key) else [rrow for rrow in right['rows'] if not any(rrow[i] is None for i in ri) and tuple(rrow[i] for i in ri)==key]
        if matches: output.extend(lrow+rrow for rrow in matches)
        else: output.append(lrow+[None]*len(right['columns']))
    return {'columns':columns,'rows':output}
