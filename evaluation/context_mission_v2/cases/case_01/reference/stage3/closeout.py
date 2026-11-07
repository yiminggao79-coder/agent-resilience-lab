"""Split a transfer plan into sender-capped rounds."""
from reimburse import recommend
from validation import integer

def installments(entries, payments=(), cap=1000):
    integer(cap, 'cap', 1)
    plan = recommend(entries, payments)
    remaining = [dict(transfer) for transfer in plan['transfers']]
    rounds = []
    while any(item['cents'] for item in remaining):
        budgets = {}
        batch = []
        for transfer in remaining:
            sender = transfer['from']
            available = budgets.setdefault(sender, cap)
            amount = min(transfer['cents'], available)
            if amount:
                batch.append({'from': sender, 'to': transfer['to'], 'cents': amount})
                transfer['cents'] -= amount
                budgets[sender] -= amount
        rounds.append(batch)
    return {'balances': plan['balances'], 'rounds': rounds,
            'principal': sum(value for value in plan['balances'].values() if value > 0),
            'count': sum(len(batch) for batch in rounds)}
