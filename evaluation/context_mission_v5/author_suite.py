"""New synthetic acceptance v5 author source, never a model payload."""
import json
from pathlib import Path
import textwrap
ROOT=Path(__file__).resolve().parent

def put(name,text):
    path=ROOT/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip('\n'))
def install(case,where,mapping):
    for name,text in mapping.items(): put(f'cases/{case}/{where}/{name}',text)

OBSERVE='''
    import copy
    def _mutable_ids(value,seen=None):
        if seen is None: seen=set()
        if not isinstance(value,(dict,list)): return set()
        identity=id(value)
        if identity in seen: return set()
        seen.add(identity)
        result={identity}
        children=value.values() if isinstance(value,dict) else value
        for child in children: result.update(_mutable_ids(child,seen))
        return result
    def _observe(payload,action):
        before=copy.deepcopy(payload)
        try: result=action()
        except (ValueError,KeyError,ImportError,AttributeError) as exc: result={'error':type(exc).__name__}
        return {'result':result,'preserved':payload==before,
                'detached':not bool(_mutable_ids(result)&_mutable_ids(payload))}
'''

A={
 'rows.py': '''
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
 ''',
 'joins.py': '''
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
            matches=[rrow for rrow in right['rows'] if tuple(rrow[i] for i in ri)==key]
            # Current code joins null keys and drops duplicate matches.
            if matches: output.append(lrow+matches[0])
            else: output.append(lrow+[None]*len(right['columns']))
        return {'columns':columns,'rows':output}
 ''',
 'grouping.py': '''
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
 ''',
 'api.py': '''
    """Integrated relational observations, with no SQL compatibility claim."""
    from joins import left_join
    from grouping import summarize
    def combine(left,right,left_keys,right_keys,group_keys,value,prefix='r_'):
        joined=left_join(left,right,left_keys,right_keys,prefix)
        return {'joined':joined,'groups':summarize(joined,group_keys,value)}
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from rows import owned
    from joins import left_join
    from grouping import summarize
    def T(columns,rows): return {'columns':columns,'rows':rows}
    class OriginalContracts(unittest.TestCase):
        def test_duplicate_join_matches(self):
            got=left_join(T(['k'],[[1]]),T(['k','v'],[[1,2],[1,3]]),['k'],['k'])
            self.assertEqual(got['rows'],[[1,1,2],[1,1,3]])
        def test_null_keys_do_not_join(self):
            got=left_join(T(['k'],[[None]]),T(['k','v'],[[None,9]]),['k'],['k'])
            self.assertEqual(got['rows'],[[None,None,None]])
        def test_left_padding(self):
            self.assertEqual(left_join(T(['k'],[[2]]),T(['k'],[[1]]),['k'],['k'])['rows'],[[2,None]])
        def test_present_count(self):
            got=summarize(T(['g','v'],[['a',None],['a',4]]),['g'],'v')
            self.assertEqual(got['rows'],[['a',2,1,4]])
        def test_null_group_key(self):
            self.assertEqual(summarize(T(['g','v'],[[None,2],[None,3]]),['g'],'v')['rows'],[[None,2,2,5]])
        def test_first_seen_groups(self):
            self.assertEqual(summarize(T(['g','v'],[['z',1],['a',2]]),['g'],'v')['rows'],[['z',1,1,1],['a',1,1,2]])
        def test_empty_table(self):
            self.assertEqual(summarize(T(['v'],[]),[],'v')['rows'],[])
        def test_owned_rows(self):
            data=T(['x'],[[1]])
            before=copy.deepcopy(data)
            owned(data)['rows'][0][0]=7
            self.assertEqual(data,before)
        def test_join_no_alias(self):
            left=T(['k'],[[1]])
            result=left_join(left,T(['k'],[]),['k'],['k'])
            result['rows'][0][0]=99
            self.assertEqual(left['rows'],[[1]])
        def test_duplicate_columns(self):
            with self.assertRaises(ValueError): owned(T(['x','x'],[[1,2]]))
        def test_invalid_cells(self):
            for value in [True,1.5,[]]:
                with self.assertRaises(ValueError): owned(T(['x'],[[value]]))
        def test_join_collision(self):
            with self.assertRaises(ValueError): left_join(T(['k'],[]),T(['k'],[]),['k'],['k'],'')
    if __name__=='__main__': unittest.main()
 ''',
 'observe.py':OBSERVE+'''
    def first(payload):
        def action():
            from api import combine
            from joins import left_join
            from grouping import summarize
            args=[payload[key] for key in ('left','right','left_keys','right_keys','group_keys','value')]
            prefix=payload.get('prefix','r_')
            report=combine(*args,prefix)
            direct=left_join(payload['left'],payload['right'],payload['left_keys'],payload['right_keys'],prefix)
            consistent=report['joined']==direct and report['groups']==summarize(direct,payload['group_keys'],payload['value'])
            return {'report':report,'direct_consistent':consistent}
        return _observe(payload,action)
    def second(payload):
        def action():
            from api import analyze
            from analytics import aggregate
            from ordering import sort_table
            report=analyze(payload['table'],payload['keys'],payload['measures'],payload.get('order',[]))
            direct=sort_table(aggregate(payload['table'],payload['keys'],payload['measures']),payload.get('order',[]))
            return {'report':report,'direct_consistent':report==direct}
        return _observe(payload,action)
    def third(payload):
        def action():
            from api import enrich
            from windows import compute
            report=enrich(payload['table'],payload['partition_by'],payload['order_by'],payload['calculations'],payload.get('output_order',[]))
            direct=compute(payload['table'],payload['partition_by'],payload['order_by'],payload['calculations'])
            return {'report':report,'direct_consistent':report['table']==direct}
        return _observe(payload,action)
    def fourth(payload):
        def action():
            from api import pipeline
            from query import execute
            report=pipeline(payload['sources'],payload['steps'],payload['outputs'])
            direct=execute(payload['sources'],payload['steps'],payload['outputs'])
            return {'report':report,'direct_consistent':report==direct}
        return _observe(payload,action)
 ''',
 'README.md': '''
    # Small scalar-table toolkit

    Tables are {columns: [unique names], rows: [scalar lists]}. A scalar is None,
    an integer (not bool), or text. There is no SQL parser or external database.
    APIs return owned tables and preserve caller objects. All code is standard
    library only. Keep the original tests and observe.py byte-identical; add
    separate discoverable tests. Run python3 -m unittest discover -s tests.
 ''',
}
A1={
 'joins.py':A['joins.py'].replace("            matches=[rrow for rrow in right['rows'] if tuple(rrow[i] for i in ri)==key]\n            # Current code joins null keys and drops duplicate matches.\n            if matches: output.append(lrow+matches[0])", "            matches=[] if any(value is None for value in key) else [rrow for rrow in right['rows'] if not any(rrow[i] is None for i in ri) and tuple(rrow[i] for i in ri)==key]\n            if matches: output.extend(lrow+rrow for rrow in matches)"),
 'grouping.py':A['grouping.py'].replace('len(values),len(values),sum(v for v in values if v is not None)','len(values),sum(v is not None for v in values),sum(v for v in values if v is not None)'),
}
A2={
 'analytics.py': '''
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
 ''',
 'ordering.py': '''
    """Stable multi-key sorting with null placement independent of direction."""
    from functools import cmp_to_key
    from rows import owned
    def _indices(table,order):
        table=owned(table)
        if not isinstance(order,list): raise ValueError('order must be a list')
        prepared=[]
        seen=set()
        for key in order:
            if not isinstance(key,dict): raise ValueError('invalid sort key')
            column=key.get('column')
            descending=key.get('descending',False)
            nulls=key.get('nulls','last')
            if column not in table['columns'] or column in seen or type(descending) is not bool or nulls not in ('first','last'): raise ValueError('invalid sort key')
            seen.add(column)
            position=table['columns'].index(column)
            if len({type(row[position]) for row in table['rows'] if row[position] is not None})>1: raise ValueError('mixed sort types')
            prepared.append((position,descending,nulls))
        def compare(left,right):
            for position,descending,nulls in prepared:
                a,b=table['rows'][left][position],table['rows'][right][position]
                if a is None and b is None: difference=0
                elif a is None: difference=-1 if nulls=='first' else 1
                elif b is None: difference=1 if nulls=='first' else -1
                else:
                    difference=(a>b)-(a<b)
                    if descending: difference=-difference
                if difference: return difference
            return 0
        return sorted(range(len(table['rows'])),key=cmp_to_key(compare))
    def sort_table(table,order):
        table=owned(table)
        return {'columns':table['columns'][:],'rows':[table['rows'][index][:] for index in _indices(table,order)]}
 ''',
 'api.py':A['api.py']+'''
    def analyze(table,keys,measures,order=()):
        from analytics import aggregate
        from ordering import sort_table
        return sort_table(aggregate(table,keys,measures),[] if order==() else order)
 ''',
}
A3={
 'ordering.py':A2['ordering.py'].replace('_indices','permutation'),
 'windows.py': '''
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
 ''',
 'api.py':A2['api.py']+'''
    def enrich(table,partition_by,order_by,calculations,output_order=()):
        from windows import compute
        from ordering import sort_table
        result=compute(table,partition_by,order_by,calculations)
        return {'table':result,'ordered':sort_table(result,[] if output_order==() else output_order)}
 ''',
}
A4={
 'projection.py': '''
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
 ''',
 'query.py': '''
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
 ''',
 'api.py':A3['api.py']+'''
    def pipeline(sources,steps,outputs):
        from query import execute
        return execute(sources,steps,outputs)
 ''',
}
from adapter_replacement import A_ACTIONS,B_ACTIONS,PAIR
A['observe.py']=OBSERVE+PAIR+A_ACTIONS
install('case_01','workspace',A)
for stage,mapping in enumerate([A1,A2,A3,A4],1): install('case_01',f'reference/stage{stage}',mapping)
put('cases/case_01/turn1.md','''
Repair the scalar-table toolkit's left join and legacy summary together. Keep original tests and observe.py byte-identical; add separate tests as useful.

left_join preserves left row order and, for each left row, emits EVERY matching right row in right input order. A composite join key containing None never matches any key, including another null key. Unmatched left rows are padded with one None per right column. Keep all right columns prefixed, preserve declared column order, reject output-name collisions and invalid key lists, and leave all inputs unchanged. Keys are nonempty equal-length lists of distinct existing columns.

summarize(table, keys, value) keeps groups in first-appearance order. Null grouping keys group normally. Its count is every group row, present is only non-None value cells, and total is the exact sum of integer non-None values (all-null total is zero). Reject noninteger present values, reserved output-name collisions, malformed tables and booleans. Empty keys mean a single group if any rows exist; an empty table still has no groups. Keep api.combine composing these corrected direct interfaces. All returned mutable tables must be detached from inputs. Briefly report the fixes and checks run.
''')
put('cases/case_01/turn2.md','''
Add named aggregation and stable sorting, integrated through api.analyze(table, keys, measures, order=()). Create analytics.aggregate and ordering.sort_table. Preserve Turn 1 and protected originals.

aggregate groups in first-seen order by the supplied distinct existing keys (possibly empty). measures is a nonempty list of {"as": unique nonempty trimmed output name, "op": operation, "column": name when needed}. Output aliases cannot collide with keys. Operations: count_all counts every row and MUST NOT have column; count counts non-null values; distinct counts distinct non-null values; sum adds integer present values (zero if none); min/max ignore None and return None if none. A sum rejects present text; min/max reject mixed integer/text types. Validate aliases, operations and column references even on empty tables. Return keys followed by aliases; empty input yields no groups.

sort_table accepts a list of distinct-column sort keys {"column", "descending": actual_bool default False, "nulls":"first"|"last" default "last"}. Null placement is independent of descending direction. Non-null values in each used sort column must have one scalar type. Apply keys lexicographically, keep input order for ties, and return an owned table. No keys means original order. analyze aggregates then sorts. Preserve all inputs and earlier APIs; add tests for nulls, mixed types, repeated values, aliases and stable ties.
''')
put('cases/case_01/turn3.md','''
Add partitioned window calculations and expose ordering.permutation(table, order), returning original row indices in the same stable order as sort_table. Create windows.compute(table, partition_by, order_by, calculations), and api.enrich(..., output_order=()). Keep all earlier behavior and protected original bytes.

Partition keys are distinct existing columns, possibly empty; None keys group normally. Order each partition using the previous sort rules. calculations is a nonempty list with unique new aliases, never colliding with existing columns. Support {"as","op"} for row_number, rank, dense_rank; and {"as","op","column"} for running_sum or lag. row_number starts at 1. rank gives equal order-key tuples the same first position with gaps; dense_rank increments once per peer group. With no order keys all rows are peers, but their original order remains available for numbering/running operations.

running_sum uses the sorted partition prefix, ignoring None and rejecting noninteger present values. lag additionally accepts positive integer offset (default 1, bool invalid) and scalar default (default None), returning the prior row's column value or default. Validate even empty tables. Append calculated columns, then restore ORIGINAL global row order. Return owned rows. enrich returns {"table": computed_table, "ordered": sort_table(computed_table, output_order)}. Preserve earlier APIs and caller objects. Add tests for ties, interleaved partitions, nulls, lag boundaries and returned-table isolation.
''')
put('cases/case_01/turn4.md','''
Add sequential named relational pipelines. Create projection.project/filter_rows, query.execute(sources, steps, outputs), and api.pipeline with the same arguments. Preserve all three earlier Turns and protected original bytes.

sources maps nonempty trimmed names to tables; validate/copy ALL sources, including unused ones. Each step has a unique new "out" name and "op". It may reference only sources or previous step outputs. join uses left/right/left_keys/right_keys and optional prefix; group uses source/keys/measures; window uses source/partition_by/order_by/calculations; sort uses source/order; project uses source/columns; filter uses source/conditions. Call the existing direct interfaces. Missing fields, unknown operations/references and overwritten names raise ValueError. Execute every step even if outputs is empty.

project selects a nonempty distinct existing-column list in requested order. filter preserves rows satisfying ALL conditions. Each condition has column and op: eq/ne/lt/le/gt/ge with a scalar value, or is_null/not_null with NO value. For eq/ne, None is an ordinary comparable scalar. Ordered comparisons require a non-null literal; a null row cell is false and incompatible present types raise ValueError. Evaluate all conditions for each row so an earlier false condition cannot conceal a type error. Empty conditions keep all rows. Validate references/operators even on empty tables.

outputs is a list of distinct known table names in requested order, possibly empty. Return {"results":[{"name","table"}], "lineage": sorted-name map for EVERY step output to its sorted unique ultimate SOURCE names}. Unary steps inherit lineage; joins union both sides. All result tables must be detached copies. Add tests composing joins/aggregates/windows/projection/filtering, source lineage, requested source tables, invalid unused sources, forward references and input/alias preservation.
''')

