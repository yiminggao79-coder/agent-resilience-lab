"""Partitioned window calculations over owned scalar tables."""
from rows import cell, indices, name, owned
from ordering import permutation

_OPS=('row_number','rank','dense_rank','running_sum','lag')

def _calculation_specs(table,calculations):
    if not isinstance(calculations,list) or not calculations:
        raise ValueError('calculations must be a nonempty list')
    aliases=[]
    specs=[]
    existing=set(table['columns'])
    for calculation in calculations:
        if not isinstance(calculation,dict) or 'as' not in calculation or 'op' not in calculation:
            raise ValueError('invalid calculation')
        alias=name(calculation['as'])
        if alias in existing or alias in aliases:
            raise ValueError('calculation alias collision')
        op=calculation['op']
        if not isinstance(op,str) or op not in _OPS:
            raise ValueError('invalid window operation')
        column_index=None
        if op in ('running_sum','lag'):
            column=calculation.get('column')
            if not isinstance(column,str) or column not in table['columns']:
                raise ValueError('invalid calculation column')
            column_index=table['columns'].index(column)
        elif 'column' in calculation:
            raise ValueError('row operations must not have a column')
        offset=None
        default=None
        if op=='lag':
            offset=calculation.get('offset',1)
            if type(offset) is not int or offset<=0:
                raise ValueError('lag offset must be a positive integer')
            default=cell(calculation.get('default',None))
        aliases.append(alias)
        specs.append((op,column_index,offset,default))
    return aliases,specs

def compute(table,partition_by,order_by,calculations):
    table=owned(table)
    partition_indices=indices(table,partition_by)
    aliases,specs=_calculation_specs(table,calculations)
    ordered=permutation(table,order_by)
    order_indices=[table['columns'].index(spec['column']) for spec in order_by]

    groups={}
    for index in ordered:
        key=tuple(table['rows'][index][column] for column in partition_indices)
        groups.setdefault(key,[]).append(index)

    values=[[None]*len(table['rows']) for _ in specs]
    for output,(op,column_index,offset,default) in zip(values,specs):
        for group in groups.values():
            if op=='row_number':
                for position,index in enumerate(group,1):
                    output[index]=position
            elif op in ('rank','dense_rank'):
                previous=None
                rank=0
                dense=0
                for position,index in enumerate(group,1):
                    key=tuple(table['rows'][index][column] for column in order_indices)
                    if position==1 or key!=previous:
                        rank=position
                        dense+=1
                        previous=key
                    output[index]=rank if op=='rank' else dense
            elif op=='running_sum':
                total=0
                for index in group:
                    value=table['rows'][index][column_index]
                    if value is not None:
                        if type(value) is not int:
                            raise ValueError('running_sum requires integer values')
                        total+=value
                    output[index]=total
            else:
                for position,index in enumerate(group):
                    if position<offset:
                        output[index]=default
                    else:
                        output[index]=table['rows'][group[position-offset]][column_index]

    result_rows=[]
    for index,row in enumerate(table['rows']):
        result_rows.append(row+[output[index] for output in values])
    return {'columns':table['columns']+aliases,'rows':result_rows}
