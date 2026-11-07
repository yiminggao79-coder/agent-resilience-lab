"""Partitioned windows restored to original row order."""
from rows import owned,indices,name,cell
from ordering import permutation
def compute(table,partition_by,order_by,calculations):
    table=owned(table)
    partitions=indices(table,partition_by)
    permutation(table,order_by)
    if not isinstance(calculations,list) or not calculations: raise ValueError('calculations must be nonempty')
    prepared=[]
    aliases=set(table['columns'])
    for calculation in calculations:
        if not isinstance(calculation,dict): raise ValueError('invalid calculation')
        alias=name(calculation.get('as'))
        operation=calculation.get('op')
        if alias in aliases or operation not in ('row_number','rank','dense_rank','running_sum','lag'): raise ValueError('invalid window operation')
        aliases.add(alias)
        if operation in ('running_sum','lag'):
            column=calculation.get('column')
            if column not in table['columns']: raise ValueError('unknown window column')
            position=table['columns'].index(column)
        else: position=None
        offset=calculation.get('offset',1)
        default=calculation.get('default')
        if operation=='lag' and (type(offset) is not int or offset<1): raise ValueError('invalid lag offset')
        cell(default)
        if operation=='running_sum' and any(row[position] is not None and type(row[position]) is not int for row in table['rows']): raise ValueError('running_sum requires integers')
        prepared.append((alias,operation,position,offset,default))
    grouped={}
    for index,row in enumerate(table['rows']):
        grouped.setdefault(tuple(row[i] for i in partitions),[]).append(index)
    extra=[[] for row in table['rows']]
    peer_positions=[table['columns'].index(key['column']) for key in order_by]
    for members in grouped.values():
        sub={'columns':table['columns'][:],'rows':[table['rows'][i][:] for i in members]}
        ordered=[members[i] for i in permutation(sub,order_by)]
        previous=None
        rank=dense=0
        totals={alias:0 for alias,operation,position,offset,default in prepared if operation=='running_sum'}
        for offset_in_partition,index in enumerate(ordered):
            row=table['rows'][index]
            peer=tuple(row[i] for i in peer_positions)
            if offset_in_partition==0 or peer!=previous:
                rank=offset_in_partition+1
                dense+=1
            previous=peer
            values=[]
            for alias,operation,position,offset,default in prepared:
                if operation=='row_number': result=offset_in_partition+1
                elif operation=='rank': result=rank
                elif operation=='dense_rank': result=dense
                elif operation=='running_sum':
                    totals[alias]+=0 if row[position] is None else row[position]
                    result=totals[alias]
                else: result=table['rows'][ordered[offset_in_partition-offset]][position] if offset_in_partition>=offset else default
                values.append(result)
            extra[index]=values
    return {'columns':table['columns']+[alias for alias,operation,position,offset,default in prepared],
            'rows':[row[:]+extra[i] for i,row in enumerate(table['rows'])]}
