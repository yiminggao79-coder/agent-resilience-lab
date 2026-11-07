"""Named, sequential relational pipelines with ultimate-source lineage."""
from rows import owned,name
from joins import left_join
from analytics import aggregate
from windows import compute
from ordering import sort_table
from projection import project,filter_rows
def execute(sources,steps,outputs):
    if not isinstance(sources,dict) or not isinstance(steps,list): raise ValueError('invalid pipeline containers')
    tables={name(key):owned(value) for key,value in sources.items()}
    ancestry={key:{key} for key in tables}
    lineage={}
    def source(key):
        if not isinstance(key,str) or key not in tables: raise ValueError('unknown table reference')
        return tables[key]
    for step in steps:
        if not isinstance(step,dict): raise ValueError('invalid step')
        destination=name(step.get('out'))
        if destination in tables: raise ValueError('table name already exists')
        operation=step.get('op')
        try:
            if operation=='join':
                left,right=step['left'],step['right']
                result=left_join(source(left),source(right),step['left_keys'],step['right_keys'],step.get('prefix','r_'))
                ancestors=ancestry[left]|ancestry[right]
            else:
                key=step['source']
                table=source(key)
                ancestors=ancestry[key].copy()
                if operation=='group': result=aggregate(table,step['keys'],step['measures'])
                elif operation=='window': result=compute(table,step['partition_by'],step['order_by'],step['calculations'])
                elif operation=='sort': result=sort_table(table,step['order'])
                elif operation=='project': result=project(table,step['columns'])
                elif operation=='filter': result=filter_rows(table,step['conditions'])
                else: raise ValueError('unknown pipeline operation')
        except KeyError: raise ValueError('required step field missing') from None
        tables[destination]=result
        ancestry[destination]=ancestors
        lineage[destination]=sorted(ancestors)
    if not isinstance(outputs,list) or any(not isinstance(key,str) or key not in tables for key in outputs) or len(set(outputs))!=len(outputs): raise ValueError('invalid requested outputs')
    return {'results':[{'name':key,'table':owned(tables[key])} for key in outputs],
            'lineage':dict(sorted(lineage.items()))}
