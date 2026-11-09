"""Expense records and per-member net balances."""
from portions import apportion
from validation import integer, member, weights

def records(entries):
    output, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('expense must be a dictionary')
        try:
            identity = member(entry['id'])
            payer = member(entry['payer'])
            amount = integer(entry['amount'], 'amount')
            shares = weights(entry['weights'])
        except KeyError:
            raise ValueError('expense is missing a required field') from None
        if identity in seen:
            raise ValueError('duplicate expense id')
        seen.add(identity)
        output.append(dict(id=identity, payer=payer, amount=amount, weights=shares))
    return output

def balances(entries):
    result = {}
    for entry in records(entries):
        payer = entry['payer']
        amount = entry['amount']
        result[payer] = result.get(payer, 0) + amount
        shares = apportion(amount, entry['weights'])
        # Include every weighted member, even those owing nothing, and keep
        # each member's offsetting cents (refunds sum to the payer's amount).
        for name in entry['weights']:
            result[name] = result.get(name, 0) - shares[name]
    return dict(sorted(result.items()))
