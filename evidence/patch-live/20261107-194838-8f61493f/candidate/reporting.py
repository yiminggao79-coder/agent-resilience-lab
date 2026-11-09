from pricing import total
from parsing import InvalidLine, parse_lines


def report(lines):
    try:
        parsed = parse_lines(lines)
        return {
            'total': total(parsed),
            'quantity': sum(qty for _, qty in parsed),
            'lines': len(parsed),
        }
    except (InvalidLine, ValueError, TypeError, KeyError):
        return {'error': 'invalid_input'}
