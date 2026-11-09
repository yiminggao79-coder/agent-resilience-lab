"""Sequential named relational pipelines over owned scalar tables."""
from rows import name, owned
from joins import left_join
from analytics import aggregate
from windows import compute
from ordering import sort_table
from projection import project, filter_rows


def _required(step, field):
    if field not in step:
        raise ValueError('missing pipeline field')
    return step[field]


def _reference(step, field, tables):
    reference = _required(step, field)
    if not isinstance(reference, str) or reference not in tables:
        raise ValueError('unknown pipeline reference')
    return reference


def _check_outputs(outputs):
    if not isinstance(outputs, list):
        raise ValueError('outputs must be a list')
    seen = set()
    for output in outputs:
        if not isinstance(output, str):
            raise ValueError('invalid output name')
        if output in seen:
            raise ValueError('duplicate output name')
        seen.add(output)


def execute(sources, steps, outputs):
    if not isinstance(sources, dict):
        raise ValueError('sources must be a dictionary')
    tables = {}
    lineage = {}
    step_outputs = set()
    for source, table in sources.items():
        source_name = name(source)
        tables[source_name] = owned(table)
        lineage[source_name] = [source_name]

    if not isinstance(steps, list):
        raise ValueError('steps must be a list')
    _check_outputs(outputs)

    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('invalid pipeline step')
        if 'out' not in step or 'op' not in step:
            raise ValueError('missing pipeline step field')
        output_name = name(step['out'])
        if output_name in tables:
            raise ValueError('pipeline output overwrites a table')
        operation = step['op']
        if not isinstance(operation, str):
            raise ValueError('invalid pipeline operation')

        if operation == 'join':
            left_name = _reference(step, 'left', tables)
            right_name = _reference(step, 'right', tables)
            left_keys = _required(step, 'left_keys')
            right_keys = _required(step, 'right_keys')
            prefix = step.get('prefix', 'r_')
            result = left_join(tables[left_name], tables[right_name], left_keys, right_keys, prefix)
            result_lineage = sorted(set(lineage[left_name]) | set(lineage[right_name]))
        elif operation == 'group':
            source_name = _reference(step, 'source', tables)
            keys = _required(step, 'keys')
            measures = _required(step, 'measures')
            result = aggregate(tables[source_name], keys, measures)
            result_lineage = sorted(set(lineage[source_name]))
        elif operation == 'window':
            source_name = _reference(step, 'source', tables)
            partition_by = _required(step, 'partition_by')
            order_by = _required(step, 'order_by')
            calculations = _required(step, 'calculations')
            result = compute(tables[source_name], partition_by, order_by, calculations)
            result_lineage = sorted(set(lineage[source_name]))
        elif operation == 'sort':
            source_name = _reference(step, 'source', tables)
            order = _required(step, 'order')
            result = sort_table(tables[source_name], order)
            result_lineage = sorted(set(lineage[source_name]))
        elif operation == 'project':
            source_name = _reference(step, 'source', tables)
            columns = _required(step, 'columns')
            result = project(tables[source_name], columns)
            result_lineage = sorted(set(lineage[source_name]))
        elif operation == 'filter':
            source_name = _reference(step, 'source', tables)
            conditions = _required(step, 'conditions')
            result = filter_rows(tables[source_name], conditions)
            result_lineage = sorted(set(lineage[source_name]))
        else:
            raise ValueError('unknown pipeline operation')

        tables[output_name] = result
        lineage[output_name] = result_lineage
        step_outputs.add(output_name)

    results = []
    for output in outputs:
        if output not in tables:
            raise ValueError('unknown pipeline output')
        results.append({'name': output, 'table': owned(tables[output])})

    return {
        'results': results,
        'lineage': {output: lineage[output]
                    for output in sorted(step_outputs)},
    }