B={
 'pitch.py': r'''
    """Spelled pitches or integers in the 0..127 palette."""
    import re
    def parse(value):
        if type(value) is int:
            if 0<=value<=127: return value
            raise ValueError('pitch out of range')
        if not isinstance(value,str): raise ValueError('invalid pitch')
        found=re.fullmatch(r'([A-Ga-g])([#b]?)(-?[0-9]+)',value)
        if found is None: raise ValueError('invalid pitch spelling')
        letter,accidental,octave=found.groups()
        semitone={'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[letter.upper()]
        alteration={'':0,'#':1,'b':-1}[accidental]
        # Current accidental wrapping loses octave carry/borrow.
        result=12*(int(octave)+1)+(semitone+alteration)%12
        if not 0<=result<=127: raise ValueError('pitch out of range')
        return result
 ''',
 'score.py': '''
    """Owned note events and simple polyphonic measures."""
    from pitch import parse
    def integer(value,minimum=0):
        if type(value) is not int or value<minimum: raise ValueError('invalid integer')
        return value
    def identity(value):
        if not isinstance(value,str) or not value or value.strip()!=value: raise ValueError('invalid event identity')
        return value
    def canonical(events):
        if not isinstance(events,list): raise ValueError('events must be a list')
        output=[]
        seen=set()
        for event in events:
            if not isinstance(event,dict): raise ValueError('event must be a dictionary')
            try:
                key=identity(event['id'])
                start=integer(event['start'])
                duration=integer(event['duration'],1)
                pitch=parse(event['pitch'])
                velocity=integer(event.get('velocity',64),1)
            except KeyError: raise ValueError('event field missing') from None
            if key in seen or velocity>127: raise ValueError('duplicate event or invalid velocity')
            seen.add(key)
            output.append(dict(id=key,start=start,duration=duration,pitch=pitch,velocity=velocity))
        return sorted(output,key=lambda event:event['id'])
    def measure(notes):
        return {'notes':len(notes),'end':max((event['start']+event['duration'] for event in notes),default=0),
                'duration_sum':sum(event['duration'] for event in notes),'pitches':sorted({event['pitch'] for event in notes})}
 ''',
 'api.py': '''
    """JSON-ready musical event observations, without audio or external devices."""
    from score import canonical,measure
    def normalize(events):
        notes=canonical(events)
        return {'events':notes,'metrics':measure(notes)}
 ''',
 'tests/test_original.py': '''
    import copy
    import unittest
    from pitch import parse
    from score import canonical,measure
    from api import normalize
    def E(key='n',start=0,duration=4,pitch='C4',velocity=64): return dict(id=key,start=start,duration=duration,pitch=pitch,velocity=velocity)
    class OriginalContracts(unittest.TestCase):
        def test_central_pitch(self): self.assertEqual(parse('C4'),60)
        def test_sharp_carries(self): self.assertEqual(parse('B#3'),60)
        def test_flat_borrows(self): self.assertEqual(parse('Cb4'),59)
        def test_lowercase(self): self.assertEqual(parse('db4'),61)
        def test_chronological_order(self):
            notes=canonical([E('a',8),E('z',0),E('b',0,pitch='B3')])
            self.assertEqual([n['id'] for n in notes],['b','z','a'])
        def test_integer_pitch(self): self.assertEqual(parse(127),127)
        def test_metrics(self):
            self.assertEqual(measure(canonical([E('a',0,4),E('b',2,3,pitch='D4')])),
                {'notes':2,'end':5,'duration_sum':7,'pitches':[60,62]})
        def test_owned_output(self):
            events=[E()]
            before=copy.deepcopy(events)
            normalize(events)['events'][0]['start']=99
            self.assertEqual(events,before)
        def test_duplicate_identity(self):
            with self.assertRaises(ValueError): canonical([E(),E()])
        def test_invalid_pitch(self):
            for value in [True,128,'H4','C#b4','C99']:
                with self.assertRaises(ValueError): parse(value)
        def test_invalid_time_velocity(self):
            for event in [E(start=-1),E(duration=0),E(velocity=128),E(start=True)]:
                with self.assertRaises(ValueError): canonical([event])
        def test_empty_score(self): self.assertEqual(normalize([]),{'events':[],'metrics':{'notes':0,'end':0,'duration_sum':0,'pitches':[]}})
    if __name__=='__main__': unittest.main()
 ''',
 'observe.py':OBSERVE+'''
    def first(payload):
        def action():
            from api import normalize
            from score import canonical,measure
            report=normalize(payload['events'])
            notes=canonical(payload['events'])
            return {'report':report,'direct_consistent':report=={'events':notes,'metrics':measure(notes)}}
        return _observe(payload,action)
    def second(payload):
        def action():
            from api import transform_report
            from transforms import apply
            options=payload.get('options',{})
            report=transform_report(payload['events'],**options)
            return {'report':report,'direct_consistent':report['events']==apply(payload['events'],**options)}
        return _observe(payload,action)
    def third(payload):
        def action():
            from api import arrange
            from arrangement import build
            report=arrange(payload['tracks'])
            return {'report':report,'direct_consistent':report==build(payload['tracks'])}
        return _observe(payload,action)
    def fourth(payload):
        def action():
            from api import render
            from sequencer import compile
            report=render(payload['tracks'],payload['tempo'],payload.get('ticks_per_beat',480))
            direct=compile(payload['tracks'],payload['tempo'],payload.get('ticks_per_beat',480))
            return {'report':report,'direct_consistent':report==direct}
        return _observe(payload,action)
 ''',
 'README.md': '''
    # Tick-score composition helpers

    Synthetic musical note events use integer ticks, pitches 0..127 and velocity
    1..127. Spelling maps C/D/E/F/G/A/B to 0/2/4/5/7/9/11 with C0=12. This is an
    in-memory toolkit, not an audio engine or hardware protocol. Standard library
    only, no devices/services/files. Preserve inputs and returned-container
    isolation. Keep original tests/observe.py byte-identical; add separate tests.
    Run python3 -m unittest discover -s tests.
 ''',
}
B1={
 'pitch.py':B['pitch.py'].replace('        # Current accidental wrapping loses octave carry/borrow.\n        result=12*(int(octave)+1)+(semitone+alteration)%12','        result=12*(int(octave)+1)+semitone+alteration'),
 'score.py':B['score.py'].replace("return sorted(output,key=lambda event:event['id'])","return sorted(output,key=lambda event:(event['start'],event['pitch'],event['id']))"),
}
B2={
 'pitch.py':B1['pitch.py']+'''
    def transpose(value,semitones):
        if type(semitones) is not int: raise ValueError('transpose must be an integer')
        result=parse(value)+semitones
        if not 0<=result<=127: raise ValueError('transposed pitch out of range')
        return result
 ''',
 'rhythm.py': '''
    """Exact integer-tick scaling, without rounding."""
    from fractions import Fraction
    from score import integer
    def ratio(value):
        if value is None: return Fraction(1)
        if not isinstance(value,list) or len(value)!=2: raise ValueError('stretch must be a positive pair')
        return Fraction(integer(value[0],1),integer(value[1],1))
    def scale(value,stretch):
        result=integer(value)*ratio(stretch)
        if result.denominator!=1: raise ValueError('nonintegral transformed tick')
        return result.numerator
 ''',
 'transforms.py': '''
    """Transpose, reverse, exact stretch, then shift."""
    from score import canonical,measure
    from pitch import transpose as shifted_pitch
    from rhythm import ratio,scale
    def apply(events,transpose=0,stretch=None,shift=0,reverse=False):
        if type(transpose) is not int or type(shift) is not int or type(reverse) is not bool: raise ValueError('invalid transform option')
        ratio(stretch)
        notes=canonical(events)
        end=measure(notes)['end']
        output=[]
        for event in notes:
            start=end-event['start']-event['duration'] if reverse else event['start']
            transformed=scale(start,stretch)+shift
            if transformed<0: raise ValueError('negative transformed start')
            output.append(dict(event,start=transformed,duration=scale(event['duration'],stretch),pitch=shifted_pitch(event['pitch'],transpose)))
        return canonical(output)
 ''',
 'api.py':B['api.py']+'''
    def transform_report(events,**options):
        from transforms import apply
        notes=apply(events,**options)
        return {'events':notes,'metrics':measure(notes)}
 ''',
}
B3={
 'score.py':B1['score.py']+'''
    def by_track(notes,track_ids):
        return {key:measure([event for event in notes if event['track']==key]) for key in sorted(track_ids)}
 ''',
 'arrangement.py': '''
    """Track transformations, repetition and half-open clipping."""
    from score import integer,identity,measure,by_track
    from transforms import apply
    def build(tracks):
        if not isinstance(tracks,list): raise ValueError('tracks must be a list')
        seen=set()
        output=[]
        for track in tracks:
            if not isinstance(track,dict): raise ValueError('invalid track')
            try:
                key=identity(track['id'])
                source=track['events']
            except KeyError: raise ValueError('track field missing') from None
            if key in seen: raise ValueError('duplicate track')
            seen.add(key)
            options=track.get('transform',{})
            if not isinstance(options,dict) or set(options)-{'transpose','stretch','shift','reverse'}: raise ValueError('unknown transform option')
            notes=apply(source,**options)
            repeat=integer(track.get('repeat',1),1)
            period=track.get('period')
            if period is None:
                period=measure(notes)['end']
                if repeat>1 and period==0: raise ValueError('repeated empty track needs a period')
            else: integer(period,1)
            crop=track.get('crop')
            if crop is not None:
                if not isinstance(crop,list) or len(crop)!=2: raise ValueError('crop must be [start,end]')
                start,end=integer(crop[0]),integer(crop[1])
                if start>=end: raise ValueError('crop must have positive span')
            for cycle in range(repeat):
                for note in notes:
                    start=note['start']+cycle*period
                    end=start+note['duration']
                    if crop is not None:
                        start=max(start,crop[0])
                        end=min(end,crop[1])
                    if start<end: output.append(dict(note,track=key,cycle=cycle,start=start,duration=end-start))
        output.sort(key=lambda note:(note['start'],note['pitch'],note['track'],note['cycle'],note['id']))
        return {'notes':output,'metrics':measure(output),'tracks':by_track(output,seen)}
 ''',
 'api.py':B2['api.py']+'''
    def arrange(tracks):
        from arrangement import build
        return build(tracks)
 ''',
}
B4={
 'timing.py': '''
    """Piecewise tempo conversion, flooring only the accumulated total."""
    from score import integer
    def validate(tempo,ticks_per_beat):
        integer(ticks_per_beat,1)
        if not isinstance(tempo,list) or not tempo: raise ValueError('tempo must be nonempty')
        output=[]
        previous=-1
        for entry in tempo:
            if not isinstance(entry,dict): raise ValueError('invalid tempo entry')
            try: tick,rate=integer(entry['tick']),integer(entry['microseconds_per_beat'],1)
            except KeyError: raise ValueError('tempo field missing') from None
            if tick<=previous: raise ValueError('tempo ticks must strictly increase')
            previous=tick
            output.append(dict(tick=tick,microseconds_per_beat=rate))
        if output[0]['tick']!=0: raise ValueError('tempo must start at zero')
        return output
    def to_microseconds(tick,tempo,ticks_per_beat=480):
        integer(tick)
        tempo=validate(tempo,ticks_per_beat)
        cursor=0
        rate=tempo[0]['microseconds_per_beat']
        numerator=0
        for change in tempo[1:]:
            if change['tick']>=tick: break
            numerator+=(change['tick']-cursor)*rate
            cursor=change['tick']
            rate=change['microseconds_per_beat']
        numerator+=(tick-cursor)*rate
        return numerator//ticks_per_beat
 ''',
 'sequencer.py': '''
    """Stable release/attack commands from a composed arrangement."""
    from arrangement import build
    from timing import validate,to_microseconds
    def compile(tracks,tempo,ticks_per_beat=480):
        tempo=validate(tempo,ticks_per_beat)
        score=build(tracks)
        commands=[]
        for note in score['notes']:
            for kind,tick,velocity in [('on',note['start'],note['velocity']),('off',note['start']+note['duration'],0)]:
                commands.append({'tick':tick,'microseconds':to_microseconds(tick,tempo,ticks_per_beat),
                    'kind':kind,'pitch':note['pitch'],'velocity':velocity,'track':note['track'],'cycle':note['cycle'],'id':note['id']})
        commands.sort(key=lambda command:(command['tick'],0 if command['kind']=='off' else 1,
            command['pitch'],command['track'],command['cycle'],command['id']))
        end=score['metrics']['end']
        return {'commands':commands,'end_tick':end,'end_microseconds':to_microseconds(end,tempo,ticks_per_beat),
                'notes':len(score['notes']),'tracks':sorted(score['tracks'])}
 ''',
 'api.py':B3['api.py']+'''
    def render(tracks,tempo,ticks_per_beat=480):
        from sequencer import compile
        return compile(tracks,tempo,ticks_per_beat)
 ''',
}
B['observe.py']=OBSERVE+PAIR+B_ACTIONS
install('case_02','workspace',B)
for stage,mapping in enumerate([B1,B2,B3,B4],1): install('case_02',f'reference/stage{stage}',mapping)
put('cases/case_02/turn1.md','''
Repair pitch spelling across octave boundaries and canonical event ordering together. Keep original tests and observe.py byte-identical, and add separate tests as useful.

Pitch input is an integer 0..127 (not bool) or spelling letter A..G case-insensitive, optional single # or lowercase b, then a decimal octave with an optional minus sign (no plus sign). Map C/D/E/F/G/A/B to 0/2/4/5/7/9/11, with C0=12. Apply accidental arithmetic BEFORE range checking, including carry/borrow across octaves: do not wrap a pitch class inside its original octave. Reject malformed spellings and out-of-range results.

Canonical events have unique nonempty trimmed IDs, nonnegative integer start, positive integer duration, pitch as the normalized integer, and integer velocity 1..127 (default 64); booleans are invalid numbers. Return only id/start/duration/pitch/velocity, sorted by start then pitch then ID. Keep measure's note count, maximum end (zero if empty), sum of durations and sorted distinct pitches correct. api.normalize must compose these direct interfaces, preserve all inputs and return detached mutable containers. Briefly report fixes and validation.
''')
put('cases/case_02/turn2.md','''
Add exact musical transformations across the pitch/time/score modules. Create rhythm.ratio/scale and transforms.apply(events, transpose=0, stretch=None, shift=0, reverse=False); add pitch.transpose(value, semitones) and api.transform_report(events, **options). Preserve Turn 1 and protected originals.

Validate transpose and shift as integers (not bool), reverse as actual bool, and stretch as None (identity) or a pair of positive integers [numerator,denominator]. Every transformed tick must be exact: a fractional start or duration raises ValueError, never rounds. Validate options even for an empty score.

Canonicalize the input, then if reversing reflect each note about the ORIGINAL maximum end: new start = end - old start - duration. Next scale start and duration by stretch, then add shift to start. Negative final starts are invalid. Add transpose to pitch and reject results outside 0..127. Preserve IDs and velocities, and return canonical chronological order. transform_report returns {"events": transformed_notes, "metrics": measure(transformed_notes)}. All outputs are detached and callers unchanged. Add tests for reverse order, exact ratios, combined shift/transpose, boundaries, invalid empty-score options and nonintegral timing.
''')
put('cases/case_02/turn3.md','''
Compose repeated, clipped tracks. Create arrangement.build(tracks), add score.by_track(notes, track_ids) using the existing measure semantics, and api.arrange(tracks). Preserve both prior Turns and protected originals.

Each track has unique nonempty trimmed id, events, optional transform dictionary containing ONLY transpose/stretch/shift/reverse, positive integer repeat (default 1), optional positive integer period, and optional crop [nonnegative_start, larger_nonnegative_end]. Booleans are invalid numbers. Apply the earlier transform FIRST. Default period is that transformed score's maximum end; an empty score repeated more than once requires an explicit positive period. A single empty track needs no period. Validate every track even if it emits no notes.

For cycle 0..repeat-1, offset transformed starts by cycle*period. Then clip each note to the half-open crop interval, discarding disjoint notes and truncating intersecting ones. Keep global tick coordinates; do not shift the crop to zero. Never conceal invalid source events/transforms by cropping. Each output note adds track and zero-based cycle while retaining its original event ID and velocity. Sort by start, pitch, track, cycle, ID. Different cycles/tracks may reuse a source event ID.

Return {"notes": arranged_notes, "metrics": measure(all_notes), "tracks": sorted-ID map to measure(each_track_notes)}, including empty tracks with zero measures. Track metrics use global end ticks and summed clipped durations, not interval unions. Keep outputs detached and inputs unchanged. Add tests for transform/repeat/crop order, overlapping repetitions, boundary exclusion, empty tracks and duplicate IDs.
''')
put('cases/case_02/turn4.md','''
Render the composed arrangement to deterministic timed note commands. Create timing.validate/to_microseconds and sequencer.compile(tracks, tempo, ticks_per_beat=480); add api.render with the same arguments. Preserve all earlier behavior and protected originals. This is an in-memory command list, not a hardware protocol or audio engine.

tempo is a nonempty list of {"tick":nonnegative_integer, "microseconds_per_beat":positive_integer}, strictly increasing by tick and starting at zero. ticks_per_beat is positive integer; booleans invalid. Validate EVERY tempo entry even beyond the last note, including an empty arrangement. to_microseconds integrates all tempo segments from tick zero to its nonnegative target tick exactly, then floors only the accumulated rational total; do not floor each segment separately.

Compile the earlier arrangement. Emit on at each note start with its velocity and off at its end with velocity zero. Each command has tick, microseconds, kind (on/off), pitch, velocity, track, cycle, id. Sort by TICK first, then off before on at the same tick, then pitch, track, cycle, ID. Do not merge separate notes or sort by rounded microseconds, which can collapse different ticks.

Return {"commands":ordered_commands, "end_tick":maximum_note_end_or_zero, "end_microseconds":converted_end, "notes":arranged_note_count, "tracks":sorted_track_IDs_including_empty_tracks}. Keep inputs unchanged and outputs detached. Add tests for tempo boundaries, accumulated fractional times, simultaneous releases/attacks, repeated/cropped notes, empty tracks and invalid tempo history.
''')

