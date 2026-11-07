"""Whole-order stock reservation with deterministic packing and priority."""
from catalog import products,name,integer
from orders import quantities
from boxes import validate
from policies import compile_policy
from manifest import constrained
def allocate(catalog,stock,requests,boxes,policy=None):
    entries=products(catalog)
    if not isinstance(stock,dict) or any(sku not in entries for sku in stock): raise ValueError('invalid stock map')
    remaining={sku:integer(stock.get(sku,0),0) for sku in entries}
    box_types=validate(boxes)
    compile_policy(policy,entries,box_types)
    if not isinstance(requests,list): raise ValueError('requests must be a list')
    prepared=[]
    seen=set()
    for request in requests:
        if not isinstance(request,dict): raise ValueError('request must be a dictionary')
        try:
            identity,destination=name(request['id']),name(request['destination'])
            counts=quantities(request['lines'],entries)
        except KeyError: raise ValueError('missing request field') from None
        priority=request.get('priority',0)
        if type(priority) is not int or identity in seen: raise ValueError('invalid priority or duplicate request id')
        seen.add(identity)
        prepared.append((priority,identity,destination,counts))
    accepted=[]
    rejected=[]
    totals=dict(units=0,cartons=0,weight=0,volume=0)
    for priority,identity,destination,counts in sorted(prepared,key=lambda item:-item[0]):
        if any(quantity>remaining[sku] for sku,quantity in counts.items()):
            rejected.append({'id':identity,'reason':'stock'})
            continue
        lines=[{'sku':sku,'quantity':quantity} for sku,quantity in counts.items()]
        try: report=constrained(catalog,lines,boxes,policy)
        except ValueError:
            rejected.append({'id':identity,'reason':'unpackable'})
            continue
        for sku,quantity in counts.items(): remaining[sku]-=quantity
        accepted.append({'id':identity,'destination':destination,'manifest':report})
        for key in ('units','weight','volume'): totals[key]+=report['totals'][key]
        totals['cartons']+=len(report['cartons'])
    return {'accepted':accepted,'rejected':rejected,'remaining':remaining,'totals':totals}
