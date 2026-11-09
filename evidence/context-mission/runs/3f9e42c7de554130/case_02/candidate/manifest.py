"""JSON-ready shipment measures."""
from catalog import products
from orders import quantities
from packing import pack
def make(catalog,lines):
    entries=products(catalog)
    counts=quantities(lines,entries)
    items=[{'sku':sku,'quantity':quantity,'weight':entries[sku]['weight']*quantity,
            'volume':entries[sku]['volume']*quantity} for sku,quantity in counts.items()]
    return {'items':items,'totals':{'units':sum(item['quantity'] for item in items),
            'weight':sum(item['weight'] for item in items),'volume':sum(item['volume'] for item in items)}}
def packed(catalog,lines,boxes):
    report=make(catalog,lines)
    report['cartons']=pack(catalog,lines,boxes)
    return report
def constrained(catalog,lines,boxes,policy=None):
    report=make(catalog,lines)
    report['cartons']=pack(catalog,lines,boxes,policy)
    return report
def dispatch_report(catalog,stock,requests,boxes,policy=None):
    from dispatch import allocate
    return allocate(catalog,stock,requests,boxes,policy)
