"""JSON-ready expense statement."""
from ledger import records, balances

def statement(entries):
    canonical = records(entries)
    paid = {}
    for entry in canonical:
        paid[entry['payer']] = paid.get(entry['payer'], 0) + entry['amount']
    return {'balances': balances(canonical), 'paid': dict(sorted(paid.items())),
            'spent': sum(entry['amount'] for entry in canonical)}
