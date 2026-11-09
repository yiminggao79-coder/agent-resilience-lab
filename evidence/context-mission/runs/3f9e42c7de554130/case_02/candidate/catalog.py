"""Owned product records with integer unit measures."""
def name(value):
    if not isinstance(value,str) or not value or value.strip()!=value: raise ValueError('invalid name')
    return value
def integer(value,minimum=1):
    if type(value) is not int or value<minimum: raise ValueError('invalid integer')
    return value
def products(rows):
    if not isinstance(rows,list): raise ValueError('catalog must be a list')
    output={}
    for row in rows:
        if not isinstance(row,dict): raise ValueError('product must be a dictionary')
        try:
            sku=name(row['sku'])
            weight,volume=integer(row['weight']),integer(row['volume'])
        except KeyError: raise ValueError('missing product field') from None
        if sku in output: raise ValueError('duplicate sku')
        output[sku]=dict(sku=sku,weight=weight,volume=volume)
    return dict(sorted(output.items()))