# Finite expectations are directly authored, not computed by reference execution.
def T(columns,rows): return {'columns':columns,'rows':rows}
def Expected(report):
    value={'report':report,'direct_consistent':True}
    return {'result':value,'preserved':True,'detached':True}
def oracle(function,pairs):
    return {'module':'observe','function':function,'inputs':[[p] for p,e in pairs],'expected':[Expected(e) for p,e in pairs]}
ERROR={'error':'ValueError'}
def Join(left,right,group_keys,value,left_keys=None,right_keys=None,prefix='r_'):
    return dict(left=left,right=right,left_keys=left_keys or ['id'],right_keys=right_keys or ['id'],group_keys=group_keys,value=value,prefix=prefix)
def Agg(table,keys,measures,order=None): return dict(table=table,keys=keys,measures=measures,order=[] if order is None else order)
def M(alias,op,column=None,**extra):
    result={'as':alias,'op':op,**extra}
    if column is not None: result['column']=column
    return result
def Sort(column,descending=False,nulls='last'): return dict(column=column,descending=descending,nulls=nulls)
def Win(table,partition,order,calcs,output_order=None): return dict(table=table,partition_by=partition,order_by=order,calculations=calcs,output_order=[] if output_order is None else output_order)
def WindowReport(table,ordered=None): return {'table':table,'ordered':table if ordered is None else ordered}
def Plan(sources,steps,outputs): return dict(sources=sources,steps=steps,outputs=outputs)
def Pipeline(results,lineage): return {'results':[{'name':name,'table':table} for name,table in results],'lineage':lineage}

