"""Canonical ordered quantities."""
from catalog import name,integer
def quantities(lines,products):
    if not isinstance(lines,list): raise ValueError('lines must be a list')
    output={}
    for line in lines:
        if not isinstance(line,dict): raise ValueError('line must be a dictionary')
        try: sku,quantity=name(line['sku']),integer(line['quantity'])
        except KeyError: raise ValueError('missing order field') from None
        if sku not in products: raise ValueError('unknown sku')
        output[sku]=output.get(sku,0)+quantity
    return dict(sorted(output.items()))
