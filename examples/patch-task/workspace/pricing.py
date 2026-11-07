from rounding import money


def total(lines):
    return money(sum(line['unit_price'] * line['qty'] for line in lines))