A_ORACLES=[
 oracle('first',[
  (Join(T(['id','g'],[[1,'a'],[2,'b'],[None,'n']]),T(['id','v'],[[1,5],[1,None],[None,9]]),['g'],'r_v'),
   {'joined':T(['id','g','r_id','r_v'],[[1,'a',1,5],[1,'a',1,None],[2,'b',None,None],[None,'n',None,None]]),'groups':T(['g','count','present','total'],[['a',2,1,5],['b',1,0,0],['n',1,0,0]])}),
  (Join(T(['x','y'],[[1,2],[1,None]]),T(['x','y','v'],[[1,2,7],[1,None,9]]),[],'r_v',['x','y'],['x','y']),
   {'joined':T(['x','y','r_x','r_y','r_v'],[[1,2,1,2,7],[1,None,None,None,None]]),'groups':T(['count','present','total'],[[2,1,7]])}),
  (Join(T(['id'],[[3],[3]]),T(['k','v'],[]),['id'],'q_v',['id'],['k'],'q_'),
   {'joined':T(['id','q_k','q_v'],[[3,None,None],[3,None,None]]),'groups':T(['id','count','present','total'],[[3,2,0,0]])}),
  (Join(T(['id','g'],[]),T(['id','v'],[[1,2]]),['g'],'r_v'),
   {'joined':T(['id','g','r_id','r_v'],[]),'groups':T(['g','count','present','total'],[])}),
  (Join(T(['id'],[['1']]),T(['id','v'],[[1,9]]),['id'],'r_v'),
   {'joined':T(['id','r_id','r_v'],[['1',None,None]]),'groups':T(['id','count','present','total'],[['1',1,0,0]])}),
  (Join(T(['id'],[[1]]),T(['id','v'],[[1,-2],[1,3]]),[],'r_v'),
   {'joined':T(['id','r_id','r_v'],[[1,1,-2],[1,1,3]]),'groups':T(['count','present','total'],[[2,2,1]])}),
  (Join(T(['id'],[]),T(['id','v'],[]),[],'v',prefix=''),ERROR),
  (Join(T(['id'],[[1]]),T(['id','v'],[[1,'bad']]),[],'r_v'),ERROR),
  (Join(T(['id'],[[1,2]]),T(['id','v'],[]),[],'r_v'),ERROR),
 ]),
 oracle('second',[
  (Agg(T(['g','v'],[['a',1],['b',None],['a',3],['a',1],['b',None]]),['g'],[M('n','count_all'),M('p','count','v'),M('d','distinct','v'),M('t','sum','v'),M('lo','min','v'),M('hi','max','v')],[Sort('t',True)]),
   T(['g','n','p','d','t','lo','hi'],[['a',3,3,2,5,1,3],['b',2,0,0,0,None,None]])),
  (Agg(T(['g','word'],[['z','b'],['a',None],['z','a'],['a','c']]),['g'],[M('first','min','word'),M('last','max','word'),M('n','count','word')],[Sort('g')]),
   T(['g','first','last','n'],[['a','c','c',1],['z','a','b',2]])),
  (Agg(T(['v'],[[None],[2],[2]]),[],[M('n','count_all'),M('p','count','v'),M('d','distinct','v'),M('t','sum','v')]),T(['n','p','d','t'],[[3,2,1,4]])),
  (Agg(T(['v'],[]),[],[M('t','sum','v')]),T(['t'],[])),
  (Agg(T(['g','v'],[[None,1],['a',1],['b',1]]),['g'],[M('n','count_all')],[Sort('g',True,'first')]),T(['g','n'],[[None,1],['b',1],['a',1]])),
  (Agg(T(['g','v'],[['z',2],['a',2],['m',1]]),['g'],[M('t','sum','v')],[Sort('t',True)]),T(['g','t'],[['z',2],['a',2],['m',1]])),
  (Agg(T(['g','v'],[['a',1]]),['g'],[M('g','sum','v')]),ERROR),
  (Agg(T(['v'],[['bad']]),[],[M('t','sum','v')]),ERROR),
  (Agg(T(['g'],[[1],['x']]),['g'],[M('n','count_all')],[Sort('g')]),ERROR),
 ]),
 oracle('third',[
  (Win(T(['g','k','v'],[['a',2,5],['b',1,7],['a',1,3],['a',1,None],['b',2,2]]),['g'],[Sort('k')],
       [M('rn','row_number'),M('r','rank'),M('d','dense_rank'),M('total','running_sum','v'),M('prior','lag','v',default=-1)],[Sort('g'),Sort('rn')]),
   WindowReport(T(['g','k','v','rn','r','d','total','prior'],[['a',2,5,3,3,2,8,None],['b',1,7,1,1,1,7,-1],['a',1,3,1,1,1,3,-1],['a',1,None,2,1,1,3,3],['b',2,2,2,2,2,9,7]]),
                T(['g','k','v','rn','r','d','total','prior'],[['a',1,3,1,1,1,3,-1],['a',1,None,2,1,1,3,3],['a',2,5,3,3,2,8,None],['b',1,7,1,1,1,7,-1],['b',2,2,2,2,2,9,7]]))),
  (Win(T(['v'],[[4],[None],[1]]),[],[],[M('r','rank'),M('d','dense_rank'),M('n','row_number'),M('total','running_sum','v'),M('prior','lag','v',offset=2,default='start')]),
   WindowReport(T(['v','r','d','n','total','prior'],[[4,1,1,1,4,'start'],[None,1,1,2,4,'start'],[1,1,1,3,5,4]]))),
  (Win(T(['k','v'],[[1,10],[None,20],[2,None]]),[],[Sort('k',True,'first')],[M('rn','row_number'),M('total','running_sum','v')]),
   WindowReport(T(['k','v','rn','total'],[[1,10,3,30],[None,20,1,20],[2,None,2,20]]))),
  (Win(T(['g','v'],[[None,2],['a',5],[None,1]]),['g'],[Sort('v')],[M('prior','lag','v'),M('rn','row_number')]),
   WindowReport(T(['g','v','prior','rn'],[[None,2,1,2],['a',5,None,1],[None,1,None,1]]))),
  (Win(T(['v'],[]),[],[],[M('rn','row_number')]),WindowReport(T(['v','rn'],[]))),
  (Win(T(['v'],[['a'],['b']]),[],[],[M('prior','lag','v',default='z')]),WindowReport(T(['v','prior'],[['a','z'],['b','a']]))),
  (Win(T(['v'],[[1]]),[],[],[M('v','row_number')]),ERROR),
  (Win(T(['v'],[['bad']]),[],[],[M('total','running_sum','v')]),ERROR),
  (Win(T(['v'],[[1]]),[],[],[M('prior','lag','v',offset=0)]),ERROR),
 ]),
 oracle('fourth',[
  (Plan({'L':T(['id','g'],[[1,'a'],[2,'b']]),'R':T(['id','v'],[[1,2],[1,3],[2,None]])},[
      {'op':'join','out':'j','left':'L','right':'R','left_keys':['id'],'right_keys':['id']},
      {'op':'group','out':'agg','source':'j','keys':['g'],'measures':[M('total','sum','r_v'),M('n','count_all')]},
      {'op':'filter','out':'f','source':'agg','conditions':[{'column':'total','op':'gt','value':0}]},
      {'op':'window','out':'w','source':'f','partition_by':[],'order_by':[Sort('total',True)],'calculations':[M('cumulative','running_sum','total')]},
      {'op':'project','out':'p','source':'w','columns':['g','total','cumulative']}],['p','agg']),
   Pipeline([('p',T(['g','total','cumulative'],[['a',5,5]])),('agg',T(['g','total','n'],[['a',5,2],['b',0,1]]))],{'agg':['L','R'],'f':['L','R'],'j':['L','R'],'p':['L','R'],'w':['L','R']})),
  (Plan({'a':T(['x'],[[None],[2],[1]])},[{'op':'filter','out':'kept','source':'a','conditions':[{'column':'x','op':'not_null'}]},
       {'op':'sort','out':'ordered','source':'kept','order':[Sort('x',True)]}],['ordered','a']),
   Pipeline([('ordered',T(['x'],[[2],[1]])),('a',T(['x'],[[None],[2],[1]]))],{'kept':['a'],'ordered':['a']})),
  (Plan({'s':T(['k','v'],[[1,4],[1,2],[2,3]])},[
      {'op':'window','out':'w','source':'s','partition_by':['k'],'order_by':[Sort('v')],'calculations':[M('r','rank'),M('prior','lag','v',default=0)]},
      {'op':'filter','out':'first','source':'w','conditions':[{'column':'r','op':'eq','value':1}]}],['first']),
   Pipeline([('first',T(['k','v','r','prior'],[[1,2,1,0],[2,3,1,0]]))],{'first':['s'],'w':['s']})),
  (Plan({'s':T(['x'],[[1]])},[],['s']),Pipeline([('s',T(['x'],[[1]]))],{})),
  (Plan({'s':T(['v'],[])},[{'op':'group','out':'g','source':'s','keys':[],'measures':[M('t','sum','v')]}],[]),Pipeline([],{'g':['s']})),
  (Plan({'s':T(['x'],[[1]])},[{'op':'project','out':'p','source':'later','columns':['x']}],['p']),ERROR),
  (Plan({'s':T(['x'],[[1]]),'unused':T(['x'],[[1,2]])},[],['s']),ERROR),
  (Plan({'s':T(['x'],[['bad']])},[{'op':'filter','out':'f','source':'s','conditions':[{'column':'x','op':'eq','value':'never'},{'column':'x','op':'lt','value':1}]}],['f']),ERROR),
  (Plan({'s':T(['x'],[[1]])},[{'op':'project','out':'s','source':'s','columns':['x']}],['s']),ERROR),
 ])]

