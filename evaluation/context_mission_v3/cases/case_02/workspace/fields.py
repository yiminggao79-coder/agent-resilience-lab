"""Validation and owned canonical record dictionaries."""
def tag(value):
    if not isinstance(value, str) or not value or value.strip() != value or '\r' in value:
        raise ValueError('tag must be nonempty trimmed text without carriage returns')
    return value

def record(value):
    if not isinstance(value, dict):
        raise ValueError('record must be a dictionary')
    try:
        result = {'tag': tag(value['tag']), 'value': value['value'], 'unit': value['unit']}
    except KeyError:
        raise ValueError('missing record field') from None
    if any(not isinstance(result[key], str) or '\r' in result[key] for key in ('value','unit')):
        raise ValueError('fields must be strings without carriage returns')
    return result
