"""Column projection and row filtering over owned scalar tables."""
from rows import cell, indices, owned

_ORDERED = ('lt', 'le', 'gt', 'ge')
_FILTER_OPS = ('eq', 'ne', 'lt', 'le', 'gt', 'ge', 'is_null', 'not_null')


def project(table, columns):
    table = owned(table)
    positions = indices(table, columns, False)
    return {
        'columns': columns[:],
        'rows': [[row[index] for index in positions] for row in table['rows']],
    }


def _filter_specs(table, conditions):
    if not isinstance(conditions, list):
        raise ValueError('conditions must be a list')
    columns = table['columns']
    specs = []
    for condition in conditions:
        if not isinstance(condition, dict) or 'column' not in condition or 'op' not in condition:
            raise ValueError('invalid filter condition')
        column = condition['column']
        if not isinstance(column, str) or column not in columns:
            raise ValueError('invalid filter column')
        op = condition['op']
        if not isinstance(op, str) or op not in _FILTER_OPS:
            raise ValueError('invalid filter operation')
        index = columns.index(column)
        literal = None
        if op in ('is_null', 'not_null'):
            if 'value' in condition:
                raise ValueError('null predicate must not have a value')
        else:
            if 'value' not in condition:
                raise ValueError('missing filter value')
            literal = cell(condition['value'])
            if op in _ORDERED and literal is None:
                raise ValueError('ordered comparison requires a non-null value')
        specs.append((index, op, literal))
    return specs


def filter_rows(table, conditions):
    table = owned(table)
    specs = _filter_specs(table, conditions)
    result = []
    for row in table['rows']:
        keep = True
        for index, op, literal in specs:
            value = row[index]
            if op == 'is_null':
                matches = value is None
            elif op == 'not_null':
                matches = value is not None
            elif op == 'eq':
                matches = value == literal
            elif op == 'ne':
                matches = value != literal
            else:
                if value is None:
                    matches = False
                elif type(value) is not type(literal):
                    raise ValueError('incompatible ordered comparison types')
                elif op == 'lt':
                    matches = value < literal
                elif op == 'le':
                    matches = value <= literal
                elif op == 'gt':
                    matches = value > literal
                else:
                    matches = value >= literal
            if not matches:
                keep = False
        if keep:
            result.append(row[:])
    return {'columns': table['columns'], 'rows': result}