# Music expectations contain explicit ticks/pitches/times; no renderer is used.
def E(key='n',start=0,duration=4,pitch='C4',velocity=64): return dict(id=key,start=start,duration=duration,pitch=pitch,velocity=velocity)
def N(key,start,duration,pitch,velocity=64,track=None,cycle=None):
    result=dict(id=key,start=start,duration=duration,pitch=pitch,velocity=velocity)
    if track is not None: result.update(track=track,cycle=cycle)
    return result
def Measure(notes,end,duration_sum,pitches): return dict(notes=notes,end=end,duration_sum=duration_sum,pitches=pitches)
def Normal(events,metrics): return dict(events=events,metrics=metrics)
def Track(key,events,**options): return dict(id=key,events=events,**options)
def Arranged(notes,metrics,tracks): return dict(notes=notes,metrics=metrics,tracks=tracks)
def Tempo(tick,rate): return dict(tick=tick,microseconds_per_beat=rate)
def Command(tick,us,kind,pitch,track,key,cycle=0,velocity=None):
    return dict(tick=tick,microseconds=us,kind=kind,pitch=pitch,velocity=(0 if kind=='off' else 64) if velocity is None else velocity,track=track,cycle=cycle,id=key)
def Rendered(commands,end,us,notes,tracks): return dict(commands=commands,end_tick=end,end_microseconds=us,notes=notes,tracks=tracks)
def RenderInput(tracks,tempo,tpb=480): return dict(tracks=tracks,tempo=tempo,ticks_per_beat=tpb)
ZERO=Measure(0,0,0,[])
REPEATED=[Track('t',[E('n',0,4,60)],transform={'shift':1},repeat=2,period=3,crop=[2,7])]
B_ORACLES=[
 oracle('first',[
  ({'events':[E('z',4,2,'Cb4'),E('a',0,4,'B#3',80)]},Normal([N('a',0,4,60,80),N('z',4,2,59)],Measure(2,6,6,[59,60]))),
  ({'events':[E('b',0,1,'C4'),E('a',0,2,'C4'),E('c',0,1,'B3')]},Normal([N('c',0,1,59),N('a',0,2,60),N('b',0,1,60)],Measure(3,2,4,[59,60]))),
  ({'events':[E('low',0,1,'C-1'),E('high',1,1,'G9')]},Normal([N('low',0,1,0),N('high',1,1,127)],Measure(2,2,2,[0,127]))),
  ({'events':[E('n',0,2,'db4')]},Normal([N('n',0,2,61)],Measure(1,2,2,[61]))),
  ({'events':[]},Normal([],ZERO)),
  ({'events':[E('a',0,1,60,1),E('b',0,1,62,127)]},Normal([N('a',0,1,60,1),N('b',0,1,62,127)],Measure(2,1,2,[60,62]))),
  ({'events':[E(),E()]},ERROR),
  ({'events':[E(pitch='Cb-1')]},ERROR),
  ({'events':[E(start=True)]},ERROR),
 ]),
 oracle('second',[
  ({'events':[E('a',0,2,60),E('b',4,2,64)],'options':{'reverse':True,'transpose':2,'stretch':[2,1],'shift':1}},
   Normal([N('b',1,4,66),N('a',9,4,62)],Measure(2,13,8,[62,66]))),
  ({'events':[E('a',2,4,60)],'options':{'stretch':[1,2],'shift':3}},Normal([N('a',4,2,60)],Measure(1,6,2,[60]))),
  ({'events':[E('n',0,1,'C0')],'options':{'transpose':-1}},Normal([N('n',0,1,11)],Measure(1,1,1,[11]))),
  ({'events':[E('n',0,3,60)],'options':{'stretch':[2,3]}},Normal([N('n',0,2,60)],Measure(1,2,2,[60]))),
  ({'events':[],'options':{'stretch':[3,2]}},Normal([],ZERO)),
  ({'events':[E('z',4,2,60),E('a',0,1,64)],'options':{}},Normal([N('a',0,1,64),N('z',4,2,60)],Measure(2,6,3,[60,64]))),
  ({'events':[E('n',1,2,60)],'options':{'stretch':[1,2]}},ERROR),
  ({'events':[E('n',0,1,127)],'options':{'transpose':1}},ERROR),
  ({'events':[],'options':{'reverse':1}},ERROR),
 ]),
 oracle('third',[
  ({'tracks':REPEATED},Arranged([N('n',2,3,60,track='t',cycle=0),N('n',4,3,60,track='t',cycle=1)],Measure(2,7,6,[60]),{'t':Measure(2,7,6,[60])})),
  ({'tracks':[Track('z',[E('a',0,2,64)]),Track('a',[E('a',0,1,60)],repeat=2)]},
   Arranged([N('a',0,1,60,track='a',cycle=0),N('a',0,2,64,track='z',cycle=0),N('a',1,1,60,track='a',cycle=1)],Measure(3,2,4,[60,64]),{'a':Measure(2,2,2,[60]),'z':Measure(1,2,2,[64])})),
  ({'tracks':[Track('empty',[]),Track('gone',[E('n',0,2,60)],crop=[2,4])]},Arranged([],ZERO,{'empty':ZERO,'gone':ZERO})),
  ({'tracks':[Track('r',[E('a',0,1,60),E('b',2,1,62)],transform={'reverse':True,'transpose':-12})]},
   Arranged([N('b',0,1,50,track='r',cycle=0),N('a',2,1,48,track='r',cycle=0)],Measure(2,3,2,[48,50]),{'r':Measure(2,3,2,[48,50])})),
  ({'tracks':[Track('e',[],repeat=3,period=2)]},Arranged([],ZERO,{'e':ZERO})),
  ({'tracks':[Track('x',[E('n',0,2,60)],repeat=2,crop=[1,3])]},Arranged([N('n',1,1,60,track='x',cycle=0),N('n',2,1,60,track='x',cycle=1)],Measure(2,3,2,[60]),{'x':Measure(2,3,2,[60])})),
  ({'tracks':[Track('same',[]),Track('same',[])]},ERROR),
  ({'tracks':[Track('e',[],repeat=2)]},ERROR),
  ({'tracks':[Track('bad',[E(pitch=128)],crop=[100,101])]},ERROR),
 ]),
 oracle('fourth',[
  (RenderInput([Track('t',[E('a',0,2,60),E('b',2,1,60)])],[Tempo(0,600)],3),
   Rendered([Command(0,0,'on',60,'t','a'),Command(2,400,'off',60,'t','a'),Command(2,400,'on',60,'t','b'),Command(3,600,'off',60,'t','b')],3,600,2,['t'])),
  (RenderInput([Track('t',[E('n',1,3,62)])],[Tempo(0,10),Tempo(2,5)],3),
   Rendered([Command(1,3,'on',62,'t','n'),Command(4,10,'off',62,'t','n')],4,10,1,['t'])),
  (RenderInput([Track('t',[E('a',0,1,64),E('b',1,1,60)])],[Tempo(0,1)],10),
   Rendered([Command(0,0,'on',64,'t','a'),Command(1,0,'off',64,'t','a'),Command(1,0,'on',60,'t','b'),Command(2,0,'off',60,'t','b')],2,0,2,['t'])),
  (RenderInput(REPEATED,[Tempo(0,100)],1),
   Rendered([Command(2,200,'on',60,'t','n',0),Command(4,400,'on',60,'t','n',1),Command(5,500,'off',60,'t','n',0),Command(7,700,'off',60,'t','n',1)],7,700,2,['t'])),
  (RenderInput([Track('e',[])],[Tempo(0,500)]),Rendered([],0,0,0,['e'])),
  (RenderInput([Track('z',[E('n',0,1,60)]),Track('a',[E('n',0,1,60)])],[Tempo(0,1)],1),
   Rendered([Command(0,0,'on',60,'a','n'),Command(0,0,'on',60,'z','n'),Command(1,1,'off',60,'a','n'),Command(1,1,'off',60,'z','n')],1,1,2,['a','z'])),
  (RenderInput([], [Tempo(1,100)]),ERROR),
  (RenderInput([Track('e',[])],[Tempo(0,100),Tempo(100,0)]),ERROR),
  (RenderInput([], [Tempo(0,100)],True),ERROR),
 ])]

