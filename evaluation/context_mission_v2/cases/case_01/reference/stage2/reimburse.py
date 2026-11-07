"""Record real payments, then suggest deterministic exact transfers."""
from ledger import balances
from validation import integer, member

def after_payments(entries, payments):
    net = balances(entries)
    seen = set()
    for payment in payments:
        if not isinstance(payment, dict):
            raise ValueError('payment must be a dictionary')
        try:
            identity = member(payment['id'])
            sender = member(payment['from'])
            recipient = member(payment['to'])
            cents = integer(payment['cents'], 'cents', 1)
        except KeyError:
            raise ValueError('payment is missing a required field') from None
        if identity in seen or sender == recipient:
            raise ValueError('duplicate payment id or self payment')
        if sender not in net or recipient not in net:
            raise ValueError('payment names an unknown member')
        seen.add(identity)
        net[sender] += cents
        net[recipient] -= cents
    return net

def recommend(entries, payments=()):
    net = after_payments(entries, payments)
    debtors = [[name, -value] for name, value in sorted(net.items()) if value < 0]
    creditors = [[name, value] for name, value in sorted(net.items()) if value > 0]
    transfers = []
    left = right = 0
    while left < len(debtors) and right < len(creditors):
        cents = min(debtors[left][1], creditors[right][1])
        transfers.append({'from': debtors[left][0], 'to': creditors[right][0], 'cents': cents})
        debtors[left][1] -= cents
        creditors[right][1] -= cents
        if debtors[left][1] == 0:
            left += 1
        if creditors[right][1] == 0:
            right += 1
    return {'balances': net, 'transfers': transfers}
