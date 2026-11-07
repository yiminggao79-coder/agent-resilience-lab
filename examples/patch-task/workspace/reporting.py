from pricing import total


def report(lines):
    try:
        return {'total': total(lines), 'quantity': sum(line['qty'] for line in lines), 'lines': len(lines)}
    except (ValueError, TypeError, KeyError):
        return {'error': 'invalid_input'}