cases=[]
for identity,checks in [('case_01',A_ORACLES),('case_02',B_ORACLES)]:
    cases.append({'id':identity,'workspace_dir':f'cases/{identity}/workspace',
        'immutable_files':['tests/test_original.py','observe.py'],
        'turns':[{'task':f'cases/{identity}/turn{stage}.md','oracle':check} for stage,check in enumerate(checks,1)]})
manifest={'version':1,'runtime_profile':{'experiment_profile':'thinking128k','model':'deepseek-flash'},'cases':cases}
put('manifest.json',json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
put('PROVENANCE.md','''
# New dispatch v5 synthetic provenance

Two wholly new application domains and APIs were authored, with four substantive
natural feature Turns per case and at least two integrated implementation modules
per stage. They are not renamed prior tasks or simplified to chase prior grades.
All candidate code, prompts, protected original tests, no-answer observation
adapters, external finite inputs/expected values and reference overlays are new.
Expected values were directly specified, not reverse-engineered from reference
execution. No old suite files, runtime code, private diagnostics/transcripts,
credentials or outside solutions were read. No API/model coding calls were made.

This explicit new dispatch follows the parent's reported generic correction;
it does not extend an expired author assignment or rescore any previous result.
The exact resource profile remains thinking128k; no resources or permissions are
changed by this author. Prior exposures, original scores and failed attempts
remain unchanged. Independence is role separation within the same AI environment,
not external statistical independence. The implementer sees only public metadata
before source/profile freeze and first exposure; no semantic tuning is authorized.

Coding-model payload is only current synthetic workspace and current natural task.
Oracle data, expected values, references, author sources and validation records
stay outside workspaces/model payload. Protected adapters contain computed
observations of integrated/direct APIs and mutable-container identity, not hidden
answers. Real-model quality, actual final-Turn automatic-note/implementation-AST
binding, distinct PIDs and execution completion are Runner measurements; offline
references never substitute for them. No filler, case recognition, prescribed tool
sequence, model selection or manufactured summary request appears in tasks.

Every stage requires unchanged original bytes, original and all added tests, and
all current/prior finite behavior. Unknown never passes; later repairs cannot
erase earlier failure. Freeze before exposure; never edit a frozen semantic file
to improve a score. Stop this dispatch after freeze. No automatic v6, API calls,
Git commits, root-document edits or previous-artifact changes belong to this role.
''')
